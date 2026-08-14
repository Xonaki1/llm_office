"""Google Gemini adapter (google-genai SDK).

Gemini expresses reasoning depth as a *thinking token budget* rather than a
categorical effort level, so the registry maps our scale onto a token count.
"""

from __future__ import annotations

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

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

        contents = [
            types.Content(
                role="model" if m.role == "assistant" else "user",
                parts=[types.Part.from_text(text=m.content)],
            )
            for m in request.messages
        ]

        config_kwargs: dict = {
            "max_output_tokens": min(request.max_tokens, spec.max_output_tokens),
        }
        if request.system:
            config_kwargs["system_instruction"] = request.system
        if spec.effort_style == EffortStyle.GOOGLE_THINKING:
            config_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_budget=GOOGLE_THINKING_BUDGET[request.effort]
            )
        if request.json_mode and spec.supports_json_mode:
            config_kwargs["response_mime_type"] = "application/json"

        parts: list[str] = []
        usage = Usage()
        stop_reason: str | None = None

        try:
            stream = await self._client.aio.models.generate_content_stream(
                model=spec.id,
                contents=contents,
                config=types.GenerateContentConfig(**config_kwargs),
            )
            async for chunk in stream:
                text = getattr(chunk, "text", None)
                if text:
                    parts.append(text)
                    if on_token is not None:
                        await on_token(text)
                if getattr(chunk, "usage_metadata", None):
                    usage = _read_usage(chunk.usage_metadata)
                for candidate in getattr(chunk, "candidates", None) or []:
                    if candidate.finish_reason is not None:
                        stop_reason = str(candidate.finish_reason)
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
        )


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
