"use client";

import { useCallback, useState } from "react";

import { api } from "@/lib/api";
import { useAuth, useOrgId } from "@/lib/auth";
import { useT } from "@/lib/i18n";
import { useLoader } from "@/lib/use-loader";
import type { Agent, Effort, ModelInfo, ToolCatalogue, ToolInfo } from "@/lib/types";
import { PROVIDER_LABELS } from "@/lib/types";
import {
  Badge,
  Button,
  Card,
  cx,
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

const EFFORTS: Effort[] = ["low", "medium", "high", "xhigh", "max"];

interface Draft {
  id?: string;
  name: string;
  role: string;
  model: string;
  effort: Effort;
  max_tokens: number;
  system_prompt: string;
  tools: string[];
}

const BLANK: Draft = {
  name: "",
  role: "",
  model: "",
  effort: "medium",
  max_tokens: 8000,
  system_prompt: "",
  tools: [],
};

/** Only the colour lives here; the wording comes from `team.sideEffect.*`. */
const SIDE_EFFECT_TONES: Record<string, "neutral" | "warning" | "danger"> = {
  read_only: "neutral",
  write: "warning",
  network: "danger",
};

export default function AgentsPage() {
  const t = useT();
  const orgId = useOrgId();
  const { org } = useAuth();
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [toolCatalogue, setToolCatalogue] = useState<ToolCatalogue | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    const [agentList, modelList, tools] = await Promise.all([
      api.get<Agent[]>(`/orgs/${orgId}/agents`),
      api.get<ModelInfo[]>(`/orgs/${orgId}/models`),
      api.get<ToolCatalogue>("/tools"),
    ]);
    setAgents(agentList);
    setModels(modelList);
    setToolCatalogue(tools);
  }, [orgId]);

  const { error, setError } = useLoader(load);

  const usableModels = models.filter((m) => m.available);
  const selectedModel = models.find((m) => m.id === draft?.model);

  async function save() {
    if (!draft) return;
    setSaving(true);
    setError(null);
    try {
      const payload = {
        name: draft.name,
        role: draft.role,
        model: draft.model,
        effort: draft.effort,
        max_tokens: draft.max_tokens,
        system_prompt: draft.system_prompt,
        tools: draft.tools,
      };
      if (draft.id) {
        await api.patch(`/orgs/${orgId}/agents/${draft.id}`, payload);
      } else {
        await api.post(`/orgs/${orgId}/agents`, payload);
      }
      setDraft(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.error"));
    } finally {
      setSaving(false);
    }
  }

  async function remove(agent: Agent) {
    if (!window.confirm(t("team.dismissConfirm", { name: agent.name }))) return;
    try {
      await api.delete(`/orgs/${orgId}/agents/${agent.id}`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.error"));
    }
  }

  return (
    <>
      <PageHeader
        title={t("team.title")}
        description={t("team.subtitle")}
        action={
          <Button
            onClick={() =>
              setDraft({ ...BLANK, model: usableModels[0]?.id ?? "" })
            }
          >
            {t("team.newAgent")}
          </Button>
        }
      />

      <div className="mb-4">
        <ErrorBanner message={error} />
      </div>

      {agents === null ? (
        <Spinner className="h-5 w-5 text-ink-500" />
      ) : agents.length === 0 ? (
        <EmptyState
          title={t("team.empty")}
          description={t("team.emptyHint")}
          action={
            <Button onClick={() => setDraft({ ...BLANK, model: usableModels[0]?.id ?? "" })}>
              {t("team.hireFirst")}
            </Button>
          }
        />
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {agents.map((agent) => {
            const model = models.find((m) => m.id === agent.model);
            return (
              <Card key={agent.id} className="flex flex-col">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="font-medium text-ink-50">{agent.name}</p>
                    <p className="text-xs text-ink-500">{agent.role}</p>
                  </div>
                  <Badge tone={model?.available === false ? "danger" : "info"}>
                    {PROVIDER_LABELS[model?.provider ?? ""] ?? model?.provider ?? t("common.none")}
                  </Badge>
                </div>

                <p className="mt-3 line-clamp-3 flex-1 text-xs text-ink-400">
                  {agent.system_prompt || t("team.noPrompt")}
                </p>

                {agent.tools.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {agent.tools.map((tool) => (
                      <span
                        key={tool}
                        className="rounded bg-ink-800 px-1.5 py-0.5 font-mono text-[10px] text-ink-400"
                      >
                        {tool}
                      </span>
                    ))}
                  </div>
                )}

                <dl className="mt-3 grid grid-cols-3 gap-2 text-xs text-ink-500">
                  <div>
                    <dt className="text-ink-600">{t("team.model")}</dt>
                    <dd className="truncate text-ink-300">{agent.model}</dd>
                  </div>
                  <div>
                    <dt className="text-ink-600">{t("team.effort")}</dt>
                    <dd className="text-ink-300">
                      {model?.supports_effort === false
                        ? t("common.none")
                        : t(`team.effortLevel.${agent.effort}`)}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-ink-600">{t("team.maxTokens")}</dt>
                    <dd className="text-ink-300">{agent.max_tokens.toLocaleString()}</dd>
                  </div>
                </dl>

                <div className="mt-4 flex gap-2">
                  <Button
                    variant="secondary"
                    className="flex-1"
                    onClick={() =>
                      setDraft({
                        id: agent.id,
                        name: agent.name,
                        role: agent.role,
                        model: agent.model,
                        effort: agent.effort,
                        max_tokens: agent.max_tokens,
                        system_prompt: agent.system_prompt,
                        tools: agent.tools ?? [],
                      })
                    }
                  >
                    {t("common.edit")}
                  </Button>
                  <Button variant="ghost" onClick={() => remove(agent)}>
                    {t("team.dismiss")}
                  </Button>
                </div>
              </Card>
            );
          })}
        </div>
      )}

      <Modal
        open={draft !== null}
        title={draft?.id ? t("team.editAgent") : t("team.newAgent")}
        onClose={() => setDraft(null)}
      >
        {draft && (
          <div className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={t("team.name")}>
                <Input
                  value={draft.name}
                  onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                />
              </Field>
              <Field label={t("team.role")} hint={t("team.roleHint")}>
                <Input
                  value={draft.role}
                  onChange={(e) => setDraft({ ...draft, role: e.target.value })}
                  placeholder={t("team.rolePlaceholder")}
                />
              </Field>
            </div>

            <Field
              label={t("team.model")}
              hint={org?.key_mode === "byok" ? t("team.modelHint") : undefined}
            >
              <Select
                value={draft.model}
                onChange={(e) => setDraft({ ...draft, model: e.target.value })}
              >
                {usableModels.length === 0 && <option value="">{t("team.noModels")}</option>}
                {usableModels.map((model) => (
                  <option key={model.id} value={model.id}>
                    {model.display_name} — ${model.input_per_mtok}/${model.output_per_mtok}{" "}
                    {t("team.perMtok")}
                  </option>
                ))}
              </Select>
            </Field>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field
                label={t("team.effort")}
                hint={
                  selectedModel?.supports_effort === false
                    ? t("team.effortUnsupported")
                    : t("team.effortHint")
                }
              >
                <Select
                  value={draft.effort}
                  disabled={selectedModel?.supports_effort === false}
                  onChange={(e) => setDraft({ ...draft, effort: e.target.value as Effort })}
                >
                  {EFFORTS.map((level) => (
                    <option key={level} value={level}>
                      {t(`team.effortLevel.${level}`)}
                    </option>
                  ))}
                </Select>
              </Field>

              <Field
                label={t("team.maxTokens")}
                hint={
                  selectedModel
                    ? t("team.modelCeiling", {
                        value: selectedModel.max_output_tokens.toLocaleString(),
                      })
                    : undefined
                }
              >
                <Input
                  type="number"
                  min={256}
                  max={selectedModel?.max_output_tokens ?? 128000}
                  value={draft.max_tokens}
                  onChange={(e) =>
                    setDraft({ ...draft, max_tokens: Number(e.target.value) })
                  }
                />
              </Field>
            </div>

            <Field
              label={t("team.tools")}
              hint={
                toolCatalogue?.limits.tools_enabled
                  ? t("team.toolsHint", { count: toolCatalogue.limits.max_iterations })
                  : t("team.toolsDisabled")
              }
            >
              <div className="space-y-1.5">
                {(toolCatalogue?.tools ?? []).map((tool: ToolInfo) => {
                  const tone = SIDE_EFFECT_TONES[tool.side_effect] ?? "neutral";
                  return (
                    <label
                      key={tool.name}
                      className={cx(
                        "flex cursor-pointer gap-2 rounded-md border border-ink-800 p-2",
                        !tool.available && "cursor-not-allowed opacity-50",
                      )}
                    >
                      <input
                        type="checkbox"
                        className="mt-1"
                        disabled={!tool.available}
                        checked={draft.tools.includes(tool.name)}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            tools: e.target.checked
                              ? [...draft.tools, tool.name]
                              : draft.tools.filter((name) => name !== tool.name),
                          })
                        }
                      />
                      <span className="min-w-0">
                        <span className="flex flex-wrap items-center gap-2">
                          <span className="font-mono text-xs text-ink-100">{tool.name}</span>
                          <Badge tone={tone}>{t(`team.sideEffect.${tool.side_effect}`)}</Badge>
                        </span>
                        <span className="mt-0.5 block text-xs text-ink-500">
                          {tool.available
                            ? tool.description
                            : (tool.unavailable_reason ?? tool.description)}
                        </span>
                      </span>
                    </label>
                  );
                })}
              </div>
            </Field>

            <Field label={t("team.prompt")} hint={t("team.promptHint")}>
              <Textarea
                rows={8}
                value={draft.system_prompt}
                onChange={(e) => setDraft({ ...draft, system_prompt: e.target.value })}
              />
            </Field>

            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setDraft(null)}>
                {t("common.cancel")}
              </Button>
              <Button
                onClick={save}
                loading={saving}
                disabled={!draft.name || !draft.role || !draft.model}
              >
                {t("common.save")}
              </Button>
            </div>
          </div>
        )}
      </Modal>
    </>
  );
}
