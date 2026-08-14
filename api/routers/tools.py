from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from api.deps import ClaimsDep
from core.config import get_settings
from core.tools import catalogue

router = APIRouter(prefix="/tools", tags=["tools"])


class ToolOut(BaseModel):
    name: str
    description: str
    side_effect: str
    available: bool
    unavailable_reason: str | None


class ToolLimits(BaseModel):
    max_iterations: int
    max_calls_per_turn: int
    tools_enabled: bool


class ToolCatalogue(BaseModel):
    tools: list[ToolOut]
    limits: ToolLimits


@router.get("", response_model=ToolCatalogue)
async def list_tools(_: ClaimsDep) -> ToolCatalogue:
    """Tools an agent may be given on this deployment.

    `available` reflects configuration, not permission: web search is listed but
    unavailable when no search provider is configured, so the UI can explain why
    it cannot be enabled instead of silently hiding it.
    """
    settings = get_settings()
    return ToolCatalogue(
        tools=[
            ToolOut(
                name=info.name,
                description=info.description,
                side_effect=info.side_effect.value,
                available=info.available and settings.tools_enabled,
                unavailable_reason=(
                    "tools are disabled on this deployment"
                    if not settings.tools_enabled
                    else info.unavailable_reason
                ),
            )
            for info in catalogue()
        ],
        limits=ToolLimits(
            max_iterations=settings.max_tool_iterations,
            max_calls_per_turn=settings.max_tool_calls_per_turn,
            tools_enabled=settings.tools_enabled,
        ),
    )
