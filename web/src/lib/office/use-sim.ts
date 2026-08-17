"use client";

import { useEffect, useState } from "react";

import { OfficeSim } from "@/lib/office/sim";

/** One simulation per mounted view, created lazily and kept for its lifetime. */
export function useOfficeSim(): OfficeSim {
  const [sim] = useState(() => new OfficeSim());
  return sim;
}

/**
 * Re-render on discrete changes only.
 *
 * The simulation rate-limits its own notifications to ten a second, and
 * positions never go through React at all, so this stays cheap even while a
 * model is streaming tokens.
 */
export function useSimVersion(sim: OfficeSim): number {
  const [version, setVersion] = useState(0);
  useEffect(() => sim.subscribe(() => setVersion((value) => value + 1)), [sim]);
  return version;
}
