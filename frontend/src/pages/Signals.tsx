import { useState } from "react";
import { fmtNum, fmtPrice, fmtTs, type Signal } from "../api";
import { DirBadge, ErrorLine, ProvBadge, useApi } from "../common";

const STATUS_FILTERS = ["", "ACTIVE", "SUPPRESSED", "TARGET_HIT", "STOP_HIT", "INVALIDATED", "EXPIRED"];

export function SignalCard({ s, onOpen }: { s: Signal; onOpen: (id: string) => void }) {
  return (
    <article className="card">
      <div className="row between">
        <div className="row">
          <DirBadge d={s.direction} />
          <b>{s.symbol}</b>
          <span className="muted small">{s.timeframe}</span>
        </div>
        <ProvBadge p={s.provenance} />
      </div>
      <div className="small muted" style={{ marginTop: 6 }}>
        {s.strategy} · {s.status} · {s.alert_level === "ACTIONABLE" ? "paper alert" : s.alert_level.toLowerCase().replace("_", " ")} · risk{" "}
        <span className={s.risk_status === "APPROVED" ? "ok" : "warn"}>{s.risk_status}</span>
      </div>
      <div className="levels num">
        <div><span>Ref</span>{fmtPrice(s.entry_ref)}</div>
        <div><span>Stop</span>{fmtPrice(s.stop)}</div>
        <div><span>Target</span>{fmtPrice(s.target)}</div>
      </div>
      <div className="small muted">Bar closed {fmtTs(s.signal_ts)} · strength {fmtNum(s.score)}</div>
      <ul className="reasons small">{s.reasons.slice(0, 3).map((r, i) => <li key={i}>{r}</li>)}</ul>
      {s.outcome_r !== null && <div className="small">Paper outcome: <b className={s.outcome_r >= 0 ? "ok" : "bad"}>{fmtNum(s.outcome_r)} R</b></div>}
      <button className="btn secondary" style={{ marginTop: 8 }} onClick={() => onOpen(s.id)}>Details</button>
    </article>
  );
}

export function SignalDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const { data: s, error } = useApi<Signal>(`/api/signals/${id}`);
  return (
    <div className="card stack">
      <div className="row between">
        <h2>Signal detail</h2>
        <button className="btn secondary" onClick={onClose}>Close</button>
      </div>
      <ErrorLine error={error} />
      {s && (
        <>
          <div className="row">
            <DirBadge d={s.direction} /> <b>{s.symbol}</b> {s.timeframe} <ProvBadge p={s.provenance} />
          </div>
          <div className="small muted">{s.strategy} · status {s.status}{s.close_reason ? ` (${s.close_reason})` : ""}</div>
          <div className="levels num">
            <div><span>Ref (signal close)</span>{fmtPrice(s.entry_ref)}</div>
            <div><span>Stop</span>{fmtPrice(s.stop)}</div>
            <div><span>Target</span>{fmtPrice(s.target)}</div>
            <div><span>Invalidation</span>{fmtPrice(s.invalidation)}</div>
            <div><span>Entry window until</span>{fmtTs(s.entry_window_until).slice(5)}</div>
            <div><span>Expires</span>{fmtTs(s.expires_at).slice(5)}</div>
          </div>
          <h3>Why</h3>
          <ul className="reasons small">{s.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
          <h3>Risk engine</h3>
          <div className="small">
            {s.risk_status} · alert level {s.alert_level}
            {s.risk && s.risk_status === "APPROVED" && <> · est. risk {fmtNum(s.risk.risk_amount)} ({fmtNum(s.risk.risk_pct)}% of paper equity)</>}
          </div>
          <div className="table-wrap">
            <table>
              <tbody>
                {s.risk?.checks.map((c) => (
                  <tr key={c.name}><td>{c.passed ? <span className="ok">✓</span> : <span className="bad">✗</span>}</td><td>{c.name}</td><td className="muted">{c.detail}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
          <h3>Regime &amp; fundamentals</h3>
          <div className="small">Trend {s.regime.trend} · volatility {s.regime.volatility}</div>
          <div className="small muted">Fundamentals: {String(s.fundamentals?.status ?? "—")} — {String(s.fundamentals?.reason ?? s.fundamentals?.block ?? "")}</div>
          <h3>Indicators at signal bar</h3>
          <div className="table-wrap">
            <table><tbody>{Object.entries(s.indicators ?? {}).map(([k, v]) => <tr key={k}><td>{k}</td><td className="num">{typeof v === "number" ? fmtPrice(v) : String(v)}</td></tr>)}</tbody></table>
          </div>
          <h3>Lifecycle &amp; alerts</h3>
          <ul className="reasons small">{s.events?.map((e, i) => <li key={i}>{e.ts.slice(0, 16)} {e.kind} {e.detail}</li>)}</ul>
          <ul className="reasons small">{s.deliveries?.map((d, i) => <li key={i}>{d.event} → {d.channel}: {d.status}{d.error ? ` (${d.error})` : ""}</li>)}</ul>
          <details>
            <summary>Audit fingerprints</summary>
            <div className="small mono" style={{ wordBreak: "break-all" }}>
              params {s.params_hash}<br />dataset {s.dataset_hash}<br />risk config {s.risk_config_hash}<br />code {s.code_version}
            </div>
            <pre className="small mono" style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(s.params, null, 1)}</pre>
          </details>
        </>
      )}
    </div>
  );
}

export default function Signals({ openId, setOpenId }: { openId: string | null; setOpenId: (id: string | null) => void }) {
  const [status, setStatus] = useState("ACTIVE");
  const { data, error, loading } = useApi<Signal[]>(`/api/signals?limit=100${status ? `&status=${status}` : ""}`, 20000);
  if (openId) return <SignalDetail id={openId} onClose={() => setOpenId(null)} />;
  return (
    <section className="stack">
      <div className="row between">
        <h2>Signals</h2>
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status filter">
          {STATUS_FILTERS.map((s) => <option key={s} value={s}>{s || "All statuses"}</option>)}
        </select>
      </div>
      <ErrorLine error={error} />
      {data && data.length === 0 && (
        <p className="muted">No signals {status ? `with status ${status}` : ""}. That is normal: most bars are NO_TRADE. See the Scanner for current reasons.</p>
      )}
      {loading && !data && <p className="muted">Loading…</p>}
      <div className="grid">{data?.map((s) => <SignalCard key={s.id} s={s} onOpen={setOpenId} />)}</div>
    </section>
  );
}
