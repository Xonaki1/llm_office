/**
 * Turning a finished run back into a performance.
 *
 * A run that ended before you opened it has no live stream, but the database
 * kept everything the office needs: the steps in order, every tool call with
 * the step it belonged to, and the artifacts each step produced. Rebuilding a
 * plausible event sequence from those rows lets the same simulation play a
 * recording, so the office is never a dead room with a table of numbers.
 *
 * The reconstruction is honest about what it is: the order is exactly what the
 * engine did, the pacing is not (real latencies would make a two-minute run a
 * two-minute video).
 */

import type { RunDetail, RunEvent } from "@/lib/types";

/** Rough stage times, in milliseconds, for a played-back run. */
const PACE: Record<string, number> = {
  "run.start": 400,
  "step.start": 800,
  "tool.call": 550,
  "tool.result": 450,
  "artifact.written": 350,
  "step.end": 900,
  "run.end": 600,
};

export function replayEvents(detail: RunDetail): RunEvent[] {
  const events: RunEvent[] = [];
  let seq = 0;
  const ts = detail.created_at;
  const next = () => (seq += 1);

  events.push({
    seq: next(),
    type: "run.start",
    run_id: detail.id,
    ts,
    workflow_name: "",
    preset: "",
    key_mode: detail.key_mode,
    max_steps: detail.max_steps,
    max_cost_cents: detail.max_cost_cents,
  });

  const steps = [...detail.steps].sort((a, b) => a.index - b.index);

  for (const step of steps) {
    events.push({
      seq: next(),
      type: "step.start",
      run_id: detail.id,
      ts: step.created_at,
      step: step.index,
      agent_id: step.agent_id ?? step.agent_name,
      agent_name: step.agent_name,
      role: step.role,
      model: step.model,
      effort: "",
      instruction: step.input,
      tools: [],
    });

    const calls = detail.tool_calls
      .filter((call) => call.step_index === step.index)
      .sort((a, b) => a.call_index - b.call_index);

    for (const call of calls) {
      events.push({
        seq: next(),
        type: "tool.call",
        run_id: detail.id,
        ts: call.created_at,
        step: step.index,
        call: call.call_index,
        tool: call.tool,
        agent_name: call.agent_name,
        arguments: call.arguments,
      });
      events.push({
        seq: next(),
        type: "tool.result",
        run_id: detail.id,
        ts: call.created_at,
        step: step.index,
        call: call.call_index,
        tool: call.tool,
        agent_name: call.agent_name,
        is_error: call.is_error,
        latency_ms: call.latency_ms,
        preview: call.result.slice(0, 200),
        metadata: {},
      });
    }

    for (const artifact of detail.artifacts.filter((a) => a.produced_by_step === step.index)) {
      events.push({
        seq: next(),
        type: "artifact.written",
        run_id: detail.id,
        ts: artifact.created_at,
        step: step.index,
        path: artifact.path,
        version: artifact.version,
        kind: artifact.kind,
        size_bytes: artifact.size_bytes,
        agent_name: artifact.produced_by_agent ?? step.agent_name,
        via: "tool",
      });
    }

    events.push({
      seq: next(),
      type: "step.end",
      run_id: detail.id,
      ts: step.created_at,
      step: step.index,
      agent_id: step.agent_id ?? step.agent_name,
      agent_name: step.agent_name,
      role: step.role,
      model: step.model,
      provider: step.provider,
      tokens_in: step.tokens_in,
      tokens_out: step.tokens_out,
      cached_tokens: step.cached_tokens,
      cost_microcents: step.cost_microcents,
      latency_ms: step.latency_ms,
      attempts: step.attempts,
      spent_microcents: 0,
      steps_used: step.index + 1,
      output: step.output,
    });
  }

  events.push({
    seq: next(),
    type: "run.end",
    run_id: detail.id,
    ts: detail.finished_at ?? ts,
    status: detail.status,
    error: detail.error,
    output: detail.output,
  });

  return events;
}

/**
 * Feed events to a handler on a timer. Returns a cancel function; calling it
 * stops the playback wherever it got to.
 */
export function playEvents(
  events: RunEvent[],
  onEvent: (event: RunEvent) => void,
  options: { speed?: number; onFinish?: () => void } = {},
): () => void {
  const speed = options.speed ?? 1;
  let index = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let cancelled = false;

  const step = () => {
    if (cancelled) return;
    const event = events[index];
    if (!event) {
      options.onFinish?.();
      return;
    }
    index += 1;
    onEvent(event);
    timer = setTimeout(step, (PACE[event.type] ?? 250) / speed);
  };

  step();

  return () => {
    cancelled = true;
    if (timer) clearTimeout(timer);
  };
}
