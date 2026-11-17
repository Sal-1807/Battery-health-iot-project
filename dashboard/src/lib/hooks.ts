import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";

export interface Polled<T> {
  data: T | undefined;
  error: ApiError | null;
  loading: boolean; // true only until the first response (success or failure)
  refreshing: boolean;
  reload: () => void;
  updatedAt: number | null;
}

/**
 * Fetch `fn` now and every `intervalMs` (0 = once). Keeps the previous data
 * while refetching so charts hold their frame instead of flashing.
 */
export function usePoll<T>(fn: (() => Promise<T>) | null, intervalMs: number, deps: unknown[]): Polled<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const gen = useRef(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const run = useCallback(async () => {
    const f = fnRef.current;
    if (!f) return;
    const my = ++gen.current;
    setRefreshing(true);
    try {
      const d = await f();
      if (my !== gen.current) return; // a newer request superseded this one
      setData(d);
      setError(null);
      setUpdatedAt(Date.now());
    } catch (e) {
      if (my !== gen.current) return;
      setError(e instanceof ApiError ? e : new ApiError(0, String(e)));
    } finally {
      if (my === gen.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []);

  useEffect(() => {
    setData(undefined);
    setError(null);
    setLoading(true);
    if (!fnRef.current) {
      setLoading(false);
      return;
    }
    run();
    if (!intervalMs) return;
    const t = setInterval(() => {
      if (document.visibilityState !== "hidden") run();
    }, intervalMs);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, ...deps]);

  return { data, error, loading, refreshing, reload: run, updatedAt };
}

export function useLocalStorage(key: string, initial: string): [string, (v: string) => void] {
  const [value, setValue] = useState<string>(() => {
    try {
      return localStorage.getItem(key) ?? initial;
    } catch {
      return initial;
    }
  });
  const set = useCallback(
    (v: string) => {
      setValue(v);
      try {
        localStorage.setItem(key, v);
      } catch {
        /* storage unavailable (private mode) - keep in memory only */
      }
    },
    [key],
  );
  return [value, set];
}
