import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Provenance } from "./api";

export function useApi<T>(path: string | null, refreshMs = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const alive = useRef(true);
  const load = useCallback(async () => {
    if (!path) return;
    setLoading(true);
    try {
      const d = await api<T>(path);
      if (alive.current) {
        setData(d);
        setError(null);
      }
    } catch (e) {
      if (alive.current) setError((e as Error).message);
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [path]);
  useEffect(() => {
    alive.current = true;
    load();
    if (!refreshMs) return () => { alive.current = false; };
    const t = setInterval(load, refreshMs);
    return () => {
      alive.current = false;
      clearInterval(t);
    };
  }, [load, refreshMs]);
  return { data, error, loading, reload: load };
}

const PROV_LABEL: Record<Provenance, string> = {
  LIVE_DATA: "LIVE",
  DELAYED_DATA: "DELAYED",
  SIMULATED_DATA: "SIMULATED",
};

export function ProvBadge({ p }: { p: Provenance }) {
  return (
    <span className={`badge prov-${p}`} title={p}>
      ● {PROV_LABEL[p] ?? p}
    </span>
  );
}

export function DirBadge({ d }: { d: string }) {
  const sym = d === "LONG" ? "▲" : d === "SHORT" ? "▼" : "—";
  return <span className={`badge dir-${d}`}>{sym} {d.replace("_", " ")}</span>;
}

export function ErrorLine({ error }: { error: string | null }) {
  return error ? <p className="error" role="alert">{error}</p> : null;
}

export function Disclaimer() {
  return (
    <div className="banner">
      <strong>Research &amp; paper mode.</strong> Decision support only, not financial advice. No orders are placed.
      Signals can be wrong; NO_TRADE is a normal outcome. Strength scores are rankings, not probabilities.
    </div>
  );
}
