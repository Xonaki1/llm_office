from __future__ import annotations

import json

import pytest

from core.orchestration.presets import (
    WorkflowConfigError,
    run_blackboard,
    run_custom,
    run_debate,
    run_pipeline,
    run_supervisor,
    run_swarm,
    validate_graph,
)
from core.orchestration.state import RunState
from tests.conftest import FakeEmitter, FakeLLM
from tests.test_engine import agent, make_engine


class TestPipeline:
    async def test_runs_every_node_in_order(self):
        emitter = FakeEmitter()
        llm = FakeLLM(replies=["spec", "code", "review"])
        engine = make_engine(llm, emitter)
        agents = {
            "a": agent("a", "Nadia", "pm"),
            "b": agent("b", "Ravi", "dev"),
            "c": agent("c", "Mei", "reviewer"),
        }
        state = RunState(user_input="build a todo app")

        await run_pipeline(
            engine,
            {"nodes": [{"agent_id": "a"}, {"agent_id": "b"}, {"agent_id": "c"}]},
            agents,
            state,
        )

        assert [e.agent_name for e in state.board] == ["Nadia", "Ravi", "Mei"]
        assert state.final_output == "review"
        assert "spec" in llm.calls[1]["prompt"]

    async def test_custom_instructions_are_used(self):
        llm = FakeLLM(replies=["ok"])
        engine = make_engine(llm, FakeEmitter())
        await run_pipeline(
            engine,
            {"nodes": [{"agent_id": "a", "instruction": "Do exactly this."}]},
            {"a": agent("a", "Ann", "worker")},
            RunState(user_input="x"),
        )
        assert "Do exactly this." in llm.calls[0]["prompt"]

    async def test_unknown_agent_is_rejected(self):
        engine = make_engine(FakeLLM(replies=["x"]), FakeEmitter())
        with pytest.raises(WorkflowConfigError):
            await run_pipeline(
                engine, {"nodes": [{"agent_id": "ghost"}]}, {}, RunState(user_input="x")
            )


class TestSupervisor:
    async def test_delegates_then_finishes(self):
        llm = FakeLLM(
            replies=[
                json.dumps({"action": "delegate", "worker": "w1", "task": "research it"}),
                "research findings",
                json.dumps({"action": "finish", "answer": "the final answer"}),
            ]
        )
        engine = make_engine(llm, FakeEmitter())
        agents = {
            "s": agent("s", "Boss", "supervisor"),
            "w1": agent("w1", "Worker", "researcher"),
        }
        state = RunState(user_input="research X")

        await run_supervisor(
            engine,
            {"supervisor_agent_id": "s", "workers": ["w1"], "max_rounds": 4},
            agents,
            state,
        )

        assert state.final_output == "the final answer"
        assert len(llm.calls) == 3

    async def test_invalid_worker_id_does_not_fail_the_run(self):
        llm = FakeLLM(
            replies=[
                json.dumps({"action": "delegate", "worker": "nope", "task": "x"}),
                json.dumps({"action": "finish", "answer": "recovered"}),
            ]
        )
        engine = make_engine(llm, FakeEmitter())
        agents = {"s": agent("s", "Boss", "supervisor"), "w1": agent("w1", "W", "dev")}
        state = RunState(user_input="x")

        await run_supervisor(
            engine,
            {"supervisor_agent_id": "s", "workers": ["w1"], "max_rounds": 4},
            agents,
            state,
        )
        assert state.final_output == "recovered"
        assert any("routing error" in e.content for e in state.board)

    async def test_wraps_up_when_the_round_budget_runs_out(self):
        llm = FakeLLM(
            replies=[json.dumps({"action": "delegate", "worker": "w1", "task": "more"})] * 20
        )
        engine = make_engine(llm, FakeEmitter(), max_steps=30, max_cents=100)
        agents = {"s": agent("s", "Boss", "supervisor"), "w1": agent("w1", "W", "dev")}
        state = RunState(user_input="never-ending")

        await run_supervisor(
            engine,
            {"supervisor_agent_id": "s", "workers": ["w1"], "max_rounds": 2},
            agents,
            state,
        )
        assert state.final_output is not None
        assert state.notes["exhausted"] == "supervisor round budget"


class TestDebate:
    async def test_ends_with_the_judge(self):
        llm = FakeLLM(replies=["pro-1", "con-1", "pro-2", "con-2", "verdict"])
        engine = make_engine(llm, FakeEmitter())
        agents = {
            "d1": agent("d1", "Alpha", "advocate"),
            "d2": agent("d2", "Beta", "skeptic"),
            "j": agent("j", "Judge", "judge"),
        }
        state = RunState(user_input="Postgres or MySQL?")

        await run_debate(
            engine,
            {"debaters": ["d1", "d2"], "judge_agent_id": "j", "rounds": 2},
            agents,
            state,
        )

        assert len(state.board) == 5
        assert state.board[-1].agent_name == "Judge"
        assert state.final_output == "verdict"

    async def test_second_round_asks_for_rebuttal(self):
        llm = FakeLLM(replies=["x"])
        engine = make_engine(llm, FakeEmitter(), max_steps=20)
        agents = {
            "d1": agent("d1", "A", "advocate"),
            "d2": agent("d2", "B", "skeptic"),
            "j": agent("j", "J", "judge"),
        }
        await run_debate(
            engine,
            {"debaters": ["d1", "d2"], "judge_agent_id": "j", "rounds": 2},
            agents,
            RunState(user_input="x"),
        )
        assert "Attack the weakest point" in llm.calls[2]["prompt"]


class TestBlackboard:
    async def test_runs_tasks_in_dependency_order(self):
        plan = json.dumps(
            {
                "tasks": [
                    {"id": "t2", "title": "build", "worker": "dev", "depends_on": ["t1"]},
                    {"id": "t1", "title": "research", "worker": "res", "depends_on": []},
                ]
            }
        )
        llm = FakeLLM(replies=[plan, "research done", "build done", "final"])
        engine = make_engine(llm, FakeEmitter(), max_steps=20)
        agents = {
            "p": agent("p", "Planner", "planner"),
            "res": agent("res", "Rita", "researcher"),
            "dev": agent("dev", "Dan", "developer"),
        }
        state = RunState(user_input="ship a feature")

        await run_blackboard(
            engine,
            {"planner_agent_id": "p", "workers": ["res", "dev"], "max_tasks": 5},
            agents,
            state,
        )

        names = [e.agent_name for e in state.board]
        assert names.index("Rita") < names.index("Dan"), "dependency order was not honoured"
        assert state.final_output == "final"

    async def test_dependency_cycle_does_not_deadlock(self):
        plan = json.dumps(
            {
                "tasks": [
                    {"id": "t1", "title": "a", "worker": "dev", "depends_on": ["t2"]},
                    {"id": "t2", "title": "b", "worker": "dev", "depends_on": ["t1"]},
                ]
            }
        )
        llm = FakeLLM(replies=[plan, "a done", "b done", "final"])
        engine = make_engine(llm, FakeEmitter(), max_steps=20)
        agents = {"p": agent("p", "P", "planner"), "dev": agent("dev", "D", "dev")}
        state = RunState(user_input="x")

        await run_blackboard(
            engine, {"planner_agent_id": "p", "workers": ["dev"]}, agents, state
        )
        assert state.final_output == "final"

    async def test_unknown_worker_falls_back_rather_than_failing(self):
        plan = json.dumps(
            {"tasks": [{"id": "t1", "title": "a", "worker": "ghost", "depends_on": []}]}
        )
        llm = FakeLLM(replies=[plan, "done", "final"])
        engine = make_engine(llm, FakeEmitter(), max_steps=20)
        agents = {"p": agent("p", "P", "planner"), "dev": agent("dev", "D", "dev")}
        state = RunState(user_input="x")

        await run_blackboard(
            engine, {"planner_agent_id": "p", "workers": ["dev"]}, agents, state
        )
        assert state.final_output == "final"


class TestSwarm:
    async def test_hands_off_then_stops(self):
        llm = FakeLLM(
            replies=[
                json.dumps({"work": "triaged", "handoff_to": "b", "reason": "needs code"}),
                json.dumps({"work": "implemented", "handoff_to": None, "reason": "done"}),
            ]
        )
        engine = make_engine(llm, FakeEmitter())
        agents = {"a": agent("a", "Ann", "triage"), "b": agent("b", "Bob", "dev")}
        state = RunState(user_input="fix the bug")

        await run_swarm(
            engine, {"entry_agent_id": "a", "agents": ["a", "b"], "max_hops": 5}, agents, state
        )

        assert state.final_output == "implemented"
        assert state.notes["path"] == ["a", "b"]

    async def test_self_handoff_is_refused(self):
        llm = FakeLLM(replies=[json.dumps({"work": "looping", "handoff_to": "a"})])
        engine = make_engine(llm, FakeEmitter())
        agents = {"a": agent("a", "Ann", "triage")}
        state = RunState(user_input="x")

        await run_swarm(
            engine, {"entry_agent_id": "a", "agents": ["a"], "max_hops": 5}, agents, state
        )
        assert state.final_output == "looping"
        assert state.notes["invalid_handoff"] == "a"

    async def test_hop_budget_forces_a_wrap_up(self):
        # Alternate the hand-offs so the loop never trips the self-hand-off
        # guard and actually reaches the hop ceiling.
        llm = FakeLLM(
            replies=[
                json.dumps({"work": "more", "handoff_to": "b"}),
                json.dumps({"work": "more", "handoff_to": "a"}),
                json.dumps({"work": "more", "handoff_to": "b"}),
                "wrap-up answer",
            ]
        )
        engine = make_engine(llm, FakeEmitter(), max_steps=20, max_cents=100)
        agents = {"a": agent("a", "Ann", "triage"), "b": agent("b", "Bob", "dev")}
        state = RunState(user_input="x")

        await run_swarm(
            engine, {"entry_agent_id": "a", "agents": ["a", "b"], "max_hops": 3}, agents, state
        )
        assert state.notes["exhausted"] == "swarm hop budget"
        assert state.final_output is not None


class TestCustomGraph:
    async def test_follows_edges(self):
        llm = FakeLLM(replies=["one", "two"])
        engine = make_engine(llm, FakeEmitter())
        agents = {"a": agent("a", "Ann", "w"), "b": agent("b", "Bob", "w")}
        state = RunState(user_input="x")

        await run_custom(
            engine,
            {
                "start": "n1",
                "nodes": [
                    {"id": "n1", "type": "agent", "agent_id": "a"},
                    {"id": "n2", "type": "agent", "agent_id": "b"},
                ],
                "edges": [{"from": "n1", "to": "n2"}],
            },
            agents,
            state,
        )
        assert state.notes["path"] == ["n1", "n2"]
        assert state.final_output == "two"

    async def test_router_node_picks_a_branch(self):
        llm = FakeLLM(
            replies=[json.dumps({"next": "n3", "reason": "needs review"}), "reviewed"]
        )
        engine = make_engine(llm, FakeEmitter())
        agents = {
            "r": agent("r", "Router", "router"),
            "b": agent("b", "Bob", "dev"),
            "c": agent("c", "Cat", "reviewer"),
        }
        state = RunState(user_input="x")

        await run_custom(
            engine,
            {
                "start": "n1",
                "nodes": [
                    {"id": "n1", "type": "router", "agent_id": "r"},
                    {"id": "n2", "type": "agent", "agent_id": "b"},
                    {"id": "n3", "type": "agent", "agent_id": "c"},
                ],
                "edges": [{"from": "n1", "to": "n2"}, {"from": "n1", "to": "n3"}],
            },
            agents,
            state,
        )
        assert state.notes["path"] == ["n1", "n3"]

    async def test_invalid_router_choice_falls_back_to_the_first_edge(self):
        llm = FakeLLM(replies=[json.dumps({"next": "nowhere"}), "done"])
        engine = make_engine(llm, FakeEmitter())
        agents = {"r": agent("r", "Router", "router"), "b": agent("b", "Bob", "dev")}
        state = RunState(user_input="x")

        await run_custom(
            engine,
            {
                "start": "n1",
                "nodes": [
                    {"id": "n1", "type": "router", "agent_id": "r"},
                    {"id": "n2", "type": "agent", "agent_id": "b"},
                ],
                "edges": [{"from": "n1", "to": "n2"}],
            },
            agents,
            state,
        )
        assert state.notes["path"] == ["n1", "n2"]


class TestValidation:
    """Validation runs when a workflow is saved, so a broken graph never reaches
    a paid run."""

    def test_pipeline_requires_nodes(self):
        with pytest.raises(WorkflowConfigError):
            validate_graph("pipeline", {})

    def test_debate_requires_two_debaters(self):
        with pytest.raises(WorkflowConfigError, match="two debaters"):
            validate_graph("debate", {"debaters": ["a"], "judge_agent_id": "j"})

    def test_custom_rejects_a_dangling_start(self):
        with pytest.raises(WorkflowConfigError, match="start node"):
            validate_graph(
                "custom",
                {"start": "missing", "nodes": [{"id": "n1", "agent_id": "a"}]},
            )

    def test_custom_rejects_a_dangling_edge(self):
        with pytest.raises(WorkflowConfigError, match="unknown node"):
            validate_graph(
                "custom",
                {
                    "start": "n1",
                    "nodes": [{"id": "n1", "agent_id": "a"}],
                    "edges": [{"from": "n1", "to": "ghost"}],
                },
            )

    def test_custom_rejects_duplicate_node_ids(self):
        with pytest.raises(WorkflowConfigError, match="duplicate"):
            validate_graph(
                "custom",
                {
                    "start": "n1",
                    "nodes": [{"id": "n1", "agent_id": "a"}, {"id": "n1", "agent_id": "b"}],
                },
            )

    def test_out_of_range_limits_are_rejected(self):
        with pytest.raises(WorkflowConfigError, match="max_cost_cents"):
            validate_graph(
                "pipeline", {"nodes": [{"agent_id": "a"}], "max_cost_cents": 0}
            )

    def test_unknown_preset_is_rejected(self):
        with pytest.raises(WorkflowConfigError, match="unknown preset"):
            validate_graph("telepathy", {})

    def test_returns_every_referenced_agent(self):
        referenced = validate_graph(
            "supervisor", {"supervisor_agent_id": "s", "workers": ["a", "b"]}
        )
        assert set(referenced) == {"s", "a", "b"}
