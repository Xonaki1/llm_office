from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select

from api.deps import OrgDep, SessionDep, enforce_api_rate_limit
from api.schemas import ModelOut
from core.llm.keys import platform_key
from core.llm.pricing import default_price_book
from core.llm.registry import EffortStyle, Provider, list_models
from core.models import ApiKey

router = APIRouter(
    prefix="/orgs/{org_id}/models",
    tags=["models"],
    dependencies=[Depends(enforce_api_rate_limit)],
)


@router.get("", response_model=list[ModelOut])
async def list_available_models(ctx: OrgDep, session: SessionDep) -> list[ModelOut]:
    """Catalogue of models this organisation can actually run.

    `available` reflects credential reality: a model is usable when the platform
    holds a key for its provider, or this organisation has supplied its own.
    Prices come from the live price book, so the UI quotes what will be charged.
    """
    stmt = select(ApiKey.provider).where(
        ApiKey.org_id == ctx.org_id, ApiKey.is_active.is_(True)
    )
    org_providers = set((await session.execute(stmt)).scalars().all())

    book = default_price_book()
    await book.refresh(session)

    results: list[ModelOut] = []
    for spec in list_models():
        provider = Provider(spec.provider)
        has_platform = bool(platform_key(provider))
        has_own = spec.provider in org_providers
        rate = book.rate_for(spec.id)

        results.append(
            ModelOut(
                id=spec.id,
                provider=spec.provider,
                display_name=spec.display_name,
                input_per_mtok=rate.input_per_mtok,
                output_per_mtok=rate.output_per_mtok,
                cached_input_per_mtok=rate.cached_input_per_mtok,
                max_output_tokens=spec.max_output_tokens,
                context_window=spec.context_window,
                supports_effort=spec.effort_style != EffortStyle.NONE,
                available=(
                    has_own
                    if ctx.org.key_mode == "byok"
                    else (has_platform or has_own)
                ),
            )
        )
    return results
