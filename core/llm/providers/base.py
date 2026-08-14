"""Provider abstraction.

One `CompletionRequest` shape goes in, one `LLMResult` comes out, regardless of
whether the model is served by Anthropic, OpenAI, xAI or Google. Each adapter is
responsible for translating the canonical effort level onto its vendor's knob and
for reporting usage in a comparable form.
"""

from __future__ import annotations

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
class Message:
    role: str  # "user" | "assistant"
    content: str


@dataclass
class CompletionRequest:
    spec: ModelSpec
    system: str
    messages: list[Message]
    max_tokens: int
    effort: str = "medium"
    json_mode: bool = False
    timeout_seconds: float = 600.0
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
