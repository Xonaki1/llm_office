"use client";

import { useCallback, useState } from "react";

import { api } from "@/lib/api";
import { canManage, useAuth, useOrgId } from "@/lib/auth";
import { useT } from "@/lib/i18n";
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

// Dictionary keys, not copy: this map lives outside the component, where the
// `useT` hook cannot be called. The lookup is translated at render time.
const KEY_MODE_HELP: Record<KeyMode, string> = {
  managed: "keys.modeManagedHint",
  byok: "keys.modeByokHint",
  hybrid: "keys.modeHybridHint",
};

export default function KeysPage() {
  const t = useT();
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
      setError(err instanceof Error ? err.message : t("keys.addError"));
    } finally {
      setSaving(false);
    }
  }

  async function removeKey(key: ProviderKey) {
    if (!window.confirm(t("keys.removeConfirm", { mask: key.mask }))) return;
    try {
      await api.delete(`/orgs/${orgId}/keys/${key.id}`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("keys.removeError"));
    }
  }

  async function setKeyMode(mode: KeyMode) {
    try {
      await api.patch(`/orgs/${orgId}`, { key_mode: mode });
      await reloadSession();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("keys.modeError"));
    }
  }

  return (
    <>
      <PageHeader title={t("keys.title")} description={t("keys.subtitle")} />

      <div className="mb-4">
        <ErrorBanner message={error} />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card className="space-y-4">
          <div>
            {/* "Key mode" is the product's own term for the setting and stays
                as-is in both languages, like the mode names below. */}
            <p className="text-sm font-medium text-ink-100">Key mode</p>
            <p className="mt-1 text-xs text-ink-500">
              {org ? t(KEY_MODE_HELP[org.key_mode]) : ""}
            </p>
          </div>
          <Select
            value={org?.key_mode ?? "managed"}
            disabled={!editable}
            onChange={(e) => setKeyMode(e.target.value as KeyMode)}
          >
            <option value="managed">{t("keys.modeManaged")}</option>
            <option value="byok">{t("keys.modeByok")}</option>
            <option value="hybrid">{t("keys.modeHybrid")}</option>
          </Select>
        </Card>

        {editable && (
          <Card className="space-y-4">
            <p className="text-sm font-medium text-ink-100">{t("keys.add")}</p>
            <Field label={t("keys.provider")}>
              <Select value={provider} onChange={(e) => setProvider(e.target.value)}>
                {PROVIDERS.map((name) => (
                  <option key={name} value={name}>
                    {PROVIDER_LABELS[name]}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label={t("keys.key")} hint={t("keys.keyHint")}>
              <Input
                type="password"
                autoComplete="off"
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
                placeholder="sk-…"
              />
            </Field>
            <Button onClick={addKey} loading={saving} disabled={secret.trim().length < 16}>
              {t("common.save")}
            </Button>
          </Card>
        )}
      </div>

      <h2 className="mb-3 mt-8 text-sm font-medium text-ink-200">{t("keys.stored")}</h2>

      {keys === null ? (
        <Spinner className="h-5 w-5 text-ink-500" />
      ) : keys.length === 0 ? (
        <Card>
          <p className="text-sm font-medium text-ink-100">{t("keys.empty")}</p>
          <p className="mt-1 text-sm text-ink-500">{t("keys.emptyHint")}</p>
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
                    {key.is_active ? t("keys.active") : t("keys.inactive")}
                  </Badge>
                </div>
                <p className="mt-1 font-mono text-xs text-ink-500">{key.mask}</p>
                {key.last_error && (
                  <p className="mt-1 text-xs text-red-400">{key.last_error}</p>
                )}
              </div>
              <div className="flex items-center gap-4 text-xs text-ink-500">
                <span>
                  {t("keys.lastUsed")}:{" "}
                  {key.last_used_at ? formatDateTime(key.last_used_at) : t("keys.never")}
                </span>
                {editable && (
                  <Button variant="ghost" onClick={() => removeKey(key)}>
                    {t("common.remove")}
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
