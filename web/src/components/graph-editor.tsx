"use client";

import { useCallback, useMemo } from "react";
import {
  Background,
  Controls,
  MarkerType,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";

import type { Agent, Preset } from "@/lib/types";
import { useT } from "@/lib/i18n";
import { Button, Card, Field, Input, Select } from "@/components/ui";

export type Graph = Record<string, unknown>;

/** A usable starting graph for a freshly created workflow of each topology. */
export function blankGraph(preset: Preset, agents: Agent[]): Graph {
  const ids = agents.map((a) => a.id);
  const [first, second, third] = ids;

  switch (preset) {
    case "pipeline":
      return {
        nodes: ids.slice(0, 3).map((id) => ({ agent_id: id })),
        max_steps: 10,
        max_cost_cents: 100,
      };
    case "supervisor":
      return {
        supervisor_agent_id: first ?? "",
        workers: ids.slice(1, 4),
        max_rounds: 6,
        max_steps: 16,
        max_cost_cents: 200,
      };
    case "debate":
      return {
        debaters: [first ?? "", second ?? ""].filter(Boolean),
        judge_agent_id: third ?? first ?? "",
        rounds: 2,
        max_steps: 8,
        max_cost_cents: 200,
      };
    case "blackboard":
      return {
        planner_agent_id: first ?? "",
        workers: ids.slice(1, 4),
        max_tasks: 6,
        max_steps: 16,
        max_cost_cents: 200,
      };
    case "swarm":
      return {
        entry_agent_id: first ?? "",
        agents: ids.slice(0, 4),
        max_hops: 6,
        max_steps: 12,
        max_cost_cents: 200,
      };
    case "custom":
      return {
        start: "n1",
        nodes: first ? [{ id: "n1", type: "agent", agent_id: first }] : [],
        edges: [],
        max_hops: 12,
        max_steps: 16,
        max_cost_cents: 200,
      };
  }
}

// --- graph -> React Flow -------------------------------------------------

const NODE_STYLE = {
  background: "#1b1e26",
  border: "1px solid #424959",
  borderRadius: 8,
  color: "#eceef2",
  fontSize: 12,
  padding: "8px 12px",
  width: 180,
};

const ACCENT_STYLE = { ...NODE_STYLE, border: "1px solid #5b6cf5" };

function label(agents: Agent[], id: unknown): string {
  const agent = agents.find((a) => a.id === id);
  return agent ? `${agent.name}\n${agent.role}` : "(unassigned)";
}

const defaultEdge = {
  markerEnd: { type: MarkerType.ArrowClosed, color: "#515a70" },
  style: { stroke: "#515a70" },
};

/**
 * Render a topology as a diagram.
 *
 * Every preset compiles to the same node/edge shape, which is what makes one
 * viewer work for all six — and why the custom editor can produce a graph the
 * engine already knows how to run.
 */
export function graphToFlow(
  preset: Preset,
  graph: Graph,
  agents: Agent[],
): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = [];
  const edges: Edge[] = [];

  const push = (id: string, text: string, x: number, y: number, accent = false) => {
    nodes.push({
      id,
      position: { x, y },
      data: { label: text },
      style: accent ? ACCENT_STYLE : NODE_STYLE,
      draggable: preset === "custom",
    });
  };

  switch (preset) {
    case "pipeline": {
      const list = (graph.nodes as Array<{ agent_id: string }>) ?? [];
      list.forEach((node, index) => {
        push(`p${index}`, label(agents, node.agent_id), index * 220, 0);
        if (index > 0) {
          edges.push({ id: `e${index}`, source: `p${index - 1}`, target: `p${index}`, ...defaultEdge });
        }
      });
      break;
    }
    case "supervisor":
    case "blackboard": {
      const leadKey = preset === "supervisor" ? "supervisor_agent_id" : "planner_agent_id";
      push("lead", label(agents, graph[leadKey]), 220, 0, true);
      const workers = (graph.workers as string[]) ?? [];
      workers.forEach((worker, index) => {
        push(`w${index}`, label(agents, worker), index * 220, 160);
        edges.push({
          id: `ew${index}`,
          source: "lead",
          target: `w${index}`,
          animated: true,
          ...defaultEdge,
        });
      });
      break;
    }
    case "debate": {
      const debaters = (graph.debaters as string[]) ?? [];
      debaters.forEach((debater, index) => {
        push(`d${index}`, label(agents, debater), index * 260, 0);
        edges.push({ id: `ed${index}`, source: `d${index}`, target: "judge", ...defaultEdge });
      });
      push("judge", label(agents, graph.judge_agent_id), 130, 180, true);
      break;
    }
    case "swarm": {
      const roster = (graph.agents as string[]) ?? [];
      const entry = graph.entry_agent_id as string;
      roster.forEach((member, index) => {
        const angle = (index / Math.max(roster.length, 1)) * Math.PI * 2;
        push(
          `s${index}`,
          label(agents, member),
          220 + Math.cos(angle) * 200,
          160 + Math.sin(angle) * 130,
          member === entry,
        );
      });
      // Hand-offs are decided at run time, so show the roster as fully connected.
      roster.forEach((_, i) =>
        roster.forEach((__, j) => {
          if (i < j) {
            edges.push({
              id: `es${i}-${j}`,
              source: `s${i}`,
              target: `s${j}`,
              style: { stroke: "#393e4b", strokeDasharray: "4 4" },
            });
          }
        }),
      );
      break;
    }
    case "custom": {
      const list =
        (graph.nodes as Array<{ id: string; type?: string; agent_id?: string }>) ?? [];
      list.forEach((node, index) => {
        push(
          node.id,
          `${node.type === "router" ? "◆ " : ""}${label(agents, node.agent_id)}`,
          (index % 4) * 220,
          Math.floor(index / 4) * 160,
          node.id === graph.start,
        );
      });
      ((graph.edges as Array<{ from: string; to: string }>) ?? []).forEach((edge, index) => {
        edges.push({
          id: `ec${index}`,
          source: edge.from,
          target: edge.to,
          ...defaultEdge,
        });
      });
      break;
    }
  }

  return { nodes, edges };
}

export function GraphPreview({
  preset,
  graph,
  agents,
}: {
  preset: Preset;
  graph: Graph;
  agents: Agent[];
}) {
  const { nodes, edges } = useMemo(
    () => graphToFlow(preset, graph, agents),
    [preset, graph, agents],
  );

  return (
    <div className="h-[420px] rounded-lg border border-ink-800 bg-ink-950">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        proOptions={{ hideAttribution: true }}
        nodesConnectable={false}
        nodesDraggable={preset === "custom"}
      >
        <Background color="#2a2f3a" gap={20} />
        <Controls showInteractive={false} className="!bg-ink-900 !border-ink-800" />
      </ReactFlow>
    </div>
  );
}

// --- topology forms ------------------------------------------------------

function AgentPicker({
  value,
  agents,
  onChange,
}: {
  value: unknown;
  agents: Agent[];
  onChange: (id: string) => void;
}) {
  const t = useT();

  return (
    <Select value={typeof value === "string" ? value : ""} onChange={(e) => onChange(e.target.value)}>
      <option value="">{t("workflows.unassigned")}</option>
      {agents.map((agent) => (
        <option key={agent.id} value={agent.id}>
          {agent.name} — {agent.role}
        </option>
      ))}
    </Select>
  );
}

function AgentList({
  ids,
  agents,
  onChange,
  label: listLabel,
}: {
  ids: string[];
  agents: Agent[];
  onChange: (next: string[]) => void;
  label: string;
}) {
  const t = useT();

  return (
    <Field label={listLabel}>
      <div className="space-y-2">
        {ids.map((id, index) => (
          <div key={`${id}-${index}`} className="flex gap-2">
            <AgentPicker
              value={id}
              agents={agents}
              onChange={(next) => onChange(ids.map((v, i) => (i === index ? next : v)))}
            />
            <Button
              variant="ghost"
              onClick={() => onChange(ids.filter((_, i) => i !== index))}
              aria-label={t("common.remove")}
            >
              ✕
            </Button>
          </div>
        ))}
        <Button
          variant="secondary"
          onClick={() => onChange([...ids, agents[0]?.id ?? ""])}
          disabled={agents.length === 0}
        >
          {t("common.add")}
        </Button>
      </div>
    </Field>
  );
}

function NumberField({
  label: fieldLabel,
  value,
  onChange,
  min,
  max,
  hint,
}: {
  label: string;
  value: unknown;
  onChange: (value: number) => void;
  min: number;
  max: number;
  hint?: string;
}) {
  return (
    <Field label={fieldLabel} hint={hint}>
      <Input
        type="number"
        min={min}
        max={max}
        value={typeof value === "number" ? value : min}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </Field>
  );
}

export function GraphForm({
  preset,
  graph,
  agents,
  onChange,
}: {
  preset: Preset;
  graph: Graph;
  agents: Agent[];
  onChange: (graph: Graph) => void;
}) {
  const t = useT();
  const patch = useCallback(
    (changes: Graph) => onChange({ ...graph, ...changes }),
    [graph, onChange],
  );

  const pipelineNodes = (graph.nodes as Array<{ agent_id: string }>) ?? [];

  return (
    <div className="space-y-4">
      {preset === "pipeline" && (
        <Field label={t("workflows.order")}>
          <div className="space-y-2">
            {pipelineNodes.map((node, index) => (
              <div key={index} className="flex gap-2">
                <AgentPicker
                  value={node.agent_id}
                  agents={agents}
                  onChange={(id) =>
                    patch({
                      nodes: pipelineNodes.map((n, i) =>
                        i === index ? { ...n, agent_id: id } : n,
                      ),
                    })
                  }
                />
                <Button
                  variant="ghost"
                  disabled={index === 0}
                  onClick={() => {
                    const next = [...pipelineNodes];
                    const moved = next[index]!;
                    next[index] = next[index - 1]!;
                    next[index - 1] = moved;
                    patch({ nodes: next });
                  }}
                  aria-label={t("workflows.moveUp")}
                >
                  ↑
                </Button>
                <Button
                  variant="ghost"
                  onClick={() =>
                    patch({ nodes: pipelineNodes.filter((_, i) => i !== index) })
                  }
                  aria-label={t("common.remove")}
                >
                  ✕
                </Button>
              </div>
            ))}
            <Button
              variant="secondary"
              onClick={() =>
                patch({ nodes: [...pipelineNodes, { agent_id: agents[0]?.id ?? "" }] })
              }
            >
              {t("workflows.addStep")}
            </Button>
          </div>
        </Field>
      )}

      {preset === "supervisor" && (
        <>
          <Field label={t("workflows.supervisor")}>
            <AgentPicker
              value={graph.supervisor_agent_id}
              agents={agents}
              onChange={(id) => patch({ supervisor_agent_id: id })}
            />
          </Field>
          <AgentList
            label={t("workflows.workers")}
            ids={(graph.workers as string[]) ?? []}
            agents={agents}
            onChange={(workers) => patch({ workers })}
          />
          <NumberField
            label={t("workflows.maxRounds")}
            value={graph.max_rounds}
            min={1}
            max={30}
            hint={t("workflows.maxRoundsHint")}
            onChange={(max_rounds) => patch({ max_rounds })}
          />
        </>
      )}

      {preset === "debate" && (
        <>
          <AgentList
            label={t("workflows.debaters")}
            ids={(graph.debaters as string[]) ?? []}
            agents={agents}
            onChange={(debaters) => patch({ debaters })}
          />
          <Field label={t("workflows.judge")}>
            <AgentPicker
              value={graph.judge_agent_id}
              agents={agents}
              onChange={(id) => patch({ judge_agent_id: id })}
            />
          </Field>
          <NumberField
            label={t("workflows.rounds")}
            value={graph.rounds}
            min={1}
            max={6}
            hint={t("workflows.roundsHint")}
            onChange={(rounds) => patch({ rounds })}
          />
        </>
      )}

      {preset === "blackboard" && (
        <>
          <Field label={t("workflows.planner")}>
            <AgentPicker
              value={graph.planner_agent_id}
              agents={agents}
              onChange={(id) => patch({ planner_agent_id: id })}
            />
          </Field>
          <AgentList
            label={t("workflows.workers")}
            ids={(graph.workers as string[]) ?? []}
            agents={agents}
            onChange={(workers) => patch({ workers })}
          />
          <NumberField
            label={t("workflows.maxTasks")}
            value={graph.max_tasks}
            min={1}
            max={25}
            onChange={(max_tasks) => patch({ max_tasks })}
          />
        </>
      )}

      {preset === "swarm" && (
        <>
          <Field label={t("workflows.entry")}>
            <AgentPicker
              value={graph.entry_agent_id}
              agents={agents}
              onChange={(id) => patch({ entry_agent_id: id })}
            />
          </Field>
          <AgentList
            label={t("workflows.roster")}
            ids={(graph.agents as string[]) ?? []}
            agents={agents}
            onChange={(roster) => patch({ agents: roster })}
          />
          <NumberField
            label={t("workflows.maxHops")}
            value={graph.max_hops}
            min={1}
            max={30}
            onChange={(max_hops) => patch({ max_hops })}
          />
        </>
      )}

      {preset === "custom" && <CustomGraphForm graph={graph} agents={agents} patch={patch} />}

      <Card className="space-y-4">
        <p className="text-xs font-medium uppercase tracking-wide text-ink-400">
          {t("workflows.limits")}
        </p>
        <p className="text-xs text-ink-500">{t("workflows.limitsHint")}</p>
        <div className="grid gap-4 sm:grid-cols-2">
          <NumberField
            label={t("workflows.maxSteps")}
            value={graph.max_steps}
            min={1}
            max={200}
            onChange={(max_steps) => patch({ max_steps })}
          />
          <NumberField
            label={t("workflows.maxCost")}
            value={graph.max_cost_cents}
            min={1}
            max={100000}
            onChange={(max_cost_cents) => patch({ max_cost_cents })}
          />
        </div>
      </Card>
    </div>
  );
}

function CustomGraphForm({
  graph,
  agents,
  patch,
}: {
  graph: Graph;
  agents: Agent[];
  patch: (changes: Graph) => void;
}) {
  const t = useT();
  const nodes =
    (graph.nodes as Array<{ id: string; type?: string; agent_id?: string }>) ?? [];
  const edges = (graph.edges as Array<{ from: string; to: string }>) ?? [];

  function addNode(type: "agent" | "router") {
    const id = `n${nodes.length + 1}`;
    patch({
      nodes: [...nodes, { id, type, agent_id: agents[0]?.id ?? "" }],
      start: graph.start || id,
    });
  }

  return (
    <>
      <Field label={t("workflows.nodes")} hint={t("workflows.nodesHint")}>
        <div className="space-y-2">
          {nodes.map((node, index) => (
            <div key={node.id} className="rounded-md border border-ink-800 p-2">
              <div className="mb-2 flex items-center justify-between">
                <span className="font-mono text-xs text-ink-400">
                  {node.id} · {node.type ?? "agent"}
                </span>
                <Button
                  variant="ghost"
                  onClick={() =>
                    patch({
                      nodes: nodes.filter((_, i) => i !== index),
                      edges: edges.filter((e) => e.from !== node.id && e.to !== node.id),
                    })
                  }
                  aria-label={t("workflows.removeNode")}
                >
                  ✕
                </Button>
              </div>
              <AgentPicker
                value={node.agent_id}
                agents={agents}
                onChange={(id) =>
                  patch({
                    nodes: nodes.map((n, i) => (i === index ? { ...n, agent_id: id } : n)),
                  })
                }
              />
            </div>
          ))}
          <div className="flex gap-2">
            <Button variant="secondary" onClick={() => addNode("agent")}>
              {t("workflows.addAgent")}
            </Button>
            <Button variant="secondary" onClick={() => addNode("router")}>
              {t("workflows.addRouter")}
            </Button>
          </div>
        </div>
      </Field>

      <Field label={t("workflows.startNode")}>
        <Select
          value={(graph.start as string) ?? ""}
          onChange={(e) => patch({ start: e.target.value })}
        >
          {nodes.map((node) => (
            <option key={node.id} value={node.id}>
              {node.id}
            </option>
          ))}
        </Select>
      </Field>

      <Field label={t("workflows.edges")}>
        <div className="space-y-2">
          {edges.map((edge, index) => (
            <div key={index} className="flex items-center gap-2">
              <Select
                value={edge.from}
                onChange={(e) =>
                  patch({
                    edges: edges.map((v, i) =>
                      i === index ? { ...v, from: e.target.value } : v,
                    ),
                  })
                }
              >
                {nodes.map((node) => (
                  <option key={node.id} value={node.id}>
                    {node.id}
                  </option>
                ))}
              </Select>
              <span className="text-ink-500">→</span>
              <Select
                value={edge.to}
                onChange={(e) =>
                  patch({
                    edges: edges.map((v, i) =>
                      i === index ? { ...v, to: e.target.value } : v,
                    ),
                  })
                }
              >
                {nodes.map((node) => (
                  <option key={node.id} value={node.id}>
                    {node.id}
                  </option>
                ))}
                <option value="end">end</option>
              </Select>
              <Button
                variant="ghost"
                onClick={() => patch({ edges: edges.filter((_, i) => i !== index) })}
                aria-label={t("workflows.removeEdge")}
              >
                ✕
              </Button>
            </div>
          ))}
          <Button
            variant="secondary"
            disabled={nodes.length < 1}
            onClick={() =>
              patch({
                edges: [
                  ...edges,
                  { from: nodes[0]!.id, to: nodes[1]?.id ?? "end" },
                ],
              })
            }
          >
            {t("workflows.addEdge")}
          </Button>
        </div>
      </Field>
    </>
  );
}
