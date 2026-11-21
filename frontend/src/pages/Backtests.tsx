import { useState } from "react";
import { api, fmtNum, fmtTs, STRATEGIES, TIMEFRAMES, type BacktestRun, type Metrics } from "../api";
import { ErrorLine, ProvBadge, useApi } from "../common";
import { EquityChart } from "./ChartView";

const ROWS: [keyof Metrics, string, number][] = [
  ["trades", "Trades", 0],
  ["net_return_pct", "Return %", 2],
  ["net_pnl", "Net P&L", 2],
  ["expectancy", "Expectancy", 2],
  ["expectancy_r", "Expectancy (R)", 3],
  ["total_r", "Total R", 2],
  ["win_rate", "Win rate", 3],
  ["avg_win", "Avg win", 2],
  ["avg_loss", "Avg loss", 2],
  ["payoff_ratio", "Payoff ratio", 2],
  ["profit_factor", "Profit factor", 2],
  ["max_drawdown_pct", "Max DD %", 2],
  ["max_drawdown_duration_hours", "Max DD duration (h)", 1],
  ["max_consecutive_losses", "Max consec. losses", 0],
  ["sharpe", "Sharpe", 2],
  ["sortino", "Sortino", 2],
];

function RunDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const { data: r, error } = useApi<BacktestRun>(`/api/backtests/${id}`);
  return (
    <div className="card stack">
      <div className="row between">
        <h2>{r ? `${r.kind}: ${r.strategy} on ${r.symbol} ${r.timeframe}` : "Run"}</h2>
        <button className="btn secondary" onClick={onClose}>Close</button>
      </div>
      <ErrorLine error={error ?? r?.error ?? null} />
      {r && r.metrics && (
        <>
          <div className="row">
            <ProvBadge p={r.provenance} />
            {r.label && <span className={`badge ${r.label.startsWith("ELIGIBLE") ? "ok" : "warn"}`}>{r.label}</span>}
          </div>
          {r.provenance === "SIMULATED_DATA" && (
            <p className="small warn">Simulated data: results say nothing about real markets. They only exercise the engine.</p>
          )}
          {r.report?.eligibility_reasons && (
            <>
              <h3>Out-of-sample eligibility (hold-out test segment)</h3>
              <ul className="reasons small">{r.report.eligibility_reasons.map((x, i) => <li key={i} className={x.startsWith("PASS") ? "ok" : "bad"}>{x}</li>)}</ul>
              <p className="small muted">{r.report.disclaimer}</p>
              <div className="small muted">Chosen params (train-only selection): <span className="mono">{JSON.stringify(r.report.chosen_params)}</span></div>
            </>
          )}
          <h3>Metrics — before vs after estimated costs</h3>
          <div className="table-wrap">
            <table className="compact">
              <thead><tr><th>Metric</th><th className="num">Gross</th><th className="num">Net</th></tr></thead>
              <tbody>
                {ROWS.map(([k, label, d]) => (
                  <tr key={k}><td>{label}</td><td className="num">{fmtNum(r.metrics!.gross[k] as number, d)}</td><td className="num">{fmtNum(r.metrics!.net[k] as number, d)}</td></tr>
                ))}
                <tr><td>Exposure %</td><td /><td className="num">{fmtNum(r.metrics.exposure_pct)}</td></tr>
                <tr><td>Turnover (× avg equity)</td><td /><td className="num">{fmtNum(r.metrics.turnover)}</td></tr>
                <tr><td>Total costs</td><td /><td className="num">{fmtNum(r.metrics.total_costs)}</td></tr>
                <tr><td>Ambiguous-bar trades (stop assumed)</td><td /><td className="num">{r.metrics.ambiguous_bar_trades}</td></tr>
              </tbody>
            </table>
          </div>
          {r.equity && <EquityChart net={r.equity.net} gross={r.equity.gross} />}
          {r.report?.benchmarks && (
            <>
              <h3>Benchmarks</h3>
              <pre className="small mono" style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(r.report.benchmarks, null, 1)}</pre>
            </>
          )}
          <details>
            <summary>Assumptions &amp; reproducibility</summary>
            <ul className="reasons small">{Object.entries(r.metrics.assumptions).map(([k, v]) => <li key={k}><b>{k}</b>: {v}</li>)}</ul>
            <div className="small mono" style={{ wordBreak: "break-all" }}>config {r.config_hash}<br />dataset {r.dataset_hash}</div>
            <pre className="small mono" style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(r.dataset_range, null, 1)}</pre>
          </details>
          <h3>Trade log ({r.trades?.length ?? 0})</h3>
          <div className="table-wrap">
            <table>
              <thead><tr><th>#</th><th>Dir</th><th>Signal close</th><th>Entry (next open)</th><th className="num">Entry px</th><th className="num">Exit px</th><th>Exit</th><th className="num">Net</th><th className="num">R</th><th>Regime</th></tr></thead>
              <tbody>
                {r.trades?.slice(0, 300).map((t) => (
                  <tr key={String(t.trade_id)}>
                    <td>{String(t.trade_id)}</td>
                    <td className={`dir-${t.direction}`}>{String(t.direction)}</td>
                    <td>{fmtTs(t.signal_ts as number).slice(5, 16)}</td>
                    <td>{fmtTs(t.entry_ts as number).slice(5, 16)}</td>
                    <td className="num">{fmtNum(t.entry_price as number, 4)}</td>
                    <td className="num">{fmtNum(t.exit_price as number, 4)}</td>
                    <td>{String(t.exit_reason)}</td>
                    <td className="num">{fmtNum(t.net_pnl as number)}</td>
                    <td className="num">{fmtNum(t.r_net as number)}</td>
                    <td className="muted">{String(t.trend_regime)}/{String(t.vol_regime)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

export default function Backtests({ symbols }: { symbols: string[] }) {
  const runs = useApi<BacktestRun[]>("/api/backtests");
  const [open, setOpen] = useState<string | null>(null);
  const [form, setForm] = useState({ symbol: symbols[0] ?? "", timeframe: "15m", strategy: "trend_pullback", bars: 3000, kind: "evaluation" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const symbol = form.symbol || symbols[0] || "";

  async function submit() {
    setBusy(true);
    setErr(null);
    try {
      const r = await api<BacktestRun>("/api/backtests", { method: "POST", body: JSON.stringify({ ...form, symbol }) });
      runs.reload();
      setOpen(r.id);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (open) return <RunDetail id={open} onClose={() => setOpen(null)} />;
  return (
    <section className="stack">
      <h2>Backtests &amp; evaluations</h2>
      <div className="card stack">
        <div className="row">
          <label className="field">Symbol
            <select value={symbol} onChange={(e) => setForm({ ...form, symbol: e.target.value })}>{symbols.map((s) => <option key={s}>{s}</option>)}</select>
          </label>
          <label className="field">Timeframe
            <select value={form.timeframe} onChange={(e) => setForm({ ...form, timeframe: e.target.value })}>{TIMEFRAMES.map((t) => <option key={t}>{t}</option>)}</select>
          </label>
          <label className="field">Strategy
            <select value={form.strategy} onChange={(e) => setForm({ ...form, strategy: e.target.value })}>{STRATEGIES.map((t) => <option key={t}>{t}</option>)}</select>
          </label>
          <label className="field">Bars
            <input type="number" min={400} max={20000} value={form.bars} onChange={(e) => setForm({ ...form, bars: Number(e.target.value) })} style={{ width: 100 }} />
          </label>
          <label className="field">Type
            <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
              <option value="evaluation">Walk-forward + hold-out (eligibility)</option>
              <option value="backtest">Single backtest (default params)</option>
            </select>
          </label>
        </div>
        <div><button className="btn" onClick={submit} disabled={busy || !symbol}>{busy ? "Running… (can take ~30s)" : "Run"}</button></div>
        <ErrorLine error={err} />
        <p className="small muted">Execution at the next bar open after the signal bar closes; spread, slippage and commission applied; ambiguous bars assume the stop was hit first.</p>
      </div>
      <ErrorLine error={runs.error} />
      <div className="table-wrap card">
        <table>
          <thead><tr><th>When</th><th>Type</th><th>Run</th><th>Data</th><th className="num">Trades</th><th className="num">Net %</th><th className="num">Exp. R</th><th className="num">PF</th><th className="num">Max DD %</th><th>Label</th></tr></thead>
          <tbody>
            {runs.data?.map((r) => (
              <tr key={r.id} onClick={() => setOpen(r.id)} style={{ cursor: "pointer" }}>
                <td>{r.created_at.slice(5, 16)}</td><td>{r.kind}</td><td>{r.strategy} {r.symbol} {r.timeframe}</td><td><ProvBadge p={r.provenance} /></td>
                <td className="num">{r.summary.trades ?? "—"}</td><td className="num">{fmtNum(r.summary.net_return_pct)}</td>
                <td className="num">{fmtNum(r.summary.expectancy_r, 3)}</td><td className="num">{fmtNum(r.summary.profit_factor)}</td>
                <td className="num">{fmtNum(r.summary.max_drawdown_pct)}</td><td>{r.status === "FAILED" ? <span className="bad">FAILED</span> : r.label ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
