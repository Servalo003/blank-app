import { useState } from "react";
import { api, fmtTs, type Snapshot, type Watchlist } from "../api";
import { DirBadge, ErrorLine, ProvBadge, useApi } from "../common";

export default function Scanner() {
  const snaps = useApi<Snapshot[]>("/api/scan/snapshots", 30000);
  const wls = useApi<Watchlist[]>("/api/watchlists");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function runNow() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api<{ results: { symbol: string; signals: unknown[]; error?: string }[] }>("/api/scan/run", { method: "POST" });
      const n = r.results.reduce((a, x) => a + (x.signals?.length ?? 0), 0);
      const errs = r.results.filter((x) => x.error).map((x) => `${x.symbol}: ${x.error}`);
      setMsg(`Scanned ${r.results.length} items, ${n} new signal(s).${errs.length ? " Errors: " + errs.join("; ") : ""}`);
      snaps.reload();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const groups = new Map<string, Snapshot[]>();
  for (const s of snaps.data ?? []) {
    const k = `${s.symbol} ${s.timeframe}`;
    groups.set(k, [...(groups.get(k) ?? []), s]);
  }

  return (
    <section className="stack">
      <div className="row between">
        <h2>Scanner</h2>
        <button className="btn" onClick={runNow} disabled={busy}>{busy ? "Scanning…" : "Scan now"}</button>
      </div>
      <p className="small muted">
        Latest evaluation of each enabled strategy on the most recent <b>closed</b> bar. Watchlists:{" "}
        {wls.data?.map((w) => `${w.name} (${w.items.length})`).join(", ") ?? "…"}
      </p>
      {msg && <p className="small">{msg}</p>}
      <ErrorLine error={snaps.error} />
      {groups.size === 0 && !snaps.error && <p className="muted">No scans yet. Press “Scan now” or start the worker.</p>}
      <div className="grid">
        {[...groups.entries()].map(([k, rows]) => {
          const first = rows[0];
          return (
            <article className="card" key={k}>
              <div className="row between">
                <b>{k}</b>
                <ProvBadge p={first.provenance} />
              </div>
              <div className="small muted">
                Bar {fmtTs(first.bar_ts)} · {first.regime.trend} / {first.regime.volatility} · data{" "}
                <span className={first.data_quality.ok ? "ok" : "bad"}>{first.data_quality.ok ? "OK" : "POOR"}</span>
              </div>
              {!first.data_quality.ok && <div className="small warn">{first.data_quality.issues.join("; ")}</div>}
              <ul className="snap-list">
                {rows.map((r) => (
                  <li key={r.strategy}>
                    <div className="row between">
                      <span className="small"><b>{r.strategy}</b></span>
                      <DirBadge d={r.direction} />
                    </div>
                    <div className="small muted">{r.reasons[0]}</div>
                  </li>
                ))}
              </ul>
            </article>
          );
        })}
      </div>
    </section>
  );
}
