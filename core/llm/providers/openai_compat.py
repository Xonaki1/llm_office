"""OpenAI-compatible providers: OpenAI itself, xAI (Grok) and OpenRouter.

All three speak the Chat Completions wire format, so one adapter covers them —
only the base URL and the reasoning parameter differ.
"""

from __future__ import annotations

from typing import Any

import openai
from openai import AsyncOpenAI

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
from core.llm.registry import OPENAI_EFFORT, EffortStyle, Provider

BASE_URLS: dict[str, str | None] = {
    Provider.OPENAI: None,  # SDK default
    Provider.XAI: "https://api.x.ai/v1",
    Provider.OPENROUTER: "https://openrouter.ai/api/v1",
}


class OpenAICompatProvider(BaseProvider):
    def __init__(self, api_key: str, *, provider: str, base_url: str | None = None) -> None:
        resolved = base_url or BASE_URLS.get(provider)
        super().__init__(api_key, base_url=resolved)
        self.provider_name = provider
        self._client = AsyncOpenAI(api_key=api_key, base_url=resolved, max_retries=2)

    async def aclose(self) -> None:
        await self._client.close()

    async def complete(
        self, request: CompletionRequest, *, on_token: TokenCallback | None = None
    ) -> LLMResult:
        spec = request.spec
        messages: list[dict[str, Any]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        for message in request.messages:
            messages.extend(_to_openai(message))

        params: dict[str, Any] = {
            "model": spec.id,
            "messages": messages,
            "max_completion_tokens": min(request.max_tokens, spec.max_output_tokens),
            "stream": True,
            "stream_options": {"include_usage": True},
            "timeout": request.timeout_seconds,
        }
        if spec.effort_style == EffortStyle.OPENAI_REASONING:
            params["reasoning_effort"] = OPENAI_EFFORT[request.effort]
        if request.tools:
            params["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in request.tools
            ]
        elif request.json_mode and spec.supports_json_mode:
            # Structured output and tool calling are mutually exclusive here:
            # forcing a JSON object would make the model answer instead of
            # calling the tool it was given.
            params["response_format"] = {"type": "json_object"}

        parts: list[str] = []
        usage = Usage()
        stop_reason: str | None = None
        # Tool calls arrive as deltas keyed by index; the name comes in the first
        # chunk and the arguments accumulate across the rest.
        pending: dict[int, dict[str, str]] = {}

        try:
            stream = await self._client.chat.completions.create(**params)
            async for event in stream:
                if getattr(event, "usage", None):
                    usage = _read_usage(event.usage)
                for choice in event.choices:
                    if choice.finish_reason:
                        stop_reason = choice.finish_reason
                    delta = choice.delta
                    text = getattr(delta, "content", None)
                    if text:
                        parts.append(text)
                        if on_token is not None:
                            await on_token(text)
                    for fragment in getattr(delta, "tool_calls", None) or []:
                        slot = pending.setdefault(
                            fragment.index, {"id": "", "name": "", "arguments": ""}
                        )
                        if fragment.id:
                            slot["id"] = fragment.id
                        function = getattr(fragment, "function", None)
                        if function is not None:
                            if function.name:
                                slot["name"] = function.name
                            if function.arguments:
                                slot["arguments"] += function.arguments
        except openai.RateLimitError as exc:
            raise RetryableLLMError(f"{self.provider_name} rate limit: {exc}") from exc
        except openai.APIStatusError as exc:
            if exc.status_code >= 500:
                raise RetryableLLMError(
                    f"{self.provider_name} server error {exc.status_code}"
                ) from exc
            detail = str(exc).lower()
            if "context" in detail and ("length" in detail or "window" in detail):
                raise ContextWindowExceeded(str(exc)) from exc
            raise LLMError(f"{self.provider_name} error {exc.status_code}: {exc}") from exc
        except openai.APIConnectionError as exc:
            raise RetryableLLMError(f"{self.provider_name} connection error: {exc}") from exc

        if stop_reason == "content_filter":
            raise RefusalError("content_filter", "provider content filter blocked the response")

        tool_calls = [
            ToolCallRequest(
                id=slot["id"] or f"call_{index}",
                name=slot["name"],
                arguments=parse_arguments(slot["arguments"]),
            )
            for index, slot in sorted(pending.items())
            if slot["name"]
        ]

        return LLMResult(
            text="".join(parts),
            model=spec.id,
            provider=self.provider_name,
            usage=usage,
            stop_reason=stop_reason,
            tool_calls=tool_calls,
        )


def _to_openai(message: Message) -> list[dict[str, Any]]:
    """Map one neutral turn onto Chat Completions messages.

    Returns a list because tool results become one message *each*, unlike
    Anthropic where they share a single turn.
    """
    if message.tool_results:
        return [
            {
                "role": "tool",
                "tool_call_id": result.tool_call_id,
                "content": result.content,
            }
            for result in message.tool_results
        ]

    if message.tool_calls:
        import json

        return [
            {
                "role": "assistant",
                "content": message.content or None,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, ensure_ascii=False),
                        },
                    }
                    for call in message.tool_calls
                ],
            }
        ]

    return [{"role": message.role, "content": message.content}]


def _read_usage(raw) -> Usage:
    cached = 0
    reasoning = 0
    details = getattr(raw, "prompt_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", 0) or 0
    completion_details = getattr(raw, "completion_tokens_details", None)
    if completion_details is not None:
        reasoning = getattr(completion_details, "reasoning_tokens", 0) or 0

    return Usage(
        input_tokens=raw.prompt_tokens or 0,
        output_tokens=raw.completion_tokens or 0,
        cached_input_tokens=cached,
        reasoning_tokens=reasoning,
        raw=raw.model_dump() if hasattr(raw, "model_dump") else {},
    )
