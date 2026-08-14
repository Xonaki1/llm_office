"""Platform-operator endpoints. Superuser only."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.deps import SessionDep, SuperuserDep
from api.schemas import LedgerEntryOut
from core import audit, billing
from core.llm.pricing import default_price_book
from core.llm.registry import get_spec
from core.models import CreditLedger, ModelPrice, Org

router = APIRouter(prefix="/admin", tags=["admin"])


class ModelPriceIn(BaseModel):
    model: str
    input_per_mtok: float = Field(ge=0)
    output_per_mtok: float = Field(ge=0)
    cached_input_per_mtok: float | None = Field(default=None, ge=0)
    note: str = Field(default="", max_length=300)


class ModelPriceOut(ModelPriceIn):
    pass


class AdjustCreditsIn(BaseModel):
    org_id: str
    amount_cents: int = Field(description="positive credits the org, negative debits it")
    description: str = Field(default="operator adjustment", max_length=400)
    idempotency_key: str = Field(min_length=8, max_length=128)


@router.get("/model-prices", response_model=list[ModelPriceOut])
async def list_prices(_: SuperuserDep, session: SessionDep) -> list[ModelPriceOut]:
    rows = (await session.execute(select(ModelPrice))).scalars().all()
    return [ModelPriceOut.model_validate(row, from_attributes=True) for row in rows]


@router.put("/model-prices", response_model=ModelPriceOut)
async def upsert_price(
    payload: ModelPriceIn, user: SuperuserDep, session: SessionDep
) -> ModelPriceOut:
    """Override a model's price without a redeploy.

    Vendors change rates on their own schedule; baking prices into the image
    means every change is a release. Overrides take effect on every worker
    within the price book's refresh interval.
    """
    get_spec(payload.model)  # reject prices for models we cannot route

    row = await session.get(ModelPrice, payload.model)
    if row is None:
        row = ModelPrice(model=payload.model)
        session.add(row)
    row.input_per_mtok = payload.input_per_mtok
    row.output_per_mtok = payload.output_per_mtok
    row.cached_input_per_mtok = payload.cached_input_per_mtok
    row.note = payload.note
    row.updated_by = user.id
    await session.flush()

    await default_price_book().refresh(session, force=True)
    audit.record(
        session,
        action="admin.model_price_updated",
        actor_user_id=user.id,
        target_type="model_price",
        target_id=payload.model,
        input_per_mtok=payload.input_per_mtok,
        output_per_mtok=payload.output_per_mtok,
    )
    return ModelPriceOut.model_validate(row, from_attributes=True)


@router.delete("/model-prices/{model}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_price(model: str, user: SuperuserDep, session: SessionDep) -> None:
    row = await session.get(ModelPrice, model)
    if row is None:
        raise HTTPException(status_code=404, detail="no override for that model")
    await session.delete(row)
    await session.flush()
    await default_price_book().refresh(session, force=True)
    audit.record(
        session,
        action="admin.model_price_deleted",
        actor_user_id=user.id,
        target_type="model_price",
        target_id=model,
    )


@router.post("/credits", response_model=LedgerEntryOut, status_code=201)
async def adjust_credits(
    payload: AdjustCreditsIn, user: SuperuserDep, session: SessionDep
) -> LedgerEntryOut:
    org = await session.get(Org, payload.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="organisation not found")
    if payload.amount_cents == 0:
        raise HTTPException(status_code=422, detail="amount must not be zero")

    entry = await billing.post_entry(
        session,
        org_id=payload.org_id,
        kind="adjustment",
        amount_cents=payload.amount_cents,
        idempotency_key=payload.idempotency_key,
        description=payload.description,
        created_by=user.id,
    )
    audit.record(
        session,
        action=audit.CREDITS_ADJUSTED,
        org_id=payload.org_id,
        actor_user_id=user.id,
        target_type="org",
        target_id=payload.org_id,
        amount_cents=payload.amount_cents,
    )
    row = await session.get(CreditLedger, entry.id)
    assert row is not None
    return LedgerEntryOut.model_validate(row)
