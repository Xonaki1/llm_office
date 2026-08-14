"""Audit log helper.

Entries are added to the caller's session so they commit atomically with the
change they describe — an audit row can never claim something the transaction
rolled back, and a successful change can never go unrecorded.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.models import AuditLog

# Keys whose values must never reach the audit log.
_REDACT = {"password", "api_key", "token", "secret", "ciphertext", "authorization"}


def _scrub(detail: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in detail.items():
        if any(marker in key.lower() for marker in _REDACT):
            clean[key] = "[redacted]"
        elif isinstance(value, dict):
            clean[key] = _scrub(value)
        else:
            clean[key] = value
    return clean


def record(
    session: AsyncSession,
    *,
    action: str,
    org_id: str | None = None,
    actor_user_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    **detail: Any,
) -> AuditLog:
    entry = AuditLog(
        org_id=org_id,
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        ip_address=ip_address,
        user_agent=(user_agent or "")[:400] or None,
        detail=_scrub(detail),
    )
    session.add(entry)
    return entry


# Action names, kept as constants so queries and dashboards do not drift.
USER_REGISTERED = "user.registered"
USER_LOGIN = "user.login"
USER_LOGIN_FAILED = "user.login_failed"
USER_LOGOUT = "user.logout"
TOKEN_REFRESH_REUSE = "token.refresh_reuse_detected"  # noqa: S105 - action name
PASSWORD_CHANGED = "user.password_changed"  # noqa: S105 - action name
API_KEY_ADDED = "api_key.added"
API_KEY_DELETED = "api_key.deleted"
AGENT_CREATED = "agent.created"
AGENT_UPDATED = "agent.updated"
AGENT_DELETED = "agent.deleted"
WORKFLOW_CREATED = "workflow.created"
WORKFLOW_UPDATED = "workflow.updated"
RUN_CREATED = "run.created"
RUN_CANCELLED = "run.cancelled"
CREDITS_TOPPED_UP = "credits.topup"
CREDITS_ADJUSTED = "credits.adjustment"
MEMBER_INVITED = "member.invited"
MEMBER_ROLE_CHANGED = "member.role_changed"
MEMBER_REMOVED = "member.removed"
