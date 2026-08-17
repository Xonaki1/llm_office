"use client";

/**
 * The technical view of a run: exact numbers and the full transcript.
 *
 * It used to be the whole page. It is now what you get when you ask for it —
 * the office answers "what is happening", this answers "what exactly happened".
 */

import { useState } from "react";

import { useT } from "@/lib/i18n";
import {
  formatBytes,
  formatCents,
  formatDuration,
  formatMicrocents,
  formatTokens,
} from "@/lib/format";
import type { RunDetail } from "@/lib/types";
import type { LiveArtifact, LiveStep, LiveToolCall } from "@/lib/use-run-stream";
import { Badge, Card, Spinner, cx } from "@/components/ui";

export function RunDetails({
  run,
  steps,
  artifacts,
  spentMicrocents,
  live,
  onViewArtifact,
}: {
  run: RunDetail | null;
  steps: LiveStep[];
  artifacts: LiveArtifact[];
  spentMicrocents: number;
  live: boolean;
  onViewArtifact: (path: string) => void;
}) {
  const t = useT();

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-4">
        <Stat
          label={t("run.steps")}
          value={`${run?.steps_used ?? steps.length}${run?.max_steps ? ` / ${run.max_steps}` : ""}`}
        />
        <Stat
          label={t("run.spent")}
          value={live ? formatMicrocents(spentMicrocents) : formatCents(run?.cost_cents ?? 0)}
          hint={run?.max_cost_cents ? `≤ ${formatCents(run.max_cost_cents)}` : undefined}
        />
        <Stat
          label={t("tasks.columnTokens")}
          value={formatTokens((run?.tokens_in ?? 0) + (run?.tokens_out ?? 0))}
        />
        <Stat label="key mode" value={run?.key_mode ?? "—"} />
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_260px]">
        <div className="space-y-3">
          {steps.map((step) => (
            <StepCard key={step.index} step={step} />
          ))}
        </div>

        <Card>
          <p className="mb-3 text-xs font-medium uppercase tracking-wide text-ink-400">
            {t("run.documents")}
          </p>
          {artifacts.length === 0 ? (
            <p className="text-sm text-ink-500">{t("run.noDocuments")}</p>
          ) : (
            <ul className="space-y-2">
              {artifacts.map((artifact) => (
                <li key={artifact.path}>
                  <button onClick={() => onViewArtifact(artifact.path)} className="w-full text-left">
                    <p className="truncate text-sm text-ink-100 hover:text-brand-400">
                      {artifact.path}
                    </p>
                    <p className="text-xs text-ink-500">
                      v{artifact.version} · {formatBytes(artifact.sizeBytes)} · {artifact.agentName}
                    </p>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card className="py-3">
      <p className="text-xs uppercase tracking-wide text-ink-500">{label}</p>
      <p className="mt-1 text-lg font-medium text-ink-50">{value}</p>
      {hint && <p className="text-xs text-ink-600">{hint}</p>}
    </Card>
  );
}

function StepCard({ step }: { step: LiveStep }) {
  const text = step.done ? (step.output ?? "") : step.streaming;

  return (
    <Card className={cx(!step.done && "border-brand-500/40")}>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs text-ink-600">#{step.index}</span>
          <span className="text-sm font-medium text-ink-100">{step.agentName}</span>
          <span className="text-xs text-ink-500">{step.role}</span>
        </div>
        <div className="flex items-center gap-2 text-xs text-ink-500">
          <Badge>{step.model}</Badge>
          {step.done ? (
            <>
              <span>{formatTokens((step.tokensIn ?? 0) + (step.tokensOut ?? 0))} tok</span>
              <span>{formatMicrocents(step.costMicrocents ?? 0)}</span>
              <span>{formatDuration(step.latencyMs ?? 0)}</span>
            </>
          ) : (
            <Spinner className="h-3 w-3" />
          )}
        </div>
      </div>

      {step.toolCalls.length > 0 && (
        <div className="mb-3 space-y-1.5">
          {step.toolCalls.map((call) => (
            <ToolCallRow key={call.call} call={call} />
          ))}
        </div>
      )}

      {text && (
        <pre
          className={cx(
            "max-h-96 overflow-y-auto whitespace-pre-wrap break-words text-sm text-ink-200",
            !step.done && "stream-cursor",
          )}
        >
          {text}
        </pre>
      )}
    </Card>
  );
}

function ToolCallRow({ call }: { call: LiveToolCall }) {
  const [open, setOpen] = useState(false);
  // Show the argument that identifies the call at a glance — a path or a URL
  // says far more than the tool name alone.
  const summary =
    (call.arguments.path as string) ??
    (call.arguments.url as string) ??
    (call.arguments.query as string) ??
    (call.arguments.prefix as string) ??
    "";

  return (
    <div
      className={cx(
        "rounded-md border px-2.5 py-1.5 text-xs",
        call.isError ? "border-red-500/30 bg-red-500/5" : "border-ink-800 bg-ink-950/60",
      )}
    >
      <button
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-2 text-left"
      >
        <span className="text-ink-500">{open ? "▾" : "▸"}</span>
        <span className="font-mono text-brand-400">{call.tool}</span>
        {summary && <span className="truncate text-ink-400">{summary}</span>}
        <span className="ml-auto flex items-center gap-2 text-ink-600">
          {call.isError && <span className="text-red-400">error</span>}
          {call.done ? <span>{formatDuration(call.latencyMs ?? 0)}</span> : <Spinner className="h-3 w-3" />}
        </span>
      </button>

      {open && (
        <div className="mt-2 space-y-2 border-t border-ink-800 pt-2">
          <div>
            <p className="mb-1 text-ink-600">Arguments</p>
            <pre className="overflow-x-auto rounded bg-ink-950 p-2 text-ink-300">
              {JSON.stringify(call.arguments, null, 2)}
            </pre>
          </div>
          {call.preview && (
            <div>
              <p className="mb-1 text-ink-600">Result</p>
              <pre className="max-h-48 overflow-y-auto whitespace-pre-wrap rounded bg-ink-950 p-2 text-ink-300">
                {call.preview}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
