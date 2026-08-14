"""Structured logging.

JSON in production so logs are queryable; a human-readable renderer in dev.
A scrubbing processor strips anything that looks like a credential before it
reaches a handler — the goal is that no code path can leak a provider key into
logs even by accident.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

from core.config import get_settings

# Provider key shapes: Anthropic sk-ant-, OpenAI sk-, xAI xai-, Google AIza.
_SECRET_PATTERN = re.compile(
    r"\b(sk-ant-[A-Za-z0-9_\-]{8,}|sk-[A-Za-z0-9_\-]{16,}|xai-[A-Za-z0-9_\-]{8,}"
    r"|AIza[A-Za-z0-9_\-]{16,}|Bearer\s+[A-Za-z0-9._\-]{16,})"
)

_SENSITIVE_KEYS = {
    "password",
    "api_key",
    "apikey",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "authorization",
    "ciphertext",
    "jwt_secret",
    "master_key",
    "master_keys",
}


def _scrub(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key, value in list(event_dict.items()):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = "[redacted]"
        elif isinstance(value, str):
            event_dict[key] = _SECRET_PATTERN.sub("[redacted]", value)
    return event_dict


def configure_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    for noisy in ("uvicorn.access", "httpx", "httpcore", "asyncio"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _scrub,
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if settings.log_json
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def bind_request_context(**kwargs: Any) -> None:
    """Attach ids (request_id, run_id, org_id) to every log line in this task."""
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_request_context() -> None:
    structlog.contextvars.clear_contextvars()
