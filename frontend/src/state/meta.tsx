import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api } from "../api/client";
import type { MetaResponse } from "../api/types";

interface MetaContextValue {
  meta: MetaResponse | null;
  loading: boolean;
  error: unknown | null;
  reload: () => void;
}

const MetaContext = createContext<MetaContextValue | null>(null);

export function MetaProvider({ children }: { children: ReactNode }) {
  const [meta, setMeta] = useState<MetaResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    api
      .meta(controller.signal)
      .then((value) => {
        setMeta(value);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setError(caught);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);
  const value = useMemo(
    () => ({ meta, loading, error, reload }),
    [meta, loading, error, reload],
  );

  return <MetaContext.Provider value={value}>{children}</MetaContext.Provider>;
}

export function useMeta(): MetaContextValue {
  const value = useContext(MetaContext);
  if (!value) throw new Error("useMeta must be used inside MetaProvider");
  return value;
}
