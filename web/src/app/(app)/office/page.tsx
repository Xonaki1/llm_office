"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useMemo, useState } from "react";

import { api } from "@/lib/api";
import { useOrgId } from "@/lib/auth";
import { useT } from "@/lib/i18n";
import { formatCents, formatRelative } from "@/lib/format";
import { rosterFor } from "@/lib/office/roster";
import { OfficeSim } from "@/lib/office/sim";
import { useLoader } from "@/lib/use-loader";
import type { Agent, Run, Workflow } from "@/lib/types";
import { TERMINAL_STATUSES } from "@/lib/types";
import { OfficeScene } from "@/components/office/scene";
import {
  Badge,
  Button,
  Card,
  ErrorBanner,
  Spinner,
  Textarea,
  cx,
  statusTone,
} from "@/components/ui";

/**
 * The office.
 *
 * This is the front door: the team is on screen, you type what you want, and
 * they get up and do it. Everything else in the product is reachable from
 * here, but nothing else is required to use it.
 */
export default function OfficePage() {
  const orgId = useOrgId();
  const router = useRouter();
  const t = useT();

  const [agents, setAgents] = useState<Agent[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [task, setTask] = useState("");
  const [starting, setStarting] = useState(false);

  const load = useCallback(async () => {
    const [agentList, workflowList, runList] = await Promise.all([
      api.get<Agent[]>(`/orgs/${orgId}/agents`),
      api.get<Workflow[]>(`/orgs/${orgId}/workflows`),
      api.get<Run[]>(`/orgs/${orgId}/runs?limit=8`),
    ]);
    setAgents(agentList);
    setWorkflows(workflowList);
    setRuns(runList);
    setSelected((current) => current ?? workflowList[0]?.id ?? null);
  }, [orgId]);

  const { loading, error, setError } = useLoader(load);

  const workflow = workflows.find((item) => item.id === selected) ?? workflows[0];
  const roster = useMemo(
    () => (workflow ? rosterFor(workflow.preset, workflow.graph, agents) : []),
    [workflow, agents],
  );

  // A fresh simulation per team: swapping the team reseats the room, and there
  // is no run state on this page worth preserving across the change.
  const sim = useMemo(() => {
    const office = new OfficeSim();
    office.seed(roster);
    return office;
  }, [roster]);

  const active = runs.find((run) => !TERMINAL_STATUSES.includes(run.status));

  async function start() {
    if (!workflow || !task.trim()) return;
    setStarting(true);
    setError(null);
    try {
      const run = await api.post<Run>(`/orgs/${orgId}/runs`, {
        workflow_id: workflow.id,
        input: task.trim(),
      });
      router.push(`/runs/${run.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.error"));
      setStarting(false);
    }
  }

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner className="h-6 w-6 text-ink-500" />
      </div>
    );
  }

  return (
    <>
      <div className="mb-4">
        <h1 className="text-xl font-semibold text-ink-50">{t("office.title")}</h1>
        <p className="mt-1 text-sm text-ink-500">{t("office.subtitle")}</p>
      </div>

      <div className="mb-4">
        <ErrorBanner message={error} />
      </div>

      {agents.length === 0 ? (
        <EmptyOffice
          title={t("office.hireFirst")}
          description={t("office.hireFirstHint")}
          action={
            <Link href="/agents">
              <Button>{t("office.hire")}</Button>
            </Link>
          }
        />
      ) : workflows.length === 0 ? (
        <EmptyOffice
          title={t("office.noTeams")}
          description={t("office.noTeamsHint")}
          action={
            <Link href="/workflows">
              <Button>{t("office.createTeam")}</Button>
            </Link>
          }
        />
      ) : (
        <>
          <OfficeScene sim={sim} className="mb-4" />

          {active && (
            <Card className="mb-4 flex flex-wrap items-center justify-between gap-3 border-brand-500/40">
              <div className="min-w-0">
                <p className="text-xs uppercase tracking-wide text-brand-400">
                  {t("office.atWork")}
                </p>
                <p className="truncate text-sm text-ink-200">{active.input}</p>
              </div>
              <Link href={`/runs/${active.id}`}>
                <Button>{t("office.watching")}</Button>
              </Link>
            </Card>
          )}

          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
            <Card className="space-y-3">
              <label className="block text-sm font-medium text-ink-100" htmlFor="task">
                {t("office.taskLabel")}
              </label>
              <Textarea
                id="task"
                rows={5}
                value={task}
                onChange={(event) => setTask(event.target.value)}
                placeholder={t("office.taskPlaceholder")}
              />
              <p className="text-xs text-ink-500">{t("office.taskHint")}</p>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-xs text-ink-600">
                  {t("office.ceiling", {
                    steps: String(workflow?.graph.max_steps ?? "—"),
                    cost:
                      typeof workflow?.graph.max_cost_cents === "number"
                        ? formatCents(workflow.graph.max_cost_cents)
                        : "—",
                  })}
                </p>
                <Button onClick={start} loading={starting} disabled={!task.trim() || !workflow}>
                  {starting ? t("office.starting") : t("office.startWork")}
                </Button>
              </div>
            </Card>

            <Card className="space-y-2">
              <p className="text-xs font-medium uppercase tracking-wide text-ink-400">
                {t("office.teamLabel")}
              </p>
              {workflows.map((item) => (
                <button
                  key={item.id}
                  onClick={() => setSelected(item.id)}
                  className={cx(
                    "block w-full rounded-md border px-3 py-2 text-left transition-colors",
                    item.id === workflow?.id
                      ? "border-brand-500/60 bg-brand-500/10"
                      : "border-ink-800 hover:border-ink-700",
                  )}
                >
                  <p className="text-sm text-ink-100">{item.name}</p>
                  <p className="text-xs text-ink-500">{t(`preset.${item.preset}Hint`)}</p>
                </button>
              ))}
              {roster.length > 0 && (
                <p className="pt-1 text-xs text-ink-600">
                  {roster.map((member) => member.name).join(", ")}
                </p>
              )}
            </Card>
          </div>
        </>
      )}

      <div className="mt-6">
        <h2 className="mb-2 text-sm font-medium text-ink-200">{t("office.recent")}</h2>
        {runs.length === 0 ? (
          <p className="text-sm text-ink-500">{t("office.noRecent")}</p>
        ) : (
          <ul className="grid gap-2 md:grid-cols-2">
            {runs.map((run) => (
              <li key={run.id}>
                <Link
                  href={`/runs/${run.id}`}
                  className="block rounded-lg border border-ink-800 bg-ink-900/40 px-3 py-2 hover:border-ink-700"
                >
                  <div className="flex items-center justify-between gap-2">
                    <p className="truncate text-sm text-ink-100">{run.input}</p>
                    <Badge tone={statusTone(run.status)}>{t(`status.${run.status}`)}</Badge>
                  </div>
                  <p className="mt-0.5 text-xs text-ink-600">
                    {formatRelative(run.created_at)} · {formatCents(run.cost_cents)}
                  </p>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}

function EmptyOffice({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-dashed border-ink-800 px-6 py-12 text-center">
      <p className="text-sm font-medium text-ink-200">{title}</p>
      <p className="mx-auto mt-1 max-w-md text-sm text-ink-500">{description}</p>
      <div className="mt-4 flex justify-center">{action}</div>
    </div>
  );
}
