import { useState } from "react";
import { TIMEFRAMES, type Provenance, type Signal, type Snapshot } from "../api";
import { CandleChart } from "../charts";
import {
  Back,
  DIR_LABEL,
  DirIcon,
  DirPill,
  Empty,
  ErrorLine,
  Help,
  Icon,
  PageHead,
  ProvBadge,
  STRATEGY_HELP,
  TREND_LABEL,
  VOL_LABEL,
  strategyName,
  timeAgo,
  useApi,
} from "../common";

interface CandleResp {
  provenance: Provenance;
  candles: [number, number, number, number, number][];
  data_quality: { ok: boolean; issues: string[] };
  signals: Signal[];
}

function summarize(rows: Snapshot[]): { dir: string; text: string } {
  const hits = rows.filter((r) => r.direction !== "NO_TRADE");
  if (rows.some((r) => !r.data_quality.ok)) return { dir: "NO_TRADE", text: "Paused — data problem" };
  if (hits.length === 0) return { dir: "NO_TRADE", text: `No setup · ${rows.length} strategies checked` };
  const dirs = new Set(hits.map((h) => h.direction));
  if (dirs.size > 1) return { dir: "NO_TRADE", text: "Strategies disagree — staying out" };
  const d = hits[0].direction;
  return { dir: d, text: `${DIR_LABEL[d]} from ${hits.map((h) => strategyName(h.strategy)).join(", ")}` };
}

function MarketDetail({ symbol, timeframe, rows, onBack }: { symbol: string; timeframe: string; rows: Snapshot[]; onBack: () => void }) {
  const [tf, setTf] = useState(timeframe);
  const { data, error } = useApi<CandleResp>(`/api/candles?symbol=${symbol}&timeframe=${tf}&count=300`, 60000);
  const first = rows[0];
  return (
    <section className="stack">
      <Back onClick={onBack} label="All markets" />
      <PageHead title={symbol} lead={first ? `${TREND_LABEL[first.regime.trend] ?? first.regime.trend}, ${VOL_LABEL[first.regime.volatility] ?? first.regime.volatility}` : undefined} action={data && <ProvBadge p={data.provenance} />} />
      <div className="seg" role="group" aria-label="Chart timeframe">
        {TIMEFRAMES.map((t) => <button key={t} aria-pressed={tf === t} onClick={() => setTf(t)}>{t}</button>)}
      </div>
      <ErrorLine error={error} />
      {data && !data.data_quality.ok && <div className="note warn"><b>Data issue:</b> {data.data_quality.issues.join("; ")}</div>}
      <div className="card" style={{ padding: 6 }}>
        {data ? <CandleChart candles={data.candles} signals={data.signals} label={`${symbol} ${tf} price chart`} /> : <div className="chart" />}
      </div>
      <p className="tiny faint center" style={{ margin: 0 }}>Completed candles only · arrows mark past signals · dashed lines show the open signal’s stop and target</p>

      <h3 className="section">What each strategy sees ({timeframe} chart)</h3>
      {rows.length === 0 && <Empty title="Not scanned yet">Press “Scan now” on the Today screen.</Empty>}
      <div className="list">
        {rows.map((r) => (
          <div key={r.strategy}>
            <div className="row between">
              <b>{strategyName(r.strategy)} <Help text={STRATEGY_HELP[r.strategy] ?? ""} /></b>
              <DirPill d={r.direction} />
            </div>
            <div className="small muted" style={{ marginTop: 4 }}>{r.reasons[0]}</div>
          </div>
        ))}
      </div>
      {first && <p className="tiny faint center">Last checked {timeAgo(first.updated_at)}</p>}
    </section>
  );
}

export default function Markets() {
  const { data, error } = useApi<Snapshot[]>("/api/scan/snapshots", 30000);
  const [open, setOpen] = useState<string | null>(null);
  const groups = new Map<string, Snapshot[]>();
  for (const s of data ?? []) {
    const k = `${s.symbol}|${s.timeframe}`;
    groups.set(k, [...(groups.get(k) ?? []), s]);
  }
  if (open) {
    const [symbol, timeframe] = open.split("|");
    return <MarketDetail symbol={symbol} timeframe={timeframe} rows={groups.get(open) ?? []} onBack={() => setOpen(null)} />;
  }
  return (
    <section className="stack">
      <PageHead title="Markets" lead="Your watchlist and what the strategies currently see. Tap a market for its chart." />
      <ErrorLine error={error} />
      {data && groups.size === 0 && <Empty icon="markets" title="No scans yet">Press “Scan now” on the Today screen, or start the background worker.</Empty>}
      {groups.size > 0 && (
        <div className="list">
          {[...groups.entries()].map(([k, rows]) => {
            const [symbol, tf] = k.split("|");
            const sum = summarize(rows);
            return (
              <div key={k} className="tap" role="button" tabIndex={0} onClick={() => setOpen(k)} onKeyDown={(e) => e.key === "Enter" && setOpen(k)}>
                <div className="sig-head">
                  <DirIcon d={sum.dir} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="sig-title">{symbol} <span className="small muted">· {tf}</span></div>
                    <div className="small muted">{sum.text}</div>
                  </div>
                  <Icon name="chevron" className="faint" />
                </div>
              </div>
            );
          })}
        </div>
      )}
      {data && data[0] && <div className="row" style={{ justifyContent: "center" }}><ProvBadge p={data[0].provenance} /></div>}
    </section>
  );
}
