"""The single entry point every agent step goes through.

Responsibilities:
  * pick the right provider adapter for a model and cache it per credential
  * retry transient provider failures with jittered backoff
  * report usage and cost in one comparable shape
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass

import structlog

from core.llm.keys import KeyResolver
from core.llm.pricing import PriceBook
from core.llm.providers import (
    AnthropicProvider,
    BaseProvider,
    CompletionRequest,
    GoogleProvider,
    LLMResult,
    Message,
    OpenAICompatProvider,
    RetryableLLMError,
    TokenCallback,
    ToolSpec,
)
from core.llm.registry import Provider, get_spec, normalise_effort

log = structlog.get_logger(__name__)


@dataclass
class RoutedResult:
    result: LLMResult
    cost_microcents: int
    billed_to_platform: bool
    attempts: int


class LLMRouter:
    def __init__(
        self,
        resolver: KeyResolver,
        price_book: PriceBook,
        *,
        max_attempts: int = 3,
        base_backoff: float = 1.5,
    ) -> None:
        self._resolver = resolver
        self._prices = price_book
        self._max_attempts = max_attempts
        self._base_backoff = base_backoff
        self._adapters: dict[tuple[Provider, str], BaseProvider] = {}

    def _adapter(self, provider: Provider, api_key: str) -> BaseProvider:
        cache_key = (provider, api_key)
        if cache_key not in self._adapters:
            self._adapters[cache_key] = _build_adapter(provider, api_key)
        return self._adapters[cache_key]

    async def aclose(self) -> None:
        await asyncio.gather(
            *(adapter.aclose() for adapter in self._adapters.values()),
            return_exceptions=True,
        )
        self._adapters.clear()

    async def complete(
        self,
        *,
        model: str,
        system: str,
        messages: list[Message],
        max_tokens: int,
        effort: str = "medium",
        json_mode: bool = False,
        timeout_seconds: float = 600.0,
        tools: list[ToolSpec] | None = None,
        on_token: TokenCallback | None = None,
    ) -> RoutedResult:
        spec = get_spec(model)
        resolved = await self._resolver.resolve(model)
        adapter = self._adapter(resolved.provider, resolved.api_key)

        request = CompletionRequest(
            spec=spec,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            effort=normalise_effort(effort),
            json_mode=json_mode,
            timeout_seconds=timeout_seconds,
            tools=tools or [],
        )

        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                # Only stream tokens on the first attempt; a retry would
                # otherwise emit the partial text of the failed one twice.
                result = await adapter.complete(
                    request, on_token=on_token if attempt == 1 else None
                )
            except RetryableLLMError as exc:
                last_error = exc
                if attempt == self._max_attempts:
                    break
                delay = self._base_backoff**attempt + random.uniform(0, 0.5)  # noqa: S311
                log.warning(
                    "llm.retry",
                    model=model,
                    provider=resolved.provider.value,
                    attempt=attempt,
                    delay=round(delay, 2),
                    error=str(exc),
                )
                await asyncio.sleep(delay)
                continue

            return RoutedResult(
                result=result,
                cost_microcents=self._prices.cost_microcents(model, result.usage),
                billed_to_platform=resolved.billed_to_platform,
                attempts=attempt,
            )

        assert last_error is not None
        raise last_error


def _build_adapter(provider: Provider, api_key: str) -> BaseProvider:
    if provider is Provider.ANTHROPIC:
        return AnthropicProvider(api_key)
    if provider is Provider.GOOGLE:
        return GoogleProvider(api_key)
    if provider in (Provider.OPENAI, Provider.XAI, Provider.OPENROUTER):
        return OpenAICompatProvider(api_key, provider=provider.value)
    raise ValueError(f"no adapter for provider {provider}")
