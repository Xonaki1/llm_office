from __future__ import annotations

import base64
import os
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest

# Settings are read at import time, so the environment must be complete before
# any application module is imported.
os.environ.update(
    {
        "ENV": "test",
        "LOG_JSON": "false",
        "LOG_LEVEL": "WARNING",
        "MASTER_KEYS": base64.b64encode(b"k" * 32).decode(),
        "MASTER_KEY_VERSION": "1",
        "JWT_SECRET": secrets.token_urlsafe(48),
        "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
        "REDIS_URL": "redis://localhost:6379/15",
        "ANTHROPIC_API_KEY": "sk-ant-test-key-for-unit-tests",
        "OPENAI_API_KEY": "sk-test-key-for-unit-tests",
        "XAI_API_KEY": "xai-test-key-for-unit-tests",
        "GOOGLE_API_KEY": "AIzaTestKeyForUnitTests0000000000000",
        "BILLING_ENABLED": "true",
        "SIGNUP_BONUS_CENTS": "100",
        "CREDIT_MARKUP_PERCENT": "40",
        "PASSWORD_MIN_LENGTH": "12",
    }
)

import fakeredis.aioredis  # noqa: E402
import httpx  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from core.llm.providers.base import LLMResult, Usage  # noqa: E402
from core.llm.router import RoutedResult  # noqa: E402
from core.models import Base  # noqa: E402
from core.ratelimit import RateLimitResult  # noqa: E402

TEST_PASSWORD = "correct-horse-battery-staple"  # noqa: S105 - test fixture credential


# --- database ------------------------------------------------------------


@pytest.fixture
async def engine():
    """One in-memory database per test.

    StaticPool keeps every connection pointed at the same in-memory database,
    which is what makes `:memory:` usable with an async engine.
    """
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def session(session_factory) -> AsyncIterator[Any]:
    async with session_factory() as s:
        yield s
        await s.commit()


# --- redis ---------------------------------------------------------------


@pytest.fixture
async def redis_client():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


# --- test doubles --------------------------------------------------------


@dataclass
class FakeEmitter:
    """Stands in for EventEmitter — records events instead of touching Redis."""

    run_id: str = "test-run"
    events: list[dict[str, Any]] = field(default_factory=list)
    _seq: int = 0

    async def emit(self, type_: str, **payload: Any) -> int:
        self._seq += 1
        self.events.append({"seq": self._seq, "type": type_, **payload})
        return self._seq

    def of_type(self, type_: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == type_]

    @property
    def types(self) -> list[str]:
        return [e["type"] for e in self.events]


@dataclass
class FakeLLM:
    """Returns canned replies in order and records what it was asked.

    `cost_microcents` defaults to exactly one cent per call so budget arithmetic
    in tests is obvious by inspection.
    """

    replies: list[str]
    calls: list[dict[str, Any]] = field(default_factory=list)
    tokens_in: int = 100
    tokens_out: int = 50
    cost_microcents: int = 1_000_000
    billed_to_platform: bool = True

    async def complete(
        self,
        *,
        model: str,
        system: str,
        messages: list[Any],
        max_tokens: int = 8000,
        effort: str = "medium",
        json_mode: bool = False,
        timeout_seconds: float = 600.0,
        on_token: Any = None,
    ) -> RoutedResult:
        self.calls.append(
            {
                "model": model,
                "system": system,
                "prompt": messages[0].content if messages else "",
                "effort": effort,
                "json_mode": json_mode,
            }
        )
        text = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        if on_token is not None:
            await on_token(text)
        return RoutedResult(
            result=LLMResult(
                text=text,
                model=model,
                provider="fake",
                usage=Usage(input_tokens=self.tokens_in, output_tokens=self.tokens_out),
                stop_reason="end_turn",
            ),
            cost_microcents=self.cost_microcents,
            billed_to_platform=self.billed_to_platform,
            attempts=1,
        )

    async def aclose(self) -> None:
        return None


class PermissiveLimiter:
    """Rate limiter stub.

    The real limiter runs Lua inside Redis; fakeredis needs an extra native
    dependency for that, and API tests are not the place to exercise it — the
    limiter has its own tests against a real Redis.
    """

    def __init__(self, allow: bool = True) -> None:
        self.allow = allow
        self.slots: dict[str, int] = {}

    async def check(self, bucket: str, *, limit: int, window_seconds: int = 60):
        return RateLimitResult(
            allowed=self.allow, limit=limit, used=0 if self.allow else limit,
            retry_after_seconds=0 if self.allow else 30,
        )

    async def acquire_slot(self, bucket: str, *, limit: int, ttl_seconds: int) -> bool:
        current = self.slots.get(bucket, 0)
        if current >= limit:
            return False
        self.slots[bucket] = current + 1
        return True

    async def release_slot(self, bucket: str) -> None:
        self.slots[bucket] = max(self.slots.get(bucket, 0) - 1, 0)

    async def slots_in_use(self, bucket: str) -> int:
        return self.slots.get(bucket, 0)


@dataclass
class FakeQueue:
    """Captures enqueued jobs instead of running them."""

    jobs: list[tuple[str, tuple, dict]] = field(default_factory=list)

    async def enqueue_job(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.jobs.append((name, args, kwargs))


# --- application fixtures ------------------------------------------------


@pytest.fixture
async def app(session_factory, redis_client, monkeypatch):
    """The FastAPI app wired to the in-memory database and a fake Redis."""
    import core.events as events_module
    from api import deps
    from api.main import app as fastapi_app

    monkeypatch.setattr(events_module, "_client", redis_client)

    limiter = PermissiveLimiter()
    queue = FakeQueue()

    async def _session():
        async with session_factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    fastapi_app.dependency_overrides[deps.get_session] = _session
    fastapi_app.dependency_overrides[deps.get_rate_limiter] = lambda: limiter
    fastapi_app.dependency_overrides[deps.get_queue] = lambda: queue
    fastapi_app.dependency_overrides[deps.get_redis_client] = lambda: redis_client

    fastapi_app.state.test_limiter = limiter
    fastapi_app.state.test_queue = queue
    yield fastapi_app
    fastapi_app.dependency_overrides.clear()


@pytest.fixture
async def client(app) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@dataclass
class Account:
    email: str
    password: str
    access_token: str
    org_id: str
    user_id: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}


@pytest.fixture
async def account(client: httpx.AsyncClient) -> Account:
    email = f"user-{secrets.token_hex(4)}@example.com"
    response = await client.post(
        "/auth/register",
        json={"email": email, "password": TEST_PASSWORD, "org_name": "Test Org"},
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]

    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    body = me.json()
    return Account(
        email=email,
        password=TEST_PASSWORD,
        access_token=token,
        org_id=body["orgs"][0]["id"],
        user_id=body["id"],
    )
