import { useState } from "react";
import { api, type Signal, type Snapshot, type Status } from "../api";
import { Empty, ErrorLine, Icon, PROV_LABEL, ProvBadge, fmtR, strategyName, timeAgo, useApi } from "../common";
import { SignalCard } from "./Signals";

function parseJson<T>(s: string | null): T | null {
  if (!s) return null;
  try {
    return JSON.parse(s) as T;
  } catch {
    return null;
  }
}

interface Agg { closed: number; wins: number; total_r: number; avg_r: number | null }
interface Perf { days: number; overall: Agg; paper_alerts: Agg; by_strategy: Record<string, Agg>; note: string }

function PerformanceCard() {
  const [days, setDays] = useState(7);
  const { data } = useApi<Perf>(`/api/performance?days=${days}`, 120000);
  const o = data?.overall;
  return (
    <div className="card stack-sm">
      <div className="row between">
        <b>Paper results</b>
        <div className="seg" role="group" aria-label="Period" style={{ padding: 2 }}>
          {[7, 30].map((d) => <button key={d} aria-pressed={days === d} onClick={() => setDays(d)} style={{ padding: "4px 10px" }}>{d}d</button>)}
        </div>
      </div>
      {!o || o.closed === 0 ? (
        <p className="small muted" style={{ margin: 0 }}>No ideas closed in the last {days} days yet. Results appear here once ideas hit their stop, target or expiry.</p>
      ) : (
        <>
          <div className="kv-grid">
            <div><span>Closed</span><b>{o.closed}</b></div>
            <div><span>Wins</span><b>{o.wins}/{o.closed}</b></div>
            <div><span>Total</span><b className={o.total_r >= 0 ? "ok" : "bad"}>{fmtR(o.total_r)}</b></div>
            <div><span>Per idea</span><b className={(o.avg_r ?? 0) >= 0 ? "ok" : "bad"}>{fmtR(o.avg_r)}</b></div>
          </div>
          <table className="kv">
            <tbody>
              {Object.entries(data!.by_strategy).map(([k, a]) => (
                <tr key={k}><td>{strategyName(k)}</td><td className="num">{a.wins}/{a.closed}</td><td className={`num ${a.total_r >= 0 ? "ok" : "bad"}`}>{fmtR(a.total_r)}</td></tr>
              ))}
            </tbody>
          </table>
          <p className="tiny faint" style={{ margin: 0 }}>{data!.note} R = amount risked.</p>
        </>
      )}
    </div>
  );
}

function SystemCard({ status }: { status: Status }) {
  const hb = parseJson<{ ts: number; next_scan_at: number }>(status.worker_heartbeat);
  const workerOk = hb !== null && Date.now() / 1000 - hb.ts < 120;
  const killed = Boolean(status.risk.kill_switch);
  const tone = killed ? "stop" : workerOk ? "good" : "attn";
  const title = killed ? "Paused — kill switch is on" : workerOk ? "Watching the markets" : "Scanner is not running";
  const sub = killed
    ? "No new signals will be created until you release it in Settings."
    : workerOk
      ? `Next automatic scan ${hb ? timeAgo(hb.next_scan_at) : "soon"}.`
      : "Start the worker to scan automatically, or use “Scan now” below.";
  return (
    <div className="card hero">
      <div className={`light ${tone}`}><Icon name={killed ? "stop" : workerOk ? "check" : "alert"} /></div>
      <div style={{ flex: 1 }}>
        <b>{title}</b>
        <span className="small muted">{sub}</span>
      </div>
    </div>
  );
}

export default function Home({ status, goSignal, goTab }: { status: Status | null; goSignal: (id: string) => void; goTab: (t: string) => void }) {
  const active = useApi<Signal[]>("/api/signals?status=ACTIVE&limit=20", 20000);
  const recent = useApi<Signal[]>("/api/signals?limit=50", 60000);
  const snaps = useApi<Snapshot[]>("/api/scan/snapshots", 30000);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const markets = new Set((snaps.data ?? []).map((s) => `${s.symbol} ${s.timeframe}`)).size;
  const closed = (recent.data ?? []).filter((s) => s.outcome_r !== null);
  const wins = closed.filter((s) => (s.outcome_r ?? 0) > 0).length;
  const lastScan = parseJson<{ ts: number }>(status?.last_scan ?? null);
  const lastSnap = (snaps.data ?? []).reduce<string | null>((a, s) => (!a || s.updated_at > a ? s.updated_at : a), null);

  async function scanNow() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api<{ results: { signals?: unknown[]; error?: string }[] }>("/api/scan/run", { method: "POST" });
      const n = r.results.reduce((a, x) => a + (x.signals?.length ?? 0), 0);
      const errs = r.results.filter((x) => x.error).length;
      setMsg(n ? `Found ${n} new signal${n > 1 ? "s" : ""}.` : "Scan complete — no new setups. That’s normal.");
      if (errs) setMsg((m) => `${m} ${errs} market(s) could not be scanned.`);
      active.reload();
      snaps.reload();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="stack">
      <div>
        <h1 className="page">Today</h1>
        <p className="lead">
          {status ? <>Using <b>{PROV_LABEL[status.provenance].toLowerCase()}</b>. </> : null}
          {lastScan ? `Last scan ${timeAgo(lastScan.ts)}.` : lastSnap ? `Last scan ${timeAgo(lastSnap)}.` : "No scans yet."}
        </p>
      </div>

      {status && <SystemCard status={status} />}
      {status?.provenance === "SIMULATED_DATA" && (
        <div className="note warn"><b>Demo mode:</b> prices are simulated, not real market data. Use it to learn the app; ignore the results.</div>
      )}

      <div className="stats">
        <div className="stat"><b>{active.data?.length ?? "–"}</b><span>open signals</span></div>
        <div className="stat"><b>{markets || "–"}</b><span>markets watched</span></div>
        <div className="stat"><b>{closed.length ? `${wins}/${closed.length}` : "–"}</b><span>recent paper wins</span></div>
      </div>

      <button className="btn block" onClick={scanNow} disabled={busy}>
        <Icon name="refresh" className={busy ? "spin" : undefined} /> {busy ? "Scanning…" : "Scan now"}
      </button>
      {msg && <p className="small muted center" style={{ margin: "6px 0 0" }}>{msg}</p>}

      <PerformanceCard />

      <div className="row between">
        <h3 className="section" style={{ margin: 0, flex: 1 }}>Open signals</h3>
        <button className="btn link" onClick={() => goTab("signals")}>See all</button>
      </div>
      <ErrorLine error={active.error} />
      {active.data && active.data.length === 0 && (
        <Empty icon="check" title="Nothing to act on">
          No setup currently meets every rule. The app will alert you when one does.
        </Empty>
      )}
      <div className="grid">{active.data?.slice(0, 4).map((s) => <SignalCard key={s.id} s={s} onOpen={goSignal} />)}</div>

      <details className="card">
        <summary>How this app works</summary>
        <ol className="small muted" style={{ paddingLeft: 18, marginBottom: 0 }}>
          <li><b>Scan:</b> after every candle closes, each market on your watchlist is checked by four simple, rule-based strategies.</li>
          <li><b>Check:</b> any idea must pass the risk rules (data quality, position limits, daily loss limit, kill switch).</li>
          <li><b>Alert:</b> you get a phone notification with the plan — price, stop and target — and later when it’s reached, cancelled or expired.</li>
          <li><b>Test:</b> under <b>Research</b>, strategies are tested on past data before they’re trusted for paper alerts.</li>
        </ol>
        <p className="small muted">Nothing is ever traded for you. “No trade” is a normal, often correct, answer.</p>
      </details>
      {status && <div className="row" style={{ justifyContent: "center" }}><ProvBadge p={status.provenance} /></div>}
    </section>
  );
}
