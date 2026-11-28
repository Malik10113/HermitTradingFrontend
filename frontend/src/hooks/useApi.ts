import { useCallback, useEffect, useRef, useState } from "react";

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: unknown | null;
  reload: () => void;
}

/**
 * A fetch-on-mount resource with AbortController cleanup. `deps` controls when
 * the request is repeated; callers pass primitive URL state values.
 */
export function useApiResource<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  deps: unknown[],
  enabled = true,
): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<unknown | null>(null);
  const [nonce, setNonce] = useState(0);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  useEffect(() => {
    if (!enabled) {
      // A disabled resource must not keep presenting the previous result: the
      // consumer would show stale preview data after the last indicator is
      // removed, or another instrument's data after the selection changes.
      setData(null);
      setError(null);
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    loaderRef
      .current(controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) {
          setData(value);
          setLoading(false);
        }
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        // A failed fetch for the current deps must not sit next to a previous
        // resource's payload.
        setData(null);
        setError(caught);
        setLoading(false);
      });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, enabled, nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);
  return { data, loading, error, reload };
}

export function useDebouncedValue<T>(value: T, delayMs = 250): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const handle = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(handle);
  }, [value, delayMs]);
  return debounced;
}
