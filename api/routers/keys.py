from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select

from api.deps import AdminDep, OrgDep, SessionDep, client_ip, enforce_api_rate_limit
from api.schemas import ApiKeyCreate, ApiKeyOut
from core import audit
from core.crypto import encrypt_secret, fingerprint, mask_secret
from core.models import ApiKey

router = APIRouter(
    prefix="/orgs/{org_id}/keys",
    tags=["keys"],
    dependencies=[Depends(enforce_api_rate_limit)],
)


@router.post("", response_model=ApiKeyOut, status_code=status.HTTP_201_CREATED)
async def upsert_key(
    payload: ApiKeyCreate, ctx: AdminDep, session: SessionDep, request: Request
) -> ApiKey:
    """Store a BYOK provider credential.

    The plaintext is encrypted before it touches the database and is never read
    back through the API — responses carry only a mask and a fingerprint. The
    envelope is bound to this organisation via AEAD associated data, so a row
    copied into another tenant cannot be decrypted.
    """
    ciphertext = encrypt_secret(payload.api_key, aad=ctx.org_id)
    mask = mask_secret(payload.api_key)
    digest = fingerprint(payload.api_key)

    stmt = select(ApiKey).where(
        ApiKey.org_id == ctx.org_id, ApiKey.provider == payload.provider
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()

    if existing is not None:
        existing.ciphertext = ciphertext
        existing.mask = mask
        existing.fingerprint = digest
        existing.is_active = True
        existing.last_error = None
        key = existing
    else:
        key = ApiKey(
            org_id=ctx.org_id,
            provider=payload.provider,
            ciphertext=ciphertext,
            mask=mask,
            fingerprint=digest,
            created_by=ctx.user.id,
        )
        session.add(key)

    await session.flush()
    audit.record(
        session,
        action=audit.API_KEY_ADDED,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="api_key",
        target_id=key.id,
        ip_address=client_ip(request),
        provider=payload.provider,
        fingerprint=digest,
    )
    return key


@router.get("", response_model=list[ApiKeyOut])
async def list_keys(ctx: OrgDep, session: SessionDep) -> list[ApiKey]:
    stmt = select(ApiKey).where(ApiKey.org_id == ctx.org_id).order_by(ApiKey.provider)
    return list((await session.execute(stmt)).scalars().all())


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_key(
    key_id: str, ctx: AdminDep, session: SessionDep, request: Request
) -> None:
    key = await session.get(ApiKey, key_id)
    if key is None or key.org_id != ctx.org_id:
        raise HTTPException(status_code=404, detail="key not found")
    provider, digest = key.provider, key.fingerprint
    await session.delete(key)
    audit.record(
        session,
        action=audit.API_KEY_DELETED,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="api_key",
        target_id=key_id,
        ip_address=client_ip(request),
        provider=provider,
        fingerprint=digest,
    )
