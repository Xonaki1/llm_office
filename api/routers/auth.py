from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from api.deps import RateLimiterDep, SessionDep, UserDep, client_ip
from api.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    MeResponse,
    OrgSummary,
    RegisterRequest,
    TokenPair,
)
from core import audit, billing
from core.config import get_settings
from core.models import Membership, Org, RefreshToken, User
from core.security import (
    PasswordPolicyError,
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    needs_password_rehash,
    new_family_id,
    refresh_token_expiry,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "refresh_token"  # noqa: S105 - cookie name, not a secret


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (slug or "workspace")[:60]


def _invalid_credentials() -> HTTPException:
    # One message for both "no such user" and "wrong password", so the endpoint
    # cannot be used to discover which addresses are registered.
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid email or password"
    )


async def _issue_tokens(
    session: SessionDep,
    user: User,
    *,
    family_id: str | None = None,
    user_agent: str | None = None,
    ip: str | None = None,
) -> TokenPair:
    stmt = select(Membership).where(Membership.user_id == user.id)
    memberships = (await session.execute(stmt)).scalars().all()
    orgs = {m.org_id: m.role for m in memberships}

    access, ttl = create_access_token(
        user_id=user.id,
        email=user.email,
        token_epoch=user.token_epoch,
        is_superuser=user.is_superuser,
        orgs=orgs,
    )
    refresh = generate_refresh_token()
    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_token(refresh),
            family_id=family_id or new_family_id(),
            expires_at=refresh_token_expiry(),
            user_agent=(user_agent or "")[:400] or None,
            ip_address=ip,
        )
    )
    await session.flush()
    return TokenPair(access_token=access, refresh_token=refresh, expires_in=ttl)


def _set_refresh_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        max_age=settings.refresh_token_ttl_days * 86400,
        # Scoped to the whole site rather than /auth: the API is served under a
        # path prefix behind the reverse proxy, and a narrower path would stop
        # the browser sending the cookie to the refresh endpoint.
        path="/",
    )


@router.post("/register", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    limiter: RateLimiterDep,
) -> TokenPair:
    ip = client_ip(request) or "unknown"
    check = await limiter.check(f"register:{ip}", limit=5, window_seconds=3600)
    if not check.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many registration attempts",
            headers={"Retry-After": str(check.retry_after_seconds)},
        )

    email = payload.email.lower()
    try:
        password_hash = hash_password(payload.password)
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    user = User(email=email, password_hash=password_hash, name=payload.name)
    org = Org(
        name=payload.org_name,
        slug=f"{_slugify(payload.org_name)}-{secrets.token_hex(3)}",
    )
    session.add_all([user, org])
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="that email is already registered"
        ) from exc

    session.add(Membership(user_id=user.id, org_id=org.id, role="owner"))
    await session.flush()

    settings = get_settings()
    if settings.signup_bonus_cents > 0:
        await billing.grant(
            session,
            org_id=org.id,
            amount_cents=settings.signup_bonus_cents,
            kind="bonus",
            description="signup bonus",
            idempotency_key=f"signup:{org.id}",
        )

    audit.record(
        session,
        action=audit.USER_REGISTERED,
        org_id=org.id,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        ip_address=ip,
        user_agent=request.headers.get("user-agent"),
    )

    tokens = await _issue_tokens(
        session, user, user_agent=request.headers.get("user-agent"), ip=ip
    )
    _set_refresh_cookie(response, tokens.refresh_token)
    return tokens


@router.post("/login", response_model=TokenPair)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    limiter: RateLimiterDep,
) -> TokenPair:
    ip = client_ip(request) or "unknown"
    email = payload.email.lower()

    # Two buckets: one per address (targeted guessing) and one per source
    # address (spraying across many accounts).
    for bucket, limit in ((f"login:{email}", 10), (f"login-ip:{ip}", 30)):
        check = await limiter.check(bucket, limit=limit, window_seconds=900)
        if not check.allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="too many sign-in attempts, try again later",
                headers={"Retry-After": str(check.retry_after_seconds)},
            )

    user = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()

    if user is None or not verify_password(payload.password, user.password_hash):
        audit.record(
            session,
            action=audit.USER_LOGIN_FAILED,
            actor_user_id=user.id if user else None,
            ip_address=ip,
            user_agent=request.headers.get("user-agent"),
            email=email,
        )
        raise _invalid_credentials()

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account disabled")

    # Transparently upgrade a hash stored under weaker parameters.
    if needs_password_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)

    user.last_login_at = datetime.now(UTC)
    audit.record(
        session,
        action=audit.USER_LOGIN,
        actor_user_id=user.id,
        ip_address=ip,
        user_agent=request.headers.get("user-agent"),
    )

    tokens = await _issue_tokens(
        session, user, user_agent=request.headers.get("user-agent"), ip=ip
    )
    _set_refresh_cookie(response, tokens.refresh_token)
    return tokens


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    request: Request, response: Response, session: SessionDep
) -> TokenPair:
    """Rotate a refresh token.

    Tokens are single-use. Presenting one that was already spent means the token
    leaked, so the whole family is revoked and every session on it dies — the
    standard reuse-detection response.
    """
    # An explicit body wins over the cookie. Browsers send only the cookie;
    # native and server-side clients send the body, and when both are present
    # the caller's explicit choice is the one they mean. Reading the cookie
    # first would silently ignore the supplied token — including a deliberately
    # replayed one, defeating the reuse detection below.
    body = await _maybe_json(request)
    raw = (body or {}).get("refresh_token") or request.cookies.get(REFRESH_COOKIE)
    if not raw:
        raise HTTPException(status_code=401, detail="refresh token missing")

    token_hash = hash_refresh_token(raw)
    stored = (
        await session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
    ).scalar_one_or_none()

    if stored is None:
        raise HTTPException(status_code=401, detail="invalid refresh token")

    now = datetime.now(UTC)
    expires_at = stored.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)

    if stored.used_at is not None or stored.revoked_at is not None:
        await _revoke_family(session, stored.family_id)
        audit.record(
            session,
            action=audit.TOKEN_REFRESH_REUSE,
            actor_user_id=stored.user_id,
            ip_address=client_ip(request),
            user_agent=request.headers.get("user-agent"),
            family_id=stored.family_id,
        )
        # Commit before raising. The request fails, but the revocation is the
        # security response to a compromised token and must survive the 401 —
        # rolling it back with the failed request would leave the stolen family
        # usable.
        await session.commit()
        raise HTTPException(
            status_code=401, detail="refresh token reuse detected, sign in again"
        )

    if expires_at < now:
        raise HTTPException(status_code=401, detail="refresh token expired")

    user = await session.get(User, stored.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="account is inactive")

    stored.used_at = now
    tokens = await _issue_tokens(
        session,
        user,
        family_id=stored.family_id,
        user_agent=request.headers.get("user-agent"),
        ip=client_ip(request),
    )
    _set_refresh_cookie(response, tokens.refresh_token)
    return tokens


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, session: SessionDep) -> None:
    raw = request.cookies.get(REFRESH_COOKIE)
    if raw:
        stored = (
            await session.execute(
                select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw))
            )
        ).scalar_one_or_none()
        if stored is not None:
            await _revoke_family(session, stored.family_id)
    response.delete_cookie(REFRESH_COOKIE, path="/")


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_everywhere(user: UserDep, session: SessionDep, response: Response) -> None:
    """Invalidate every session, including access tokens that have not expired."""
    user.token_epoch += 1
    now = datetime.now(UTC)
    for token in (
        await session.execute(select(RefreshToken).where(RefreshToken.user_id == user.id))
    ).scalars():
        token.revoked_at = token.revoked_at or now
    audit.record(session, action=audit.USER_LOGOUT, actor_user_id=user.id, scope="all")
    response.delete_cookie(REFRESH_COOKIE, path="/")


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: ChangePasswordRequest, user: UserDep, session: SessionDep, request: Request
) -> None:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=403, detail="current password is incorrect")
    try:
        user.password_hash = hash_password(payload.new_password)
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Force every other session to re-authenticate with the new password.
    user.token_epoch += 1
    now = datetime.now(UTC)
    for token in (
        await session.execute(select(RefreshToken).where(RefreshToken.user_id == user.id))
    ).scalars():
        token.revoked_at = token.revoked_at or now

    audit.record(
        session,
        action=audit.PASSWORD_CHANGED,
        actor_user_id=user.id,
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


@router.get("/me", response_model=MeResponse)
async def me(user: UserDep, session: SessionDep) -> MeResponse:
    stmt = select(Membership, Org).join(Org, Org.id == Membership.org_id).where(
        Membership.user_id == user.id
    )
    rows = (await session.execute(stmt)).all()
    return MeResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        is_superuser=user.is_superuser,
        orgs=[
            OrgSummary(
                id=org.id,
                name=org.name,
                slug=org.slug,
                role=membership.role,
                key_mode=org.key_mode,
                credits_cents=org.credits_cents,
            )
            for membership, org in rows
        ],
    )


async def _revoke_family(session: SessionDep, family_id: str) -> None:
    now = datetime.now(UTC)
    stmt = select(RefreshToken).where(RefreshToken.family_id == family_id)
    for token in (await session.execute(stmt)).scalars():
        if token.revoked_at is None:
            token.revoked_at = now


async def _maybe_json(request: Request) -> dict | None:
    try:
        return await request.json()
    except Exception:  # noqa: BLE001 - a missing or malformed body is not an error here
        return None
