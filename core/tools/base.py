"""Tool contract.

A tool is a named, schema-described capability an agent can invoke. Tools run on
*our* infrastructure, not the model's, so every one of them is a place a hostile
or confused model can reach into the system — the base class exists to make the
security properties of each one explicit rather than incidental.

Three rules hold for every tool:

  * arguments are untrusted input and are validated before use;
  * a failure returns an error result the model can read and recover from,
    rather than raising and killing the run;
  * the result is bounded in size, because it is fed straight back into the
    next prompt and billed as input tokens.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# A tool result larger than this is truncated: it goes into the next prompt and
# a runaway result would blow both the context window and the run's budget.
MAX_RESULT_CHARS = 60_000


class SideEffect(StrEnum):
    """What a tool can do to the world outside the run.

    Used to decide which tools an agent may hold, and to make an audit of a run
    answer "could this have changed anything?" without reading every call.
    """

    READ_ONLY = "read_only"  # observes run state only
    NETWORK = "network"  # reaches the public internet
    WRITE = "write"  # mutates run state (artifacts)


class ToolError(Exception):
    """A failure the model should see and may be able to work around."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        self.retryable = retryable
        super().__init__(message)


@dataclass
class ToolContext:
    """Everything a tool is allowed to know about the run invoking it.

    Deliberately narrow: a tool gets the run's own state and nothing else — no
    database session, no credentials, no other tenant's data.
    """

    run_id: str
    org_id: str
    agent_id: str
    agent_name: str
    step_index: int
    # The live artifact store for this run: path -> latest content.
    artifacts: dict[str, str]
    read_artifact_version: dict[str, int]
    write_artifact: Any  # Callable[[str, str, str], Awaitable[int]]


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    # Structured payload for the UI; never sent to the model.
    metadata: dict[str, Any] = field(default_factory=dict)

    def truncated(self, limit: int = MAX_RESULT_CHARS) -> ToolResult:
        if len(self.content) <= limit:
            return self
        omitted = len(self.content) - limit
        return ToolResult(
            content=(
                self.content[:limit]
                + f"\n\n[... {omitted} characters omitted; the result was too large "
                f"to return in full. Narrow the request and try again.]"
            ),
            is_error=self.is_error,
            metadata={**self.metadata, "truncated_chars": omitted},
        )


@dataclass(frozen=True)
class ToolSchema:
    """The vendor-neutral description handed to a model."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema, object at the top level


@dataclass
class ToolCall:
    """One invocation requested by a model."""

    id: str
    name: str
    arguments: dict[str, Any]


class Tool(ABC):
    name: str
    description: str
    parameters: dict[str, Any]
    side_effect: SideEffect = SideEffect.READ_ONLY
    # Wall-clock ceiling for one invocation. A tool that hangs would otherwise
    # hold the run's step open until the step timeout.
    timeout_seconds: float = 30.0

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name, description=self.description, parameters=self.parameters
        )

    @abstractmethod
    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        ...

    async def invoke(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        """Run with the failure and size guarantees the engine relies on."""
        try:
            result = await asyncio.wait_for(
                self.run(arguments, context), timeout=self.timeout_seconds
            )
        except TimeoutError:
            return ToolResult(
                content=f"Error: {self.name} timed out after {self.timeout_seconds:.0f}s.",
                is_error=True,
            )
        except ToolError as exc:
            return ToolResult(content=f"Error: {exc}", is_error=True)
        except Exception as exc:  # noqa: BLE001 - a broken tool must not kill the run
            return ToolResult(
                content=f"Error: {self.name} failed unexpectedly ({type(exc).__name__}).",
                is_error=True,
                metadata={"exception": type(exc).__name__},
            )
        return result.truncated()


def require_str(arguments: dict[str, Any], key: str, *, max_length: int = 4000) -> str:
    """Read a required string argument. Model output is untrusted, so the type
    and the length are both checked rather than assumed."""
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"`{key}` is required and must be a non-empty string")
    if len(value) > max_length:
        raise ToolError(f"`{key}` must be at most {max_length} characters")
    return value.strip()


def optional_int(
    arguments: dict[str, Any], key: str, *, default: int, low: int, high: int
) -> int:
    value = arguments.get(key, default)
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ToolError(f"`{key}` must be an integer") from exc
    return max(low, min(high, number))
