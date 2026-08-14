"""The engine's tool-calling loop.

The loop is where a run can spend without bound, so most of these tests are
about the guards rather than the happy path.
"""

from __future__ import annotations

import pytest

from core import events as ev
from core.orchestration.budget import BudgetExceeded, BudgetGuard, RunCancelled
from core.orchestration.engine import Engine, ToolCallRecord
from core.orchestration.state import RunState
from core.tools import resolve
from tests.conftest import FakeEmitter, FakeLLM, ToolTurn
from tests.test_engine import agent


def make_engine(
    llm: FakeLLM,
    emitter: FakeEmitter,
    *,
    tools: list[str] | None = None,
    max_iterations: int = 4,
    max_calls_per_turn: int = 6,
    max_steps: int = 10,
    max_cents: int = 100,
    cancel_check=None,
    tool_records: list[ToolCallRecord] | None = None,
) -> Engine:
    async def sink(record: ToolCallRecord) -> None:
        if tool_records is not None:
            tool_records.append(record)

    resolved = resolve(tools or [])
    return Engine(
        llm=llm,  # type: ignore[arg-type]
        emitter=emitter,  # type: ignore[arg-type]
        budget=BudgetGuard(
            max_steps=max_steps,
            max_cost_microcents=max_cents * 1_000_000,
            cancel_check=cancel_check,
        ),
        tool_resolver=lambda _agent: resolved,
        tool_call_sink=sink if tool_records is not None else None,
        max_tool_iterations=max_iterations,
        max_tool_calls_per_turn=max_calls_per_turn,
    )


class TestHappyPath:
    async def test_call_then_answer(self):
        emitter = FakeEmitter()
        llm = FakeLLM(
            replies=[
                ToolTurn(calls=[("write_artifact", {"path": "a.py", "content": "x = 1"})]),
                "Done. The file is written.",
            ]
        )
        engine = make_engine(llm, emitter, tools=["write_artifact"])
        state = RunState(user_input="write a file")

        output = await engine.call_agent(agent("a", "Ann", "dev"), state, "do it")

        assert output == "Done. The file is written."
        assert len(llm.calls) == 2, "one call to request the tool, one to answer"
        assert state.artifacts["a.py"] == "x = 1"
        assert state.artifact_versions["a.py"] == 1

    async def test_tool_events_are_emitted_in_order(self):
        emitter = FakeEmitter()
        llm = FakeLLM(
            replies=[ToolTurn(calls=[("list_artifacts", {})]), "Nothing there yet."]
        )
        engine = make_engine(llm, emitter, tools=["list_artifacts"])

        await engine.call_agent(agent("a", "Ann", "dev"), RunState(user_input="x"), "go")

        types = emitter.types
        assert types.index(ev.STEP_START) < types.index(ev.TOOL_CALL)
        assert types.index(ev.TOOL_CALL) < types.index(ev.TOOL_RESULT)
        assert types.index(ev.TOOL_RESULT) < types.index(ev.STEP_END)

    async def test_the_whole_turn_is_one_step_with_summed_usage(self):
        emitter = FakeEmitter()
        llm = FakeLLM(
            replies=[
                ToolTurn(calls=[("list_artifacts", {})]),
                ToolTurn(calls=[("list_artifacts", {})]),
                "Finally an answer.",
            ],
            cost_microcents=1_000_000,
        )
        engine = make_engine(llm, emitter, tools=["list_artifacts"])
        state = RunState(user_input="x")

        await engine.call_agent(agent("a", "Ann", "dev"), state, "go")

        # A user thinks in turns, not model calls: one board entry, one step
        # event, but the cost of all three calls.
        assert len(state.board) == 1
        assert len(emitter.of_type(ev.STEP_END)) == 1
        end = emitter.of_type(ev.STEP_END)[0]
        assert end["model_calls"] == 3
        assert end["tool_calls"] == 2
        assert end["cost_microcents"] == 3_000_000

    async def test_tool_results_reach_the_next_prompt(self):
        emitter = FakeEmitter()
        llm = FakeLLM(
            replies=[
                ToolTurn(calls=[("read_artifact", {"path": "seed.txt"})]),
                "I read it.",
            ]
        )
        engine = make_engine(llm, emitter, tools=["read_artifact"])
        state = RunState(user_input="x")
        state.record_artifact("seed.txt", "the seed contents", "text", "setup")

        await engine.call_agent(agent("a", "Ann", "dev"), state, "read it")

        second = llm.calls[1]["messages"]
        results = [m for m in second if m.tool_results]
        assert results, "the second call must carry the tool result"
        assert "the seed contents" in results[0].tool_results[0].content

    async def test_tool_calls_are_recorded_for_audit(self):
        records: list[ToolCallRecord] = []
        llm = FakeLLM(
            replies=[
                ToolTurn(calls=[("write_artifact", {"path": "x.py", "content": "y"})]),
                "done",
            ]
        )
        engine = make_engine(
            llm, FakeEmitter(), tools=["write_artifact"], tool_records=records
        )

        await engine.call_agent(agent("a", "Ann", "dev"), RunState(user_input="x"), "go")

        assert len(records) == 1
        assert records[0].tool == "write_artifact"
        assert records[0].arguments["path"] == "x.py"
        assert records[0].is_error is False


class TestGuards:
    async def test_the_iteration_ceiling_forces_an_answer(self):
        # The model asks for a tool forever; the engine must stop it.
        llm = FakeLLM(replies=[ToolTurn(calls=[("list_artifacts", {})])])
        engine = make_engine(
            llm, FakeEmitter(), tools=["list_artifacts"], max_iterations=3, max_steps=50
        )
        state = RunState(user_input="x")

        await engine.call_agent(agent("a", "Ann", "dev"), state, "go")

        # 3 tool rounds plus one final call made with the tools withdrawn.
        assert len(llm.calls) == 4
        assert llm.calls[-1]["tools"] == [], "the last call must offer no tools"
        assert state.notes["tool_loops_exhausted"] == [1]

    async def test_the_cost_ceiling_stops_a_tool_loop(self):
        llm = FakeLLM(
            replies=[ToolTurn(calls=[("list_artifacts", {})])], cost_microcents=1_000_000
        )
        engine = make_engine(
            llm,
            FakeEmitter(),
            tools=["list_artifacts"],
            max_iterations=50,
            max_cents=3,
            max_steps=50,
        )

        with pytest.raises(BudgetExceeded):
            await engine.call_agent(agent("a", "Ann", "dev"), RunState(user_input="x"), "go")

        # The guard runs before every model call, not just the first.
        assert len(llm.calls) == 3

    async def test_cancellation_interrupts_a_tool_loop(self):
        cancelled = {"value": False}

        async def check():
            # Let the first model call through, then cancel.
            if len(llm.calls) >= 1:
                cancelled["value"] = True
            return cancelled["value"] and len(llm.calls) >= 1

        llm = FakeLLM(replies=[ToolTurn(calls=[("list_artifacts", {})])])
        engine = make_engine(
            llm,
            FakeEmitter(),
            tools=["list_artifacts"],
            max_iterations=50,
            max_steps=50,
            cancel_check=check,
        )

        with pytest.raises(RunCancelled):
            await engine.call_agent(agent("a", "Ann", "dev"), RunState(user_input="x"), "go")
        assert len(llm.calls) == 1

    async def test_too_many_calls_in_one_turn_are_capped(self):
        records: list[ToolCallRecord] = []
        llm = FakeLLM(
            replies=[
                ToolTurn(calls=[("list_artifacts", {}) for _ in range(10)]),
                "done",
            ]
        )
        engine = make_engine(
            llm,
            FakeEmitter(),
            tools=["list_artifacts"],
            max_calls_per_turn=3,
            tool_records=records,
        )

        await engine.call_agent(agent("a", "Ann", "dev"), RunState(user_input="x"), "go")
        assert len(records) == 3


class TestFailureHandling:
    async def test_an_unknown_tool_name_is_answered_not_fatal(self):
        llm = FakeLLM(
            replies=[ToolTurn(calls=[("teleport", {"to": "mars"})]), "I will proceed."]
        )
        engine = make_engine(llm, FakeEmitter(), tools=["list_artifacts"])

        output = await engine.call_agent(
            agent("a", "Ann", "dev"), RunState(user_input="x"), "go"
        )

        assert output == "I will proceed."
        results = [m for m in llm.calls[1]["messages"] if m.tool_results][0].tool_results
        assert results[0].is_error
        assert "list_artifacts" in results[0].content, "tell it what it may use"

    async def test_a_failing_tool_returns_an_error_the_model_can_read(self):
        llm = FakeLLM(
            replies=[
                ToolTurn(calls=[("read_artifact", {"path": "missing.py"})]),
                "It does not exist.",
            ]
        )
        engine = make_engine(llm, FakeEmitter(), tools=["read_artifact"])

        output = await engine.call_agent(
            agent("a", "Ann", "dev"), RunState(user_input="x"), "go"
        )

        assert output == "It does not exist."
        results = [m for m in llm.calls[1]["messages"] if m.tool_results][0].tool_results
        assert results[0].is_error

    async def test_a_traversal_attempt_is_refused(self):
        records: list[ToolCallRecord] = []
        llm = FakeLLM(
            replies=[
                ToolTurn(
                    calls=[
                        (
                            "write_artifact",
                            {"path": "../../.ssh/authorized_keys", "content": "k"},
                        )
                    ]
                ),
                "blocked",
            ]
        )
        engine = make_engine(
            llm, FakeEmitter(), tools=["write_artifact"], tool_records=records
        )
        state = RunState(user_input="x")

        await engine.call_agent(agent("a", "Ann", "dev"), state, "go")

        assert records[0].is_error
        assert state.artifacts == {}


class TestToolExposure:
    async def test_an_agent_only_gets_the_tools_it_was_given(self):
        llm = FakeLLM(replies=["answer"])
        engine = make_engine(llm, FakeEmitter(), tools=["read_artifact"])

        await engine.call_agent(agent("a", "Ann", "dev"), RunState(user_input="x"), "go")
        assert llm.calls[0]["tools"] == ["read_artifact"]

    async def test_json_turns_are_offered_no_tools(self):
        # A supervisor asking for a routing decision must get an object back,
        # not a tool call.
        llm = FakeLLM(replies=['{"action": "finish"}'])
        engine = make_engine(llm, FakeEmitter(), tools=["web_fetch", "read_artifact"])

        await engine.call_agent_json(
            agent("a", "Boss", "supervisor"), RunState(user_input="x"), "decide"
        )
        assert llm.calls[0]["tools"] == []

    async def test_write_tools_replace_the_inline_artifact_instructions(self):
        llm = FakeLLM(replies=["done"])
        engine = make_engine(llm, FakeEmitter(), tools=["write_artifact"])
        await engine.call_agent(agent("a", "Ann", "dev"), RunState(user_input="x"), "go")
        system = llm.calls[0]["system"]
        assert "artifact tools" in system
        assert "```python path=" not in system, "two ways to write a file would double it"

    async def test_agents_without_write_tools_keep_the_inline_instructions(self):
        llm = FakeLLM(replies=["done"])
        engine = make_engine(llm, FakeEmitter(), tools=["read_artifact"])
        await engine.call_agent(agent("a", "Ann", "dev"), RunState(user_input="x"), "go")
        assert "```python path=" in llm.calls[0]["system"]

    async def test_an_agent_with_no_tools_behaves_as_before(self):
        llm = FakeLLM(replies=["plain answer"])
        engine = make_engine(llm, FakeEmitter(), tools=[])
        output = await engine.call_agent(
            agent("a", "Ann", "dev"), RunState(user_input="x"), "go"
        )
        assert output == "plain answer"
        assert llm.calls[0]["tools"] == []


class TestSecretHygiene:
    async def test_secretish_arguments_are_masked_in_the_audit_trail(self):
        records: list[ToolCallRecord] = []
        emitter = FakeEmitter()
        llm = FakeLLM(
            replies=[
                ToolTurn(
                    calls=[
                        (
                            "write_artifact",
                            {
                                "path": "config.py",
                                "content": 'KEY = "sk-ant-api03-verysecretvalue123456"',
                            },
                        )
                    ]
                ),
                "done",
            ]
        )
        engine = make_engine(
            llm, emitter, tools=["write_artifact"], tool_records=records
        )

        await engine.call_agent(agent("a", "Ann", "dev"), RunState(user_input="x"), "go")

        # A model can be talked into putting a credential in an argument; the
        # audit row and the event stream must not become the leak.
        assert "sk-ant-api03-verysecret" not in records[0].arguments["content"]
        assert "[redacted]" in records[0].arguments["content"]
        call_event = emitter.of_type(ev.TOOL_CALL)[0]
        assert "sk-ant-api03-verysecret" not in str(call_event["arguments"])
