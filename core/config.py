from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # enable_decoding=False turns off pydantic-settings' automatic JSON parsing
    # of complex fields, so the validators below can accept the plain
    # comma-separated and bare-key forms that are natural in a .env file.
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", case_sensitive=False, enable_decoding=False
    )

    env: str = "dev"  # dev | staging | production
    service_name: str = "agents-office"
    log_level: str = "INFO"
    log_json: bool = True

    # --- infra ---
    database_url: str = "postgresql+asyncpg://office:office@localhost:5432/office"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    redis_url: str = "redis://localhost:6379/0"

    # --- crypto ---
    # Base64 32-byte key-encryption keys, keyed by version. The highest version is
    # used for new writes; older versions stay so existing rows remain readable
    # until they are re-wrapped.
    master_keys: dict[str, str] = Field(default_factory=dict)
    master_key_version: str = "1"

    # --- auth ---
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30
    password_min_length: int = 12

    # --- provider credentials (platform-owned; used in managed / hybrid mode) ---
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    xai_api_key: str | None = None
    google_api_key: str | None = None
    openrouter_api_key: str | None = None

    # --- tools ---
    tools_enabled: bool = True
    tools_network_enabled: bool = True
    # Ceiling on the tool-call round trips one agent turn may make before the
    # engine forces it to answer. Each round trip is another billed model call.
    max_tool_iterations: int = 8
    max_tool_calls_per_turn: int = 6
    tool_user_agent: str = "AgentsOffice/1.0 (+https://github.com/Xonaki1/llm_office)"
    # Empty allow list means "any public host". The deny list always applies.
    tool_allowed_hosts: list[str] = Field(default_factory=list)
    tool_blocked_hosts: list[str] = Field(default_factory=list)
    # none | brave | tavily
    search_provider: str = "none"
    brave_search_api_key: str | None = None
    tavily_api_key: str | None = None

    # --- run limits (platform ceiling; a workflow may ask for less, never more) ---
    max_steps_per_run: int = 40
    max_cost_cents_per_run: int = 500
    step_timeout_seconds: int = 600
    run_timeout_seconds: int = 3600
    max_board_chars: int = 400_000

    # --- billing ---
    billing_enabled: bool = True
    # Markup applied to platform-key token spend, in percent.
    credit_markup_percent: int = 40
    signup_bonus_cents: int = 100

    # --- rate limits (per organisation) ---
    rate_limit_runs_per_minute: int = 20
    rate_limit_api_per_minute: int = 300
    max_concurrent_runs_per_org: int = 5

    # --- http ---
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    trusted_hosts: list[str] = Field(default_factory=lambda: ["*"])
    max_request_bytes: int = 2 * 1024 * 1024

    @field_validator("master_keys", mode="before")
    @classmethod
    def _parse_master_keys(cls, value: Any) -> Any:
        """Accept either a JSON map `{"1": "<b64>"}` or a bare base64 key, which
        is treated as version "1". The bare form keeps local setup to one line."""
        if value in (None, ""):
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("{"):
                return json.loads(text)
            return {"1": text}
        return value

    @field_validator(
        "cors_origins", "trusted_hosts", "tool_allowed_hosts", "tool_blocked_hosts",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, value: Any) -> Any:
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                return json.loads(text)
            return [item.strip() for item in text.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _check_production_secrets(self) -> Settings:
        if self.is_production:
            missing: list[str] = []
            if not self.master_keys:
                missing.append("MASTER_KEYS")
            if not self.jwt_secret or len(self.jwt_secret) < 32:
                missing.append("JWT_SECRET (>=32 chars)")
            if "*" in self.cors_origins:
                missing.append("CORS_ORIGINS (wildcard is not allowed in production)")
            if missing:
                raise ValueError(
                    "refusing to start in production without: " + ", ".join(missing)
                )
        return self

    @field_validator("search_provider")
    @classmethod
    def _known_search_provider(cls, value: str) -> str:
        allowed = {"none", "brave", "tavily"}
        if value not in allowed:
            raise ValueError(f"SEARCH_PROVIDER must be one of {sorted(allowed)}")
        return value

    @property
    def is_production(self) -> bool:
        return self.env.lower() in {"production", "prod"}

    @property
    def active_master_key(self) -> tuple[str, str]:
        """(version, base64 key) used for new encryptions."""
        if not self.master_keys:
            raise ValueError("no MASTER_KEYS configured")
        version = self.master_key_version
        if version not in self.master_keys:
            version = max(self.master_keys, key=lambda v: int(v) if v.isdigit() else 0)
        return version, self.master_keys[version]


@lru_cache
def get_settings() -> Settings:
    return Settings()
