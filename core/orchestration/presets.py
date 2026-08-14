"""Topology presets.

Every preset runs on the same `Engine`. A preset only decides which agent speaks
next and what it is told to do — budget, cancellation, streaming, artifacts and
persistence are the engine's job. Adding a topology is therefore one async
function plus a validator.

Graph config lives in `Workflow.graph`, shaped per preset:

  pipeline    {"nodes": [{"agent_id", "instruction"?}, ...]}
  supervisor  {"supervisor_agent_id", "workers": [...], "max_rounds"?}
  debate      {"debaters": [...], "judge_agent_id", "rounds"?}
  blackboard  {"planner_agent_id", "workers": [...], "max_tasks"?}
  swarm       {"entry_agent_id", "agents": [...], "max_hops"?}
  custom      {"start", "nodes": [...], "edges": [...], "max_hops"?}

Common optional keys on every preset: "max_steps", "max_cost_cents".
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from core.orchestration.engine import Engine
from core.orchestration.state import AgentSpec, RunState

log = structlog.get_logger(__name__)

AgentMap = dict[str, AgentSpec]
PresetFn = Callable[[Engine, dict[str, Any], AgentMap, RunState], Awaitable[None]]


class WorkflowConfigError(ValueError):
    """Raised at workflow-save time as well as at run time, so a broken topology
    is rejected before anyone spends money on it."""


def _require(graph: dict[str, Any], key: str) -> Any:
    value = graph.get(key)
    if value in (None, "", [], {}):
        raise WorkflowConfigError(f"workflow graph is missing required key {key!r}")
    return value


def _agent(agents: AgentMap, agent_id: str, *, what: str) -> AgentSpec:
    if agent_id not in agents:
        raise WorkflowConfigError(
            f"{what} references agent {agent_id!r}, which does not exist or is inactive"
        )
    return agents[agent_id]


def _bounded(value: Any, *, default: int, low: int, high: int, name: str) -> int:
    try:
        number = int(value) if value is not None else default
    except (TypeError, ValueError) as exc:
        raise WorkflowConfigError(f"{name} must be an integer") from exc
    if not low <= number <= high:
        raise WorkflowConfigError(f"{name} must be between {low} and {high}")
    return number


# --- pipeline ------------------------------------------------------------

_DEFAULT_FIRST = "Start the work. Produce the deliverable for your role."
_DEFAULT_NEXT = (
    "Take the previous agent's output and do your part of the job. If you find "
    "problems, fix them and say what you changed and why."
)


async def run_pipeline(
    engine: Engine, graph: dict[str, Any], agents: AgentMap, state: RunState
) -> None:
    """Linear hand-off: each agent builds on the previous agent's output."""
    nodes = _require(graph, "nodes")
    for position, node in enumerate(nodes):
        agent = _agent(agents, node.get("agent_id"), what=f"pipeline node #{position}")
        instruction = node.get("instruction") or (
            _DEFAULT_FIRST if position == 0 else _DEFAULT_NEXT
        )
        await engine.call_agent(agent, state, instruction)
    state.final_output = state.last_output()


def validate_pipeline(graph: dict[str, Any]) -> list[str]:
    nodes = _require(graph, "nodes")
    if not isinstance(nodes, list):
        raise WorkflowConfigError("pipeline `nodes` must be a list")
    ids: list[str] = []
    for position, node in enumerate(nodes):
        if not isinstance(node, dict) or not node.get("agent_id"):
            raise WorkflowConfigError(f"pipeline node #{position} needs an agent_id")
        ids.append(node["agent_id"])
    return ids


# --- supervisor ----------------------------------------------------------

_SUPERVISOR_PROMPT = """\
Decide the team's next action.

Workers you can delegate to:
{roster}

Reply with one JSON object and nothing else:
  {{"action": "delegate", "worker": "<worker id>", "task": "<precise instruction>"}}
  {{"action": "finish", "answer": "<the complete final answer for the user>"}}

Delegate only when a worker can make real progress; give them everything they
need in `task`, since they cannot ask you follow-up questions. Finish as soon as
the original request is fully satisfied — do not delegate busywork."""


async def run_supervisor(
    engine: Engine, graph: dict[str, Any], agents: AgentMap, state: RunState
) -> None:
    """A manager decomposes the task, delegates, and decides when it is done."""
    supervisor = _agent(
        agents, _require(graph, "supervisor_agent_id"), what="supervisor_agent_id"
    )
    workers = {
        wid: _agent(agents, wid, what="supervisor worker")
        for wid in _require(graph, "workers")
    }
    max_rounds = _bounded(
        graph.get("max_rounds"), default=6, low=1, high=30, name="max_rounds"
    )
    roster = "\n".join(f"- {wid}: {w.name} — {w.role}" for wid, w in workers.items())

    for _ in range(max_rounds):
        decision = await engine.call_agent_json(
            supervisor,
            state,
            _SUPERVISOR_PROMPT.format(roster=roster),
            required_keys=("action",),
        )
        action = str(decision.get("action", "")).lower()

        if action == "finish":
            state.final_output = str(decision.get("answer") or state.last_output())
            return

        worker_id = str(decision.get("worker", ""))
        if worker_id not in workers:
            # Do not fail the run on a bad id — tell the supervisor and let it
            # correct itself. The bad reply stays on the board as context.
            log.info("supervisor.invalid_worker", worker=worker_id)
            state.append(
                supervisor,
                f"(routing error: {worker_id!r} is not a valid worker id; "
                f"valid ids are {sorted(workers)})",
            )
            continue

        task = str(decision.get("task") or "Continue the work.")
        await engine.call_agent(workers[worker_id], state, task)

    state.notes["exhausted"] = "supervisor round budget"
    state.final_output = await engine.call_agent(
        supervisor,
        state,
        "The round budget is spent. Write the final answer for the user from what "
        "is on the board. Do not delegate further. If the task is incomplete, say "
        "plainly what is missing.",
    )


def validate_supervisor(graph: dict[str, Any]) -> list[str]:
    supervisor = _require(graph, "supervisor_agent_id")
    workers = _require(graph, "workers")
    if not isinstance(workers, list):
        raise WorkflowConfigError("supervisor `workers` must be a list")
    _bounded(graph.get("max_rounds"), default=6, low=1, high=30, name="max_rounds")
    return [supervisor, *workers]


# --- debate --------------------------------------------------------------


async def run_debate(
    engine: Engine, graph: dict[str, Any], agents: AgentMap, state: RunState
) -> None:
    """N agents argue for R rounds, then a judge rules.

    Costs roughly (debaters x rounds + 1) steps, so it earns its price only on
    questions that have a defensible right answer.
    """
    debaters = [
        _agent(agents, did, what="debater") for did in _require(graph, "debaters")
    ]
    judge = _agent(agents, _require(graph, "judge_agent_id"), what="judge_agent_id")
    rounds = _bounded(graph.get("rounds"), default=2, low=1, high=6, name="rounds")

    for round_no in range(rounds):
        for debater in debaters:
            instruction = (
                "Give your answer to the original request, with the reasoning that "
                "supports it. Be specific enough to be checked."
                if round_no == 0
                else "Read the other positions on the board. Attack the weakest point "
                "you can actually justify, then restate your own position — revised "
                "if they changed your mind."
            )
            await engine.call_agent(debater, state, instruction)

    state.final_output = await engine.call_agent(
        judge,
        state,
        "You are the judge. Weigh the positions on the board and write the single "
        "best answer for the user. Name briefly which argument decided it and why "
        "the others fell short.",
    )


def validate_debate(graph: dict[str, Any]) -> list[str]:
    debaters = _require(graph, "debaters")
    judge = _require(graph, "judge_agent_id")
    if not isinstance(debaters, list) or len(debaters) < 2:
        raise WorkflowConfigError("a debate needs at least two debaters")
    _bounded(graph.get("rounds"), default=2, low=1, high=6, name="rounds")
    return [*debaters, judge]


# --- blackboard ----------------------------------------------------------

_PLANNER_PROMPT = """\
Break the request into independent tasks for the team.

Available workers:
{roster}

Reply with one JSON object and nothing else:
  {{"tasks": [
      {{"id": "t1", "title": "...", "worker": "<worker id>", "depends_on": []}},
      ...
  ]}}

Rules: at most {max_tasks} tasks; `depends_on` lists ids of tasks that must
finish first; assign each task to the worker whose role fits it. Prefer few
substantial tasks over many trivial ones."""

_SYNTHESIS_PROMPT = """\
Every task is done. Write the final answer for the user, drawing on the whole
board. Do not summarise the process — deliver the result."""


async def run_blackboard(
    engine: Engine, graph: dict[str, Any], agents: AgentMap, state: RunState
) -> None:
    """A planner posts a dependency-ordered task list; workers claim tasks whose
    dependencies are already satisfied. Suits work that fans out."""
    planner = _agent(agents, _require(graph, "planner_agent_id"), what="planner_agent_id")
    workers = {
        wid: _agent(agents, wid, what="blackboard worker")
        for wid in _require(graph, "workers")
    }
    max_tasks = _bounded(
        graph.get("max_tasks"), default=8, low=1, high=25, name="max_tasks"
    )
    roster = "\n".join(f"- {wid}: {w.name} — {w.role}" for wid, w in workers.items())

    plan = await engine.call_agent_json(
        planner,
        state,
        _PLANNER_PROMPT.format(roster=roster, max_tasks=max_tasks),
        required_keys=("tasks",),
    )
    tasks = _normalise_tasks(plan.get("tasks"), workers, max_tasks)
    if not tasks:
        raise WorkflowConfigError("planner produced no usable tasks")

    state.notes["tasks"] = tasks
    done: set[str] = set()
    pending = list(tasks)

    while pending:
        ready = [t for t in pending if set(t["depends_on"]) <= done]
        if not ready:
            # A dependency cycle, or a reference to a task the planner dropped.
            # Run the rest in declared order rather than deadlocking.
            log.warning("blackboard.unsatisfiable_dependencies", remaining=len(pending))
            ready = pending[:1]

        for task in ready:
            worker = workers[task["worker"]]
            await engine.call_agent(
                worker,
                state,
                f"Task {task['id']}: {task['title']}\n\n"
                f"Complete this task and nothing else. Other agents are handling the "
                f"remaining tasks in parallel.",
            )
            done.add(task["id"])
            pending.remove(task)

    state.final_output = await engine.call_agent(planner, state, _SYNTHESIS_PROMPT)


def _normalise_tasks(
    raw: Any, workers: AgentMap, max_tasks: int
) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    tasks: list[dict[str, Any]] = []
    seen: set[str] = set()
    fallback_worker = next(iter(workers))
    for position, item in enumerate(raw[:max_tasks]):
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("id") or f"t{position + 1}")
        if task_id in seen:
            continue
        seen.add(task_id)
        worker = str(item.get("worker") or "")
        if worker not in workers:
            worker = fallback_worker
        depends = [str(d) for d in (item.get("depends_on") or []) if isinstance(d, str | int)]
        tasks.append(
            {
                "id": task_id,
                "title": str(item.get("title") or "Untitled task"),
                "worker": worker,
                "depends_on": depends,
            }
        )
    # Drop dependencies on tasks that were filtered out, so nothing waits forever.
    known = {t["id"] for t in tasks}
    for task in tasks:
        task["depends_on"] = [d for d in task["depends_on"] if d in known and d != task["id"]]
    return tasks


def validate_blackboard(graph: dict[str, Any]) -> list[str]:
    planner = _require(graph, "planner_agent_id")
    workers = _require(graph, "workers")
    if not isinstance(workers, list):
        raise WorkflowConfigError("blackboard `workers` must be a list")
    _bounded(graph.get("max_tasks"), default=8, low=1, high=25, name="max_tasks")
    return [planner, *workers]


# --- swarm ---------------------------------------------------------------

_SWARM_PROMPT = """\
Do your part of the work, then decide what happens next.

Agents you can hand off to:
{roster}

Reply with one JSON object and nothing else:
  {{"work": "<your contribution, in full>",
    "handoff_to": "<agent id>" or null,
    "reason": "<why you are handing off, or why the work is complete>"}}

Set `handoff_to` to null only when the original request is fully answered. Never
hand off to yourself."""


async def run_swarm(
    engine: Engine, graph: dict[str, Any], agents: AgentMap, state: RunState
) -> None:
    """Peer-to-peer hand-off: whoever holds the task decides who gets it next."""
    entry = _agent(agents, _require(graph, "entry_agent_id"), what="entry_agent_id")
    roster_ids = _require(graph, "agents")
    swarm = {aid: _agent(agents, aid, what="swarm agent") for aid in roster_ids}
    if entry.id not in swarm:
        swarm[entry.id] = entry
    max_hops = _bounded(graph.get("max_hops"), default=8, low=1, high=30, name="max_hops")

    current = entry
    visits: list[str] = []

    for _ in range(max_hops):
        roster = "\n".join(
            f"- {aid}: {a.name} — {a.role}" for aid, a in swarm.items() if aid != current.id
        )
        decision = await engine.call_agent_json(
            current, state, _SWARM_PROMPT.format(roster=roster), required_keys=("work",)
        )
        visits.append(current.id)

        next_id = decision.get("handoff_to")
        if not next_id:
            state.final_output = str(decision.get("work") or state.last_output())
            state.notes["path"] = visits
            return
        next_id = str(next_id)
        if next_id == current.id or next_id not in swarm:
            log.info("swarm.invalid_handoff", target=next_id)
            state.final_output = str(decision.get("work") or state.last_output())
            state.notes["path"] = visits
            state.notes["invalid_handoff"] = next_id
            return
        current = swarm[next_id]

    state.notes["exhausted"] = "swarm hop budget"
    state.notes["path"] = visits
    state.final_output = await engine.call_agent(
        current,
        state,
        "The hand-off budget is spent. Write the final answer for the user from "
        "what is on the board. Do not hand off. If the task is incomplete, say "
        "plainly what is missing.",
    )


def validate_swarm(graph: dict[str, Any]) -> list[str]:
    entry = _require(graph, "entry_agent_id")
    roster = _require(graph, "agents")
    if not isinstance(roster, list):
        raise WorkflowConfigError("swarm `agents` must be a list")
    _bounded(graph.get("max_hops"), default=8, low=1, high=30, name="max_hops")
    return [entry, *roster]


# --- custom graph --------------------------------------------------------

_ROUTER_PROMPT = """\
Choose the next step.

Options:
{options}

Reply with one JSON object and nothing else:
  {{"next": "<option id>", "reason": "<one sentence>"}}

Pick "end" only when the original request is fully answered."""


async def run_custom(
    engine: Engine, graph: dict[str, Any], agents: AgentMap, state: RunState
) -> None:
    """Arbitrary directed graph — what the visual editor produces.

    Node types:
      agent   runs an agent, then follows its single outgoing edge
      router  runs an agent that picks which outgoing edge to follow
      end     terminates the run
    """
    nodes = {node["id"]: node for node in _require(graph, "nodes")}
    edges = graph.get("edges") or []
    outgoing: dict[str, list[str]] = {}
    for edge in edges:
        outgoing.setdefault(edge["from"], []).append(edge["to"])

    current_id = str(_require(graph, "start"))
    max_hops = _bounded(graph.get("max_hops"), default=20, low=1, high=60, name="max_hops")
    path: list[str] = []

    for _ in range(max_hops):
        if current_id == "end":
            break
        node = nodes.get(current_id)
        if node is None:
            raise WorkflowConfigError(f"graph points at unknown node {current_id!r}")
        path.append(current_id)
        node_type = node.get("type", "agent")

        if node_type == "end":
            break

        agent = _agent(agents, node.get("agent_id"), what=f"node {current_id!r}")
        targets = outgoing.get(current_id, [])

        if node_type == "router":
            if not targets:
                raise WorkflowConfigError(f"router node {current_id!r} has no outgoing edges")
            options = "\n".join(
                f"- {target}: {_describe(nodes.get(target), agents)}" for target in targets
            )
            decision = await engine.call_agent_json(
                agent,
                state,
                node.get("instruction")
                or _ROUTER_PROMPT.format(options=options + "\n- end: finish the run"),
                required_keys=("next",),
            )
            chosen = str(decision.get("next", ""))
            current_id = chosen if chosen in targets or chosen == "end" else targets[0]
            continue

        await engine.call_agent(
            agent, state, node.get("instruction") or _DEFAULT_NEXT
        )
        if not targets:
            break
        current_id = targets[0]
    else:
        state.notes["exhausted"] = "custom graph hop budget"

    state.notes["path"] = path
    state.final_output = state.last_output()


def _describe(node: dict[str, Any] | None, agents: AgentMap) -> str:
    if node is None:
        return "unknown node"
    agent = agents.get(node.get("agent_id", ""))
    label = node.get("label") or (f"{agent.name} ({agent.role})" if agent else node.get("type"))
    return str(label)


def validate_custom(graph: dict[str, Any]) -> list[str]:
    nodes = _require(graph, "nodes")
    start = str(_require(graph, "start"))
    if not isinstance(nodes, list):
        raise WorkflowConfigError("custom `nodes` must be a list")

    node_ids: set[str] = set()
    agent_ids: list[str] = []
    for position, node in enumerate(nodes):
        if not isinstance(node, dict) or not node.get("id"):
            raise WorkflowConfigError(f"custom node #{position} needs an id")
        if node["id"] in node_ids:
            raise WorkflowConfigError(f"duplicate node id {node['id']!r}")
        node_ids.add(node["id"])
        if node.get("type", "agent") in ("agent", "router"):
            if not node.get("agent_id"):
                raise WorkflowConfigError(f"node {node['id']!r} needs an agent_id")
            agent_ids.append(node["agent_id"])

    if start not in node_ids:
        raise WorkflowConfigError(f"start node {start!r} is not in `nodes`")

    for edge in graph.get("edges") or []:
        if not isinstance(edge, dict) or "from" not in edge or "to" not in edge:
            raise WorkflowConfigError("every edge needs `from` and `to`")
        if edge["from"] not in node_ids:
            raise WorkflowConfigError(f"edge starts at unknown node {edge['from']!r}")
        if edge["to"] not in node_ids and edge["to"] != "end":
            raise WorkflowConfigError(f"edge points at unknown node {edge['to']!r}")

    _bounded(graph.get("max_hops"), default=20, low=1, high=60, name="max_hops")
    return agent_ids


# --- registry ------------------------------------------------------------

PRESETS: dict[str, PresetFn] = {
    "pipeline": run_pipeline,
    "supervisor": run_supervisor,
    "debate": run_debate,
    "blackboard": run_blackboard,
    "swarm": run_swarm,
    "custom": run_custom,
}

VALIDATORS: dict[str, Callable[[dict[str, Any]], list[str]]] = {
    "pipeline": validate_pipeline,
    "supervisor": validate_supervisor,
    "debate": validate_debate,
    "blackboard": validate_blackboard,
    "swarm": validate_swarm,
    "custom": validate_custom,
}

PRESET_NAMES = tuple(PRESETS)


def get_preset(name: str) -> PresetFn:
    if name not in PRESETS:
        raise WorkflowConfigError(
            f"unknown preset {name!r}; available presets: {', '.join(PRESET_NAMES)}"
        )
    return PRESETS[name]


def validate_graph(preset: str, graph: dict[str, Any]) -> list[str]:
    """Check a topology and return every agent id it references.

    Called when a workflow is saved as well as before a run, so a malformed
    graph is rejected at edit time rather than after the first paid step.
    """
    if preset not in VALIDATORS:
        raise WorkflowConfigError(
            f"unknown preset {preset!r}; available presets: {', '.join(PRESET_NAMES)}"
        )
    agent_ids = VALIDATORS[preset](graph)
    _bounded(graph.get("max_steps"), default=20, low=1, high=200, name="max_steps")
    _bounded(
        graph.get("max_cost_cents"), default=100, low=1, high=100_000, name="max_cost_cents"
    )
    return agent_ids
