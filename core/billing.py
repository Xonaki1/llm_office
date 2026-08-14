"""Credit ledger.

The ledger is append-only and is the single source of truth for a balance. The
`orgs.credits_cents` column is a cached projection written in the same
transaction as every ledger entry, so the two can never drift; `reconcile`
re-derives it from the ledger if it ever does.

Only spend on *platform* credentials is billed. A run on the organisation's own
key costs us nothing, so it is recorded for reporting but charges no credits.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.llm.pricing import microcents_to_cents
from core.models import CreditLedger, Org


class InsufficientCredits(RuntimeError):
    def __init__(self, balance_cents: int, required_cents: int) -> None:
        self.balance_cents = balance_cents
        self.required_cents = required_cents
        super().__init__(
            f"organisation has {balance_cents} credit cents but the run needs "
            f"at least {required_cents}"
        )


@dataclass(frozen=True)
class LedgerEntry:
    id: str
    kind: str
    amount_cents: int
    balance_after_cents: int
    description: str


def apply_markup(raw_microcents: int) -> int:
    """Platform margin on managed-key spend. BYOK spend never reaches here."""
    percent = get_settings().credit_markup_percent
    return int(round(raw_microcents * (100 + percent) / 100))


async def _locked_org(session: AsyncSession, org_id: str) -> Org:
    """Row-level lock so two concurrent debits cannot both read the same balance.

    SQLite (used by the test suite) has no row locks but serialises writers, so
    the guarantee holds there too.
    """
    stmt = select(Org).where(Org.id == org_id)
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update()
    org = (await session.execute(stmt)).scalar_one_or_none()
    if org is None:
        raise ValueError(f"organisation {org_id} not found")
    return org


async def balance(session: AsyncSession, org_id: str) -> int:
    org = await session.get(Org, org_id)
    return org.credits_cents if org else 0


async def ensure_can_start(session: AsyncSession, org_id: str, required_cents: int) -> None:
    """Refuse to queue a run the org cannot pay for.

    The check is against the run's *ceiling*, not its expected cost: a run that
    starts and then hits a zero balance mid-way has already spent real money on
    the provider.
    """
    if not get_settings().billing_enabled:
        return
    org = await session.get(Org, org_id)
    if org is None:
        raise ValueError(f"organisation {org_id} not found")
    if org.credits_cents < required_cents:
        raise InsufficientCredits(org.credits_cents, required_cents)


async def post_entry(
    session: AsyncSession,
    *,
    org_id: str,
    kind: str,
    amount_cents: int,
    idempotency_key: str,
    description: str = "",
    run_id: str | None = None,
    created_by: str | None = None,
) -> LedgerEntry:
    """Append one entry and update the cached balance atomically.

    `idempotency_key` is unique per org, so a retried worker or a replayed
    webhook cannot double-charge.
    """
    existing = (
        await session.execute(
            select(CreditLedger).where(
                CreditLedger.org_id == org_id,
                CreditLedger.idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return LedgerEntry(
            existing.id,
            existing.kind,
            existing.amount_cents,
            existing.balance_after_cents,
            existing.description,
        )

    org = await _locked_org(session, org_id)
    org.credits_cents += amount_cents

    entry = CreditLedger(
        org_id=org_id,
        kind=kind,
        amount_cents=amount_cents,
        balance_after_cents=org.credits_cents,
        run_id=run_id,
        description=description[:400],
        idempotency_key=idempotency_key,
        created_by=created_by,
    )
    session.add(entry)
    try:
        await session.flush()
    except IntegrityError:
        # Lost a race against a concurrent identical post; the winner's entry is
        # authoritative, so roll back to it rather than charging twice.
        await session.rollback()
        return await post_entry(
            session,
            org_id=org_id,
            kind=kind,
            amount_cents=amount_cents,
            idempotency_key=idempotency_key,
            description=description,
            run_id=run_id,
            created_by=created_by,
        )

    return LedgerEntry(
        entry.id, entry.kind, entry.amount_cents, entry.balance_after_cents, entry.description
    )


async def charge_run(
    session: AsyncSession,
    *,
    org_id: str,
    run_id: str,
    billable_microcents: int,
) -> LedgerEntry | None:
    """Debit an organisation for one finished run. Idempotent per run id."""
    if not get_settings().billing_enabled or billable_microcents <= 0:
        return None
    cents = microcents_to_cents(billable_microcents)
    if cents == 0:
        return None
    return await post_entry(
        session,
        org_id=org_id,
        kind="debit",
        amount_cents=-cents,
        idempotency_key=f"run:{run_id}",
        description=f"run {run_id}",
        run_id=run_id,
    )


async def grant(
    session: AsyncSession,
    *,
    org_id: str,
    amount_cents: int,
    kind: str = "topup",
    description: str = "",
    idempotency_key: str | None = None,
    created_by: str | None = None,
) -> LedgerEntry:
    if amount_cents <= 0:
        raise ValueError("grant amount must be positive")
    import uuid

    return await post_entry(
        session,
        org_id=org_id,
        kind=kind,
        amount_cents=amount_cents,
        idempotency_key=idempotency_key or f"{kind}:{uuid.uuid4()}",
        description=description,
        created_by=created_by,
    )


async def reconcile(session: AsyncSession, org_id: str) -> int:
    """Re-derive the cached balance from the ledger. Returns the corrected value."""
    total = (
        await session.execute(
            select(func.coalesce(func.sum(CreditLedger.amount_cents), 0)).where(
                CreditLedger.org_id == org_id
            )
        )
    ).scalar_one()
    org = await _locked_org(session, org_id)
    org.credits_cents = int(total)
    await session.flush()
    return org.credits_cents
