"use client";

import { useCallback, useState } from "react";

import { api } from "@/lib/api";
import { canManage, useAuth, useOrgId } from "@/lib/auth";
import { useT } from "@/lib/i18n";
import { useLoader } from "@/lib/use-loader";
import { formatCents, formatDateTime } from "@/lib/format";
import type { Balance, LedgerEntry, Member } from "@/lib/types";
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

export default function BillingPage() {
  const t = useT();
  const orgId = useOrgId();
  const { org, reload: reloadSession } = useAuth();
  const [balance, setBalance] = useState<Balance | null>(null);
  const [ledger, setLedger] = useState<LedgerEntry[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [amount, setAmount] = useState(1000);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("member");
  const [busy, setBusy] = useState(false);

  const isOwner = org?.role === "owner";

  // Roles and ledger kinds come from the API as bare identifiers; show the
  // friendly wording when we have it and the raw value when we do not.
  function roleLabel(role: string): string {
    const key = `billing.role.${role}`;
    const label = t(key);
    return label === key ? role : label;
  }

  function kindLabel(kind: string): string {
    const key = `billing.ledgerKind.${kind}`;
    const label = t(key);
    return label === key ? kind : label;
  }

  const load = useCallback(async () => {
    const [balanceData, ledgerData, memberData] = await Promise.all([
      api.get<Balance>(`/orgs/${orgId}/billing/balance`),
      api.get<LedgerEntry[]>(`/orgs/${orgId}/billing/ledger?limit=50`),
      api.get<Member[]>(`/orgs/${orgId}/members`),
    ]);
    setBalance(balanceData);
    setLedger(ledgerData);
    setMembers(memberData);
  }, [orgId]);

  const { error, setError } = useLoader(load);

  async function topup() {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/orgs/${orgId}/billing/topup`, {
        amount_cents: amount,
        description: "manual top-up",
      });
      await Promise.all([load(), reloadSession()]);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("billing.topupFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function addMember() {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/orgs/${orgId}/members`, { email: inviteEmail, role: inviteRole });
      setInviteEmail("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("billing.addMemberFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function removeMember(member: Member) {
    if (!window.confirm(t("billing.removeMemberConfirm", { email: member.email }))) return;
    try {
      await api.delete(`/orgs/${orgId}/members/${member.user_id}`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("billing.removeMemberFailed"));
    }
  }

  return (
    <>
      <PageHeader title={t("billing.title")} description={t("billing.subtitle")} />

      <div className="mb-4">
        <ErrorBanner message={error} />
      </div>

      {balance === null ? (
        <Spinner className="h-5 w-5 text-ink-500" />
      ) : (
        <div className="grid gap-6 lg:grid-cols-2">
          <Card className="space-y-4">
            <div>
              <p className="text-xs uppercase tracking-wide text-ink-500">{t("billing.balance")}</p>
              <p className="mt-1 text-3xl font-semibold text-ink-50">
                {formatCents(balance.credits_cents)}
              </p>
              <p className="mt-1 text-xs text-ink-500">
                {balance.billing_enabled
                  ? t("billing.markup", { percent: balance.markup_percent })
                  : t("billing.disabled")}
              </p>
            </div>

            {isOwner && (
              <div className="flex items-end gap-2">
                <Field label={t("billing.topupLabel")}>
                  <Input
                    type="number"
                    min={1}
                    value={amount}
                    onChange={(e) => setAmount(Number(e.target.value))}
                  />
                </Field>
                <Button onClick={topup} loading={busy}>
                  {t("common.add")}
                </Button>
              </div>
            )}
          </Card>

          <Card className="space-y-4">
            <p className="text-sm font-medium text-ink-100">{t("billing.members")}</p>
            <ul className="space-y-2">
              {members.map((member) => (
                <li key={member.user_id} className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm text-ink-100">{member.email}</p>
                    <p className="text-xs text-ink-500">
                      {t("billing.joined", { date: formatDateTime(member.joined_at) })}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge tone={member.role === "owner" ? "info" : "neutral"}>
                      {roleLabel(member.role)}
                    </Badge>
                    {canManage(org?.role) && members.length > 1 && (
                      <Button variant="ghost" onClick={() => removeMember(member)}>
                        {t("common.remove")}
                      </Button>
                    )}
                  </div>
                </li>
              ))}
            </ul>

            {canManage(org?.role) && (
              <div className="space-y-2 border-t border-ink-800 pt-4">
                <Field label={t("billing.addMember")} hint={t("billing.addMemberHint")}>
                  <Input
                    type="email"
                    value={inviteEmail}
                    onChange={(e) => setInviteEmail(e.target.value)}
                    placeholder="teammate@example.com"
                  />
                </Field>
                <div className="flex gap-2">
                  <Select value={inviteRole} onChange={(e) => setInviteRole(e.target.value)}>
                    <option value="viewer">{roleLabel("viewer")}</option>
                    <option value="member">{roleLabel("member")}</option>
                    <option value="admin">{roleLabel("admin")}</option>
                    {isOwner && <option value="owner">{roleLabel("owner")}</option>}
                  </Select>
                  <Button onClick={addMember} loading={busy} disabled={!inviteEmail}>
                    {t("common.add")}
                  </Button>
                </div>
              </div>
            )}
          </Card>
        </div>
      )}

      <h2 className="mb-3 mt-8 text-sm font-medium text-ink-200">{t("billing.history")}</h2>

      {ledger.length === 0 ? (
        <Card>
          <p className="text-sm text-ink-500">{t("billing.empty")}</p>
        </Card>
      ) : (
        <div className="overflow-hidden rounded-lg border border-ink-800">
          <table className="w-full text-sm">
            <thead className="bg-ink-900/60 text-left text-xs uppercase tracking-wide text-ink-500">
              <tr>
                <th className="px-4 py-2 font-medium">{t("billing.date")}</th>
                <th className="px-4 py-2 font-medium">{t("billing.kind")}</th>
                <th className="px-4 py-2 font-medium">{t("billing.comment")}</th>
                <th className="px-4 py-2 text-right font-medium">{t("billing.amount")}</th>
                <th className="px-4 py-2 text-right font-medium">{t("billing.balanceAfter")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-800">
              {ledger.map((entry) => (
                <tr key={entry.id}>
                  <td className="px-4 py-2 text-ink-500">{formatDateTime(entry.created_at)}</td>
                  <td className="px-4 py-2">
                    <Badge tone={entry.amount_cents >= 0 ? "success" : "neutral"}>
                      {kindLabel(entry.kind)}
                    </Badge>
                  </td>
                  <td className="max-w-xs truncate px-4 py-2 text-ink-400">
                    {entry.description}
                  </td>
                  <td
                    className={
                      entry.amount_cents >= 0
                        ? "px-4 py-2 text-right text-emerald-400"
                        : "px-4 py-2 text-right text-ink-300"
                    }
                  >
                    {entry.amount_cents >= 0 ? "+" : ""}
                    {formatCents(entry.amount_cents)}
                  </td>
                  <td className="px-4 py-2 text-right text-ink-400">
                    {formatCents(entry.balance_after_cents)}
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
