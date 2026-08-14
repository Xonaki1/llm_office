from __future__ import annotations

import json

import pytest

from core import events as ev
from core.orchestration.budget import BudgetExceeded, BudgetGuard, RunCancelled, RunTimedOut
from core.orchestration.engine import Engine, extract_json
from core.orchestration.state import AgentSpec, RunState
from tests.conftest import FakeEmitter, FakeLLM


def agent(agent_id: str, name: str, role: str, model: str = "claude-sonnet-5") -> AgentSpec:
    return AgentSpec(id=agent_id, name=name, role=role, system_prompt="", model=model)


def make_engine(
    llm: FakeLLM,
    emitter: FakeEmitter,
    *,
    max_steps: int = 10,
    max_cents: int = 10,
    max_seconds: float = 3600.0,
    cancel_check=None,
    artifacts: list | None = None,
) -> Engine:
    async def sink(record):
        if artifacts is not None:
            artifacts.append(record)

    return Engine(
        llm=llm,  # type: ignore[arg-type]
        emitter=emitter,  # type: ignore[arg-type]
        budget=BudgetGuard(
            max_steps=max_steps,
            max_cost_microcents=max_cents * 1_000_000,
            max_seconds=max_seconds,
            cancel_check=cancel_check,
        ),
        artifact_sink=sink if artifacts is not None else None,
    )


class TestSingleTurn:
    async def test_emits_start_and_end_events(self):
        emitter = FakeEmitter()
        engine = make_engine(FakeLLM(replies=["done"]), emitter)
        state = RunState(user_input="do the thing")

        output = await engine.call_agent(agent("a", "Ann", "writer"), state, "write it")

        assert output == "done"
        assert ev.STEP_START in emitter.types
        assert ev.STEP_END in emitter.types
        assert emitter.of_type(ev.STEP_END)[0]["cost_microcents"] == 1_000_000

    async def test_prompt_carries_the_board_and_artifact_index(self):
        llm = FakeLLM(replies=["first", "second"])
        engine = make_engine(llm, FakeEmitter())
        state = RunState(user_input="build it")

        await engine.call_agent(agent("a", "Ann", "pm"), state, "spec it")
        await engine.call_agent(agent("b", "Bob", "dev"), state, "build it")

        second_prompt = llm.calls[1]["prompt"]
        assert "first" in second_prompt
        assert "## Artifacts so far" in second_prompt
        assert "## Original request" in second_prompt

    async def test_artifacts_are_captured_and_versioned(self):
        captured: list = []
        llm = FakeLLM(
            replies=[
                "```python path=src/app.py\nv1 = True\n```",
                "```python path=src/app.py\nv2 = True\n```",
            ]
        )
        engine = make_engine(llm, FakeEmitter(), artifacts=captured)
        state = RunState(user_input="build it")

        spec = agent("a", "Ann", "dev")
        await engine.call_agent(spec, state, "write it")
        await engine.call_agent(spec, state, "revise it")

        assert [record.version for record in captured] == [1, 2]
        assert state.artifact_versions["src/app.py"] == 2
        assert "v2" in state.artifacts["src/app.py"]


class TestGuards:
    async def test_cost_ceiling_stops_a_runaway_loop(self):
        # One cent per call against a two-cent ceiling: the third must not run.
        llm = FakeLLM(replies=["a", "b", "c", "d"])
        engine = make_engine(llm, FakeEmitter(), max_cents=2)
        state = RunState(user_input="loop")
        spec = agent("a", "Looper", "worker")

        with pytest.raises(BudgetExceeded, match="cost limit"):
            for _ in range(5):
                await engine.call_agent(spec, state, "keep going")

        assert len(llm.calls) == 2
        assert engine.budget.steps_used == 2

    async def test_step_ceiling_is_enforced(self):
        llm = FakeLLM(replies=["x"])
        engine = make_engine(llm, FakeEmitter(), max_steps=2, max_cents=1000)
        state = RunState(user_input="loop")
        spec = agent("a", "Looper", "worker")

        with pytest.raises(BudgetExceeded, match="step limit"):
            for _ in range(5):
                await engine.call_agent(spec, state, "again")
        assert len(llm.calls) == 2

    async def test_time_ceiling_is_enforced(self):
        llm = FakeLLM(replies=["x"])
        engine = make_engine(llm, FakeEmitter(), max_seconds=0.0)
        state = RunState(user_input="slow")

        with pytest.raises(RunTimedOut):
            await engine.call_agent(agent("a", "Ann", "worker"), state, "go")
        assert llm.calls == []

    async def test_cancellation_is_checked_before_spending(self):
        llm = FakeLLM(replies=["x"])
        cancelled = {"value": False}

        async def check():
            return cancelled["value"]

        engine = make_engine(llm, FakeEmitter(), cancel_check=check)
        state = RunState(user_input="cancel me")
        spec = agent("a", "Ann", "worker")

        await engine.call_agent(spec, state, "first")
        cancelled["value"] = True
        with pytest.raises(RunCancelled):
            await engine.call_agent(spec, state, "second")

        # The cancelled step must not have reached the provider.
        assert len(llm.calls) == 1

    async def test_budget_warning_fires_once(self):
        emitter = FakeEmitter()
        llm = FakeLLM(replies=["x"])
        engine = make_engine(llm, emitter, max_cents=3, max_steps=10)
        state = RunState(user_input="spend")
        spec = agent("a", "Ann", "worker")

        for _ in range(3):
            await engine.call_agent(spec, state, "go")

        assert len(emitter.of_type(ev.BUDGET_WARNING)) == 1


class TestJsonReplies:
    async def test_parses_a_clean_object(self):
        llm = FakeLLM(replies=['{"action": "finish"}'])
        engine = make_engine(llm, FakeEmitter())
        result = await engine.call_agent_json(
            agent("a", "Ann", "router"), RunState(user_input="x"), "decide"
        )
        assert result["action"] == "finish"

    async def test_reprompts_once_on_malformed_output(self):
        llm = FakeLLM(replies=["not json at all", '{"action": "finish"}'])
        engine = make_engine(llm, FakeEmitter())
        result = await engine.call_agent_json(
            agent("a", "Ann", "router"), RunState(user_input="x"), "decide"
        )
        assert result["action"] == "finish"
        assert len(llm.calls) == 2
        assert "could not be parsed" in llm.calls[1]["prompt"]

    async def test_missing_required_key_triggers_a_reprompt(self):
        llm = FakeLLM(replies=['{"other": 1}', '{"action": "finish"}'])
        engine = make_engine(llm, FakeEmitter())
        result = await engine.call_agent_json(
            agent("a", "Ann", "router"),
            RunState(user_input="x"),
            "decide",
            required_keys=("action",),
        )
        assert result["action"] == "finish"

    async def test_gives_up_after_the_retry_budget(self):
        llm = FakeLLM(replies=["nope", "still nope", "nope again"])
        engine = make_engine(llm, FakeEmitter())
        with pytest.raises(ValueError, match="usable JSON"):
            await engine.call_agent_json(
                agent("a", "Ann", "router"), RunState(user_input="x"), "decide"
            )


@pytest.mark.parametrize(
    "raw",
    [
        '{"action": "finish"}',
        'Sure:\n```json\n{"action": "finish"}\n```',
        'Thinking out loud... {"action": "finish"} — done.',
        '  {"action": "finish"}  ',
    ],
)
def test_extract_json_tolerates_wrapping(raw):
    assert extract_json(raw)["action"] == "finish"


@pytest.mark.parametrize("raw", ["no json here", "[1, 2, 3]", ""])
def test_extract_json_rejects_non_objects(raw):
    with pytest.raises(ValueError):
        extract_json(raw)


class TestBoardCompaction:
    def test_recent_entries_stay_verbatim_and_old_ones_shrink(self):
        state = RunState(user_input="task")
        spec = agent("a", "Ann", "worker")
        for index in range(12):
            state.step_index = index
            state.append(spec, f"entry-{index} " + "x" * 2000)

        transcript = state.transcript(max_chars=8000, keep_recent=3)
        assert "entry-11" in transcript
        assert "omitted" in transcript
        assert len(transcript) < 12 * 2000

    def test_private_entries_are_hidden_from_other_agents(self):
        state = RunState(user_input="task")
        alice, bob = agent("a", "Ann", "w"), agent("b", "Bob", "w")
        state.append(alice, "public note")
        state.append(bob, "private note", visible_to=frozenset({"b"}))

        assert "private note" not in state.transcript(agent_id="a")
        assert "private note" in state.transcript(agent_id="b")


def test_json_serialisable_events_only():
    """Every event payload must survive `json.dumps`, since the SSE layer
    serialises it verbatim."""
    emitter = FakeEmitter()
    emitter.events.append({"seq": 1, "type": "x", "value": 1, "nested": {"a": [1, 2]}})
    json.dumps(emitter.events)
