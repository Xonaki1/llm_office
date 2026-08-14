"""Google Gemini adapter (google-genai SDK).

Gemini differs from the other three in two ways that matter here: reasoning
depth is a *token budget* rather than a categorical level, and tool results are
matched to calls by function **name** rather than by an id. The adapter carries
the name alongside the id on every result so that mapping stays lossless.
"""

from __future__ import annotations

import uuid
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

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
from core.llm.registry import GOOGLE_THINKING_BUDGET, EffortStyle

# Gemini finish reasons that mean "blocked", not "finished".
_BLOCKED = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "IMAGE_SAFETY"}


class GoogleProvider(BaseProvider):
    provider_name = "google"

    def __init__(self, api_key: str, *, base_url: str | None = None) -> None:
        super().__init__(api_key, base_url=base_url)
        self._client = genai.Client(api_key=api_key)

    async def complete(
        self, request: CompletionRequest, *, on_token: TokenCallback | None = None
    ) -> LLMResult:
        spec = request.spec
        contents = [_to_google(message) for message in request.messages]

        config_kwargs: dict[str, Any] = {
            "max_output_tokens": min(request.max_tokens, spec.max_output_tokens),
        }
        if request.system:
            config_kwargs["system_instruction"] = request.system
        if spec.effort_style == EffortStyle.GOOGLE_THINKING:
            config_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_budget=GOOGLE_THINKING_BUDGET[request.effort]
            )
        if request.tools:
            config_kwargs["tools"] = [
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=tool.name,
                            description=tool.description,
                            parameters=_clean_schema(tool.parameters),
                        )
                        for tool in request.tools
                    ]
                )
            ]
        elif request.json_mode and spec.supports_json_mode:
            # A forced JSON mime type and function calling cannot coexist: the
            # model would answer in JSON instead of calling the tool.
            config_kwargs["response_mime_type"] = "application/json"

        parts: list[str] = []
        tool_calls: list[ToolCallRequest] = []
        usage = Usage()
        stop_reason: str | None = None

        try:
            stream = await self._client.aio.models.generate_content_stream(
                model=spec.id,
                contents=contents,
                config=types.GenerateContentConfig(**config_kwargs),
            )
            async for chunk in stream:
                for candidate in getattr(chunk, "candidates", None) or []:
                    if candidate.finish_reason is not None:
                        stop_reason = str(candidate.finish_reason)
                    content = getattr(candidate, "content", None)
                    for part in getattr(content, "parts", None) or []:
                        call = getattr(part, "function_call", None)
                        if call is not None and call.name:
                            tool_calls.append(
                                ToolCallRequest(
                                    # Gemini assigns no call id, so we mint one and
                                    # rely on the name when handing results back.
                                    id=getattr(call, "id", None) or f"gem_{uuid.uuid4().hex[:12]}",
                                    name=call.name,
                                    arguments=parse_arguments(dict(call.args or {})),
                                )
                            )
                            continue
                        text = getattr(part, "text", None)
                        if text:
                            parts.append(text)
                            if on_token is not None:
                                await on_token(text)
                if getattr(chunk, "usage_metadata", None):
                    usage = _read_usage(chunk.usage_metadata)
        except genai_errors.ClientError as exc:
            code = getattr(exc, "code", 400)
            if code == 429:
                raise RetryableLLMError(f"google rate limit: {exc}") from exc
            if "token" in str(exc).lower() and "exceed" in str(exc).lower():
                raise ContextWindowExceeded(str(exc)) from exc
            raise LLMError(f"google error {code}: {exc}") from exc
        except genai_errors.ServerError as exc:
            raise RetryableLLMError(f"google server error: {exc}") from exc

        if stop_reason and any(flag in stop_reason.upper() for flag in _BLOCKED):
            raise RefusalError(stop_reason, "Gemini safety filter blocked the response")

        return LLMResult(
            text="".join(parts),
            model=spec.id,
            provider=self.provider_name,
            usage=usage,
            stop_reason=stop_reason,
            tool_calls=tool_calls,
        )


def _to_google(message: Message) -> types.Content:
    if message.tool_results:
        return types.Content(
            role="user",
            parts=[
                types.Part.from_function_response(
                    name=result.name,
                    response={
                        "error" if result.is_error else "result": result.content,
                    },
                )
                for result in message.tool_results
            ],
        )

    if message.tool_calls:
        parts: list[types.Part] = []
        if message.content:
            parts.append(types.Part.from_text(text=message.content))
        parts.extend(
            types.Part.from_function_call(name=call.name, args=call.arguments)
            for call in message.tool_calls
        )
        return types.Content(role="model", parts=parts)

    return types.Content(
        role="model" if message.role == "assistant" else "user",
        parts=[types.Part.from_text(text=message.content)],
    )


# JSON Schema keys Gemini's function declarations do not accept.
_UNSUPPORTED_SCHEMA_KEYS = frozenset(
    {"additionalProperties", "$schema", "$id", "definitions", "$defs", "examples"}
)


def _clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Strip JSON Schema keywords Gemini rejects.

    The same tool schema is sent to four vendors, and Gemini is the strictest —
    it errors on keys the others ignore, so they are removed here rather than
    forcing every tool to write to the narrowest common denominator.
    """
    cleaned: dict[str, Any] = {}
    for key, value in schema.items():
        if key in _UNSUPPORTED_SCHEMA_KEYS:
            continue
        if isinstance(value, dict):
            cleaned[key] = _clean_schema(value)
        elif isinstance(value, list):
            cleaned[key] = [
                _clean_schema(item) if isinstance(item, dict) else item for item in value
            ]
        else:
            cleaned[key] = value
    return cleaned


def _read_usage(meta) -> Usage:
    prompt = getattr(meta, "prompt_token_count", 0) or 0
    cached = getattr(meta, "cached_content_token_count", 0) or 0
    output = getattr(meta, "candidates_token_count", 0) or 0
    thoughts = getattr(meta, "thoughts_token_count", 0) or 0
    return Usage(
        input_tokens=prompt,
        # Gemini bills thinking tokens as output.
        output_tokens=output + thoughts,
        cached_input_tokens=cached,
        reasoning_tokens=thoughts,
        raw={
            "prompt_token_count": prompt,
            "candidates_token_count": output,
            "thoughts_token_count": thoughts,
            "cached_content_token_count": cached,
        },
    )
