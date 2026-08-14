"""Provider abstraction.

One `CompletionRequest` shape goes in, one `LLMResult` comes out, regardless of
whether the model is served by Anthropic, OpenAI, xAI or Google. Each adapter
translates the canonical effort level onto its vendor's knob, reports usage in a
comparable form, and — where tools are involved — maps between our neutral
tool-call representation and the vendor's own.

The three vendors disagree about tool calls in ways that matter:

  * Anthropic puts `tool_use` blocks in assistant content and expects
    `tool_result` blocks in the next *user* message;
  * OpenAI puts `tool_calls` on the assistant message and expects a separate
    message per result, keyed by call id;
  * Google uses `function_call` / `function_response` parts and matches them by
    function *name*, not by id.

Keeping that translation inside the adapters is what lets the orchestration
engine run one tool loop for all of them.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from core.llm.registry import ModelSpec

TokenCallback = Callable[[str], Awaitable[None]]


class LLMError(RuntimeError):
    """Any provider-side failure that already carries a useful message."""


class RetryableLLMError(LLMError):
    """Rate limits, overload, transient network faults — safe to retry."""


class RefusalError(LLMError):
    """The model declined the request on policy grounds. Retrying the same
    prompt on the same model will not help."""

    def __init__(self, category: str | None, explanation: str | None) -> None:
        self.category = category
        self.explanation = explanation
        super().__init__(f"model refused the request (category={category}): {explanation}")


class ContextWindowExceeded(LLMError):
    """The prompt no longer fits. The caller should compact the board."""


@dataclass
class ToolCallRequest:
    """A tool invocation a model asked for."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResultPart:
    tool_call_id: str
    # Carried alongside the id because Google matches results to calls by name.
    name: str
    content: str
    is_error: bool = False


@dataclass
class Message:
    """One conversation turn.

    An assistant turn may carry text, tool calls, or both. A user turn carries
    either text or the results of the tool calls from the preceding assistant
    turn — never a mixture, because no vendor accepts that.
    """

    role: str  # "user" | "assistant"
    content: str = ""
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    tool_results: list[ToolResultPart] = field(default_factory=list)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class CompletionRequest:
    spec: ModelSpec
    system: str
    messages: list[Message]
    max_tokens: int
    effort: str = "medium"
    json_mode: bool = False
    timeout_seconds: float = 600.0
    tools: list[ToolSpec] = field(default_factory=list)
    # Stable prefix caching is only worth requesting when the same system prompt
    # is reused across the many steps of one run.
    cache_system_prompt: bool = True


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def fresh_input_tokens(self) -> int:
        return max(self.input_tokens - self.cached_input_tokens, 0)


@dataclass
class LLMResult:
    text: str
    model: str
    provider: str
    usage: Usage
    stop_reason: str | None = None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class BaseProvider(ABC):
    """Adapters are constructed per API key and cached by the router."""

    provider_name: str

    def __init__(self, api_key: str, *, base_url: str | None = None) -> None:
        self._api_key = api_key
        self._base_url = base_url

    @abstractmethod
    async def complete(
        self, request: CompletionRequest, *, on_token: TokenCallback | None = None
    ) -> LLMResult:
        ...

    async def aclose(self) -> None:
        """Release any pooled HTTP connections."""
        return None


def parse_arguments(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    """Normalise a model's tool arguments into a dict.

    Models emit arguments as a JSON *string* on some providers and a parsed
    object on others, and a model under load occasionally emits malformed JSON.
    A bad payload becomes an empty dict here so the tool's own validation
    reports a readable error to the model instead of the run dying on a
    `JSONDecodeError`.
    """
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
