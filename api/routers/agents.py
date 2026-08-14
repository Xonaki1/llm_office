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
from api.schemas import AgentCreate, AgentOut, AgentUpdate
from core import audit
from core.models import Agent

router = APIRouter(
    prefix="/orgs/{org_id}/agents",
    tags=["agents"],
    dependencies=[Depends(enforce_api_rate_limit)],
)


async def _get_owned(session: SessionDep, org_id: str, agent_id: str) -> Agent:
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.org_id != org_id:
        raise HTTPException(status_code=404, detail="agent not found")
    return agent


@router.post("", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
async def create_agent(
    payload: AgentCreate, ctx: MemberDep, session: SessionDep, request: Request
) -> Agent:
    agent = Agent(org_id=ctx.org_id, created_by=ctx.user.id, **payload.model_dump())
    session.add(agent)
    await session.flush()
    audit.record(
        session,
        action=audit.AGENT_CREATED,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="agent",
        target_id=agent.id,
        ip_address=client_ip(request),
        name=agent.name,
        model=agent.model,
    )
    return agent


@router.get("", response_model=list[AgentOut])
async def list_agents(
    ctx: OrgDep, session: SessionDep, include_inactive: bool = False
) -> list[Agent]:
    stmt = select(Agent).where(Agent.org_id == ctx.org_id)
    if not include_inactive:
        stmt = stmt.where(Agent.is_active.is_(True))
    stmt = stmt.order_by(Agent.created_at)
    return list((await session.execute(stmt)).scalars().all())


@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(agent_id: str, ctx: OrgDep, session: SessionDep) -> Agent:
    return await _get_owned(session, ctx.org_id, agent_id)


@router.patch("/{agent_id}", response_model=AgentOut)
async def update_agent(
    agent_id: str,
    payload: AgentUpdate,
    ctx: MemberDep,
    session: SessionDep,
    request: Request,
) -> Agent:
    agent = await _get_owned(session, ctx.org_id, agent_id)
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(agent, field, value)
    await session.flush()
    audit.record(
        session,
        action=audit.AGENT_UPDATED,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="agent",
        target_id=agent.id,
        ip_address=client_ip(request),
        fields=sorted(changes),
    )
    return agent


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_agent(
    agent_id: str, ctx: AdminDep, session: SessionDep, request: Request
) -> None:
    agent = await _get_owned(session, ctx.org_id, agent_id)
    # Soft delete: historical runs and steps reference this agent by id, and a
    # hard delete would orphan them.
    agent.is_active = False
    audit.record(
        session,
        action=audit.AGENT_DELETED,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="agent",
        target_id=agent.id,
        ip_address=client_ip(request),
    )
