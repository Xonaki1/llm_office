"use client";

import { useEffect, useRef } from "react";

import { toolLabel, useT } from "@/lib/i18n";
import type { FeedItem, OfficeSim } from "@/lib/office/sim";
import { useSimVersion } from "@/lib/office/use-sim";

/**
 * What is happening, in words a person can read.
 *
 * The same events that move the characters also produce this list; it is the
 * plain-language mirror of the animation, and the only running commentary a
 * non-technical user needs.
 */
export function OfficeFeed({ sim, className }: { sim: OfficeSim; className?: string }) {
  useSimVersion(sim);
  const t = useT();
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  });

  const items = sim.feed.slice(-60);

  return (
    <div className={className}>
      <ol className="space-y-1.5">
        {items.map((item) => (
          <li key={item.id} className="flex items-start gap-2 text-sm text-ink-300">
            <span
              className="mt-1.5 h-2 w-2 shrink-0 rounded-full"
              style={{ background: item.accent }}
              aria-hidden="true"
            />
            <span>{line(item, t)}</span>
          </li>
        ))}
      </ol>
      <div ref={bottomRef} />
    </div>
  );
}

function line(item: FeedItem, t: ReturnType<typeof useT>): string {
  const detail = item.kind === "tool" ? toolLabel(t, item.detail ?? "") : (item.detail ?? "");
  return t(`feed.${item.kind}`, { agent: item.agent, detail });
}
