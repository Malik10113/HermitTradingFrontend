import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { RunDetail, RunStatus } from "../api/types";

const TERMINAL: RunStatus[] = ["complete", "failed", "cancelled", "orphaned"];

export function isTerminal(status: string | null | undefined): boolean {
  return !!status && (TERMINAL as string[]).includes(status);
}

export interface RunState {
  run: RunDetail | null;
  loading: boolean;
  error: unknown | null;
  refresh: () => Promise<void>;
  reveal: () => Promise<void>;
  revealing: boolean;
  revealError: unknown | null;
}

/**
 * Fetch a run, then follow its progress over SSE when available. A slow poll
 * remains as the fallback so the UI never spins in a tight loop.
 */
export function useRun(runId: string | null): RunState {
  const [run, setRun] = useState<RunDetail | null>(null);
  const [loading, setLoading] = useState(!!runId);
  const [error, setError] = useState<unknown | null>(null);
  const [revealing, setRevealing] = useState(false);
  const [revealError, setRevealError] = useState<unknown | null>(null);
  const statusRef = useRef<string | null>(null);
  statusRef.current = run?.status ?? null;

  const load = useCallback(
    async (signal?: AbortSignal) => {
      if (!runId) return;
      const next = await api.run(runId, signal);
      setRun((previous) => ({ ...(previous ?? {}), ...next }) as RunDetail);
      setError(null);
    },
    [runId],
  );

  useEffect(() => {
    setRun(null);
    setError(null);
    if (!runId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    let cancelled = false;
    let timer: number | undefined;
    let source: EventSource | null = null;
    const controller = new AbortController();

    const schedule = () => {
      if (cancelled) return;
      if (isTerminal(statusRef.current)) {
        setLoading(false);
        return;
      }
      const delay = statusRef.current === "queued" ? 5000 : 2500;
      timer = window.setTimeout(() => {
        load(controller.signal)
          .catch((caught: unknown) => {
            if (!cancelled) setError(caught);
          })
          .finally(() => {
            setLoading(false);
            schedule();
          });
      }, delay);
    };

    load(controller.signal)
      .catch((caught: unknown) => {
        if (!cancelled) setError(caught);
      })
      .finally(() => {
        if (cancelled) return;
        setLoading(false);
        schedule();
      });

    if (typeof EventSource !== "undefined") {
      try {
        source = new EventSource(api.eventsUrl(runId));
        source.onmessage = (message) => {
          try {
            const payload = JSON.parse(message.data) as Record<string, unknown>;
            const raw = (payload.run ?? payload) as Partial<RunDetail>;
            setRun((previous) => ({ ...(previous ?? {}), ...raw }) as RunDetail);
            if (isTerminal(raw.status)) {
              source?.close();
              if (timer) window.clearTimeout(timer);
            }
          } catch {
            // Ignore keep-alives and malformed frames; polling still applies.
          }
        };
        source.onerror = () => {
          source?.close();
          source = null;
        };
      } catch {
        source = null;
      }
    }

    return () => {
      cancelled = true;
      controller.abort();
      if (timer) window.clearTimeout(timer);
      source?.close();
    };
  }, [runId, load]);

  const refresh = useCallback(async () => {
    try {
      await load();
    } catch (caught) {
      setError(caught);
    }
  }, [load]);

  const reveal = useCallback(async () => {
    if (!runId) return;
    setRevealing(true);
    setRevealError(null);
    try {
      const next = await api.revealHoldout(runId);
      setRun((previous) => ({ ...(previous ?? {}), ...next }) as RunDetail);
    } catch (caught) {
      setRevealError(caught);
    } finally {
      setRevealing(false);
    }
  }, [runId]);

  return { run, loading, error, refresh, reveal, revealing, revealError };
}
