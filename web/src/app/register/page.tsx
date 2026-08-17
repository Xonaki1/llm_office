"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { useAuth } from "@/lib/auth";
import { useT } from "@/lib/i18n";
import { Button, ErrorBanner, Field, Input } from "@/components/ui";

const MIN_PASSWORD_LENGTH = 12;

export default function RegisterPage() {
  const t = useT();
  const { register } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [orgName, setOrgName] = useState(() => t("auth.defaultOrgName"));
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const tooShort = password.length > 0 && password.length < MIN_PASSWORD_LENGTH;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await register(email, password, orgName);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("auth.signUpFailed"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-sm space-y-4 rounded-lg border border-ink-800 bg-ink-900/60 p-6"
      >
        <div>
          <h1 className="text-lg font-semibold text-ink-50">{t("auth.signUp")}</h1>
          <p className="mt-1 text-sm text-ink-500">{t("auth.signUpSubtitle")}</p>
        </div>

        <ErrorBanner message={error} />

        <Field label={t("auth.email")}>
          <Input
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </Field>

        <Field
          label={t("auth.password")}
          hint={t("auth.passwordHint", { min: MIN_PASSWORD_LENGTH })}
        >
          <Input
            type="password"
            autoComplete="new-password"
            required
            minLength={MIN_PASSWORD_LENGTH}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>

        <Field label={t("auth.orgName")}>
          <Input required value={orgName} onChange={(e) => setOrgName(e.target.value)} />
        </Field>

        <Button type="submit" loading={submitting} disabled={tooShort} className="w-full">
          {t("auth.signUpAction")}
        </Button>

        <p className="text-center text-sm text-ink-500">
          <Link href="/login" className="text-brand-400 hover:underline">
            {t("auth.toSignIn")}
          </Link>
        </p>
      </form>
    </main>
  );
}
