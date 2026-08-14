from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select

from api.deps import (
    AdminDep,
    MemberDep,
    OrgDep,
    SessionDep,
    client_ip,
    enforce_api_rate_limit,
)
from api.schemas import PresetInfo, WorkflowCreate, WorkflowOut, WorkflowUpdate
from core import audit
from core.models import Agent, Workflow
from core.orchestration.presets import WorkflowConfigError, validate_graph

router = APIRouter(
    prefix="/orgs/{org_id}/workflows",
    tags=["workflows"],
    dependencies=[Depends(enforce_api_rate_limit)],
)

meta_router = APIRouter(prefix="/presets", tags=["workflows"])

_PRESET_DOCS: list[PresetInfo] = [
    PresetInfo(
        name="pipeline",
        summary="Linear hand-off: each agent builds on the previous agent's output.",
        required_keys=["nodes"],
        optional_keys=["max_steps", "max_cost_cents"],
    ),
    PresetInfo(
        name="supervisor",
        summary="A manager decomposes the task, delegates, and decides when it is done.",
        required_keys=["supervisor_agent_id", "workers"],
        optional_keys=["max_rounds", "max_steps", "max_cost_cents"],
    ),
    PresetInfo(
        name="debate",
        summary="Agents argue for N rounds, then a judge rules. Higher cost, higher quality.",
        required_keys=["debaters", "judge_agent_id"],
        optional_keys=["rounds", "max_steps", "max_cost_cents"],
    ),
    PresetInfo(
        name="blackboard",
        summary="A planner posts a dependency-ordered task list; workers claim ready tasks.",
        required_keys=["planner_agent_id", "workers"],
        optional_keys=["max_tasks", "max_steps", "max_cost_cents"],
    ),
    PresetInfo(
        name="swarm",
        summary="Peer-to-peer hand-off: whoever holds the task decides who gets it next.",
        required_keys=["entry_agent_id", "agents"],
        optional_keys=["max_hops", "max_steps", "max_cost_cents"],
    ),
    PresetInfo(
        name="custom",
        summary="Arbitrary directed graph with agent and router nodes — the visual editor.",
        required_keys=["start", "nodes"],
        optional_keys=["edges", "max_hops", "max_steps", "max_cost_cents"],
    ),
]


@meta_router.get("", response_model=list[PresetInfo])
async def list_presets() -> list[PresetInfo]:
    return _PRESET_DOCS


async def _validate(session: SessionDep, org_id: str, preset: str, graph: dict) -> None:
    """Reject a broken topology at save time, not after the first paid step."""
    try:
        referenced = validate_graph(preset, graph)
    except WorkflowConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not referenced:
        return
    stmt = select(Agent.id).where(
        Agent.org_id == org_id, Agent.is_active.is_(True), Agent.id.in_(set(referenced))
    )
    known = set((await session.execute(stmt)).scalars().all())
    missing = sorted(set(referenced) - known)
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"graph references unknown or inactive agents: {', '.join(missing)}",
        )


async def _get_owned(session: SessionDep, org_id: str, workflow_id: str) -> Workflow:
    workflow = await session.get(Workflow, workflow_id)
    if workflow is None or workflow.org_id != org_id:
        raise HTTPException(status_code=404, detail="workflow not found")
    return workflow


@router.post("", response_model=WorkflowOut, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    payload: WorkflowCreate, ctx: MemberDep, session: SessionDep, request: Request
) -> Workflow:
    await _validate(session, ctx.org_id, payload.preset, payload.graph)
    workflow = Workflow(org_id=ctx.org_id, created_by=ctx.user.id, **payload.model_dump())
    session.add(workflow)
    await session.flush()
    audit.record(
        session,
        action=audit.WORKFLOW_CREATED,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="workflow",
        target_id=workflow.id,
        ip_address=client_ip(request),
        preset=workflow.preset,
    )
    return workflow


@router.get("", response_model=list[WorkflowOut])
async def list_workflows(
    ctx: OrgDep, session: SessionDep, include_inactive: bool = False
) -> list[Workflow]:
    stmt = select(Workflow).where(Workflow.org_id == ctx.org_id)
    if not include_inactive:
        stmt = stmt.where(Workflow.is_active.is_(True))
    return list((await session.execute(stmt.order_by(Workflow.created_at))).scalars().all())


@router.get("/{workflow_id}", response_model=WorkflowOut)
async def get_workflow(workflow_id: str, ctx: OrgDep, session: SessionDep) -> Workflow:
    return await _get_owned(session, ctx.org_id, workflow_id)


@router.patch("/{workflow_id}", response_model=WorkflowOut)
async def update_workflow(
    workflow_id: str,
    payload: WorkflowUpdate,
    ctx: MemberDep,
    session: SessionDep,
    request: Request,
) -> Workflow:
    workflow = await _get_owned(session, ctx.org_id, workflow_id)
    changes = payload.model_dump(exclude_unset=True)

    preset = changes.get("preset", workflow.preset)
    graph = changes.get("graph", workflow.graph)
    if "preset" in changes or "graph" in changes:
        await _validate(session, ctx.org_id, preset, graph)

    for field, value in changes.items():
        setattr(workflow, field, value)
    await session.flush()
    audit.record(
        session,
        action=audit.WORKFLOW_UPDATED,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="workflow",
        target_id=workflow.id,
        ip_address=client_ip(request),
        fields=sorted(changes),
    )
    return workflow


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_workflow(
    workflow_id: str, ctx: AdminDep, session: SessionDep
) -> None:
    workflow = await _get_owned(session, ctx.org_id, workflow_id)
    workflow.is_active = False
