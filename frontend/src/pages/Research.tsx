import { useEffect, useState } from "react";
import { api, fmtNum, fmtTs, STRATEGIES, TIMEFRAMES, type BacktestRun, type Metrics } from "../api";
import { EquityChart } from "../charts";
import { Back, Empty, ErrorLine, Help, Icon, PageHead, ProvBadge, STRATEGY_HELP, strategyName, timeAgo, useApi } from "../common";

const DETAIL_ROWS: [keyof Metrics, string, number][] = [
  ["trades", "Trades", 0],
  ["net_return_pct", "Return %", 2],
  ["net_pnl", "Profit / loss", 2],
  ["expectancy_r", "Average per trade (R)", 3],
  ["win_rate", "Win rate", 3],
  ["avg_win", "Average win", 2],
  ["avg_loss", "Average loss", 2],
  ["payoff_ratio", "Avg win ÷ avg loss", 2],
  ["profit_factor", "Profit factor", 2],
  ["max_drawdown_pct", "Worst drop from peak %", 2],
  ["max_drawdown_duration_hours", "Longest time under peak (h)", 1],
  ["max_consecutive_losses", "Longest losing streak", 0],
  ["sharpe", "Sharpe ratio", 2],
  ["sortino", "Sortino ratio", 2],
];

function Verdict({ r }: { r: BacktestRun }) {
  if (r.status === "FAILED") {
    return (
      <div className="card hero">
        <div className="light stop"><Icon name="stop" /></div>
        <div><b>Test failed to run</b><span className="small muted">{r.error}</span></div>
      </div>
    );
  }
  const reasons = r.report?.eligibility_reasons ?? [];
  const passed = reasons.filter((x) => x.startsWith("PASS")).length;
  const eligible = r.label === "ELIGIBLE_FOR_PAPER_ALERTS";
  const isEval = r.kind === "evaluation";
  const net = r.metrics?.net;
  return (
    <div className="card stack-sm">
      <div className="hero">
        <div className={`light ${!isEval ? "attn" : eligible ? "good" : "stop"}`}><Icon name={!isEval ? "flask" : eligible ? "check" : "stop"} /></div>
        <div style={{ flex: 1 }}>
          <b>{!isEval ? "Single backtest" : eligible ? "Passed the research screen" : "Did not pass the research screen"}</b>
          <span className="small muted">
            {isEval
              ? `${passed} of ${reasons.length} checks passed on unseen test data. ${eligible ? "Paper alerts can now be enabled for this market." : "Its signals stay research-only."}`
              : "One run with default settings. Use a full test to decide whether to trust a strategy."}
          </span>
        </div>
      </div>
      {net && (
        <div className="stats" style={{ marginTop: 10 }}>
          <div className="stat"><b className={net.net_return_pct >= 0 ? "ok" : "bad"}>{fmtNum(net.net_return_pct, 1)}%</b><span>return after costs</span></div>
          <div className="stat"><b>{net.trades}</b><span>trades</span></div>
          <div className="stat"><b>{fmtNum(net.max_drawdown_pct, 1)}%</b><span>worst drop</span></div>
        </div>
      )}
    </div>
  );
}

function RunDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const { data: r, error } = useApi<BacktestRun>(`/api/backtests/${id}`);
  const bench = r?.report?.benchmarks as Record<string, Record<string, number>> | undefined;
  return (
    <section className="stack">
      <Back onClick={onClose} label="All tests" />
      <ErrorLine error={error} />
      {r && (
        <>
          <PageHead title={strategyName(r.strategy)} lead={`${r.symbol} · ${r.timeframe} chart · ${timeAgo(r.created_at)}`} action={<ProvBadge p={r.provenance} />} />
          {r.provenance === "SIMULATED_DATA" && <div className="note warn"><b>Simulated data:</b> these results say nothing about real markets.</div>}
          {r.report?.notes?.map((n, i) => <div key={i} className="note info">{n}</div>)}
          <Verdict r={r} />
          {r.report?.eligibility_reasons && (
            <div className="card">
              <h3 className="section" style={{ marginTop: 0 }}>Checks on unseen test data</h3>
              <ul className="why checks" style={{ marginTop: 0 }}>
                {r.report.eligibility_reasons.map((x, i) => <li key={i} className={x.startsWith("PASS") ? "" : "fail"}>{x.replace(/^(PASS|FAIL): /, "")}</li>)}
              </ul>
              <p className="tiny faint" style={{ marginBottom: 0 }}>Past results never guarantee future results. Passing only means the strategy cleared a basic research bar.</p>
            </div>
          )}
          {r.equity && r.equity.net.length > 0 && (
            <div className="card" style={{ padding: 8 }}>
              <div className="small muted" style={{ padding: "4px 8px" }}>Paper balance over the test period</div>
              <EquityChart net={r.equity.net} gross={r.equity.gross} />
            </div>
          )}
          {r.metrics && (
            <details className="card">
              <summary>All numbers (before and after costs)</summary>
              <div className="table-wrap">
                <table className="kv">
                  <thead><tr><th /><th className="num">Before costs</th><th className="num">After costs</th></tr></thead>
                  <tbody>
                    {DETAIL_ROWS.map(([k, label, d]) => (
                      <tr key={k}><td>{label}</td><td className="num">{fmtNum(r.metrics!.gross[k] as number, d)}</td><td className="num">{fmtNum(r.metrics!.net[k] as number, d)}</td></tr>
                    ))}
                    <tr><td>Time in a trade %</td><td /><td className="num">{fmtNum(r.metrics.exposure_pct, 1)}</td></tr>
                    <tr><td>Total costs paid</td><td /><td className="num">{fmtNum(r.metrics.total_costs)}</td></tr>
                    <tr><td>Bars where stop &amp; target both hit (counted as loss)</td><td /><td className="num">{r.metrics.ambiguous_bar_trades}</td></tr>
                  </tbody>
                </table>
              </div>
              {bench && (
                <>
                  <h3 className="section">Compared with</h3>
                  <table className="kv"><tbody>
                    {bench.buy_and_hold?.return_pct !== undefined && <tr><td>Simply buying and holding</td><td className="num">{fmtNum(bench.buy_and_hold.return_pct, 2)}%</td></tr>}
                    {bench.random_entry?.runs ? <tr><td>Random entries (95th percentile, R per trade)</td><td className="num">{fmtNum(bench.random_entry.p95_expectancy_r, 3)}</td></tr> : null}
                    <tr><td>Not trading at all</td><td className="num">0.00%</td></tr>
                  </tbody></table>
                </>
              )}
              <h3 className="section">How it was tested</h3>
              <ul className="why plain">{Object.values(r.metrics.assumptions).map((v, i) => <li key={i}>{v}</li>)}</ul>
              <div className="mono faint">config {r.config_hash}<br />dataset {r.dataset_hash}</div>
            </details>
          )}
          {r.trades && r.trades.length > 0 && (
            <details className="card">
              <summary>Trade list ({r.trades.length})</summary>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Entered</th><th>Side</th><th>Exit reason</th><th className="num">Result (R)</th></tr></thead>
                  <tbody>
                    {r.trades.slice(0, 300).map((t) => (
                      <tr key={String(t.trade_id)}>
                        <td>{fmtTs(t.entry_ts as number).slice(5, 16)}</td>
                        <td className={t.direction === "LONG" ? "ok" : "bad"}>{t.direction === "LONG" ? "Buy" : "Sell"}</td>
                        <td>{String(t.exit_reason).split(" (")[0]}</td>
                        <td className={`num ${(t.r_net as number) >= 0 ? "ok" : "bad"}`}>{fmtNum(t.r_net as number)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          )}
        </>
      )}
    </section>
  );
}

interface BatchResult { id?: string; symbol: string; timeframe: string; strategy: string; status: string; label?: string | null; error?: string | null; summary?: { trades?: number; expectancy_r?: number; profit_factor?: number; max_drawdown_pct?: number; net_return_pct?: number } }
interface BatchState { state: string; total?: number; done?: number; current?: string; started_at?: number; finished_at?: number; results?: BatchResult[] }

function BatchPanel({ onOpen, onDone }: { onOpen: (id: string) => void; onDone: () => void }) {
  const { data, reload } = useApi<BatchState>("/api/evaluations/batch", 5000);
  const [err, setErr] = useState<string | null>(null);
  const running = data?.state === "running";
  const pct = data?.total ? Math.round(((data.done ?? 0) / data.total) * 100) : 0;
  const results = data?.results ?? [];
  const passed = results.filter((r) => r.label === "ELIGIBLE_FOR_PAPER_ALERTS").length;
  async function start() {
    setErr(null);
    try {
      await api("/api/evaluations/batch", { method: "POST" });
      reload();
    } catch (e) {
      setErr((e as Error).message);
    }
  }
  const finishedAt = data?.finished_at;
  useEffect(() => {
    if (finishedAt) onDone();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finishedAt]);
  return (
    <div className="card stack-sm">
      <div className="row between">
        <div>
          <b>Test everything</b>
          <div className="small muted">Every strategy on every watched market, about 6 months of history each.</div>
        </div>
        <button className="btn sm" onClick={start} disabled={running}><Icon name={running ? "refresh" : "layers"} className={running ? "spin" : undefined} /> {running ? "Running…" : "Test all"}</button>
      </div>
      {running && (
        <>
          <div className="bar"><i style={{ width: `${pct}%` }} /></div>
          <div className="tiny muted">{data?.done}/{data?.total} done · now: {data?.current} · this can take 10–20 minutes; you can leave this screen</div>
        </>
      )}
      {!running && results.length > 0 && (
        <>
          <div className="small">
            Last run {data?.finished_at ? timeAgo(data.finished_at) : ""}: <b className={passed ? "ok" : ""}>{passed} of {results.length}</b> passed.
            {passed === 0 && " No strategy cleared the bar on these markets, so alerts stay research-only. That is a useful, honest answer."}
          </div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Strategy · market</th><th className="num">Trades</th><th className="num">R/trade</th><th>Result</th></tr></thead>
              <tbody>
                {results.map((r, i) => (
                  <tr key={i} onClick={() => r.id && onOpen(r.id)} style={{ cursor: r.id ? "pointer" : undefined }}>
                    <td style={{ whiteSpace: "normal" }}>{strategyName(r.strategy)}<div className="ticker tiny">{r.symbol} · {r.timeframe}</div></td>
                    <td className="num">{r.summary?.trades ?? "—"}</td>
                    <td className={`num ${(r.summary?.expectancy_r ?? 0) >= 0 ? "ok" : "bad"}`}>{fmtNum(r.summary?.expectancy_r ?? null, 2)}</td>
                    <td>{r.status === "FAILED" ? <span className="pill short" title={r.error ?? ""}>Error</span> : r.label === "ELIGIBLE_FOR_PAPER_ALERTS" ? <span className="pill long">Passed</span> : <span className="pill">Not passed</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {data?.state === "interrupted" && <p className="small warn-t" style={{ margin: 0 }}>The last batch was interrupted (server restarted). Run it again.</p>}
      <ErrorLine error={err} />
    </div>
  );
}

export default function Research({ symbols }: { symbols: string[] }) {
  const runs = useApi<BacktestRun[]>("/api/backtests");
  const [open, setOpen] = useState<string | null>(null);
  const [form, setForm] = useState({ symbol: "", timeframe: "15m", strategy: "trend_pullback", kind: "evaluation" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const symbol = form.symbol || symbols[0] || "";

  async function submit() {
    setBusy(true);
    setErr(null);
    try {
      const r = await api<BacktestRun>("/api/backtests", { method: "POST", body: JSON.stringify({ ...form, symbol, bars: 3000 }) });
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
      <PageHead title="Research" lead="Test strategies on past prices before trusting their alerts." />
      <BatchPanel onOpen={setOpen} onDone={() => runs.reload()} />
      <h3 className="section">Single test</h3>
      <div className="card stack">
        <div className="form-grid">
          <label className="field">Market
            <select value={symbol} onChange={(e) => setForm({ ...form, symbol: e.target.value })}>{symbols.map((s) => <option key={s}>{s}</option>)}</select>
          </label>
          <label className="field">Chart
            <select value={form.timeframe} onChange={(e) => setForm({ ...form, timeframe: e.target.value })}>{TIMEFRAMES.map((t) => <option key={t}>{t}</option>)}</select>
          </label>
        </div>
        <label className="field">Strategy
          <select value={form.strategy} onChange={(e) => setForm({ ...form, strategy: e.target.value })}>
            {STRATEGIES.map((t) => <option key={t} value={t}>{strategyName(t)}</option>)}
          </select>
          <small>{STRATEGY_HELP[form.strategy]}</small>
        </label>
        <div className="seg" role="group" aria-label="Test type">
          <button aria-pressed={form.kind === "evaluation"} onClick={() => setForm({ ...form, kind: "evaluation" })}>Full test (recommended)</button>
          <button aria-pressed={form.kind === "backtest"} onClick={() => setForm({ ...form, kind: "backtest" })}>Quick backtest</button>
        </div>
        <p className="tiny faint" style={{ margin: 0 }}>
          {form.kind === "evaluation"
            ? "Tunes on older data, then checks on newer data it has never seen. Passing unlocks paper alerts for this market."
            : "Runs once with default settings over the whole history. Good for a quick look only."}{" "}
          <Help text="Trades are filled at the next candle’s open after a signal, with spread, slippage and fees deducted. If a candle touched both stop and target, it is counted as a loss." />
        </p>
        <button className="btn block" onClick={submit} disabled={busy || !symbol}>
          <Icon name={busy ? "refresh" : "play"} className={busy ? "spin" : undefined} /> {busy ? "Testing… (up to a minute)" : "Run test"}
        </button>
        <ErrorLine error={err} />
      </div>

      <h3 className="section">Past tests</h3>
      <ErrorLine error={runs.error} />
      {runs.data && runs.data.length === 0 && <Empty icon="flask" title="No tests yet">Run your first test above.</Empty>}
      {runs.data && runs.data.length > 0 && (
        <div className="list">
          {runs.data.map((r) => {
            const eligible = r.label === "ELIGIBLE_FOR_PAPER_ALERTS";
            const tag = r.status === "FAILED" ? <span className="pill short">Failed</span> : r.kind === "evaluation" ? <span className={`pill ${eligible ? "long" : ""}`}>{eligible ? "Passed" : "Not passed"}</span> : <span className="pill">Quick</span>;
            return (
              <div key={r.id} className="tap" role="button" tabIndex={0} onClick={() => setOpen(r.id)} onKeyDown={(e) => e.key === "Enter" && setOpen(r.id)}>
                <div className="row between">
                  <div style={{ minWidth: 0 }}>
                    <b>{strategyName(r.strategy)}</b>
                    <div className="small muted">{r.symbol} · {r.timeframe} · {timeAgo(r.created_at)}</div>
                  </div>
                  <div style={{ textAlign: "right" }}>
                    {tag}
                    {r.summary.net_return_pct !== undefined && r.summary.net_return_pct !== null && (
                      <div className={`small num ${r.summary.net_return_pct >= 0 ? "ok" : "bad"}`}>{fmtNum(r.summary.net_return_pct, 1)}%</div>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
