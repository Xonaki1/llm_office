"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { useAuth } from "@/lib/auth";
import { Button, ErrorBanner, Field, Input } from "@/components/ui";

export default function LoginPage() {
  const router = useRouter();
  const { login, me, loading } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!loading && me) router.replace("/runs");
  }, [loading, me, router]);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(email, password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "sign-in failed");
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
          <h1 className="text-lg font-semibold text-ink-50">Sign in</h1>
          <p className="mt-1 text-sm text-ink-500">Agents Office</p>
        </div>

        <ErrorBanner message={error} />

        <Field label="Email">
          <Input
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </Field>

        <Field label="Password">
          <Input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>

        <Button type="submit" loading={submitting} className="w-full">
          Sign in
        </Button>

        <p className="text-center text-sm text-ink-500">
          No account?{" "}
          <Link href="/register" className="text-brand-400 hover:underline">
            Create one
          </Link>
        </p>
      </form>
    </main>
  );
}
