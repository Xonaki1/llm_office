/**
 * Who works in the office for a given team.
 *
 * Mirrors the `validate_*` functions in `core/orchestration/presets.py`: every
 * agent the topology can reach, in the order it will most likely be seen. That
 * lets the office seat the whole team before the first step arrives, instead of
 * popping characters into existence one event at a time.
 */

import type { Agent, Preset } from "@/lib/types";

type Graph = Record<string, unknown>;

function asStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

export function agentIdsFromGraph(preset: Preset, graph: Graph): string[] {
  switch (preset) {
    case "pipeline": {
      const nodes = Array.isArray(graph.nodes) ? graph.nodes : [];
      return nodes
        .map((node) =>
          node && typeof node === "object" ? (node as { agent_id?: unknown }).agent_id : undefined,
        )
        .filter((id): id is string => typeof id === "string");
    }
    case "supervisor":
      return [
        typeof graph.supervisor_agent_id === "string" ? graph.supervisor_agent_id : "",
        ...asStrings(graph.workers),
      ].filter(Boolean);
    case "debate":
      return [
        ...asStrings(graph.debaters),
        typeof graph.judge_agent_id === "string" ? graph.judge_agent_id : "",
      ].filter(Boolean);
    case "blackboard":
      return [
        typeof graph.planner_agent_id === "string" ? graph.planner_agent_id : "",
        ...asStrings(graph.workers),
      ].filter(Boolean);
    case "swarm":
      return [
        typeof graph.entry_agent_id === "string" ? graph.entry_agent_id : "",
        ...asStrings(graph.agents),
      ].filter(Boolean);
    case "custom": {
      const nodes = Array.isArray(graph.nodes) ? graph.nodes : [];
      return nodes
        .map((node) =>
          node && typeof node === "object" ? (node as { agent_id?: unknown }).agent_id : undefined,
        )
        .filter((id): id is string => typeof id === "string");
    }
    default:
      return [];
  }
}

export interface RosterMember {
  id: string;
  name: string;
  role: string;
}

/** Resolve graph ids against the agent list, keeping order and dropping repeats. */
export function rosterFor(preset: Preset, graph: Graph, agents: Agent[]): RosterMember[] {
  const byId = new Map(agents.map((agent) => [agent.id, agent]));
  const seen = new Set<string>();
  const roster: RosterMember[] = [];

  for (const id of agentIdsFromGraph(preset, graph)) {
    if (seen.has(id)) continue;
    seen.add(id);
    const agent = byId.get(id);
    if (agent) roster.push({ id: agent.id, name: agent.name, role: agent.role });
  }

  return roster;
}
