"""Request-scoped dependencies: authentication, authorisation, rate limiting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import redis.asyncio as redis
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from fastapi import Depends, HTTPException, Path, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.db import get_session
from core.events import get_redis
from core.models import Membership, Org, User
from core.ratelimit import RateLimiter
from core.security import AccessTokenClaims, AuthError, decode_access_token

SessionDep = Annotated[AsyncSession, Depends(get_session)]

# auto_error=False so a missing header produces our own 401 shape rather than
# FastAPI's, keeping every auth failure identical from the outside.
_bearer = HTTPBearer(auto_error=False)

ROLE_ORDER = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}


_arq_pool: ArqRedis | None = None


async def get_queue() -> ArqRedis:
    global _arq_pool
    if _arq_pool is None:
        _arq_pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    return _arq_pool


async def close_queue() -> None:
    global _arq_pool
    if _arq_pool is not None:
        await _arq_pool.aclose()
        _arq_pool = None


QueueDep = Annotated[ArqRedis, Depends(get_queue)]


def get_redis_client() -> redis.Redis:
    return get_redis()


RedisDep = Annotated[redis.Redis, Depends(get_redis_client)]


def get_rate_limiter(client: RedisDep) -> RateLimiter:
    return RateLimiter(client)


RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]


def _unauthorised(detail: str = "not authenticated") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def current_claims(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AccessTokenClaims:
    if credentials is None or not credentials.credentials:
        raise _unauthorised()
    try:
        return decode_access_token(credentials.credentials)
    except AuthError as exc:
        raise _unauthorised(str(exc)) from exc


ClaimsDep = Annotated[AccessTokenClaims, Depends(current_claims)]


async def current_user(claims: ClaimsDep, session: SessionDep) -> User:
    user = await session.get(User, claims.user_id)
    if user is None or not user.is_active:
        raise _unauthorised("account is inactive")
    # A password change or a global sign-out bumps the epoch, which invalidates
    # every access token issued before it without a per-request revocation list.
    if user.token_epoch != claims.token_epoch:
        raise _unauthorised("session is no longer valid, sign in again")
    return user


UserDep = Annotated[User, Depends(current_user)]


@dataclass(frozen=True)
class OrgContext:
    org: Org
    user: User
    role: str

    @property
    def org_id(self) -> str:
        return self.org.id

    def can(self, minimum: str) -> bool:
        return ROLE_ORDER.get(self.role, -1) >= ROLE_ORDER[minimum]


async def current_org(
    org_id: Annotated[str, Path(min_length=1, max_length=36)],
    user: UserDep,
    session: SessionDep,
) -> OrgContext:
    """Resolve the org from the path and prove the caller is a member.

    Membership is re-read from the database rather than trusted from the token,
    so revoking access takes effect immediately instead of at token expiry.
    """
    from sqlalchemy import select

    stmt = select(Membership).where(
        Membership.org_id == org_id, Membership.user_id == user.id
    )
    membership = (await session.execute(stmt)).scalar_one_or_none()

    org = await session.get(Org, org_id)
    if org is None or not org.is_active:
        raise HTTPException(status_code=404, detail="organisation not found")

    if membership is None:
        if user.is_superuser:
            return OrgContext(org=org, user=user, role="owner")
        # Do not distinguish "no such org" from "not your org": that difference
        # lets an attacker enumerate which organisation ids exist.
        raise HTTPException(status_code=404, detail="organisation not found")

    return OrgContext(org=org, user=user, role=membership.role)


OrgDep = Annotated[OrgContext, Depends(current_org)]


def require_role(minimum: str):
    """Dependency factory: reject callers below `minimum` in this organisation."""

    async def _check(ctx: OrgDep) -> OrgContext:
        if not ctx.can(minimum):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"this action requires the {minimum} role or higher",
            )
        return ctx

    return _check


AdminDep = Annotated[OrgContext, Depends(require_role("admin"))]
OwnerDep = Annotated[OrgContext, Depends(require_role("owner"))]
MemberDep = Annotated[OrgContext, Depends(require_role("member"))]


async def require_superuser(user: UserDep) -> User:
    if not user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    return user


SuperuserDep = Annotated[User, Depends(require_superuser)]


async def enforce_api_rate_limit(
    request: Request, user: UserDep, limiter: RateLimiterDep
) -> None:
    """Per-user API rate limit applied to every authenticated route."""
    settings = get_settings()
    result = await limiter.check(
        f"api:{user.id}", limit=settings.rate_limit_api_per_minute, window_seconds=60
    )
    request.state.rate_limit = result
    if not result.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate limit exceeded",
            headers={"Retry-After": str(max(result.retry_after_seconds, 1))},
        )


def client_ip(request: Request) -> str | None:
    """Client address, honouring X-Forwarded-For only when a proxy is in front.

    Uvicorn is started with --proxy-headers behind the reverse proxy, so
    `request.client.host` is already the real address; the header is a fallback
    for deployments that terminate TLS elsewhere.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host[:64] if request.client else None
