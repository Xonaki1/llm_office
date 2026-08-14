from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )


# --- tenancy & identity --------------------------------------------------


class Org(Base, TimestampMixin):
    __tablename__ = "orgs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    # managed | byok | hybrid — default key strategy for runs in this org
    key_mode: Mapped[str] = mapped_column(String(16), default="managed", nullable=False)
    # Authoritative balance is the sum of the ledger; this column is a cached
    # projection kept in the same transaction as every ledger write.
    credits_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (CheckConstraint("key_mode in ('managed','byok','hybrid')"),)


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Bumped on password change or "sign out everywhere"; access tokens issued
    # before the bump are rejected without a database lookup per request.
    token_epoch: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Membership(Base, TimestampMixin):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "org_id", name="uq_membership_user_org"),
        CheckConstraint("role in ('owner','admin','member','viewer')"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[str] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), default="member", nullable=False)

    user: Mapped[User] = relationship(back_populates="memberships")
    org: Mapped[Org] = relationship()


class RefreshToken(Base, TimestampMixin):
    """Only the SHA-256 of the token is stored. Rotation is enforced: using a
    token marks it used, and re-use of an already-used token revokes the whole
    family, which is the standard detection for a stolen refresh token."""

    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    family_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(400))
    ip_address: Mapped[str | None] = mapped_column(String(64))


# --- provider credentials ------------------------------------------------


class ApiKey(Base, TimestampMixin):
    """A BYOK provider key. `ciphertext` is envelope-encrypted and bound to the
    org via AEAD associated data; only `mask` and `fingerprint` leave the server."""

    __tablename__ = "api_keys"
    __table_args__ = (
        UniqueConstraint("org_id", "provider", name="uq_api_key_org_provider"),
        CheckConstraint("provider in ('anthropic','openai','xai','google','openrouter')"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    mask: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(36))


# --- agents & workflows --------------------------------------------------


class Agent(Base, TimestampMixin):
    __tablename__ = "agents"
    __table_args__ = (
        Index("ix_agents_org_active", "org_id", "is_active"),
        CheckConstraint("effort in ('low','medium','high','xhigh','max')"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(100), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    # Canonical scale; each provider adapter maps it onto its own knob.
    effort: Mapped[str] = mapped_column(String(16), default="medium", nullable=False)
    max_tokens: Mapped[int] = mapped_column(Integer, default=8000, nullable=False)
    tools: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36))


class Workflow(Base, TimestampMixin):
    __tablename__ = "workflows"
    __table_args__ = (Index("ix_workflows_org", "org_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # pipeline | supervisor | debate | blackboard | swarm | custom
    preset: Mapped[str] = mapped_column(String(32), default="pipeline", nullable=False)
    # Topology config plus per-run ceilings. Also what the React Flow editor edits.
    graph: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36))


# --- runs ----------------------------------------------------------------


class Run(Base, TimestampMixin):
    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_org_created", "org_id", "created_at"),
        Index("ix_runs_status", "status"),
        CheckConstraint(
            "status in ('queued','running','succeeded','failed','cancelled',"
            "'budget_exceeded','timed_out')"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    input: Mapped[str] = mapped_column(Text, default="", nullable=False)
    output: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    key_mode: Mapped[str] = mapped_column(String(16), default="managed", nullable=False)
    # Sub-cent precision while the run is in flight; `cost_cents` is the rounded
    # figure that the credit ledger charges against.
    cost_microcents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    cost_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    billable_microcents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    tokens_in: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    steps_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_steps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_cost_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Set by the API on POST /runs/{id}/cancel; the engine checks it between steps.
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    steps: Mapped[list[RunStep]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="RunStep.index"
    )
    artifacts: Mapped[list[Artifact]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    tool_calls: Mapped[list[ToolCallLog]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="(ToolCallLog.step_index, ToolCallLog.call_index)",
    )


Index(
    "uq_runs_idempotency",
    Run.org_id,
    Run.idempotency_key,
    unique=True,
    postgresql_where=Run.idempotency_key.isnot(None),
    sqlite_where=Run.idempotency_key.isnot(None),
)


class RunStep(Base, TimestampMixin):
    __tablename__ = "run_steps"
    __table_args__ = (UniqueConstraint("run_id", "index", name="uq_run_step_index"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String(36))
    agent_name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    role: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    model: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    provider: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    input: Mapped[str] = mapped_column(Text, default="", nullable=False)
    output: Mapped[str] = mapped_column(Text, default="", nullable=False)
    stop_reason: Mapped[str | None] = mapped_column(String(48))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_microcents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # A turn that used tools spans several model calls; both counts are kept so
    # a slow or expensive step can be attributed to the right cause.
    model_calls: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    run: Mapped[Run] = relationship(back_populates="steps")


class Artifact(Base, TimestampMixin):
    """A durable output extracted from a run: a file, document or dataset.

    Versions are append-only — an agent revising a file writes version N+1 rather
    than overwriting, so the history of a deliverable is auditable."""

    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("run_id", "path", "version", name="uq_artifact_version"),
        Index("ix_artifacts_run_path", "run_id", "path"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    org_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    path: Mapped[str] = mapped_column(String(400), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), default="text", nullable=False)
    language: Mapped[str | None] = mapped_column(String(40))
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    produced_by_step: Mapped[int | None] = mapped_column(Integer)
    produced_by_agent: Mapped[str | None] = mapped_column(String(200))

    run: Mapped[Run] = relationship(back_populates="artifacts")


class ToolCallLog(Base, TimestampMixin):
    """Every tool invocation made during a run.

    Tools are where an agent reaches outside its own transcript, so each call is
    recorded with its arguments and result - an audit of a run has to be able to
    answer "what did it actually touch?" without replaying the model.
    """

    __tablename__ = "tool_calls"
    __table_args__ = (
        UniqueConstraint("run_id", "step_index", "call_index", name="uq_tool_call_index"),
        Index("ix_tool_calls_run", "run_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    call_index: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String(36))
    agent_name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    tool: Mapped[str] = mapped_column(String(80), nullable=False)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    result: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_error: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    run: Mapped[Run] = relationship(back_populates="tool_calls")


# --- billing -------------------------------------------------------------


class CreditLedger(Base, TimestampMixin):
    """Append-only. The org balance is the sum of `amount_cents`; entries are
    never updated or deleted, so any balance can be reconstructed and audited."""

    __tablename__ = "credit_ledger"
    __table_args__ = (
        Index("ix_ledger_org_created", "org_id", "created_at"),
        UniqueConstraint("org_id", "idempotency_key", name="uq_ledger_idempotency"),
        CheckConstraint("kind in ('topup','debit','refund','bonus','adjustment')"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    # Positive credits the org, negative debits it.
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(36), index=True)
    description: Mapped[str] = mapped_column(String(400), default="", nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36))


class ModelPrice(Base, TimestampMixin):
    """Operator-editable price override, layered on the compiled-in registry so a
    vendor price change is a database update rather than a redeploy."""

    __tablename__ = "model_prices"

    model: Mapped[str] = mapped_column(String(80), primary_key=True)
    input_per_mtok: Mapped[float] = mapped_column(Float, nullable=False)
    output_per_mtok: Mapped[float] = mapped_column(Float, nullable=False)
    cached_input_per_mtok: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(36))


# --- audit ---------------------------------------------------------------


class AuditLog(Base):
    """Security-relevant events. Written in the same transaction as the change
    it describes, so the two cannot diverge."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_org_created", "org_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    org_id: Mapped[str | None] = mapped_column(String(36))
    actor_user_id: Mapped[str | None] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(60))
    target_id: Mapped[str | None] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(400))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
