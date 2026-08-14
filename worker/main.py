"""ARQ worker.

Agent workflows run for minutes, so they execute here rather than inside an HTTP
handler. The worker owns two guarantees the API cannot provide:

  * an organisation never exceeds its concurrent-run ceiling, even across
    replicas, because the slot is taken in Redis;
  * a run that dies with its worker is eventually reaped, so no row is left
    claimed forever.
"""

from __future__ import annotations

import asyncio

import structlog
from arq import cron
from arq.connections import RedisSettings
from prometheus_client import Counter, Histogram, start_http_server
from sqlalchemy import select

from core.config import get_settings
from core.db import dispose_engine, session_scope
from core.events import close_redis, get_redis
from core.logging import bind_request_context, clear_request_context, configure_logging
from core.models import Run
from core.ratelimit import RateLimiter
from core.runner import execute_run, mark_orphaned_runs

configure_logging()
log = structlog.get_logger("worker")
settings = get_settings()

RUNS_TOTAL = Counter("office_runs_total", "Workflow runs executed", ["status"])
RUN_DURATION = Histogram(
    "office_run_duration_seconds",
    "Wall-clock duration of a workflow run",
    buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1800, 3600),
)
SLOT_REJECTIONS = Counter(
    "office_run_slot_rejections_total", "Runs deferred because the org was at its limit"
)


async def run_workflow(ctx: dict, run_id: str) -> None:
    limiter: RateLimiter = ctx["limiter"]

    async with session_scope() as session:
        run = await session.get(Run, run_id)
        if run is None:
            log.warning("worker.run_missing", run_id=run_id)
            return
        org_id = run.org_id
        status = run.status

    if status != "queued":
        log.info("worker.skip_non_queued", run_id=run_id, status=status)
        return

    bucket = f"runs:{org_id}"
    acquired = await limiter.acquire_slot(
        bucket,
        limit=settings.max_concurrent_runs_per_org,
        ttl_seconds=settings.run_timeout_seconds + 300,
    )
    if not acquired:
        # Re-queue rather than fail: the org is busy, not broken.
        SLOT_REJECTIONS.inc()
        log.info("worker.slot_unavailable", run_id=run_id, org_id=org_id)
        await ctx["redis"].enqueue_job(
            "run_workflow", run_id, _defer_by=15, _job_id=f"run:{run_id}:retry"
        )
        return

    bind_request_context(run_id=run_id, org_id=org_id)
    log.info("worker.run_started", run_id=run_id)
    try:
        with RUN_DURATION.time():
            await execute_run(run_id)
    except Exception:
        RUNS_TOTAL.labels(status="crashed").inc()
        log.exception("worker.run_crashed", run_id=run_id)
        raise
    finally:
        await limiter.release_slot(bucket)
        clear_request_context()

    async with session_scope() as session:
        finished = await session.get(Run, run_id)
        RUNS_TOTAL.labels(status=finished.status if finished else "unknown").inc()


async def reap_orphans(ctx: dict) -> None:
    """Fail runs whose worker died without reporting completion."""
    await mark_orphaned_runs(older_than_seconds=settings.run_timeout_seconds + 300)


async def release_stale_slots(ctx: dict) -> None:
    """Re-derive concurrency counters from the database.

    Slot counters carry a TTL as a crash guard, but a worker killed between
    acquire and release still leaks one until it expires. Recomputing from the
    authoritative table keeps the ceiling honest.
    """
    limiter: RateLimiter = ctx["limiter"]
    redis_client = get_redis()
    async with session_scope() as session:
        rows = (
            await session.execute(select(Run.org_id).where(Run.status == "running"))
        ).scalars().all()
    actual: dict[str, int] = {}
    for org_id in rows:
        actual[org_id] = actual.get(org_id, 0) + 1

    async for key in redis_client.scan_iter(match="slots:runs:*"):
        org_id = key.split("slots:runs:", 1)[-1]
        expected = actual.get(org_id, 0)
        current = await limiter.slots_in_use(f"runs:{org_id}")
        if current > expected:
            log.info(
                "worker.slot_drift_corrected",
                org_id=org_id,
                counter=current,
                running=expected,
            )
            await redis_client.set(key, expected, ex=settings.run_timeout_seconds + 300)


async def startup(ctx: dict) -> None:
    ctx["limiter"] = RateLimiter(get_redis())
    if settings.env != "test":
        # One metrics endpoint per worker process; the orchestrator scrapes it.
        try:
            start_http_server(9100)
        except OSError as exc:
            log.warning("worker.metrics_port_busy", error=str(exc))
    log.info("worker.started", concurrency=settings.max_concurrent_runs_per_org)


async def shutdown(ctx: dict) -> None:
    await close_redis()
    await dispose_engine()
    log.info("worker.stopped")


class WorkerSettings:
    functions = [run_workflow]
    cron_jobs = [
        cron(reap_orphans, minute={0, 15, 30, 45}, run_at_startup=True),
        cron(release_stale_slots, minute={5, 35}),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 10
    job_timeout = settings.run_timeout_seconds + 120
    keep_result = 3600
    # A run that died halfway already spent money with the provider; retrying it
    # would pay for the same work twice, so failures surface instead.
    max_tries = 1
    # Give in-flight runs a chance to record their terminal state on SIGTERM.
    handle_signals = True
    shutdown_delay = 30.0


if __name__ == "__main__":  # pragma: no cover - convenience for local runs
    from arq import run_worker

    asyncio.run(run_worker(WorkerSettings))  # type: ignore[arg-type]
