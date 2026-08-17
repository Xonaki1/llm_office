"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { useOrgId } from "@/lib/auth";
import { useT } from "@/lib/i18n";
import { formatCents, formatMicrocents } from "@/lib/format";
import { playEvents, replayEvents } from "@/lib/office/replay";
import { rosterFor } from "@/lib/office/roster";
import { useOfficeSim, useSimVersion } from "@/lib/office/use-sim";
import type { Agent, ArtifactContent, RunEvent, Workflow } from "@/lib/types";
import { TERMINAL_STATUSES } from "@/lib/types";
import { OfficeFeed } from "@/components/office/feed";
import { OfficeScene } from "@/components/office/scene";
import { RunDetails } from "@/components/run-details";
import { useRunStream } from "@/lib/use-run-stream";
import { Badge, Button, Card, ErrorBanner, Modal, Spinner, statusTone } from "@/components/ui";

export default function RunPage() {
  const orgId = useOrgId();
  const params = useParams<{ id: string }>();
  const runId = params.id;
  const t = useT();

  const sim = useOfficeSim();
  useSimVersion(sim);

  const onEvent = useCallback((event: RunEvent) => sim.handle(event), [sim]);
  const { run, steps, artifacts, status, spentMicrocents, budgetWarning, error, connected, reload } =
    useRunStream(orgId, runId, { onEvent });

  const [rosterReady, setRosterReady] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const [openArtifact, setOpenArtifact] = useState<ArtifactContent | null>(null);
  const [replaying, setReplaying] = useState(false);
  const stopReplay = useRef<(() => void) | null>(null);
  const fastForwarded = useRef(false);

  const live = status !== null && !TERMINAL_STATUSES.includes(status);
  const workflowId = run?.workflow_id;

  // Seat the whole team before anything happens, so the office is populated
  // even while the first agent is still thinking.
  useEffect(() => {
    if (!workflowId) return;
    let cancelled = false;

    (async () => {
      try {
        const [workflow, agents] = await Promise.all([
          api.get<Workflow>(`/orgs/${orgId}/workflows/${workflowId}`),
          api.get<Agent[]>(`/orgs/${orgId}/agents?include_inactive=true`),
        ]);
        if (cancelled) return;
        sim.seed(rosterFor(workflow.preset, workflow.graph, agents));
      } catch {
        // The office can still fill itself from the events; a missing roster
        // is not worth an error banner.
      } finally {
        if (!cancelled) setRosterReady(true);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [orgId, workflowId, sim]);

  // A run that finished before the page opened has no stream: rebuild its end
  // state from the database so the room shows the work that was done.
  //
  // The decision is made once, on the first load. A run that was live when the
  // page opened has already been animated by the stream — replaying it after
  // it finishes would pin every page to the board a second time.
  useEffect(() => {
    if (!run || !rosterReady || fastForwarded.current) return;
    fastForwarded.current = true;
    if (!TERMINAL_STATUSES.includes(run.status)) return;
    sim.fastForward(replayEvents(run));
  }, [run, rosterReady, sim]);

  useEffect(() => () => stopReplay.current?.(), []);

  async function cancel() {
    setCancelling(true);
    try {
      await api.post(`/orgs/${orgId}/runs/${runId}/cancel`);
      await reload();
    } finally {
      setCancelling(false);
    }
  }

  function replay() {
    if (!run) return;
    stopReplay.current?.();
    sim.reset();
    setReplaying(true);
    stopReplay.current = playEvents(replayEvents(run), (event) => sim.handle(event), {
      onFinish: () => setReplaying(false),
    });
  }

  async function viewArtifact(path: string) {
    if (!run) return;
    const match = run.artifacts
      .filter((artifact) => artifact.path === path)
      .sort((a, b) => b.version - a.version)[0];
    if (!match) return;
    const detail = await api.get<ArtifactContent>(
      `/orgs/${orgId}/runs/${runId}/artifacts/${match.id}`,
    );
    setOpenArtifact(detail);
  }

  if (!run && !error) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner className="h-6 w-6 text-ink-500" />
      </div>
    );
  }

  return (
    <>
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Link href="/office" className="text-xs text-ink-500 hover:text-ink-200">
            ← {t("nav.office")}
          </Link>
          <h1 className="mt-1 line-clamp-2 text-lg font-semibold text-ink-50">{run?.input}</h1>
        </div>
        <div className="flex items-center gap-2">
          {status && <Badge tone={statusTone(status)}>{t(`status.${status}`)}</Badge>}
          {live && (
            <Button variant="danger" onClick={cancel} loading={cancelling}>
              {cancelling ? t("run.cancelling") : t("run.cancel")}
            </Button>
          )}
          {!live && run && (
            <Button variant="secondary" onClick={replay} disabled={replaying}>
              {replaying ? t("run.replaying") : t("run.replay")}
            </Button>
          )}
        </div>
      </div>

      <div className="mb-3 space-y-2">
        <ErrorBanner message={error} />
        {budgetWarning && live && (
          <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-300">
            {t("run.budgetWarning")}
          </div>
        )}
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
        <div className="space-y-3">
          <OfficeScene sim={sim} />

          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-500">
            <span>
              {t("run.steps")}: {run?.steps_used ?? steps.length}
              {run?.max_steps ? ` / ${run.max_steps}` : ""}
            </span>
            <span>
              {t("run.spent")}:{" "}
              {live ? formatMicrocents(spentMicrocents) : formatCents(run?.cost_cents ?? 0)}
            </span>
            <span>
              {t("run.documents")}: {sim.documents}
            </span>
            {live && <span>{connected ? t("run.streaming") : t("run.reconnecting")}</span>}
          </div>
        </div>

        <Card className="flex max-h-[520px] flex-col overflow-hidden">
          <p className="mb-3 text-xs font-medium uppercase tracking-wide text-ink-400">
            {t("run.chronicle")}
          </p>
          {sim.feed.length === 0 ? (
            <p className="flex items-center gap-2 text-sm text-ink-500">
              {live && <Spinner className="h-4 w-4" />}
              {t("run.waiting")}
            </p>
          ) : (
            <OfficeFeed sim={sim} className="min-h-0 flex-1 overflow-y-auto pr-1" />
          )}
        </Card>
      </div>

      {run?.output && !live && (
        <Card className="mt-4 border-brand-500/30">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-xs font-medium uppercase tracking-wide text-brand-400">
              {t("run.result")}
            </p>
            <CopyButton text={run.output} />
          </div>
          <pre className="max-h-[420px] overflow-y-auto whitespace-pre-wrap break-words text-sm text-ink-100">
            {run.output}
          </pre>
        </Card>
      )}

      <div className="mt-4">
        <Button variant="ghost" onClick={() => setShowDetails((value) => !value)}>
          {showDetails ? "▾" : "▸"} {t("run.openDetails")}
        </Button>
        {showDetails && (
          <div className="mt-3">
            <RunDetails
              run={run}
              steps={steps}
              artifacts={artifacts}
              spentMicrocents={spentMicrocents}
              live={live}
              onViewArtifact={viewArtifact}
            />
          </div>
        )}
      </div>

      <Modal
        open={openArtifact !== null}
        title={openArtifact?.path ?? ""}
        onClose={() => setOpenArtifact(null)}
      >
        {openArtifact && (
          <pre className="overflow-x-auto rounded-md bg-ink-950 p-3 text-xs text-ink-200">
            {openArtifact.content}
          </pre>
        )}
      </Modal>
    </>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant="ghost"
      onClick={async () => {
        await navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
    >
      {copied ? "✓" : "⧉"}
    </Button>
  );
}
