"use client";

import { useParams, useRouter } from "next/navigation";
import { useCallback, useState } from "react";

import { api } from "@/lib/api";
import { useOrgId } from "@/lib/auth";
import { useLoader } from "@/lib/use-loader";
import type { Agent, PresetInfo, Run, Workflow } from "@/lib/types";
import {
  Badge,
  Button,
  Card,
  ErrorBanner,
  Field,
  Input,
  Modal,
  PageHeader,
  Spinner,
  Textarea,
} from "@/components/ui";
import { GraphForm, GraphPreview, type Graph } from "@/components/graph-editor";

export default function WorkflowDetailPage() {
  const orgId = useOrgId();
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const workflowId = params.id;

  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [presets, setPresets] = useState<PresetInfo[]>([]);
  const [graph, setGraph] = useState<Graph>({});
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [runInput, setRunInput] = useState("");
  const [starting, setStarting] = useState(false);
  const [runModal, setRunModal] = useState(false);

  const load = useCallback(async () => {
    const [detail, agentList, presetList] = await Promise.all([
      api.get<Workflow>(`/orgs/${orgId}/workflows/${workflowId}`),
      api.get<Agent[]>(`/orgs/${orgId}/agents`),
      api.get<PresetInfo[]>("/presets"),
    ]);
    setWorkflow(detail);
    setGraph(detail.graph);
    setName(detail.name);
    setDescription(detail.description);
    setAgents(agentList);
    setPresets(presetList);
    setDirty(false);
  }, [orgId, workflowId]);

  const { error, setError } = useLoader(load);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      await api.patch(`/orgs/${orgId}/workflows/${workflowId}`, {
        name,
        description,
        graph,
      });
      await load();
    } catch (err) {
      // The server validates the topology, so an invalid graph is reported
      // here rather than failing later at run time.
      setError(err instanceof Error ? err.message : "could not save the workflow");
    } finally {
      setSaving(false);
    }
  }

  async function startRun() {
    setStarting(true);
    setError(null);
    try {
      const run = await api.post<Run>(`/orgs/${orgId}/runs`, {
        workflow_id: workflowId,
        input: runInput,
      });
      router.push(`/runs/${run.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not start the run");
      setStarting(false);
    }
  }

  if (!workflow) {
    return <Spinner className="h-5 w-5 text-ink-500" />;
  }

  const presetInfo = presets.find((p) => p.name === workflow.preset);

  return (
    <>
      <PageHeader
        title={workflow.name}
        description={presetInfo?.summary}
        action={
          <div className="flex gap-2">
            <Button variant="secondary" onClick={save} loading={saving} disabled={!dirty}>
              {dirty ? "Save changes" : "Saved"}
            </Button>
            <Button onClick={() => setRunModal(true)} disabled={dirty}>
              Run
            </Button>
          </div>
        }
      />

      <div className="mb-4">
        <ErrorBanner message={error} />
      </div>

      <div className="grid gap-6 lg:grid-cols-[380px_1fr]">
        <div className="space-y-4">
          <Card className="space-y-4">
            <Field label="Name">
              <Input
                value={name}
                onChange={(e) => {
                  setName(e.target.value);
                  setDirty(true);
                }}
              />
            </Field>
            <Field label="Description">
              <Textarea
                rows={2}
                value={description}
                onChange={(e) => {
                  setDescription(e.target.value);
                  setDirty(true);
                }}
              />
            </Field>
            <div className="flex items-center gap-2">
              <Badge tone="info">{workflow.preset}</Badge>
              <span className="text-xs text-ink-500">
                Topology cannot be changed after creation.
              </span>
            </div>
          </Card>

          <GraphForm
            preset={workflow.preset}
            graph={graph}
            agents={agents}
            onChange={(next) => {
              setGraph(next);
              setDirty(true);
            }}
          />
        </div>

        <div className="space-y-3">
          <GraphPreview preset={workflow.preset} graph={graph} agents={agents} />
          <p className="text-xs text-ink-500">
            The diagram shows the topology as configured. In supervisor, swarm and custom
            workflows the actual path is decided at run time — the live run view shows the
            route that was taken.
          </p>
        </div>
      </div>

      <Modal open={runModal} title="Start a run" onClose={() => setRunModal(false)}>
        <div className="space-y-4">
          <Field
            label="Task"
            hint="Give the team the full brief up front — they cannot ask you follow-up questions."
          >
            <Textarea
              rows={8}
              value={runInput}
              onChange={(e) => setRunInput(e.target.value)}
              placeholder="Build a URL shortener with an API, rate limiting and tests."
            />
          </Field>
          <p className="text-xs text-ink-500">
            Ceiling: {String(graph.max_steps ?? "—")} steps, {String(graph.max_cost_cents ?? "—")}{" "}
            cents.
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setRunModal(false)}>
              Cancel
            </Button>
            <Button onClick={startRun} loading={starting} disabled={!runInput.trim()}>
              Start
            </Button>
          </div>
        </div>
      </Modal>
    </>
  );
}
