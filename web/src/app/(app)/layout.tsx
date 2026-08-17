"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";
import { formatCents } from "@/lib/format";
import { Select, Spinner, cx } from "@/components/ui";

/** What a person needs day to day. */
const MAIN = [
  { href: "/office", key: "nav.office", icon: "🏢" },
  { href: "/runs", key: "nav.tasks", icon: "📋" },
  { href: "/agents", key: "nav.team", icon: "🧑‍💼" },
];

/** Everything that is really configuration, folded away by default. */
const ADVANCED = [
  { href: "/workflows", key: "nav.workflows" },
  { href: "/settings/keys", key: "nav.keys" },
  { href: "/settings/billing", key: "nav.billing" },
];

export default function AppLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { me, org, loading, logout, selectOrg } = useAuth();
  const { t, lang, setLang } = useI18n();

  const onAdvancedPage = ADVANCED.some((item) => pathname.startsWith(item.href));
  const [advancedOpen, setAdvancedOpen] = useState(false);

  useEffect(() => {
    if (!loading && !me) router.replace("/login");
  }, [loading, me, router]);

  if (loading || !me || !org) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner className="h-6 w-6 text-ink-500" />
      </div>
    );
  }

  return (
    <div className="flex min-h-screen">
      <aside className="flex w-60 shrink-0 flex-col border-r border-ink-800 bg-ink-900/40">
        <div className="border-b border-ink-800 p-4">
          <p className="text-sm font-semibold text-ink-50">Agents Office</p>
          {me.orgs.length > 1 ? (
            <Select
              className="mt-3 text-xs"
              value={org.id}
              onChange={(event) => selectOrg(event.target.value)}
              aria-label="Organisation"
            >
              {me.orgs.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </Select>
          ) : (
            <p className="mt-1 truncate text-xs text-ink-500">{org.name}</p>
          )}
          <p className="mt-2 text-xs text-ink-500">
            {formatCents(org.credits_cents)} {t("nav.credits")}
          </p>
        </div>

        <nav className="flex-1 space-y-1 p-2">
          {MAIN.map((item) => {
            const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cx(
                  "flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors",
                  active
                    ? "bg-brand-500/15 text-brand-400"
                    : "text-ink-300 hover:bg-ink-800 hover:text-ink-100",
                )}
              >
                <span aria-hidden="true">{item.icon}</span>
                {t(item.key)}
              </Link>
            );
          })}

          <div className="pt-3">
            <button
              onClick={() => setAdvancedOpen((value) => !value)}
              className="flex w-full items-center gap-1 px-3 py-1 text-xs uppercase tracking-wide text-ink-600 hover:text-ink-400"
              aria-expanded={advancedOpen || onAdvancedPage}
            >
              <span aria-hidden="true">{advancedOpen || onAdvancedPage ? "▾" : "▸"}</span>
              {t("nav.advanced")}
            </button>
            {(advancedOpen || onAdvancedPage) && (
              <div className="mt-1 space-y-1">
                {ADVANCED.map((item) => {
                  const active = pathname.startsWith(item.href);
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      className={cx(
                        "block rounded-md px-3 py-1.5 text-sm transition-colors",
                        active
                          ? "bg-brand-500/15 text-brand-400"
                          : "text-ink-400 hover:bg-ink-800 hover:text-ink-100",
                      )}
                    >
                      {t(item.key)}
                    </Link>
                  );
                })}
              </div>
            )}
          </div>
        </nav>

        <div className="border-t border-ink-800 p-4">
          <div className="mb-3 flex gap-1" role="group" aria-label={t("nav.language")}>
            {(["ru", "en"] as const).map((code) => (
              <button
                key={code}
                onClick={() => setLang(code)}
                className={cx(
                  "rounded px-2 py-1 text-xs uppercase transition-colors",
                  lang === code
                    ? "bg-ink-800 text-ink-100"
                    : "text-ink-500 hover:bg-ink-800/60 hover:text-ink-300",
                )}
              >
                {code}
              </button>
            ))}
          </div>
          <p className="truncate text-xs text-ink-500">{me.email}</p>
          <button onClick={logout} className="mt-2 text-xs text-ink-400 hover:text-ink-100">
            {t("nav.signOut")}
          </button>
        </div>
      </aside>

      <main className="flex-1 overflow-x-hidden p-6">{children}</main>
    </div>
  );
}
