"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { useAuth } from "@/lib/auth";
import { Spinner } from "@/components/ui";

export default function IndexPage() {
  const router = useRouter();
  const { me, loading } = useAuth();

  useEffect(() => {
    if (loading) return;
    router.replace(me ? "/runs" : "/login");
  }, [loading, me, router]);

  return (
    <div className="flex min-h-screen items-center justify-center">
      <Spinner className="h-6 w-6 text-ink-500" />
    </div>
  );
}
