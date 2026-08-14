"""End-to-end run execution: database, engine, events, artifacts and billing.

The provider layer is the only thing faked — everything else is the real code
path the worker takes.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select

from core import billing
from core import events as ev
from core.models import Agent, Artifact, Membership, Org, Run, RunStep, User, Workflow
from core.security import hash_password
from tests.conftest import TEST_PASSWORD, FakeLLM


@pytest.fixture
async def fixtures(session):
    org = Org(name="Runner Org", slug="runner-org", credits_cents=10_000)
    user = User(email="runner@example.com", password_hash=hash_password(TEST_PASSWORD))
    session.add_all([org, user])
    await session.flush()
    session.add(Membership(user_id=user.id, org_id=org.id, role="owner"))

    pm = Agent(org_id=org.id, name="Nadia", role="pm", model="claude-sonnet-5")
    dev = Agent(org_id=org.id, name="Ravi", role="dev", model="claude-opus-5")
    session.add_all([pm, dev])
    await session.flush()

    workflow = Workflow(
        org_id=org.id,
        name="Two-step",
        preset="pipeline",
        graph={
            "nodes": [{"agent_id": pm.id}, {"agent_id": dev.id}],
            "max_steps": 5,
            "max_cost_cents": 50,
        },
    )
    session.add(workflow)
    await session.flush()
    await session.commit()
    return {"org": org, "user": user, "workflow": workflow, "pm": pm, "dev": dev}


@pytest.fixture
def patched_runner(monkeypatch, session_factory, redis_client):
    """Point the runner at the test database, fake Redis and a fake provider."""
    import core.events as events_module
    import core.runner as runner_module

    monkeypatch.setattr(events_module, "_client", redis_client)

    @asynccontextmanager
    async def scope():
        async with session_factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    monkeypatch.setattr(runner_module, "session_scope", scope)

    def install(llm: FakeLLM):
        monkeypatch.setattr(runner_module, "LLMRouter", lambda *a, **k: llm)
        return llm

    return install


async def _balance(session, org_id: str) -> int:
    """Read the balance the runner wrote.

    The runner commits on its own session; this one still holds the fixture's
    Org in its identity map, so it must be expired before re-reading or the
    assertion checks a stale value.
    """
    session.expire_all()
    return await billing.balance(session, org_id)


async def _queue_run(session, org_id: str, workflow_id: str, **kwargs) -> str:
    run = Run(
        org_id=org_id,
        workflow_id=workflow_id,
        input=kwargs.pop("input", "build a URL shortener"),
        key_mode=kwargs.pop("key_mode", "managed"),
        status="queued",
        **kwargs,
    )
    session.add(run)
    await session.flush()
    await session.commit()
    return run.id


class TestSuccessfulRun:
    async def test_completes_and_records_everything(
        self, session, fixtures, patched_runner
    ):
        from core.runner import execute_run

        patched_runner(
            FakeLLM(
                replies=[
                    "Spec: shorten URLs.",
                    "Done.\n```python path=src/app.py\nprint('hi')\n```",
                ]
            )
        )
        run_id = await _queue_run(session, fixtures["org"].id, fixtures["workflow"].id)

        await execute_run(run_id)

        run = await session.get(Run, run_id)
        await session.refresh(run)
        assert run.status == "succeeded"
        assert run.steps_used == 2
        assert run.tokens_in > 0
        assert run.finished_at is not None

        steps = (
            await session.execute(select(RunStep).where(RunStep.run_id == run_id))
        ).scalars().all()
        assert len(steps) == 2
        assert {s.agent_name for s in steps} == {"Nadia", "Ravi"}

        artifacts = (
            await session.execute(select(Artifact).where(Artifact.run_id == run_id))
        ).scalars().all()
        assert len(artifacts) == 1
        assert artifacts[0].path == "src/app.py"
        assert artifacts[0].version == 1

    async def test_emits_a_complete_event_stream(
        self, session, fixtures, patched_runner, redis_client
    ):
        from core.runner import execute_run

        patched_runner(FakeLLM(replies=["one", "two"]))
        run_id = await _queue_run(session, fixtures["org"].id, fixtures["workflow"].id)
        await execute_run(run_id)

        events = await ev.replay(run_id, client=redis_client)
        types = [e["type"] for e in events]
        assert types[0] == ev.RUN_START
        assert types[-1] == ev.RUN_END
        assert types.count(ev.STEP_START) == 2
        assert types.count(ev.STEP_END) == 2
        # Sequence numbers must be strictly increasing for Last-Event-ID resume.
        seqs = [e["seq"] for e in events]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)

    async def test_managed_run_is_charged_with_markup(
        self, session, fixtures, patched_runner
    ):
        from core.runner import execute_run

        # 1 cent raw per step, two steps, 40% markup => 2.8 cents, rounded up to 3.
        patched_runner(FakeLLM(replies=["a", "b"], cost_microcents=1_000_000))
        run_id = await _queue_run(session, fixtures["org"].id, fixtures["workflow"].id)
        await execute_run(run_id)

        assert await _balance(session, fixtures["org"].id) == 10_000 - 3

    async def test_byok_run_is_not_charged(self, session, fixtures, patched_runner):
        from core.runner import execute_run

        patched_runner(
            FakeLLM(replies=["a", "b"], cost_microcents=5_000_000, billed_to_platform=False)
        )
        run_id = await _queue_run(
            session, fixtures["org"].id, fixtures["workflow"].id, key_mode="byok"
        )
        await execute_run(run_id)

        assert await _balance(session, fixtures["org"].id) == 10_000
        run = await session.get(Run, run_id)
        await session.refresh(run)
        # Spend is still recorded for reporting, just not billed.
        assert run.cost_cents > 0


class TestFailureHandling:
    async def test_budget_ceiling_marks_the_run_and_still_bills(
        self, session, fixtures, patched_runner
    ):
        from core.runner import execute_run

        workflow = await session.get(Workflow, fixtures["workflow"].id)
        workflow.graph = {**workflow.graph, "max_cost_cents": 1}
        await session.commit()

        patched_runner(FakeLLM(replies=["a", "b"], cost_microcents=1_000_000))
        run_id = await _queue_run(session, fixtures["org"].id, fixtures["workflow"].id)
        await execute_run(run_id)

        run = await session.get(Run, run_id)
        await session.refresh(run)
        assert run.status == "budget_exceeded"
        assert run.steps_used == 1
        # The provider was already paid for the completed step.
        assert await _balance(session, fixtures["org"].id) < 10_000

    async def test_cancellation_stops_the_run(
        self, session, fixtures, patched_runner, redis_client
    ):
        from core.runner import execute_run

        patched_runner(FakeLLM(replies=["a", "b"]))
        run_id = await _queue_run(session, fixtures["org"].id, fixtures["workflow"].id)
        await ev.request_cancel(run_id, redis_client)

        await execute_run(run_id)

        run = await session.get(Run, run_id)
        await session.refresh(run)
        assert run.status == "cancelled"
        assert run.steps_used == 0

        # The flag is cleared so a re-queued run is not cancelled by a stale key.
        assert await ev.is_cancelled(run_id, redis_client) is False

    async def test_provider_failure_is_recorded_not_raised(
        self, session, fixtures, patched_runner, monkeypatch
    ):
        from core.llm.providers import LLMError
        from core.runner import execute_run

        class ExplodingLLM(FakeLLM):
            async def complete(self, **kwargs):
                raise LLMError("provider is on fire")

        patched_runner(ExplodingLLM(replies=[]))
        run_id = await _queue_run(session, fixtures["org"].id, fixtures["workflow"].id)

        await execute_run(run_id)  # must not propagate

        run = await session.get(Run, run_id)
        await session.refresh(run)
        assert run.status == "failed"
        assert "on fire" in run.error

    async def test_broken_workflow_fails_cleanly(
        self, session, fixtures, patched_runner
    ):
        from core.runner import execute_run

        workflow = await session.get(Workflow, fixtures["workflow"].id)
        workflow.graph = {"nodes": [{"agent_id": "does-not-exist"}]}
        await session.commit()

        patched_runner(FakeLLM(replies=["x"]))
        run_id = await _queue_run(session, fixtures["org"].id, fixtures["workflow"].id)
        await execute_run(run_id)

        run = await session.get(Run, run_id)
        await session.refresh(run)
        assert run.status == "failed"
        assert "does not exist" in run.error


class TestIdempotency:
    async def test_a_second_delivery_is_ignored(self, session, fixtures, patched_runner):
        """ARQ can deliver the same job twice; the second must not re-run
        agents that have already been paid for."""
        from core.runner import execute_run

        llm = patched_runner(FakeLLM(replies=["a", "b"]))
        run_id = await _queue_run(session, fixtures["org"].id, fixtures["workflow"].id)

        await execute_run(run_id)
        calls_after_first = len(llm.calls)

        await execute_run(run_id)
        assert len(llm.calls) == calls_after_first

    async def test_billing_is_posted_once_per_run(
        self, session, fixtures, patched_runner
    ):
        from core.models import CreditLedger
        from core.runner import execute_run

        patched_runner(FakeLLM(replies=["a", "b"]))
        run_id = await _queue_run(session, fixtures["org"].id, fixtures["workflow"].id)
        await execute_run(run_id)
        await execute_run(run_id)

        entries = (
            await session.execute(
                select(CreditLedger).where(CreditLedger.run_id == run_id)
            )
        ).scalars().all()
        assert len(entries) == 1
