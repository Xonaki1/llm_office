"""Resolve which provider credential a run should use.

Modes:
  managed — always the platform key; the org is billed in credits.
  byok    — always the org's own key; the platform bills nothing for tokens.
  hybrid  — the org's key for expensive reasoning models, the platform's key for
            cheap utility models (routing, summarising, classification).

Decrypted keys live only in worker memory for the duration of a call. They are
never logged, never returned over the API, and never written back to the database
in plaintext.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.crypto import decrypt_secret
from core.llm.registry import Provider, get_spec


class KeyMode(StrEnum):
    MANAGED = "managed"
    BYOK = "byok"
    HYBRID = "hybrid"


# In hybrid mode these run on the platform's keys — they are cheap enough that
# routing them through a customer credential is not worth the support burden.
UTILITY_MODELS = frozenset(
    {"claude-haiku-4-5", "gpt-5-mini", "gemini-2.5-flash", "grok-4-fast", "grok-3-mini"}
)


class KeyResolutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResolvedKey:
    provider: Provider
    api_key: str
    billed_to_platform: bool  # True => deduct credits from the org


def platform_key(provider: Provider) -> str | None:
    s = get_settings()
    return {
        Provider.ANTHROPIC: s.anthropic_api_key,
        Provider.OPENAI: s.openai_api_key,
        Provider.XAI: s.xai_api_key,
        Provider.GOOGLE: s.google_api_key,
        Provider.OPENROUTER: s.openrouter_api_key,
    }.get(provider)


def configured_platform_providers() -> list[Provider]:
    return [p for p in Provider if platform_key(p)]


class KeyResolver:
    def __init__(self, session: AsyncSession, org_id: str, mode: str) -> None:
        try:
            self._mode = KeyMode(mode)
        except ValueError as exc:
            raise KeyResolutionError(f"unknown key mode: {mode}") from exc
        self._session = session
        self._org_id = org_id
        self._byok_cache: dict[Provider, str] = {}

    @property
    def mode(self) -> KeyMode:
        return self._mode

    async def _byok_key(self, provider: Provider) -> str:
        if provider in self._byok_cache:
            return self._byok_cache[provider]

        from core.models import ApiKey  # local import: avoids a cycle at module load

        stmt = select(ApiKey).where(
            ApiKey.org_id == self._org_id,
            ApiKey.provider == provider.value,
            ApiKey.is_active.is_(True),
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise KeyResolutionError(
                f"no active {provider.value} key stored for this organisation — "
                f"add one in Settings, or switch the run to managed mode"
            )
        key = decrypt_secret(row.ciphertext, aad=self._org_id)
        self._byok_cache[provider] = key
        return key

    async def resolve(self, model: str) -> ResolvedKey:
        provider = get_spec(model).provider

        use_platform = self._mode is KeyMode.MANAGED or (
            self._mode is KeyMode.HYBRID and model in UTILITY_MODELS
        )

        if use_platform:
            key = platform_key(provider)
            if not key:
                raise KeyResolutionError(
                    f"the platform has no {provider.value} credential configured; "
                    f"this organisation must supply its own key for {model}"
                )
            return ResolvedKey(provider=provider, api_key=key, billed_to_platform=True)

        key = await self._byok_key(provider)
        return ResolvedKey(provider=provider, api_key=key, billed_to_platform=False)
