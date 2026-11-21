import { useState } from "react";
import { fmtNum, fmtPrice, fmtTs, type Signal } from "../api";
import {
  ALERT_LABEL,
  Back,
  DIR_LABEL,
  DirIcon,
  Empty,
  ErrorLine,
  Help,
  PageHead,
  ProvBadge,
  STATUS_LABEL,
  TREND_LABEL,
  VOL_LABEL,
  strategyName,
  timeAgo,
  useApi,
} from "../common";

const FILTERS: [string, string][] = [
  ["ACTIVE", "Open"],
  ["", "All"],
  ["TARGET_HIT", "Target reached"],
  ["STOP_HIT", "Stopped out"],
  ["INVALIDATED", "Cancelled"],
  ["EXPIRED", "Expired"],
  ["SUPPRESSED", "Blocked"],
];

function statusTone(s: string): string {
  if (s === "TARGET_HIT") return "long";
  if (s === "STOP_HIT" || s === "SUPPRESSED") return "short";
  if (s === "ACTIVE") return "accent";
  return "";
}

export function Plan({ s }: { s: Signal }) {
  return (
    <div className="plan">
      <div><span>Price at signal</span><b>{fmtPrice(s.entry_ref)}</b></div>
      <div className="stop"><span>Stop</span><b>{fmtPrice(s.stop)}</b></div>
      <div className="target"><span>Target</span><b>{fmtPrice(s.target)}</b></div>
    </div>
  );
}

export function SignalCard({ s, onOpen }: { s: Signal; onOpen: (id: string) => void }) {
  return (
    <article className="card tap" onClick={() => onOpen(s.id)} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && onOpen(s.id)}>
      <div className="sig-head">
        <DirIcon d={s.direction} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="sig-title">{DIR_LABEL[s.direction]} · {s.symbol}</div>
          <div className="small muted">{strategyName(s.strategy)} · {s.timeframe} chart · {timeAgo(s.signal_ts)}</div>
        </div>
        <span className={`pill ${statusTone(s.status)}`}>{STATUS_LABEL[s.status] ?? s.status}</span>
      </div>
      <Plan s={s} />
      <ul className="why">{s.reasons.slice(0, 2).map((r, i) => <li key={i}>{r}</li>)}</ul>
      <div className="row between" style={{ marginTop: 10 }}>
        <ProvBadge p={s.provenance} />
        {s.outcome_r !== null ? (
          <span className={`small ${s.outcome_r >= 0 ? "ok" : "bad"}`}><b>{s.outcome_r >= 0 ? "+" : ""}{fmtNum(s.outcome_r)} R</b> paper result</span>
        ) : (
          <span className="small muted">
            {ALERT_LABEL[s.alert_level] ?? s.alert_level}
            {s.risk_status === "RISK_UNAVAILABLE" && " · product details needed"}
          </span>
        )}
      </div>
    </article>
  );
}

export function SignalDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const { data: s, error } = useApi<Signal>(`/api/signals/${id}`);
  const failed = s?.risk?.checks.filter((c) => !c.passed) ?? [];
  return (
    <section className="stack">
      <Back onClick={onClose} label="All signals" />
      <ErrorLine error={error} />
      {s && (
        <>
          <div className="card">
            <div className="sig-head">
              <DirIcon d={s.direction} />
              <div style={{ flex: 1 }}>
                <div className="sig-title">{DIR_LABEL[s.direction]} · {s.symbol}</div>
                <div className="small muted">{strategyName(s.strategy)} · {s.timeframe} chart</div>
              </div>
              <span className={`pill ${statusTone(s.status)}`}>{STATUS_LABEL[s.status] ?? s.status}</span>
            </div>
            <Plan s={s} />
            <div className="small muted" style={{ marginTop: 10 }}>
              Signal at {fmtTs(s.signal_ts)} ({timeAgo(s.signal_ts)}).{" "}
              {s.status === "ACTIVE" && <>Idea is cancelled if price closes beyond <b>{fmtPrice(s.invalidation)}</b> before {fmtTs(s.entry_window_until).slice(11)}. </>}
              {s.close_reason && <>Closed: {s.close_reason}.</>}
            </div>
            {s.outcome_r !== null && (
              <p className={s.outcome_r >= 0 ? "ok" : "bad"} style={{ margin: "8px 0 0" }}>
                <b>Paper result: {s.outcome_r >= 0 ? "+" : ""}{fmtNum(s.outcome_r)} R</b>{" "}
                <Help text="R = the amount risked. +2 R means the move gained twice what was at risk; −1 R means the stop was hit." />
              </p>
            )}
          </div>

          <h3 className="section">Why this signal</h3>
          <div className="card">
            <ul className="why" style={{ marginTop: 0 }}>{s.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
            <p className="small muted" style={{ marginBottom: 0 }}>
              Market: {TREND_LABEL[s.regime.trend] ?? s.regime.trend}, {VOL_LABEL[s.regime.volatility] ?? s.regime.volatility}. Strength{" "}
              {fmtNum(s.score)} <Help text="A 0–1 ranking of how strongly the rules were met. It is not a probability of success." />
            </p>
          </div>

          <h3 className="section">Safety checks</h3>
          <div className="card stack-sm">
            <div className="row between">
              <span>{s.risk_status === "APPROVED" ? <span className="ok">✓ Passed risk rules</span> : s.risk_status === "RISK_UNAVAILABLE" ? <span className="warn-t">Risk can’t be calculated for this product</span> : <span className="bad">✕ Blocked by risk rules</span>}</span>
              <ProvBadge p={s.provenance} />
            </div>
            {s.risk && s.risk_status === "APPROVED" && (
              <div className="small muted">Suggested paper risk: {fmtNum(s.risk.risk_amount)} ({fmtNum(s.risk.risk_pct)}% of paper balance)</div>
            )}
            <div className="small">{ALERT_LABEL[s.alert_level] ?? s.alert_level}</div>
            {failed.length > 0 && <ul className="why checks">{failed.map((c) => <li key={c.name} className="fail">{c.detail}</li>)}</ul>}
            <details>
              <summary>All {s.risk?.checks.length ?? 0} checks</summary>
              <ul className="why checks">{s.risk?.checks.map((c) => <li key={c.name} className={c.passed ? "" : "fail"}><b>{c.name.replace(/_/g, " ")}</b> — {c.detail}</li>)}</ul>
            </details>
          </div>

          <details className="card">
            <summary>Technical details</summary>
            <h3 className="section">Indicators on the signal bar</h3>
            <div className="table-wrap">
              <table className="kv"><tbody>{Object.entries(s.indicators ?? {}).map(([k, v]) => <tr key={k}><td>{k}</td><td className="num">{typeof v === "number" ? fmtPrice(v) : String(v)}</td></tr>)}</tbody></table>
            </div>
            <h3 className="section">Fundamentals</h3>
            <p className="small muted">{String(s.fundamentals?.status ?? "—")}: {String(s.fundamentals?.reason ?? s.fundamentals?.block ?? "")}</p>
            <h3 className="section">History</h3>
            <ul className="why plain">{s.events?.map((e, i) => <li key={i}>{e.ts.slice(0, 16).replace("T", " ")} — {STATUS_LABEL[e.kind] ?? e.kind} {e.detail}</li>)}</ul>
            {s.deliveries && s.deliveries.length > 0 && (
              <ul className="why plain">{s.deliveries.map((d, i) => <li key={i}>Alert “{d.event}” via {d.channel}: {d.status}{d.error ? ` (${d.error})` : ""}</li>)}</ul>
            )}
            <h3 className="section">Audit fingerprints</h3>
            <div className="mono muted">params {s.params_hash}<br />dataset {s.dataset_hash}<br />risk config {s.risk_config_hash}<br />code {s.code_version}</div>
          </details>
        </>
      )}
    </section>
  );
}

export default function Signals({ openId, setOpenId }: { openId: string | null; setOpenId: (id: string | null) => void }) {
  const [status, setStatus] = useState("ACTIVE");
  const { data, error, loading } = useApi<Signal[]>(`/api/signals?limit=100${status ? `&status=${status}` : ""}`, 20000);
  if (openId) return <SignalDetail id={openId} onClose={() => setOpenId(null)} />;
  return (
    <section className="stack">
      <PageHead title="Signals" lead="Trade ideas found by the scanner. Tap one to see the full reasoning." />
      <div className="seg" role="group" aria-label="Filter signals">
        {FILTERS.map(([v, label]) => (
          <button key={v} aria-pressed={status === v} onClick={() => setStatus(v)}>{label}</button>
        ))}
      </div>
      <ErrorLine error={error} />
      {loading && !data && <p className="muted center">Loading…</p>}
      {data && data.length === 0 && (
        <Empty icon="inbox" title={status === "ACTIVE" ? "No open signals right now" : "Nothing here yet"}>
          That’s normal — most of the time no setup meets all the rules, and staying out is the right call. Check <b>Markets</b> to see why.
        </Empty>
      )}
      <div className="grid">{data?.map((s) => <SignalCard key={s.id} s={s} onOpen={setOpenId} />)}</div>
    </section>
  );
}
