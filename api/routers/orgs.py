from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select

from api.deps import (
    AdminDep,
    OrgDep,
    OwnerDep,
    SessionDep,
    client_ip,
    enforce_api_rate_limit,
)
from api.schemas import (
    BalanceOut,
    LedgerEntryOut,
    MemberInvite,
    MemberOut,
    MemberRoleUpdate,
    OrgUpdate,
    Role,
    TopupRequest,
)
from core import audit, billing
from core.config import get_settings
from core.models import CreditLedger, Membership, Org, User

router = APIRouter(
    prefix="/orgs/{org_id}", tags=["orgs"], dependencies=[Depends(enforce_api_rate_limit)]
)


@router.patch("", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
async def update_org(payload: OrgUpdate, ctx: AdminDep, session: SessionDep) -> None:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(ctx.org, field, value)
    await session.flush()


# --- members -------------------------------------------------------------


@router.get("/members", response_model=list[MemberOut])
async def list_members(ctx: OrgDep, session: SessionDep) -> list[MemberOut]:
    stmt = (
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.org_id == ctx.org_id)
        .order_by(Membership.created_at)
    )
    rows = (await session.execute(stmt)).all()
    return [
        MemberOut(
            user_id=user.id,
            email=user.email,
            name=user.name,
            role=cast("Role", membership.role),
            joined_at=membership.created_at,
        )
        for membership, user in rows
    ]


@router.post("/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
async def add_member(
    payload: MemberInvite, ctx: AdminDep, session: SessionDep, request: Request
) -> MemberOut:
    """Add an existing account to this organisation.

    Membership is granted only for accounts that already exist — creating an
    account on someone else's behalf would mean setting a password for them.
    """
    email = payload.email.lower()
    user = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=404,
            detail="no account with that email; ask them to register first",
        )
    if payload.role == "owner" and not ctx.can("owner"):
        raise HTTPException(status_code=403, detail="only an owner can grant the owner role")

    existing = (
        await session.execute(
            select(Membership).where(
                Membership.org_id == ctx.org_id, Membership.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="already a member")

    membership = Membership(user_id=user.id, org_id=ctx.org_id, role=payload.role)
    session.add(membership)
    await session.flush()
    audit.record(
        session,
        action=audit.MEMBER_INVITED,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="user",
        target_id=user.id,
        ip_address=client_ip(request),
        role=payload.role,
    )
    return MemberOut(
        user_id=user.id,
        email=user.email,
        name=user.name,
        role=cast("Role", membership.role),
        joined_at=membership.created_at,
    )


@router.patch("/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def change_role(
    user_id: str,
    payload: MemberRoleUpdate,
    ctx: OwnerDep,
    session: SessionDep,
    request: Request,
) -> None:
    membership = await _member(session, ctx.org_id, user_id)
    if membership.role == "owner" and payload.role != "owner":
        await _guard_last_owner(session, ctx.org_id, user_id)
    membership.role = payload.role
    audit.record(
        session,
        action=audit.MEMBER_ROLE_CHANGED,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="user",
        target_id=user_id,
        ip_address=client_ip(request),
        role=payload.role,
    )


@router.delete("/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    user_id: str, ctx: AdminDep, session: SessionDep, request: Request
) -> None:
    membership = await _member(session, ctx.org_id, user_id)
    if membership.role == "owner":
        await _guard_last_owner(session, ctx.org_id, user_id)
        if not ctx.can("owner"):
            raise HTTPException(status_code=403, detail="only an owner can remove an owner")
    await session.delete(membership)
    audit.record(
        session,
        action=audit.MEMBER_REMOVED,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="user",
        target_id=user_id,
        ip_address=client_ip(request),
    )


async def _member(session: SessionDep, org_id: str, user_id: str) -> Membership:
    membership = (
        await session.execute(
            select(Membership).where(
                Membership.org_id == org_id, Membership.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(status_code=404, detail="member not found")
    return membership


async def _guard_last_owner(session: SessionDep, org_id: str, user_id: str) -> None:
    """An organisation with no owner cannot be administered by anyone."""
    stmt = select(Membership).where(
        Membership.org_id == org_id, Membership.role == "owner", Membership.user_id != user_id
    )
    remaining = (await session.execute(stmt)).scalars().first()
    if remaining is None:
        raise HTTPException(
            status_code=409, detail="an organisation must keep at least one owner"
        )


# --- billing -------------------------------------------------------------


@router.get("/billing/balance", response_model=BalanceOut)
async def get_balance(ctx: OrgDep) -> BalanceOut:
    settings = get_settings()
    return BalanceOut(
        credits_cents=ctx.org.credits_cents,
        billing_enabled=settings.billing_enabled,
        markup_percent=settings.credit_markup_percent,
    )


@router.get("/billing/ledger", response_model=list[LedgerEntryOut])
async def get_ledger(
    ctx: OrgDep,
    session: SessionDep,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[CreditLedger]:
    stmt = (
        select(CreditLedger)
        .where(CreditLedger.org_id == ctx.org_id)
        .order_by(CreditLedger.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list((await session.execute(stmt)).scalars().all())


@router.post("/billing/topup", response_model=LedgerEntryOut, status_code=201)
async def topup(
    payload: TopupRequest, ctx: OwnerDep, session: SessionDep, request: Request
) -> LedgerEntryOut:
    """Manual credit grant.

    This is the internal path a payment integration also posts through: the
    ledger, not the payment provider, is the source of truth for a balance.
    """
    entry = await billing.grant(
        session,
        org_id=ctx.org_id,
        amount_cents=payload.amount_cents,
        description=payload.description,
        idempotency_key=payload.idempotency_key,
        created_by=ctx.user.id,
    )
    audit.record(
        session,
        action=audit.CREDITS_TOPPED_UP,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="org",
        target_id=ctx.org_id,
        ip_address=client_ip(request),
        amount_cents=payload.amount_cents,
    )
    row = await session.get(CreditLedger, entry.id)
    assert row is not None
    return LedgerEntryOut.model_validate(row)


@router.post("/billing/reconcile", response_model=BalanceOut)
async def reconcile_balance(ctx: OwnerDep, session: SessionDep) -> BalanceOut:
    """Re-derive the cached balance from the ledger. Safe to call at any time."""
    corrected = await billing.reconcile(session, ctx.org_id)
    settings = get_settings()
    org = await session.get(Org, ctx.org_id)
    assert org is not None
    return BalanceOut(
        credits_cents=corrected,
        billing_enabled=settings.billing_enabled,
        markup_percent=settings.credit_markup_percent,
    )
