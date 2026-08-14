"""Cost accounting.

Costs are tracked in **micro-cents** (1e-6 of a cent) so that summing hundreds of
small steps does not accumulate rounding error; the value is rounded to whole
cents exactly once, when it is written to the run row and the credit ledger.

Registry prices are compile-time defaults. Operators override them at runtime via
the `model_prices` table, so a vendor price change is a database update rather
than a redeploy.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.llm.providers.base import Usage
from core.llm.registry import ModelSpec, get_spec

MICROCENTS_PER_CENT = 1_000_000


@dataclass(frozen=True)
class Rate:
    input_per_mtok: float
    output_per_mtok: float
    cached_input_per_mtok: float | None = None


def _rate_from_spec(spec: ModelSpec) -> Rate:
    return Rate(spec.input_per_mtok, spec.output_per_mtok, spec.cached_input_per_mtok)


class PriceBook:
    """Registry defaults plus database overrides, cached in-process.

    The cache is refreshed on a TTL rather than invalidated, so a price change
    takes effect within `ttl_seconds` on every worker without cross-process
    coordination.
    """

    def __init__(self, ttl_seconds: float = 300.0) -> None:
        self._ttl = ttl_seconds
        self._overrides: dict[str, Rate] = {}
        self._loaded_at: float = 0.0

    async def refresh(self, session: AsyncSession, *, force: bool = False) -> None:
        if not force and (time.monotonic() - self._loaded_at) < self._ttl:
            return
        from core.models import ModelPrice  # local import: avoids a cycle at module load

        rows = (await session.execute(select(ModelPrice))).scalars().all()
        self._overrides = {
            row.model: Rate(
                row.input_per_mtok, row.output_per_mtok, row.cached_input_per_mtok
            )
            for row in rows
        }
        self._loaded_at = time.monotonic()

    def rate_for(self, model: str) -> Rate:
        if model in self._overrides:
            return self._overrides[model]
        return _rate_from_spec(get_spec(model))

    def cost_microcents(self, model: str, usage: Usage) -> int:
        rate = self.rate_for(model)
        cached_rate = (
            rate.cached_input_per_mtok
            if rate.cached_input_per_mtok is not None
            else rate.input_per_mtok
        )
        fresh = usage.fresh_input_tokens
        cached = usage.cached_input_tokens

        usd = (
            (fresh / 1_000_000) * rate.input_per_mtok
            + (cached / 1_000_000) * cached_rate
            + (usage.output_tokens / 1_000_000) * rate.output_per_mtok
        )
        return int(round(usd * 100 * MICROCENTS_PER_CENT))


def microcents_to_cents(microcents: int) -> int:
    """Round up: a run that costs a fraction of a cent still costs the platform
    money, and rounding down would let a caller loop for free."""
    if microcents <= 0:
        return 0
    return -(-microcents // MICROCENTS_PER_CENT)


def cents_to_microcents(cents: int) -> int:
    return cents * MICROCENTS_PER_CENT


_default_book = PriceBook()


def default_price_book() -> PriceBook:
    return _default_book
