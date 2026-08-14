"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";

import { api } from "@/lib/api";
import { useOrgId } from "@/lib/auth";
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
      setError(err instanceof Error ? err.message : "could not create the workflow");
    } finally {
      setSaving(false);
    }
  }

  const selectedPreset = presets.find((p) => p.name === preset);
  const enoughAgents = agents.length >= 2;

  return (
    <>
      <PageHeader
        title="Workflows"
        description="A workflow is a topology plus the agents that fill it."
        action={
          <Button onClick={() => setCreating(true)} disabled={agents.length === 0}>
            New workflow
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
          title="No workflows yet"
          description={
            agents.length === 0
              ? "Create some agents first — a workflow needs agents to fill its roles."
              : "Pick a topology and assign your agents to it."
          }
          action={
            agents.length === 0 ? (
              <Link href="/agents">
                <Button>Go to agents</Button>
              </Link>
            ) : (
              <Button onClick={() => setCreating(true)}>Create a workflow</Button>
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
                  <Badge tone="info">{workflow.preset}</Badge>
                </div>
                <p className="mt-2 line-clamp-2 text-xs text-ink-400">
                  {workflow.description || "No description."}
                </p>
                <p className="mt-3 text-xs text-ink-600">
                  Updated {formatRelative(workflow.updated_at)}
                </p>
              </Card>
            </Link>
          ))}
        </div>
      )}

      <Modal open={creating} title="New workflow" onClose={() => setCreating(false)}>
        <div className="space-y-4">
          {!enoughAgents && (
            <p className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-300">
              Most topologies need at least two agents. You can still create this one and
              assign agents later.
            </p>
          )}

          <Field label="Name">
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>

          <Field label="Description">
            <Textarea
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </Field>

          <Field label="Topology" hint={selectedPreset?.summary}>
            <Select value={preset} onChange={(e) => setPreset(e.target.value as Preset)}>
              {presets.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.name}
                </option>
              ))}
            </Select>
          </Field>

          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setCreating(false)}>
              Cancel
            </Button>
            <Button onClick={create} loading={saving} disabled={!name}>
              Create and edit
            </Button>
          </div>
        </div>
      </Modal>
    </>
  );
}
