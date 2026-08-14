from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from core.llm.registry import EFFORT_LEVELS, get_spec
from core.orchestration.presets import PRESET_NAMES

KeyMode = Literal["managed", "byok", "hybrid"]
ProviderName = Literal["anthropic", "openai", "xai", "google", "openrouter"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]
Role = Literal["owner", "admin", "member", "viewer"]
Preset = Literal["pipeline", "supervisor", "debate", "blackboard", "swarm", "custom"]

NonEmptyStr = Annotated[str, Field(min_length=1, max_length=200)]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- auth ----------------------------------------------------------------


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    name: str | None = Field(default=None, max_length=200)
    org_name: str = Field(default="My Workspace", min_length=1, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - OAuth token *type*
    expires_in: int


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class OrgSummary(BaseModel):
    id: str
    name: str
    slug: str
    role: Role
    key_mode: KeyMode
    credits_cents: int


class MeResponse(BaseModel):
    id: str
    email: EmailStr
    name: str | None
    is_superuser: bool
    orgs: list[OrgSummary]


# --- agents --------------------------------------------------------------


class AgentBase(BaseModel):
    name: NonEmptyStr
    role: Annotated[str, Field(min_length=1, max_length=100)]
    system_prompt: str = Field(default="", max_length=100_000)
    model: Annotated[str, Field(min_length=1, max_length=80)]
    effort: Effort = "medium"
    max_tokens: int = Field(default=8000, ge=256, le=128_000)
    tools: list[Any] = Field(default_factory=list)

    @field_validator("model")
    @classmethod
    def _known_model(cls, value: str) -> str:
        # Reject unknown models here rather than at run time: an unknown model
        # cannot be priced, and an unpriced run cannot be billed correctly.
        get_spec(value)
        return value

    @field_validator("effort")
    @classmethod
    def _known_effort(cls, value: str) -> str:
        if value not in EFFORT_LEVELS:
            raise ValueError(f"effort must be one of {', '.join(EFFORT_LEVELS)}")
        return value


class AgentCreate(AgentBase):
    pass


class AgentUpdate(BaseModel):
    name: NonEmptyStr | None = None
    role: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    system_prompt: str | None = Field(default=None, max_length=100_000)
    model: str | None = None
    effort: Effort | None = None
    max_tokens: int | None = Field(default=None, ge=256, le=128_000)
    tools: list[Any] | None = None
    is_active: bool | None = None

    @field_validator("model")
    @classmethod
    def _known_model(cls, value: str | None) -> str | None:
        if value is not None:
            get_spec(value)
        return value


class AgentOut(ORMModel):
    id: str
    name: str
    role: str
    system_prompt: str
    model: str
    effort: str
    max_tokens: int
    tools: list[Any]
    is_active: bool
    created_at: datetime
    updated_at: datetime


# --- workflows -----------------------------------------------------------


class WorkflowBase(BaseModel):
    name: NonEmptyStr
    description: str = Field(default="", max_length=4000)
    preset: Preset = "pipeline"
    graph: dict[str, Any] = Field(default_factory=dict)

    @field_validator("preset")
    @classmethod
    def _known_preset(cls, value: str) -> str:
        if value not in PRESET_NAMES:
            raise ValueError(f"preset must be one of {', '.join(PRESET_NAMES)}")
        return value


class WorkflowCreate(WorkflowBase):
    pass


class WorkflowUpdate(BaseModel):
    name: NonEmptyStr | None = None
    description: str | None = Field(default=None, max_length=4000)
    preset: Preset | None = None
    graph: dict[str, Any] | None = None
    is_active: bool | None = None


class WorkflowOut(ORMModel):
    id: str
    name: str
    description: str
    preset: str
    graph: dict[str, Any]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class PresetInfo(BaseModel):
    name: str
    summary: str
    required_keys: list[str]
    optional_keys: list[str]


# --- runs ----------------------------------------------------------------


class RunCreate(BaseModel):
    workflow_id: str
    input: str = Field(min_length=1, max_length=200_000)
    key_mode: KeyMode | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)


class RunStepOut(ORMModel):
    index: int
    agent_id: str | None
    agent_name: str
    role: str
    model: str
    provider: str
    input: str
    output: str
    stop_reason: str | None
    tokens_in: int
    tokens_out: int
    cached_tokens: int
    cost_microcents: int
    latency_ms: int
    attempts: int
    created_at: datetime


class ArtifactOut(ORMModel):
    id: str
    path: str
    kind: str
    language: str | None
    version: int
    size_bytes: int
    produced_by_step: int | None
    produced_by_agent: str | None
    created_at: datetime


class ArtifactContent(ArtifactOut):
    content: str


class RunOut(ORMModel):
    id: str
    workflow_id: str
    status: str
    input: str
    output: str | None
    error: str | None
    key_mode: str
    cost_cents: int
    tokens_in: int
    tokens_out: int
    steps_used: int
    max_steps: int
    max_cost_cents: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RunDetail(RunOut):
    steps: list[RunStepOut] = Field(default_factory=list)
    artifacts: list[ArtifactOut] = Field(default_factory=list)


# --- provider keys -------------------------------------------------------


class ApiKeyCreate(BaseModel):
    provider: ProviderName
    api_key: str = Field(min_length=16, max_length=500)

    @field_validator("api_key")
    @classmethod
    def _no_whitespace(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped or " " in stripped:
            raise ValueError("api key must not contain whitespace")
        return stripped


class ApiKeyOut(ORMModel):
    """The plaintext key is never returned — only its mask and fingerprint."""

    id: str
    provider: str
    mask: str
    fingerprint: str
    is_active: bool
    last_used_at: datetime | None
    last_error: str | None
    created_at: datetime


# --- billing -------------------------------------------------------------


class BalanceOut(BaseModel):
    credits_cents: int
    billing_enabled: bool
    markup_percent: int


class LedgerEntryOut(ORMModel):
    id: str
    kind: str
    amount_cents: int
    balance_after_cents: int
    run_id: str | None
    description: str
    created_at: datetime


class TopupRequest(BaseModel):
    """Manual top-up. Guarded by the `owner` role and recorded in the audit log;
    a payment-provider integration posts through the same ledger."""

    amount_cents: int = Field(gt=0, le=10_000_000)
    description: str = Field(default="manual top-up", max_length=400)
    idempotency_key: str | None = Field(default=None, max_length=128)


# --- models catalogue ----------------------------------------------------


class ModelOut(BaseModel):
    id: str
    provider: str
    display_name: str
    input_per_mtok: float
    output_per_mtok: float
    cached_input_per_mtok: float | None
    max_output_tokens: int
    context_window: int
    supports_effort: bool
    available: bool  # platform key present, or org has its own


# --- org & members -------------------------------------------------------


class OrgUpdate(BaseModel):
    name: NonEmptyStr | None = None
    key_mode: KeyMode | None = None


class MemberOut(BaseModel):
    user_id: str
    email: EmailStr
    name: str | None
    role: Role
    joined_at: datetime


class MemberInvite(BaseModel):
    email: EmailStr
    role: Role = "member"


class MemberRoleUpdate(BaseModel):
    role: Role


# --- misc ----------------------------------------------------------------


class HealthOut(BaseModel):
    status: str
    env: str
    version: str
    database: str
    redis: str


class ErrorOut(BaseModel):
    detail: str
    request_id: str | None = None
