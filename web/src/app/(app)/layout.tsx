"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { useAuth } from "@/lib/auth";
import { formatCents } from "@/lib/format";
import { Select, Spinner, cx } from "@/components/ui";

const NAV = [
  { href: "/runs", label: "Runs" },
  { href: "/workflows", label: "Workflows" },
  { href: "/agents", label: "Agents" },
  { href: "/settings/keys", label: "Provider keys" },
  { href: "/settings/billing", label: "Billing" },
];

export default function AppLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { me, org, loading, logout, selectOrg } = useAuth();

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
              onChange={(e) => selectOrg(e.target.value)}
              aria-label="Organisation"
            >
              {me.orgs.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </Select>
          ) : (
            <p className="mt-1 truncate text-xs text-ink-500">{org.name}</p>
          )}
          <p className="mt-2 text-xs text-ink-500">
            {formatCents(org.credits_cents)} credit · {org.role}
          </p>
        </div>

        <nav className="flex-1 space-y-1 p-2">
          {NAV.map((item) => {
            const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cx(
                  "block rounded-md px-3 py-2 text-sm transition-colors",
                  active
                    ? "bg-brand-500/15 text-brand-400"
                    : "text-ink-300 hover:bg-ink-800 hover:text-ink-100",
                )}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="border-t border-ink-800 p-4">
          <p className="truncate text-xs text-ink-500">{me.email}</p>
          <button
            onClick={logout}
            className="mt-2 text-xs text-ink-400 hover:text-ink-100"
          >
            Sign out
          </button>
        </div>
      </aside>

      <main className="flex-1 overflow-x-hidden p-6">{children}</main>
    </div>
  );
}
