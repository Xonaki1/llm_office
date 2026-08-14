from __future__ import annotations

from typing import Any

import anthropic
from anthropic import AsyncAnthropic

from core.llm.providers.base import (
    BaseProvider,
    CompletionRequest,
    ContextWindowExceeded,
    LLMError,
    LLMResult,
    Message,
    RefusalError,
    RetryableLLMError,
    TokenCallback,
    ToolCallRequest,
    Usage,
    parse_arguments,
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
        params: dict[str, Any] = {
            "model": spec.id,
            "max_tokens": min(request.max_tokens, spec.max_output_tokens),
            "messages": [_to_anthropic(message) for message in request.messages],
        }

        if request.system:
            block: dict[str, Any] = {"type": "text", "text": request.system}
            if spec.supports_prompt_cache and request.cache_system_prompt:
                # The system prompt and the tool list are identical on every step
                # of a run, so caching them turns later steps into cache reads at
                # roughly a tenth of the input price.
                block["cache_control"] = {"type": "ephemeral"}
            params["system"] = [block]

        if request.tools:
            params["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.parameters,
                }
                for tool in request.tools
            ]

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
            text = str(exc).lower()
            if "context" in text and ("long" in text or "window" in text):
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

        text_parts: list[str] = []
        tool_calls: list[ToolCallRequest] = []
        for block in message.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCallRequest(
                        id=block.id, name=block.name, arguments=parse_arguments(block.input)
                    )
                )

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
            text="".join(text_parts),
            model=spec.id,
            provider=self.provider_name,
            usage=usage,
            stop_reason=message.stop_reason,
            tool_calls=tool_calls,
        )


def _to_anthropic(message: Message) -> dict[str, Any]:
    """Map one neutral turn onto Anthropic's content-block shape."""
    if message.tool_results:
        # Results go back as a user turn made of tool_result blocks. Anthropic
        # rejects a turn that mixes them with anything else.
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": result.tool_call_id,
                    "content": result.content,
                    "is_error": result.is_error,
                }
                for result in message.tool_results
            ],
        }

    if message.tool_calls:
        content: list[dict[str, Any]] = []
        if message.content:
            content.append({"type": "text", "text": message.content})
        content.extend(
            {
                "type": "tool_use",
                "id": call.id,
                "name": call.name,
                "input": call.arguments,
            }
            for call in message.tool_calls
        )
        return {"role": "assistant", "content": content}

    return {"role": message.role, "content": message.content}
