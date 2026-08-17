export type KeyMode = "managed" | "byok" | "hybrid";
export type Effort = "low" | "medium" | "high" | "xhigh" | "max";
export type Role = "owner" | "admin" | "member" | "viewer";
export type Preset =
  | "pipeline"
  | "supervisor"
  | "debate"
  | "blackboard"
  | "swarm"
  | "custom";

export const RUN_STATUSES = [
  "queued",
  "running",
  "succeeded",
  "failed",
  "cancelled",
  "budget_exceeded",
  "timed_out",
] as const;
export type RunStatus = (typeof RUN_STATUSES)[number];

export const TERMINAL_STATUSES: readonly RunStatus[] = [
  "succeeded",
  "failed",
  "cancelled",
  "budget_exceeded",
  "timed_out",
];

export interface OrgSummary {
  id: string;
  name: string;
  slug: string;
  role: Role;
  key_mode: KeyMode;
  credits_cents: number;
}

export interface Me {
  id: string;
  email: string;
  name: string | null;
  is_superuser: boolean;
  orgs: OrgSummary[];
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
  expires_in: number;
}

export type SideEffect = "read_only" | "network" | "write";

export interface ToolInfo {
  name: string;
  description: string;
  side_effect: SideEffect;
  available: boolean;
  unavailable_reason: string | null;
}

export interface ToolCatalogue {
  tools: ToolInfo[];
  limits: {
    max_iterations: number;
    max_calls_per_turn: number;
    tools_enabled: boolean;
  };
}

export interface ToolCallRecord {
  step_index: number;
  call_index: number;
  agent_name: string;
  tool: string;
  arguments: Record<string, unknown>;
  result: string;
  is_error: boolean;
  latency_ms: number;
  created_at: string;
}

export interface Agent {
  id: string;
  name: string;
  role: string;
  system_prompt: string;
  model: string;
  effort: Effort;
  max_tokens: number;
  tools: string[];
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface Workflow {
  id: string;
  name: string;
  description: string;
  preset: Preset;
  graph: Record<string, unknown>;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface PresetInfo {
  name: Preset;
  summary: string;
  required_keys: string[];
  optional_keys: string[];
}

export interface Run {
  id: string;
  workflow_id: string;
  status: RunStatus;
  input: string;
  output: string | null;
  error: string | null;
  key_mode: KeyMode;
  cost_cents: number;
  tokens_in: number;
  tokens_out: number;
  steps_used: number;
  max_steps: number;
  max_cost_cents: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface RunStep {
  index: number;
  agent_id: string | null;
  agent_name: string;
  role: string;
  model: string;
  provider: string;
  input: string;
  output: string;
  stop_reason: string | null;
  tokens_in: number;
  tokens_out: number;
  cached_tokens: number;
  cost_microcents: number;
  latency_ms: number;
  attempts: number;
  model_calls: number;
  tool_calls: number;
  created_at: string;
}

export interface ArtifactSummary {
  id: string;
  path: string;
  kind: string;
  language: string | null;
  version: number;
  size_bytes: number;
  produced_by_step: number | null;
  produced_by_agent: string | null;
  created_at: string;
}

export interface ArtifactContent extends ArtifactSummary {
  content: string;
}

export interface RunDetail extends Run {
  steps: RunStep[];
  artifacts: ArtifactSummary[];
  tool_calls: ToolCallRecord[];
}

export interface ProviderKey {
  id: string;
  provider: string;
  mask: string;
  fingerprint: string;
  is_active: boolean;
  last_used_at: string | null;
  last_error: string | null;
  created_at: string;
}

export interface ModelInfo {
  id: string;
  provider: string;
  display_name: string;
  input_per_mtok: number;
  output_per_mtok: number;
  cached_input_per_mtok: number | null;
  max_output_tokens: number;
  context_window: number;
  supports_effort: boolean;
  available: boolean;
}

export interface Balance {
  credits_cents: number;
  billing_enabled: boolean;
  markup_percent: number;
}

export interface LedgerEntry {
  id: string;
  kind: string;
  amount_cents: number;
  balance_after_cents: number;
  run_id: string | null;
  description: string;
  created_at: string;
}

export interface Member {
  user_id: string;
  email: string;
  name: string | null;
  role: Role;
  joined_at: string;
}

/** Server-sent events emitted by a run. Mirrors core/events.py. */
export type RunEvent =
  | { seq: number; type: "run.start"; run_id: string; ts: string; workflow_name: string; preset: string; key_mode: string; max_steps: number; max_cost_cents: number }
  | { seq: number; type: "step.start"; run_id: string; ts: string; step: number; agent_id: string; agent_name: string; role: string; model: string; effort: string; instruction: string; tools: string[] }
  | { seq: number; type: "step.token"; run_id: string; ts: string; step: number; text: string }
  // A refusal ends the step early: only `refused` and `category` come with it,
  // so every metric here has to be treated as optional by the UI.
  | { seq: number; type: "step.end"; run_id: string; ts: string; step: number; agent_name: string; agent_id?: string; role?: string; model?: string; provider?: string; tokens_in?: number; tokens_out?: number; cached_tokens?: number; cost_microcents?: number; latency_ms?: number; attempts?: number; spent_microcents?: number; steps_used?: number; output?: string; refused?: boolean; category?: string }
  | { seq: number; type: "tool.call"; run_id: string; ts: string; step: number; call: number; tool: string; agent_name: string; arguments: Record<string, unknown> }
  | { seq: number; type: "tool.result"; run_id: string; ts: string; step: number; call: number; tool: string; agent_name: string; is_error: boolean; latency_ms: number; preview: string; metadata: Record<string, unknown> }
  | { seq: number; type: "artifact.written"; run_id: string; ts: string; step: number; path: string; version: number; kind: string; size_bytes: number; agent_name: string; via?: "tool" | "message" }
  | { seq: number; type: "budget.warning"; run_id: string; ts: string; spent_microcents: number; limit_microcents: number }
  | { seq: number; type: "budget.exceeded"; run_id: string; ts: string; reason: string }
  | { seq: number; type: "run.end" | "run.cancelled" | "run.error"; run_id: string; ts: string; status: RunStatus; error: string | null; output: string | null; steps_used?: number; cost_cents?: number };

export const PROVIDER_LABELS: Record<string, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  xai: "xAI",
  google: "Google",
  openrouter: "OpenRouter",
};
