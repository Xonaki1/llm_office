from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from api.deps import (
    MemberDep,
    OrgDep,
    QueueDep,
    RateLimiterDep,
    SessionDep,
    client_ip,
    enforce_api_rate_limit,
)
from api.schemas import (
    ArtifactContent,
    ArtifactOut,
    RunCreate,
    RunDetail,
    RunOut,
    ToolCallOut,
)
from core import audit, billing
from core import events as ev
from core.config import get_settings
from core.models import Artifact, Run, ToolCallLog, Workflow
from core.orchestration.presets import WorkflowConfigError, validate_graph
from core.runner import TERMINAL_STATUSES

router = APIRouter(
    prefix="/orgs/{org_id}/runs",
    tags=["runs"],
    dependencies=[Depends(enforce_api_rate_limit)],
)

# SSE heartbeat. Proxies drop idle connections; a comment frame keeps the stream
# alive while an agent is thinking for a minute with no tokens to show.
_PING_SECONDS = 15


async def _get_owned(session: SessionDep, org_id: str, run_id: str) -> Run:
    run = await session.get(Run, run_id)
    if run is None or run.org_id != org_id:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@router.post("", response_model=RunOut, status_code=status.HTTP_201_CREATED)
async def create_run(
    payload: RunCreate,
    ctx: MemberDep,
    session: SessionDep,
    queue: QueueDep,
    limiter: RateLimiterDep,
    request: Request,
    idempotency_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Run:
    settings = get_settings()

    workflow = await session.get(Workflow, payload.workflow_id)
    if workflow is None or workflow.org_id != ctx.org_id or not workflow.is_active:
        raise HTTPException(status_code=404, detail="workflow not found")

    # Re-validate at submit time: agents referenced by the graph may have been
    # deactivated since the workflow was saved.
    try:
        validate_graph(workflow.preset, dict(workflow.graph or {}))
    except WorkflowConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    idempotency_key = payload.idempotency_key or idempotency_header
    if idempotency_key:
        existing = (
            await session.execute(
                select(Run).where(
                    Run.org_id == ctx.org_id, Run.idempotency_key == idempotency_key
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    throttle = await limiter.check(
        f"runs:{ctx.org_id}",
        limit=settings.rate_limit_runs_per_minute,
        window_seconds=60,
    )
    if not throttle.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="run rate limit exceeded for this organisation",
            headers={"Retry-After": str(max(throttle.retry_after_seconds, 1))},
        )

    in_flight = await limiter.slots_in_use(f"runs:{ctx.org_id}")
    if in_flight >= settings.max_concurrent_runs_per_org:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"this organisation already has {in_flight} runs in flight "
                f"(limit {settings.max_concurrent_runs_per_org})"
            ),
        )

    key_mode = payload.key_mode or ctx.org.key_mode
    graph = dict(workflow.graph or {})
    ceiling_cents = min(
        int(graph.get("max_cost_cents", settings.max_cost_cents_per_run)),
        settings.max_cost_cents_per_run,
    )

    # Only managed spend draws on credits; a BYOK run costs the platform nothing.
    if key_mode != "byok":
        try:
            await billing.ensure_can_start(session, ctx.org_id, ceiling_cents)
        except billing.InsufficientCredits as exc:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=(
                    f"insufficient credits: balance {exc.balance_cents} cents, "
                    f"this run may cost up to {exc.required_cents}"
                ),
            ) from exc

    run = Run(
        org_id=ctx.org_id,
        workflow_id=workflow.id,
        created_by=ctx.user.id,
        input=payload.input,
        key_mode=key_mode,
        status="queued",
        max_cost_cents=ceiling_cents,
        idempotency_key=idempotency_key,
    )
    session.add(run)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        duplicate = (
            await session.execute(
                select(Run).where(
                    Run.org_id == ctx.org_id, Run.idempotency_key == idempotency_key
                )
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            return duplicate
        raise

    audit.record(
        session,
        action=audit.RUN_CREATED,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="run",
        target_id=run.id,
        ip_address=client_ip(request),
        workflow_id=workflow.id,
        key_mode=key_mode,
    )

    # Commit before enqueuing: the worker looks the row up by id, and a job that
    # arrives before the transaction lands would not find it.
    await session.commit()
    await queue.enqueue_job("run_workflow", run.id, _job_id=f"run:{run.id}")
    return run


@router.get("", response_model=list[RunOut])
async def list_runs(
    ctx: OrgDep,
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status_filter: str | None = Query(default=None, alias="status"),
) -> list[Run]:
    stmt = select(Run).where(Run.org_id == ctx.org_id)
    if status_filter:
        stmt = stmt.where(Run.status == status_filter)
    stmt = stmt.order_by(Run.created_at.desc()).limit(limit).offset(offset)
    return list((await session.execute(stmt)).scalars().all())


@router.get("/{run_id}", response_model=RunDetail)
async def get_run(run_id: str, ctx: OrgDep, session: SessionDep) -> Run:
    stmt = (
        select(Run)
        .where(Run.id == run_id)
        .options(
            selectinload(Run.steps),
            selectinload(Run.artifacts),
            selectinload(Run.tool_calls),
        )
    )
    run = (await session.execute(stmt)).scalar_one_or_none()
    if run is None or run.org_id != ctx.org_id:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@router.get("/{run_id}/artifacts", response_model=list[ArtifactOut])
async def list_artifacts(run_id: str, ctx: OrgDep, session: SessionDep) -> list[Artifact]:
    await _get_owned(session, ctx.org_id, run_id)
    stmt = (
        select(Artifact)
        .where(Artifact.run_id == run_id)
        .order_by(Artifact.path, Artifact.version.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


@router.get("/{run_id}/artifacts/{artifact_id}", response_model=ArtifactContent)
async def get_artifact(
    run_id: str, artifact_id: str, ctx: OrgDep, session: SessionDep
) -> Artifact:
    await _get_owned(session, ctx.org_id, run_id)
    artifact = await session.get(Artifact, artifact_id)
    if artifact is None or artifact.run_id != run_id:
        raise HTTPException(status_code=404, detail="artifact not found")
    return artifact


@router.get("/{run_id}/tool-calls", response_model=list[ToolCallOut])
async def list_tool_calls(
    run_id: str, ctx: OrgDep, session: SessionDep
) -> list[ToolCallLog]:
    """Every tool a run invoked, with arguments and results — the audit view of
    what the agents actually touched."""
    await _get_owned(session, ctx.org_id, run_id)
    stmt = (
        select(ToolCallLog)
        .where(ToolCallLog.run_id == run_id)
        .order_by(ToolCallLog.step_index, ToolCallLog.call_index)
    )
    return list((await session.execute(stmt)).scalars().all())


@router.get("/{run_id}/stream")
async def stream_run(
    run_id: str,
    ctx: OrgDep,
    session: SessionDep,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> EventSourceResponse:
    """Server-sent event feed for one run.

    History is replayed before live events, so a client that connects mid-run —
    or reconnects after a dropped connection with `Last-Event-ID` — sees the
    whole trace exactly once.
    """
    run = await _get_owned(session, ctx.org_id, run_id)

    try:
        after_seq = int(last_event_id) if last_event_id else 0
    except ValueError:
        after_seq = 0

    async def generator() -> AsyncIterator[dict]:
        highest = after_seq
        for event in await ev.replay(run_id, after_seq=after_seq):
            highest = max(highest, event.get("seq", 0))
            yield _frame(event)
            if event["type"] in ev.TERMINAL_EVENTS:
                return

        # A run that finished before the stream opened has no live events coming.
        if run.status in TERMINAL_STATUSES and highest == after_seq:
            yield _frame(
                {
                    "seq": highest + 1,
                    "type": ev.RUN_END,
                    "run_id": run_id,
                    "status": run.status,
                    "output": run.output,
                    "error": run.error,
                }
            )
            return

        async for event in ev.subscribe(run_id):
            if await request.is_disconnected():
                return
            if event.get("seq", 0) <= highest:
                continue
            highest = event["seq"]
            yield _frame(event)
            if event["type"] in ev.TERMINAL_EVENTS:
                return

    return EventSourceResponse(generator(), ping=_PING_SECONDS)


def _frame(event: dict) -> dict:
    return {
        "id": str(event.get("seq", "")),
        "event": event["type"],
        "data": json.dumps(event, ensure_ascii=False),
    }


@router.post("/{run_id}/cancel", response_model=RunOut)
async def cancel_run(
    run_id: str, ctx: MemberDep, session: SessionDep, request: Request
) -> Run:
    """Request cancellation.

    A queued run is stopped outright. A running one is flagged in Redis; the
    engine checks the flag before every step, so it stops at the next boundary
    rather than mid-call — a provider call already in flight is paid for either
    way, and aborting it would lose the result we were charged for.
    """
    run = await _get_owned(session, ctx.org_id, run_id)
    if run.status in TERMINAL_STATUSES:
        return run

    run.cancel_requested = True
    await ev.request_cancel(run_id)

    if run.status == "queued":
        from datetime import UTC, datetime

        run.status = "cancelled"
        run.error = "cancelled before it started"
        run.finished_at = datetime.now(UTC)
        await ev.EventEmitter(run_id).emit(
            ev.RUN_CANCELLED, status="cancelled", error=run.error
        )

    audit.record(
        session,
        action=audit.RUN_CANCELLED,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="run",
        target_id=run_id,
        ip_address=client_ip(request),
        previous_status=run.status,
    )
    await asyncio.sleep(0)  # let the flag land before the client re-reads status
    return run
