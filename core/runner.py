"""Glue between persistence and the orchestration engine.

`execute_run` is what the worker calls. It is the only module that knows about
both the database and the engine, and it owns the run's terminal state: whatever
happens, the run row ends in a terminal status, the credit ledger is settled
exactly once, and a terminal event reaches the client.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core import audit, billing
from core import events as ev
from core.billing import apply_markup
from core.config import get_settings
from core.db import session_scope
from core.events import EventEmitter
from core.llm.keys import KeyResolutionError, KeyResolver
from core.llm.pricing import default_price_book, microcents_to_cents
from core.llm.providers import LLMError, RefusalError
from core.llm.registry import UnknownModelError
from core.llm.router import LLMRouter
from core.models import Agent, Artifact, Run, RunStep, Workflow
from core.orchestration.budget import BudgetGuard, RunAborted
from core.orchestration.engine import ArtifactRecord, Engine, StepRecord
from core.orchestration.presets import WorkflowConfigError, get_preset
from core.orchestration.state import AgentSpec, RunState

log = structlog.get_logger(__name__)

TERMINAL_STATUSES = frozenset(
    {"succeeded", "failed", "cancelled", "budget_exceeded", "timed_out"}
)


def _now() -> datetime:
    return datetime.now(UTC)


def _to_spec(row: Agent) -> AgentSpec:
    return AgentSpec(
        id=row.id,
        name=row.name,
        role=row.role,
        system_prompt=row.system_prompt,
        model=row.model,
        effort=row.effort,
        max_tokens=row.max_tokens,
        tools=list(row.tools or []),
    )


async def _load_agents(session: AsyncSession, org_id: str) -> dict[str, AgentSpec]:
    stmt = select(Agent).where(Agent.org_id == org_id, Agent.is_active.is_(True))
    rows = (await session.execute(stmt)).scalars().all()
    return {row.id: _to_spec(row) for row in rows}


async def _claim(session: AsyncSession, run_id: str) -> Run | None:
    """Move a queued run to `running`, or return None if someone else has it.

    The transition is the claim: a duplicate delivery from the queue finds the
    run already running and exits without re-charging for work in flight.
    """
    run = await session.get(Run, run_id, with_for_update=True)
    if run is None:
        raise ValueError(f"run {run_id} not found")
    if run.status != "queued":
        log.info("runner.already_claimed", run_id=run_id, status=run.status)
        return None
    run.status = "running"
    run.started_at = _now()
    await session.flush()
    return run


async def execute_run(run_id: str) -> None:
    settings = get_settings()
    emitter = EventEmitter(run_id)
    router: LLMRouter | None = None

    async with session_scope() as session:
        run = await _claim(session, run_id)
        if run is None:
            return

        workflow = await session.get(Workflow, run.workflow_id)
        if workflow is None:
            raise ValueError(f"workflow {run.workflow_id} not found")

        agents = await _load_agents(session, run.org_id)
        graph = dict(workflow.graph or {})

        budget = BudgetGuard(
            max_steps=min(
                int(graph.get("max_steps", settings.max_steps_per_run)),
                settings.max_steps_per_run,
            ),
            max_cost_microcents=min(
                int(graph.get("max_cost_cents", settings.max_cost_cents_per_run)),
                settings.max_cost_cents_per_run,
            )
            * 1_000_000,
            max_seconds=float(settings.run_timeout_seconds),
            cancel_check=lambda: ev.is_cancelled(run_id),
        )
        run.max_steps = budget.max_steps
        run.max_cost_cents = budget.max_cost_microcents // 1_000_000

        totals = {"tokens_in": 0, "tokens_out": 0, "billable": 0}

        async def persist_step(record: StepRecord) -> None:
            totals["tokens_in"] += record.tokens_in
            totals["tokens_out"] += record.tokens_out
            totals["billable"] += record.billable_microcents
            session.add(
                RunStep(
                    run_id=run_id,
                    index=record.index,
                    agent_id=record.agent.id,
                    agent_name=record.agent.name,
                    role=record.agent.role,
                    model=record.agent.model,
                    provider=record.provider,
                    input=record.instruction,
                    output=record.output,
                    stop_reason=record.stop_reason,
                    tokens_in=record.tokens_in,
                    tokens_out=record.tokens_out,
                    cached_tokens=record.cached_tokens,
                    reasoning_tokens=record.reasoning_tokens,
                    cost_microcents=record.cost_microcents,
                    latency_ms=record.latency_ms,
                    attempts=record.attempts,
                )
            )
            # Flush per step so a crashed worker still leaves a partial trace
            # the user can inspect and bill against.
            await session.flush()

        async def persist_artifact(record: ArtifactRecord) -> None:
            session.add(
                Artifact(
                    run_id=run_id,
                    org_id=run.org_id,
                    path=record.path,
                    kind=record.kind,
                    language=record.language,
                    content=record.content,
                    size_bytes=len(record.content.encode()),
                    version=record.version,
                    produced_by_step=record.step_index,
                    produced_by_agent=record.agent_name,
                )
            )
            await session.flush()

        price_book = default_price_book()
        await price_book.refresh(session)

        router = LLMRouter(KeyResolver(session, run.org_id, run.key_mode), price_book)
        engine = Engine(
            llm=router,
            emitter=emitter,
            budget=budget,
            step_sink=persist_step,
            artifact_sink=persist_artifact,
            markup=apply_markup,
            max_board_chars=settings.max_board_chars,
            step_timeout_seconds=float(settings.step_timeout_seconds),
        )
        state = RunState(user_input=run.input)

        await emitter.emit(
            ev.RUN_START,
            workflow_id=workflow.id,
            workflow_name=workflow.name,
            preset=workflow.preset,
            key_mode=run.key_mode,
            max_steps=budget.max_steps,
            max_cost_cents=budget.max_cost_microcents // 1_000_000,
        )

        status = "succeeded"
        error: str | None = None
        try:
            preset = get_preset(workflow.preset)
            await asyncio.wait_for(
                preset(engine, graph, agents, state),
                timeout=settings.run_timeout_seconds,
            )
        except RunAborted as exc:
            status, error = exc.status, exc.reason
        except TimeoutError:
            status, error = "timed_out", f"run exceeded {settings.run_timeout_seconds}s"
        except asyncio.CancelledError:
            # Worker shutdown. Record it and let the cancellation propagate so
            # the process can exit; the finally block still settles billing.
            status, error = "cancelled", "worker shut down"
            raise
        except (WorkflowConfigError, KeyResolutionError, UnknownModelError) as exc:
            status, error = "failed", str(exc)
        except RefusalError as exc:
            status, error = "failed", f"model refused the request: {exc.explanation or ''}"
        except LLMError as exc:
            status, error = "failed", str(exc)
        except Exception as exc:  # noqa: BLE001 - the run row must not be left running
            log.exception("runner.unhandled", run_id=run_id)
            status, error = "failed", f"{type(exc).__name__}: {exc}"
        finally:
            run.status = status
            run.error = error
            run.output = state.final_output or (
                state.last_output() if state.board else None
            )
            run.steps_used = budget.steps_used
            run.cost_microcents = budget.spent_microcents
            run.cost_cents = microcents_to_cents(budget.spent_microcents)
            run.billable_microcents = totals["billable"]
            run.tokens_in = totals["tokens_in"]
            run.tokens_out = totals["tokens_out"]
            run.finished_at = _now()

            # Charge for what was actually consumed, even on failure — the
            # provider already billed us for every completed step.
            await billing.charge_run(
                session,
                org_id=run.org_id,
                run_id=run_id,
                billable_microcents=totals["billable"],
            )
            await ev.clear_cancel(run_id)
            if router is not None:
                await router.aclose()

        await emitter.emit(
            ev.RUN_CANCELLED if status == "cancelled" else ev.RUN_END,
            status=status,
            error=error,
            output=run.output,
            steps_used=budget.steps_used,
            cost_cents=run.cost_cents,
            tokens_in=run.tokens_in,
            tokens_out=run.tokens_out,
            artifacts=[
                {"path": ref.path, "version": ref.version, "kind": ref.kind}
                for ref in state.artifact_log
            ],
            notes=state.notes,
        )
        audit.record(
            session,
            action="run.finished",
            org_id=run.org_id,
            target_type="run",
            target_id=run_id,
            status=status,
            steps=budget.steps_used,
            cost_cents=run.cost_cents,
        )
        log.info(
            "runner.finished",
            run_id=run_id,
            status=status,
            steps=budget.steps_used,
            cost_cents=run.cost_cents,
        )


async def mark_orphaned_runs(older_than_seconds: int = 3600) -> int:
    """Fail runs stuck in `running` past the timeout.

    A worker killed mid-run leaves its row claimed forever; without this sweep
    the org's concurrency slots leak and the UI shows a spinner that never ends.
    """
    cutoff = _now().timestamp() - older_than_seconds
    fixed = 0
    async with session_scope() as session:
        stmt = select(Run).where(Run.status == "running")
        for run in (await session.execute(stmt)).scalars().all():
            started = run.started_at.timestamp() if run.started_at else 0
            if started and started > cutoff:
                continue
            run.status = "timed_out"
            run.error = "worker did not report completion"
            run.finished_at = _now()
            fixed += 1
            await EventEmitter(run.id).emit(
                ev.RUN_END, status="timed_out", error=run.error
            )
    if fixed:
        log.warning("runner.orphans_reaped", count=fixed)
    return fixed


def is_terminal(status: str) -> bool:
    return status in TERMINAL_STATUSES
