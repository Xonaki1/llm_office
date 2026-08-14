from __future__ import annotations

import anthropic
from anthropic import AsyncAnthropic

from core.llm.providers.base import (
    BaseProvider,
    CompletionRequest,
    ContextWindowExceeded,
    LLMError,
    LLMResult,
    RefusalError,
    RetryableLLMError,
    TokenCallback,
    Usage,
)
from core.llm.registry import EffortStyle


class AnthropicProvider(BaseProvider):
    provider_name = "anthropic"

    def __init__(self, api_key: str, *, base_url: str | None = None) -> None:
        super().__init__(api_key, base_url=base_url)
        # The SDK retries 429/5xx itself; our own retry layer sits above it and
        # handles the cases the SDK gives up on.
        self._client = AsyncAnthropic(api_key=api_key, base_url=base_url, max_retries=2)

    async def aclose(self) -> None:
        await self._client.close()

    async def complete(
        self, request: CompletionRequest, *, on_token: TokenCallback | None = None
    ) -> LLMResult:
        spec = request.spec
        params: dict = {
            "model": spec.id,
            "max_tokens": min(request.max_tokens, spec.max_output_tokens),
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
        }

        if request.system:
            block: dict = {"type": "text", "text": request.system}
            if spec.supports_prompt_cache and request.cache_system_prompt:
                # The system prompt is identical on every step of a run, so this
                # turns later steps into cache reads at ~0.1x input price.
                block["cache_control"] = {"type": "ephemeral"}
            params["system"] = [block]

        if spec.effort_style == EffortStyle.ANTHROPIC:
            params["thinking"] = {"type": "adaptive"}
            params["output_config"] = {"effort": request.effort}

        client = self._client.with_options(timeout=request.timeout_seconds)

        try:
            async with client.messages.stream(**params) as stream:
                async for chunk in stream.text_stream:
                    if on_token is not None:
                        await on_token(chunk)
                message = await stream.get_final_message()
        except anthropic.RateLimitError as exc:
            raise RetryableLLMError(f"anthropic rate limit: {exc}") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise RetryableLLMError(f"anthropic server error {exc.status_code}") from exc
            if "context" in str(exc).lower() and "long" in str(exc).lower():
                raise ContextWindowExceeded(str(exc)) from exc
            raise LLMError(f"anthropic error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise RetryableLLMError(f"anthropic connection error: {exc}") from exc

        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            raise RefusalError(
                getattr(details, "category", None), getattr(details, "explanation", None)
            )
        if message.stop_reason == "model_context_window_exceeded":
            raise ContextWindowExceeded("conversation exceeded the model context window")

        text = "".join(b.text for b in message.content if b.type == "text")
        raw = message.usage
        cache_read = getattr(raw, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(raw, "cache_creation_input_tokens", 0) or 0

        usage = Usage(
            # Anthropic reports uncached input separately from cache reads/writes.
            input_tokens=raw.input_tokens + cache_read + cache_write,
            output_tokens=raw.output_tokens,
            cached_input_tokens=cache_read,
            raw=raw.model_dump() if hasattr(raw, "model_dump") else {},
        )
        return LLMResult(
            text=text,
            model=spec.id,
            provider=self.provider_name,
            usage=usage,
            stop_reason=message.stop_reason,
        )
