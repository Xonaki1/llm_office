"""Password hashing and token issuance.

Passwords use Argon2id. Access tokens are short-lived JWTs carrying the user's
memberships so the hot path needs no database read. Refresh tokens are opaque
random strings stored only as SHA-256 digests, and they rotate on every use:
replaying a spent token revokes the entire family, which is how a stolen token
is detected.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from core.config import get_settings

# Tuned for an interactive login on modest server hardware: ~64 MiB, ~50-100 ms.
_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)

ACCESS_TOKEN_TYPE = "access"  # noqa: S105 - a token *type* label, not a secret


class AuthError(Exception):
    """Raised for any credential failure. The message is deliberately vague so
    it can be surfaced to clients without revealing which half was wrong."""


class PasswordPolicyError(ValueError):
    pass


def hash_password(password: str) -> str:
    validate_password(password)
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return True


def needs_password_rehash(password_hash: str) -> bool:
    """True when the stored hash used weaker parameters than the current policy."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def validate_password(password: str) -> None:
    minimum = get_settings().password_min_length
    if len(password) < minimum:
        raise PasswordPolicyError(f"password must be at least {minimum} characters")
    if password.strip() != password:
        raise PasswordPolicyError("password must not start or end with whitespace")
    # Length is the dominant factor; a character-class rule on top of a 12-char
    # minimum mostly drives users toward predictable substitutions.
    if len(set(password)) < 5:
        raise PasswordPolicyError("password is too repetitive")


# --- access tokens -------------------------------------------------------


@dataclass(frozen=True)
class AccessTokenClaims:
    user_id: str
    email: str
    token_epoch: int
    is_superuser: bool
    # org id -> role, embedded so authorisation needs no per-request query
    orgs: dict[str, str]
    expires_at: datetime


def _jwt_secret() -> str:
    secret = get_settings().jwt_secret
    if not secret:
        raise AuthError("JWT_SECRET is not configured")
    return secret


def create_access_token(
    *,
    user_id: str,
    email: str,
    token_epoch: int,
    is_superuser: bool,
    orgs: dict[str, str],
) -> tuple[str, int]:
    """Returns (token, ttl_seconds)."""
    settings = get_settings()
    ttl = timedelta(minutes=settings.access_token_ttl_minutes)
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": user_id,
        "email": email,
        "epoch": token_epoch,
        "su": is_superuser,
        "orgs": orgs,
        "typ": ACCESS_TOKEN_TYPE,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "jti": secrets.token_urlsafe(8),
    }
    token = jwt.encode(payload, _jwt_secret(), algorithm=settings.jwt_algorithm)
    return token, int(ttl.total_seconds())


def decode_access_token(token: str) -> AccessTokenClaims:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            _jwt_secret(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub", "typ"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("invalid token") from exc

    if payload.get("typ") != ACCESS_TOKEN_TYPE:
        raise AuthError("invalid token")

    return AccessTokenClaims(
        user_id=payload["sub"],
        email=payload.get("email", ""),
        token_epoch=int(payload.get("epoch", 0)),
        is_superuser=bool(payload.get("su", False)),
        orgs=dict(payload.get("orgs") or {}),
        expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
    )


# --- refresh tokens ------------------------------------------------------


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """SHA-256 is correct here: the token is 48 bytes of CSPRNG output, so it has
    no guessable structure for a slow KDF to protect against."""
    return hashlib.sha256(token.encode()).hexdigest()


def refresh_token_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(days=get_settings().refresh_token_ttl_days)


def new_family_id() -> str:
    return secrets.token_urlsafe(16)
