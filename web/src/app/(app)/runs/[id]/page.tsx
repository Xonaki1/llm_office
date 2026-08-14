"use client";

import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { useOrgId } from "@/lib/auth";
import {
  formatBytes,
  formatCents,
  formatDuration,
  formatMicrocents,
  formatTokens,
} from "@/lib/format";
import type { ArtifactContent } from "@/lib/types";
import { TERMINAL_STATUSES } from "@/lib/types";
import { useRunStream, type LiveStep } from "@/lib/use-run-stream";
import {
  Badge,
  Button,
  Card,
  ErrorBanner,
  Modal,
  PageHeader,
  Spinner,
  cx,
  statusTone,
} from "@/components/ui";

export default function RunDetailPage() {
  const orgId = useOrgId();
  const params = useParams<{ id: string }>();
  const runId = params.id;

  const { run, steps, artifacts, status, spentMicrocents, budgetWarning, error, connected, reload } =
    useRunStream(orgId, runId);

  const [cancelling, setCancelling] = useState(false);
  const [openArtifact, setOpenArtifact] = useState<ArtifactContent | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (autoScroll) bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [steps, autoScroll]);

  const live = status !== null && !TERMINAL_STATUSES.includes(status);

  async function cancel() {
    setCancelling(true);
    try {
      await api.post(`/orgs/${orgId}/runs/${runId}/cancel`);
      await reload();
    } finally {
      setCancelling(false);
    }
  }

  async function viewArtifact(path: string) {
    if (!run) return;
    const match = run.artifacts
      .filter((a) => a.path === path)
      .sort((a, b) => b.version - a.version)[0];
    if (!match) return;
    const detail = await api.get<ArtifactContent>(
      `/orgs/${orgId}/runs/${runId}/artifacts/${match.id}`,
    );
    setOpenArtifact(detail);
  }

  if (!run && !error) return <Spinner className="h-5 w-5 text-ink-500" />;

  return (
    <>
      <PageHeader
        title="Run"
        description={run?.input}
        action={
          <div className="flex items-center gap-2">
            {status && <Badge tone={statusTone(status)}>{status.replace("_", " ")}</Badge>}
            {live && (
              <Button variant="danger" onClick={cancel} loading={cancelling}>
                Cancel
              </Button>
            )}
          </div>
        }
      />

      <div className="mb-4 space-y-2">
        <ErrorBanner message={error} />
        {budgetWarning && live && (
          <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-300">
            This run has used more than 80% of its cost ceiling.
          </div>
        )}
      </div>

      <div className="mb-6 grid gap-3 sm:grid-cols-4">
        <Stat label="Steps" value={`${run?.steps_used ?? steps.length}${run?.max_steps ? ` / ${run.max_steps}` : ""}`} />
        <Stat
          label="Spent"
          value={live ? formatMicrocents(spentMicrocents) : formatCents(run?.cost_cents ?? 0)}
          hint={run?.max_cost_cents ? `ceiling ${formatCents(run.max_cost_cents)}` : undefined}
        />
        <Stat
          label="Tokens"
          value={formatTokens((run?.tokens_in ?? 0) + (run?.tokens_out ?? 0))}
        />
        <Stat label="Key mode" value={run?.key_mode ?? "—"} />
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_300px]">
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium text-ink-200">Transcript</h2>
            <label className="flex items-center gap-2 text-xs text-ink-500">
              <input
                type="checkbox"
                checked={autoScroll}
                onChange={(e) => setAutoScroll(e.target.checked)}
              />
              Follow output
            </label>
          </div>

          {steps.length === 0 && live && (
            <Card className="flex items-center gap-3 text-sm text-ink-400">
              <Spinner className="h-4 w-4" />
              Waiting for the first agent…
            </Card>
          )}

          {steps.map((step) => (
            <StepCard key={step.index} step={step} />
          ))}

          {run?.output && !live && (
            <Card className="border-brand-500/30">
              <p className="mb-2 text-xs font-medium uppercase tracking-wide text-brand-400">
                Final answer
              </p>
              <pre className="whitespace-pre-wrap break-words text-sm text-ink-100">
                {run.output}
              </pre>
            </Card>
          )}

          <div ref={bottomRef} />
        </div>

        <aside className="space-y-4">
          <Card>
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-400">
              Connection
            </p>
            <p className="text-sm text-ink-300">
              {live ? (connected ? "Streaming" : "Reconnecting…") : "Finished"}
            </p>
          </Card>

          <Card>
            <p className="mb-3 text-xs font-medium uppercase tracking-wide text-ink-400">
              Artifacts
            </p>
            {artifacts.length === 0 ? (
              <p className="text-sm text-ink-500">None yet.</p>
            ) : (
              <ul className="space-y-2">
                {artifacts.map((artifact) => (
                  <li key={artifact.path}>
                    <button
                      onClick={() => viewArtifact(artifact.path)}
                      className="w-full text-left"
                    >
                      <p className="truncate text-sm text-ink-100 hover:text-brand-400">
                        {artifact.path}
                      </p>
                      <p className="text-xs text-ink-500">
                        v{artifact.version} · {formatBytes(artifact.sizeBytes)} ·{" "}
                        {artifact.agentName}
                      </p>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </aside>
      </div>

      <Modal
        open={openArtifact !== null}
        title={openArtifact?.path ?? ""}
        onClose={() => setOpenArtifact(null)}
      >
        {openArtifact && (
          <>
            <p className="mb-3 text-xs text-ink-500">
              v{openArtifact.version} · {openArtifact.kind} ·{" "}
              {formatBytes(openArtifact.size_bytes)} · by {openArtifact.produced_by_agent}
            </p>
            <pre className="overflow-x-auto rounded-md bg-ink-950 p-3 text-xs text-ink-200">
              {openArtifact.content}
            </pre>
          </>
        )}
      </Modal>
    </>
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

      <pre
        className={cx(
          "max-h-96 overflow-y-auto whitespace-pre-wrap break-words text-sm text-ink-200",
          !step.done && "stream-cursor",
        )}
      >
        {text}
      </pre>
    </Card>
  );
}
