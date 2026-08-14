"""Run event bus and cancellation channel.

The worker publishes to `run:{id}`; the API subscribes and re-emits over SSE.
Every event is also appended to a capped list so a client that connects late —
or reconnects after a dropped connection — can replay what it missed. Each event
carries a monotonic `seq`, which is what the client sends back as
`Last-Event-ID` to resume without duplicates.

Cancellation rides the same Redis: the API sets a flag, and the engine checks it
between steps. Redis is used rather than the database so the check is one cheap
round-trip on a hot path.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as redis

from core.config import get_settings

HISTORY_LIMIT = 5000
HISTORY_TTL_SECONDS = 60 * 60 * 24 * 3

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(
            get_settings().redis_url, decode_responses=True, health_check_interval=30
        )
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _channel(run_id: str) -> str:
    return f"run:{run_id}:events"


def _history_key(run_id: str) -> str:
    return f"run:{run_id}:history"


def _seq_key(run_id: str) -> str:
    return f"run:{run_id}:seq"


def _cancel_key(run_id: str) -> str:
    return f"run:{run_id}:cancel"


class EventEmitter:
    """Bound to a single run; handed to the orchestration engine."""

    def __init__(self, run_id: str, client: redis.Redis | None = None) -> None:
        self.run_id = run_id
        self._redis = client or get_redis()

    async def emit(self, type_: str, **payload: Any) -> int:
        seq = await self._redis.incr(_seq_key(self.run_id))
        event = {
            "seq": seq,
            "type": type_,
            "run_id": self.run_id,
            "ts": datetime.now(UTC).isoformat(),
            **payload,
        }
        raw = json.dumps(event, ensure_ascii=False)

        pipe = self._redis.pipeline()
        pipe.rpush(_history_key(self.run_id), raw)
        pipe.ltrim(_history_key(self.run_id), -HISTORY_LIMIT, -1)
        pipe.expire(_history_key(self.run_id), HISTORY_TTL_SECONDS)
        pipe.expire(_seq_key(self.run_id), HISTORY_TTL_SECONDS)
        pipe.publish(_channel(self.run_id), raw)
        await pipe.execute()
        return seq


class TokenBuffer:
    """Coalesces streamed tokens.

    One Redis publish per token would make the event stream the bottleneck on a
    fast model. Tokens are batched by size and flushed on step boundaries, which
    keeps the UI responsive without a write per character.
    """

    def __init__(self, emitter: EventEmitter, step: int, *, flush_chars: int = 80) -> None:
        self._emitter = emitter
        self._step = step
        self._flush_chars = flush_chars
        self._buffer: list[str] = []
        self._size = 0

    async def push(self, chunk: str) -> None:
        self._buffer.append(chunk)
        self._size += len(chunk)
        if self._size >= self._flush_chars:
            await self.flush()

    async def flush(self) -> None:
        if not self._buffer:
            return
        text = "".join(self._buffer)
        self._buffer.clear()
        self._size = 0
        await self._emitter.emit(STEP_TOKEN, step=self._step, text=text)


async def replay(
    run_id: str, *, after_seq: int = 0, client: redis.Redis | None = None
) -> list[dict[str, Any]]:
    r = client or get_redis()
    raw = await r.lrange(_history_key(run_id), 0, -1)
    events = [json.loads(item) for item in raw]
    if after_seq:
        events = [e for e in events if e.get("seq", 0) > after_seq]
    return events


async def subscribe(
    run_id: str, client: redis.Redis | None = None
) -> AsyncIterator[dict[str, Any]]:
    """Yield live events. Callers should send `replay()` first so nothing is lost
    between the client connecting and the subscription being established."""
    r = client or get_redis()
    pubsub = r.pubsub(ignore_subscribe_messages=True)
    await pubsub.subscribe(_channel(run_id))
    try:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            yield json.loads(message["data"])
    finally:
        await pubsub.unsubscribe(_channel(run_id))
        await pubsub.aclose()


# --- cancellation --------------------------------------------------------


async def request_cancel(run_id: str, client: redis.Redis | None = None) -> None:
    r = client or get_redis()
    await r.set(_cancel_key(run_id), "1", ex=HISTORY_TTL_SECONDS)


async def is_cancelled(run_id: str, client: redis.Redis | None = None) -> bool:
    r = client or get_redis()
    return await r.exists(_cancel_key(run_id)) == 1


async def clear_cancel(run_id: str, client: redis.Redis | None = None) -> None:
    r = client or get_redis()
    await r.delete(_cancel_key(run_id))


# --- event type constants ------------------------------------------------

RUN_START = "run.start"
RUN_END = "run.end"
RUN_ERROR = "run.error"
RUN_CANCELLED = "run.cancelled"
STEP_START = "step.start"
STEP_TOKEN = "step.token"  # noqa: S105 - event name, not a credential
STEP_END = "step.end"
STEP_RETRY = "step.retry"
ARTIFACT_WRITTEN = "artifact.written"
BUDGET_WARNING = "budget.warning"
BUDGET_EXCEEDED = "budget.exceeded"

TERMINAL_EVENTS = frozenset({RUN_END, RUN_ERROR, RUN_CANCELLED})
