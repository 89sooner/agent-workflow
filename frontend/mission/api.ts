import { useEffect, useState } from "react";
export function endpoint(
  path: string,
  ledger: string,
  params: Record<string, string> = {},
) {
  const q = new URLSearchParams(params);
  if (ledger) q.set("ledger", ledger);
  return path + (q.size ? "?" + q : "");
}
export async function get<T>(
  url: string,
  signal?: AbortSignal,
  validate?: (v: unknown) => void,
): Promise<T> {
  const response = await fetch(url, { signal, cache: "no-store" });
  const value = await response.json();
  if (!response.ok)
    throw Error(
      typeof value?.error === "string"
        ? value.error
        : `조회 오류 ${response.status}`,
    );
  validate?.(value);
  return value as T;
}
export async function configure<T>(
  path: string,
  data: Record<string, unknown>,
  token: string,
  signal: AbortSignal,
): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Workflow-Token": token },
    body: JSON.stringify(data),
    signal,
  });
  const value = await response.json();
  if (!response.ok)
    throw Error(
      typeof value?.error === "string"
        ? value.error
        : `연결 설정 오류 ${response.status}`,
    );
  return value as T;
}
export function usePoll<T>(
  url: string | null,
  interval: number,
  refresh: number,
  validate?: (v: unknown) => void,
) {
  const [result, setResult] = useState<{
    url: string | null;
    data: T | null;
    error: string;
    at: number;
    loading: boolean;
    fresh: boolean;
  }>({ url: null, data: null, error: "", at: 0, loading: true, fresh: false });
  useEffect(() => {
    setResult((old) => ({
      url,
      data: old.url === url ? old.data : null,
      error: "",
      at: old.url === url ? old.at : 0,
      loading: !!url,
      fresh: false,
    }));
    if (!url) return;
    let disposed = false,
      timer: ReturnType<typeof setTimeout>,
      controller: AbortController | undefined;
    let failures = 0,
      generation = 0;
    async function poll() {
      clearTimeout(timer);
      controller?.abort();
      const ticket = ++generation,
        request = new AbortController();
      controller = request;
      const timeout = setTimeout(() => request.abort(), 12000);
      try {
        const data = await get<T>(url!, request.signal, validate);
        if (!disposed && ticket === generation) {
          failures = 0;
          setResult({
            url,
            data,
            error: "",
            at: Date.now(),
            loading: false,
            fresh: true,
          });
        }
      } catch (e) {
        if (!disposed && ticket === generation) {
          failures++;
          setResult((old) => ({
            url,
            data: old.url === url ? old.data : null,
            error: request.signal.aborted
              ? "응답 시간이 초과되었습니다."
              : e instanceof Error
                ? e.message
                : "연결 오류",
            at: old.url === url ? old.at : 0,
            loading: false,
            fresh: false,
          }));
        }
      } finally {
        clearTimeout(timeout);
        if (!disposed && ticket === generation && !document.hidden)
          timer = setTimeout(
            poll,
            Math.min(30000, interval * 2 ** Math.min(failures, 3)),
          );
      }
    }
    const visibility = () => {
      clearTimeout(timer);
      if (document.hidden) {
        ++generation;
        controller?.abort();
        // Retain the records, but revalidate activity after returning to the tab.
        setResult((old) => ({ ...old, fresh: false }));
      } else void poll();
    };
    if (!document.hidden) void poll();
    document.addEventListener("visibilitychange", visibility);
    return () => {
      disposed = true;
      ++generation;
      clearTimeout(timer);
      controller?.abort();
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [url, interval, refresh, validate]);
  return result.url === url
    ? result
    : { url, data: null, error: "", at: 0, loading: !!url, fresh: false };
}
export function useVisible() {
  const [visible, set] = useState(!document.hidden);
  useEffect(() => {
    const update = () => set(!document.hidden);
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  return visible;
}
export function useReducedMotion() {
  const [reduced, set] = useState(
    matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  useEffect(() => {
    const q = matchMedia("(prefers-reduced-motion: reduce)"),
      update = () => set(q.matches);
    q.addEventListener("change", update);
    return () => q.removeEventListener("change", update);
  }, []);
  return reduced;
}
