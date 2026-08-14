"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export interface Loader {
  /** Message from the most recent failed load, or null. */
  error: string | null;
  setError: (message: string | null) => void;
  /** Re-run the loader, e.g. after a mutation. */
  reload: () => Promise<void>;
  loading: boolean;
}

/**
 * Run an async loader on mount and whenever it changes.
 *
 * The result is discarded if the component unmounted while the request was in
 * flight, which is both a real bug fix — a late `setState` on an unmounted
 * component — and what keeps the effect body free of synchronous state updates.
 */
export function useLoader(load: () => Promise<void>): Loader {
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const mounted = useRef(true);

  const reload = useCallback(async () => {
    try {
      await load();
      if (mounted.current) setError(null);
    } catch (err) {
      if (mounted.current) {
        setError(err instanceof Error ? err.message : "something went wrong");
      }
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, [load]);

  useEffect(() => {
    mounted.current = true;
    // `reload` is async: nothing is set before the first await, so there is no
    // cascading render, but the rule cannot see through the async boundary.
    // Fetching on mount from a client component does need an effect, and
    // centralising it here means the exemption exists once rather than in
    // every page.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void reload();
    return () => {
      mounted.current = false;
    };
  }, [reload]);

  return { error, setError, reload, loading };
}
