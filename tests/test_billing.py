from __future__ import annotations

import pytest
from sqlalchemy import select

from core import billing
from core.llm.pricing import PriceBook, microcents_to_cents
from core.llm.providers.base import Usage
from core.models import CreditLedger, Org


@pytest.fixture
async def org(session):
    row = Org(name="Test", slug="test-org", credits_cents=0)
    session.add(row)
    await session.flush()
    return row


class TestLedger:
    async def test_balance_follows_the_ledger(self, session, org):
        await billing.grant(session, org_id=org.id, amount_cents=500, description="top-up")
        assert await billing.balance(session, org.id) == 500

        await billing.post_entry(
            session,
            org_id=org.id,
            kind="debit",
            amount_cents=-120,
            idempotency_key="run:1",
        )
        assert await billing.balance(session, org.id) == 380

    async def test_entries_are_idempotent(self, session, org):
        for _ in range(3):
            await billing.post_entry(
                session,
                org_id=org.id,
                kind="topup",
                amount_cents=100,
                idempotency_key="same-key",
            )
        assert await billing.balance(session, org.id) == 100

        rows = (
            await session.execute(select(CreditLedger).where(CreditLedger.org_id == org.id))
        ).scalars().all()
        assert len(rows) == 1

    async def test_charge_run_is_idempotent(self, session, org):
        await billing.grant(session, org_id=org.id, amount_cents=1000)
        for _ in range(3):
            await billing.charge_run(
                session, org_id=org.id, run_id="run-1", billable_microcents=2_500_000
            )
        # 2.5 cents rounds up to 3 — a partial cent still costs the platform money.
        assert await billing.balance(session, org.id) == 997

    async def test_balance_after_is_recorded_on_each_entry(self, session, org):
        await billing.grant(session, org_id=org.id, amount_cents=100)
        await billing.grant(session, org_id=org.id, amount_cents=50)
        rows = (
            await session.execute(
                select(CreditLedger)
                .where(CreditLedger.org_id == org.id)
                .order_by(CreditLedger.created_at)
            )
        ).scalars().all()
        assert [r.balance_after_cents for r in rows] == [100, 150]

    async def test_reconcile_repairs_a_drifted_cache(self, session, org):
        await billing.grant(session, org_id=org.id, amount_cents=400)
        org.credits_cents = 9999  # simulate drift
        await session.flush()

        assert await billing.reconcile(session, org.id) == 400

    async def test_grant_rejects_a_non_positive_amount(self, session, org):
        with pytest.raises(ValueError, match="positive"):
            await billing.grant(session, org_id=org.id, amount_cents=0)


class TestStartGuard:
    async def test_run_is_refused_without_enough_credit(self, session, org):
        await billing.grant(session, org_id=org.id, amount_cents=10)
        with pytest.raises(billing.InsufficientCredits):
            await billing.ensure_can_start(session, org.id, required_cents=100)

    async def test_run_is_allowed_at_exactly_the_ceiling(self, session, org):
        await billing.grant(session, org_id=org.id, amount_cents=100)
        await billing.ensure_can_start(session, org.id, required_cents=100)


class TestMarkup:
    def test_markup_is_applied(self):
        # 40% by default in the test environment.
        assert billing.apply_markup(1_000_000) == 1_400_000


class TestPricing:
    def test_cost_accounts_for_cached_input_separately(self):
        book = PriceBook()
        plain = book.cost_microcents(
            "claude-opus-5", Usage(input_tokens=1_000_000, output_tokens=0)
        )
        cached = book.cost_microcents(
            "claude-opus-5",
            Usage(input_tokens=1_000_000, output_tokens=0, cached_input_tokens=1_000_000),
        )
        assert plain > cached, "cache reads must be cheaper than fresh input"

    def test_output_is_priced_higher_than_input(self):
        book = PriceBook()
        as_input = book.cost_microcents(
            "gpt-5", Usage(input_tokens=1_000_000, output_tokens=0)
        )
        as_output = book.cost_microcents(
            "gpt-5", Usage(input_tokens=0, output_tokens=1_000_000)
        )
        assert as_output > as_input

    def test_database_override_wins_over_the_registry(self):
        book = PriceBook()
        from core.llm.pricing import Rate

        book._overrides = {"gpt-5": Rate(99.0, 99.0, None)}  # noqa: SLF001 - direct seed
        cost = book.cost_microcents("gpt-5", Usage(input_tokens=1_000_000, output_tokens=0))
        assert cost == 99 * 100 * 1_000_000

    def test_every_provider_is_priced(self):
        from core.llm.registry import list_models

        book = PriceBook()
        for spec in list_models():
            rate = book.rate_for(spec.id)
            assert rate.input_per_mtok > 0, f"{spec.id} has no input price"
            assert rate.output_per_mtok > 0, f"{spec.id} has no output price"

    @pytest.mark.parametrize(
        ("microcents", "cents"),
        [(0, 0), (1, 1), (999_999, 1), (1_000_000, 1), (1_000_001, 2), (2_500_000, 3)],
    )
    def test_rounding_never_favours_the_caller(self, microcents, cents):
        assert microcents_to_cents(microcents) == cents
