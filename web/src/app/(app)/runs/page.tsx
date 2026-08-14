"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";
import { useOrgId } from "@/lib/auth";
import { formatCents, formatRelative, formatTokens } from "@/lib/format";
import type { Run, Workflow } from "@/lib/types";
import { TERMINAL_STATUSES } from "@/lib/types";
import {
  Badge,
  Button,
  EmptyState,
  ErrorBanner,
  PageHeader,
  Spinner,
  statusTone,
} from "@/components/ui";

const POLL_INTERVAL_MS = 4000;

export default function RunsPage() {
  const orgId = useOrgId();
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [workflows, setWorkflows] = useState<Record<string, Workflow>>({});
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [runList, workflowList] = await Promise.all([
      api.get<Run[]>(`/orgs/${orgId}/runs?limit=50`),
      api.get<Workflow[]>(`/orgs/${orgId}/workflows?include_inactive=true`),
    ]);
    setRuns(runList);
    setWorkflows(Object.fromEntries(workflowList.map((w) => [w.id, w])));
    return runList;
  }, [orgId]);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    let cancelled = false;

    async function tick() {
      try {
        const list = await load();
        if (cancelled) return;
        // Poll only while something is in flight; a settled list is static, and
        // the detail page has a live stream for the run you are watching.
        const active = list.some((run) => !TERMINAL_STATUSES.includes(run.status));
        if (active) timer = setTimeout(tick, POLL_INTERVAL_MS);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "could not load runs");
      }
    }

    tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [load]);

  return (
    <>
      <PageHeader
        title="Runs"
        description="Every execution, with what it cost and what it produced."
        action={
          <Link href="/workflows">
            <Button>Start a run</Button>
          </Link>
        }
      />

      <div className="mb-4">
        <ErrorBanner message={error} />
      </div>

      {runs === null ? (
        <Spinner className="h-5 w-5 text-ink-500" />
      ) : runs.length === 0 ? (
        <EmptyState
          title="No runs yet"
          description="Open a workflow and give the team a task."
          action={
            <Link href="/workflows">
              <Button>Go to workflows</Button>
            </Link>
          }
        />
      ) : (
        <div className="overflow-hidden rounded-lg border border-ink-800">
          <table className="w-full text-sm">
            <thead className="bg-ink-900/60 text-left text-xs uppercase tracking-wide text-ink-500">
              <tr>
                <th className="px-4 py-2 font-medium">Task</th>
                <th className="px-4 py-2 font-medium">Workflow</th>
                <th className="px-4 py-2 font-medium">Status</th>
                <th className="px-4 py-2 text-right font-medium">Steps</th>
                <th className="px-4 py-2 text-right font-medium">Tokens</th>
                <th className="px-4 py-2 text-right font-medium">Cost</th>
                <th className="px-4 py-2 text-right font-medium">Started</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-800">
              {runs.map((run) => (
                <tr key={run.id} className="hover:bg-ink-900/40">
                  <td className="max-w-md px-4 py-2">
                    <Link href={`/runs/${run.id}`} className="block truncate text-ink-100 hover:text-brand-400">
                      {run.input}
                    </Link>
                  </td>
                  <td className="px-4 py-2 text-ink-400">
                    {workflows[run.workflow_id]?.name ?? "—"}
                  </td>
                  <td className="px-4 py-2">
                    <Badge tone={statusTone(run.status)}>{run.status.replace("_", " ")}</Badge>
                  </td>
                  <td className="px-4 py-2 text-right text-ink-400">
                    {run.steps_used}
                    {run.max_steps ? `/${run.max_steps}` : ""}
                  </td>
                  <td className="px-4 py-2 text-right text-ink-400">
                    {formatTokens(run.tokens_in + run.tokens_out)}
                  </td>
                  <td className="px-4 py-2 text-right text-ink-400">
                    {formatCents(run.cost_cents)}
                  </td>
                  <td className="px-4 py-2 text-right text-ink-500">
                    {formatRelative(run.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
