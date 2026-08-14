"use client";

import { useCallback, useState } from "react";

import { api } from "@/lib/api";
import { canManage, useAuth, useOrgId } from "@/lib/auth";
import { useLoader } from "@/lib/use-loader";
import { formatDateTime } from "@/lib/format";
import type { KeyMode, ProviderKey } from "@/lib/types";
import { PROVIDER_LABELS } from "@/lib/types";
import {
  Badge,
  Button,
  Card,
  ErrorBanner,
  Field,
  Input,
  PageHeader,
  Select,
  Spinner,
} from "@/components/ui";

const PROVIDERS = ["anthropic", "openai", "xai", "google", "openrouter"] as const;

const KEY_MODE_HELP: Record<KeyMode, string> = {
  managed:
    "Runs use the platform's provider keys and are billed against your credits.",
  byok: "Runs use only your own keys. The platform bills nothing for tokens.",
  hybrid:
    "Your keys for the expensive reasoning models, the platform's for cheap utility models.",
};

export default function KeysPage() {
  const orgId = useOrgId();
  const { org, reload: reloadSession } = useAuth();
  const [keys, setKeys] = useState<ProviderKey[] | null>(null);
  const [provider, setProvider] = useState<string>("anthropic");
  const [secret, setSecret] = useState("");
  const [saving, setSaving] = useState(false);

  const editable = canManage(org?.role);

  const load = useCallback(async () => {
    setKeys(await api.get<ProviderKey[]>(`/orgs/${orgId}/keys`));
  }, [orgId]);

  const { error, setError } = useLoader(load);

  async function addKey() {
    setSaving(true);
    setError(null);
    try {
      await api.post(`/orgs/${orgId}/keys`, { provider, api_key: secret.trim() });
      setSecret("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not store the key");
    } finally {
      setSaving(false);
    }
  }

  async function removeKey(key: ProviderKey) {
    if (!window.confirm(`Delete the ${key.provider} key ${key.mask}?`)) return;
    try {
      await api.delete(`/orgs/${orgId}/keys/${key.id}`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not delete the key");
    }
  }

  async function setKeyMode(mode: KeyMode) {
    try {
      await api.patch(`/orgs/${orgId}`, { key_mode: mode });
      await reloadSession();
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not change the key mode");
    }
  }

  return (
    <>
      <PageHeader
        title="Provider keys"
        description="Bring your own credentials, or run on the platform's."
      />

      <div className="mb-4">
        <ErrorBanner message={error} />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card className="space-y-4">
          <div>
            <p className="text-sm font-medium text-ink-100">Key mode</p>
            <p className="mt-1 text-xs text-ink-500">
              {org ? KEY_MODE_HELP[org.key_mode] : ""}
            </p>
          </div>
          <Select
            value={org?.key_mode ?? "managed"}
            disabled={!editable}
            onChange={(e) => setKeyMode(e.target.value as KeyMode)}
          >
            <option value="managed">Managed — platform keys</option>
            <option value="byok">BYOK — your keys only</option>
            <option value="hybrid">Hybrid — yours for the big models</option>
          </Select>
        </Card>

        {editable && (
          <Card className="space-y-4">
            <p className="text-sm font-medium text-ink-100">Add a key</p>
            <Field label="Provider">
              <Select value={provider} onChange={(e) => setProvider(e.target.value)}>
                {PROVIDERS.map((name) => (
                  <option key={name} value={name}>
                    {PROVIDER_LABELS[name]}
                  </option>
                ))}
              </Select>
            </Field>
            <Field
              label="API key"
              hint="Encrypted before storage and never shown again — only a mask is returned."
            >
              <Input
                type="password"
                autoComplete="off"
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
                placeholder="sk-…"
              />
            </Field>
            <Button onClick={addKey} loading={saving} disabled={secret.trim().length < 16}>
              Store key
            </Button>
          </Card>
        )}
      </div>

      <h2 className="mb-3 mt-8 text-sm font-medium text-ink-200">Stored keys</h2>

      {keys === null ? (
        <Spinner className="h-5 w-5 text-ink-500" />
      ) : keys.length === 0 ? (
        <Card>
          <p className="text-sm text-ink-500">
            No keys stored. In managed mode that is fine — the platform supplies them.
          </p>
        </Card>
      ) : (
        <div className="space-y-2">
          {keys.map((key) => (
            <Card key={key.id} className="flex items-center justify-between gap-4 py-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-ink-100">
                    {PROVIDER_LABELS[key.provider] ?? key.provider}
                  </span>
                  <Badge tone={key.is_active ? "success" : "neutral"}>
                    {key.is_active ? "active" : "disabled"}
                  </Badge>
                </div>
                <p className="mt-1 font-mono text-xs text-ink-500">{key.mask}</p>
                {key.last_error && (
                  <p className="mt-1 text-xs text-red-400">{key.last_error}</p>
                )}
              </div>
              <div className="flex items-center gap-4 text-xs text-ink-500">
                <span>Last used {formatDateTime(key.last_used_at)}</span>
                {editable && (
                  <Button variant="ghost" onClick={() => removeKey(key)}>
                    Delete
                  </Button>
                )}
              </div>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
