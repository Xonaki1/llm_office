"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api, openRunStream } from "@/lib/api";
import type { RunDetail, RunEvent, RunStatus } from "@/lib/types";
import { TERMINAL_STATUSES } from "@/lib/types";

export interface LiveStep {
  index: number;
  agentName: string;
  role: string;
  model: string;
  provider?: string;
  /** Text accumulated from `step.token` while the step is in flight. */
  streaming: string;
  output?: string;
  tokensIn?: number;
  tokensOut?: number;
  costMicrocents?: number;
  latencyMs?: number;
  done: boolean;
}

export interface LiveArtifact {
  path: string;
  version: number;
  kind: string;
  sizeBytes: number;
  agentName: string;
}

export interface RunStreamState {
  run: RunDetail | null;
  steps: LiveStep[];
  artifacts: LiveArtifact[];
  status: RunStatus | null;
  spentMicrocents: number;
  budgetWarning: boolean;
  error: string | null;
  connected: boolean;
  reload: () => Promise<void>;
}

/**
 * Live view of one run.
 *
 * Loads the persisted run first, then attaches to the event stream. The stream
 * replays from `Last-Event-ID`, so a dropped connection reconnects without
 * duplicating or losing steps.
 */
export function useRunStream(orgId: string, runId: string): RunStreamState {
  const [run, setRun] = useState<RunDetail | null>(null);
  const [steps, setSteps] = useState<LiveStep[]>([]);
  const [artifacts, setArtifacts] = useState<LiveArtifact[]>([]);
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [spentMicrocents, setSpent] = useState(0);
  const [budgetWarning, setBudgetWarning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);

  const lastSeq = useRef(0);
  const finished = useRef(false);

  const reload = useCallback(async () => {
    const detail = await api.get<RunDetail>(`/orgs/${orgId}/runs/${runId}`);
    setRun(detail);
    setStatus(detail.status);
  }, [orgId, runId]);

  const handleEvent = useCallback((raw: unknown) => {
    const event = raw as RunEvent;
    if (typeof event?.seq === "number") lastSeq.current = Math.max(lastSeq.current, event.seq);

    switch (event.type) {
      case "run.start":
        setStatus("running");
        break;

      case "step.start":
        setSteps((current) => {
          if (current.some((s) => s.index === event.step)) return current;
          return [
            ...current,
            {
              index: event.step,
              agentName: event.agent_name,
              role: event.role,
              model: event.model,
              streaming: "",
              done: false,
            },
          ];
        });
        break;

      case "step.token":
        setSteps((current) =>
          current.map((step) =>
            step.index === event.step
              ? { ...step, streaming: step.streaming + event.text }
              : step,
          ),
        );
        break;

      case "step.end":
        setSpent(event.spent_microcents);
        setSteps((current) =>
          current.map((step) =>
            step.index === event.step
              ? {
                  ...step,
                  provider: event.provider,
                  output: event.output,
                  tokensIn: event.tokens_in,
                  tokensOut: event.tokens_out,
                  costMicrocents: event.cost_microcents,
                  latencyMs: event.latency_ms,
                  done: true,
                }
              : step,
          ),
        );
        break;

      case "artifact.written":
        setArtifacts((current) => [
          ...current.filter((a) => a.path !== event.path),
          {
            path: event.path,
            version: event.version,
            kind: event.kind,
            sizeBytes: event.size_bytes,
            agentName: event.agent_name,
          },
        ]);
        break;

      case "budget.warning":
        setBudgetWarning(true);
        break;

      case "budget.exceeded":
        setError(event.reason);
        break;

      case "run.end":
      case "run.cancelled":
      case "run.error":
        finished.current = true;
        setStatus(event.status);
        if (event.error) setError(event.error);
        break;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    (async () => {
      try {
        const detail = await api.get<RunDetail>(`/orgs/${orgId}/runs/${runId}`);
        if (cancelled) return;
        setRun(detail);
        setStatus(detail.status);
        setArtifacts(
          detail.artifacts.map((a) => ({
            path: a.path,
            version: a.version,
            kind: a.kind,
            sizeBytes: a.size_bytes,
            agentName: a.produced_by_agent ?? "",
          })),
        );

        // A finished run has nothing to stream; render its persisted steps.
        if (TERMINAL_STATUSES.includes(detail.status)) {
          setSteps(
            detail.steps.map((step) => ({
              index: step.index,
              agentName: step.agent_name,
              role: step.role,
              model: step.model,
              provider: step.provider,
              streaming: "",
              output: step.output,
              tokensIn: step.tokens_in,
              tokensOut: step.tokens_out,
              costMicrocents: step.cost_microcents,
              latencyMs: step.latency_ms,
              done: true,
            })),
          );
          return;
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "failed to load run");
        return;
      }

      // Reconnect with backoff until the run reaches a terminal state.
      let attempt = 0;
      while (!cancelled && !finished.current) {
        try {
          setConnected(true);
          await openRunStream(orgId, runId, {
            signal: controller.signal,
            lastEventId: lastSeq.current,
            onEvent: handleEvent,
          });
          attempt = 0;
        } catch (err) {
          if (cancelled || controller.signal.aborted) return;
          if (err instanceof Error && err.name === "AbortError") return;
        } finally {
          setConnected(false);
        }
        if (cancelled || finished.current) break;
        attempt += 1;
        const delay = Math.min(1000 * 2 ** attempt, 15_000);
        await new Promise((resolve) => setTimeout(resolve, delay));
      }

      // Re-read the persisted row so totals match the database exactly.
      if (!cancelled) {
        try {
          await reload();
        } catch {
          // The stream already reported the terminal state.
        }
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [orgId, runId, handleEvent, reload]);

  return {
    run,
    steps,
    artifacts,
    status,
    spentMicrocents,
    budgetWarning,
    error,
    connected,
    reload,
  };
}
