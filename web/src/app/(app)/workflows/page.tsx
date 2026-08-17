"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";

import { api } from "@/lib/api";
import { useOrgId } from "@/lib/auth";
import { useT } from "@/lib/i18n";
import { useLoader } from "@/lib/use-loader";
import { formatRelative } from "@/lib/format";
import type { Agent, Preset, PresetInfo, Workflow } from "@/lib/types";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorBanner,
  Field,
  Input,
  Modal,
  PageHeader,
  Select,
  Spinner,
  Textarea,
} from "@/components/ui";
import { blankGraph } from "@/components/graph-editor";

export default function WorkflowsPage() {
  const t = useT();
  const orgId = useOrgId();
  const router = useRouter();
  const [workflows, setWorkflows] = useState<Workflow[] | null>(null);
  const [presets, setPresets] = useState<PresetInfo[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [preset, setPreset] = useState<Preset>("pipeline");

  const load = useCallback(async () => {
    const [workflowList, presetList, agentList] = await Promise.all([
      api.get<Workflow[]>(`/orgs/${orgId}/workflows`),
      api.get<PresetInfo[]>("/presets"),
      api.get<Agent[]>(`/orgs/${orgId}/agents`),
    ]);
    setWorkflows(workflowList);
    setPresets(presetList);
    setAgents(agentList);
  }, [orgId]);

  const { error, setError } = useLoader(load);

  async function create() {
    setSaving(true);
    setError(null);
    try {
      const created = await api.post<Workflow>(`/orgs/${orgId}/workflows`, {
        name,
        description,
        preset,
        graph: blankGraph(preset, agents),
      });
      setCreating(false);
      router.push(`/workflows/${created.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("workflows.createFailed"));
    } finally {
      setSaving(false);
    }
  }

  const enoughAgents = agents.length >= 2;

  return (
    <>
      <PageHeader
        title={t("workflows.title")}
        description={t("workflows.subtitle")}
        action={
          <Button onClick={() => setCreating(true)} disabled={agents.length === 0}>
            {t("workflows.newWorkflow")}
          </Button>
        }
      />

      <div className="mb-4">
        <ErrorBanner message={error} />
      </div>

      {workflows === null ? (
        <Spinner className="h-5 w-5 text-ink-500" />
      ) : workflows.length === 0 ? (
        <EmptyState
          title={t("workflows.empty")}
          description={agents.length === 0 ? t("office.hireFirstHint") : t("workflows.emptyHint")}
          action={
            agents.length === 0 ? (
              <Link href="/agents">
                <Button>{t("office.hire")}</Button>
              </Link>
            ) : (
              <Button onClick={() => setCreating(true)}>{t("office.createTeam")}</Button>
            )
          }
        />
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {workflows.map((workflow) => (
            <Link key={workflow.id} href={`/workflows/${workflow.id}`}>
              <Card className="h-full transition-colors hover:border-ink-700">
                <div className="flex items-start justify-between gap-2">
                  <p className="font-medium text-ink-50">{workflow.name}</p>
                  <Badge tone="info">{t(`preset.${workflow.preset}`)}</Badge>
                </div>
                <p className="mt-2 line-clamp-2 text-xs text-ink-400">
                  {workflow.description || t("workflows.noDescription")}
                </p>
                <p className="mt-3 text-xs text-ink-600">
                  {t("workflows.updated", { when: formatRelative(workflow.updated_at) })}
                </p>
              </Card>
            </Link>
          ))}
        </div>
      )}

      <Modal open={creating} title={t("workflows.newWorkflow")} onClose={() => setCreating(false)}>
        <div className="space-y-4">
          {!enoughAgents && (
            <p className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-300">
              {t("workflows.needTwoAgents")}
            </p>
          )}

          <Field label={t("workflows.name")}>
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>

          <Field label={t("workflows.description")}>
            <Textarea
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </Field>

          <Field label={t("workflows.preset")} hint={t(`preset.${preset}Hint`)}>
            <Select value={preset} onChange={(e) => setPreset(e.target.value as Preset)}>
              {presets.map((p) => (
                <option key={p.name} value={p.name}>
                  {t(`preset.${p.name}`)}
                </option>
              ))}
            </Select>
          </Field>

          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setCreating(false)}>
              {t("common.cancel")}
            </Button>
            <Button onClick={create} loading={saving} disabled={!name}>
              {t("workflows.create")}
            </Button>
          </div>
        </div>
      </Modal>
    </>
  );
}
