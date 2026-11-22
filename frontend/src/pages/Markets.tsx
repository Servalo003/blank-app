import { useMemo, useState } from "react";
import { api, fmtNum, TIMEFRAMES, type Provenance, type Signal, type Snapshot, type Watchlist } from "../api";
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

interface SymbolInfo {
  symbol: string;
  display_name: string;
  market: string;
  submarket: string;
  is_open: boolean;
  is_suspended: boolean;
}

export interface InstrumentRow {
  symbol: string;
  display_name: string;
  market: string;
  product_spec: (Record<string, unknown> & { costs?: Record<string, number | string> }) | null;
  adapter: { product_type: string; confirmed: boolean } | null;
  fundamentals: { status: string; reason?: string };
  mt5_available: boolean;
  observed_spread: { median_spread: number; median_spread_bps: number | null; samples: number; feed: string } | null;
}

const MARKET_LABEL: Record<string, string> = {
  synthetic_index: "Derived indices",
  forex: "Forex",
  indices: "Stock indices",
  commodities: "Commodities",
  cryptocurrency: "Crypto",
};

function summarize(rows: Snapshot[]): { dir: string; text: string } {
  const hits = rows.filter((r) => r.direction !== "NO_TRADE");
  if (rows.length === 0) return { dir: "NO_TRADE", text: "Waiting for the first scan" };
  if (rows.some((r) => !r.data_quality.ok)) return { dir: "NO_TRADE", text: "Paused — data problem or market closed" };
  if (hits.length === 0) return { dir: "NO_TRADE", text: `No setup · ${rows.length} strategies checked` };
  const dirs = new Set(hits.map((h) => h.direction));
  if (dirs.size > 1) return { dir: "NO_TRADE", text: "Strategies disagree — staying out" };
  const d = hits[0].direction;
  return { dir: d, text: `${DIR_LABEL[d]} from ${hits.map((h) => strategyName(h.strategy)).join(", ")}` };
}

/* ------------------------------------------------------------------ product (MT5) panel */
export function ProductPanel({ inst, onChanged }: { inst: InstrumentRow | undefined; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (!inst) return null;
  const spec = inst.product_spec;
  const confirmed = Boolean(inst.adapter?.confirmed);
  const costs = spec?.costs ?? {};
  async function apply() {
    setBusy(true);
    setErr(null);
    try {
      await api(`/api/instruments/${inst!.symbol}/apply-mt5`, { method: "POST" });
      onChanged();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="card stack-sm">
      <div className="row between">
        <b>MT5 contract</b>
        {confirmed ? <span className="pill long">Confirmed</span> : <span className="pill warn">Not confirmed</span>}
      </div>
      {spec && spec.platform === "mt5" ? (
        <>
          <div className="kv-grid">
            <div><span>MT5 name</span><b style={{ fontFamily: "inherit" }}>{String(spec.mt5_name)}</b></div>
            <div><span>Contract size</span><b>{fmtNum(spec.contract_size as number, 0)}</b></div>
            <div><span>Min lot</span><b>{String(spec.min_units)}</b></div>
            <div><span>Max lot</span><b>{String(spec.max_units ?? "—")}</b></div>
            <div><span>Min spread</span><b>{String(costs.spread ?? "—")}</b></div>
            <div><span>Typical spread</span><b>{fmtNum(Number(costs.spread_bps) / 100, 3)}%</b></div>
            <div><span>Swap long/short</span><b>{String(spec.swap_long_pts ?? "—")} / {String(spec.swap_short_pts ?? "—")}</b></div>
            <div><span>Max leverage</span><b>{String(spec.max_leverage ?? "—")}</b></div>
          </div>
          <p className="tiny faint" style={{ margin: 0 }}>
            From Deriv’s published MT5 trading specifications. Check them against <b>Specification</b> in your MT5 terminal (right-click the symbol), since values can change and can differ by account type.
            Swap is shown but not included in tests.
          </p>
        </>
      ) : (
        <p className="small muted" style={{ margin: 0 }}>{inst.mt5_available ? "MT5 specifications are available for this market." : "No matching MT5 instrument was found in Deriv’s published specifications."}</p>
      )}
      {inst.observed_spread && (
        <p className="tiny muted" style={{ margin: 0 }}>
          Live spread seen on Deriv’s data feed: <span className="num">{inst.observed_spread.median_spread.toPrecision(3)}</span>
          {inst.observed_spread.median_spread_bps !== null && <> (~{fmtNum(inst.observed_spread.median_spread_bps, 1)} bps)</>} over {inst.observed_spread.samples} ticks. Your MT5 spread may differ.
        </p>
      )}
      {!confirmed && inst.mt5_available && (
        <button className="btn" onClick={apply} disabled={busy}>{busy ? "Saving…" : "Use these MT5 specs"}</button>
      )}
      <ErrorLine error={err} />
    </div>
  );
}

/* ------------------------------------------------------------------ add market sheet */
function AddMarket({ watchlistId, existing, onClose, onAdded }: { watchlistId: number; existing: Set<string>; onClose: () => void; onAdded: () => void }) {
  const { data, error } = useApi<{ symbols: SymbolInfo[] }>("/api/symbols");
  const [q, setQ] = useState("");
  const [group, setGroup] = useState("all");
  const [sel, setSel] = useState<string | null>(null);
  const [tf, setTf] = useState("15m");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const groups = useMemo(() => [...new Set((data?.symbols ?? []).map((s) => s.market))].sort(), [data]);
  const list = (data?.symbols ?? [])
    .filter((s) => group === "all" || s.market === group)
    .filter((s) => !q || `${s.symbol} ${s.display_name}`.toLowerCase().includes(q.toLowerCase()))
    .slice(0, 80);
  async function add() {
    if (!sel) return;
    setBusy(true);
    setErr(null);
    try {
      await api(`/api/watchlists/${watchlistId}/items`, { method: "POST", body: JSON.stringify({ symbol: sel, timeframe: tf }) });
      onAdded();
      onClose();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <div className="sheet stack" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Add a market">
        <div className="row between">
          <h2>Add a market</h2>
          <button className="iconbtn" onClick={onClose} aria-label="Close"><Icon name="x" /></button>
        </div>
        <input placeholder="Search, e.g. Volatility 75 or EUR/USD" value={q} onChange={(e) => setQ(e.target.value)} autoFocus />
        <div className="seg" role="group" aria-label="Market type">
          <button aria-pressed={group === "all"} onClick={() => setGroup("all")}>All</button>
          {groups.map((g) => <button key={g} aria-pressed={group === g} onClick={() => setGroup(g)}>{MARKET_LABEL[g] ?? g}</button>)}
        </div>
        <ErrorLine error={error} />
        <div style={{ maxHeight: 300, overflow: "auto" }}>
          {list.map((s) => {
            const taken = existing.has(`${s.symbol}|${tf}`);
            return (
              <div key={s.symbol} className="sym-row" aria-selected={sel === s.symbol} onClick={() => !taken && setSel(s.symbol)} style={taken ? { opacity: 0.45, cursor: "default" } : undefined}>
                <div>
                  <div style={{ fontWeight: 600 }}>{s.display_name}</div>
                  <div className="ticker">{s.symbol} · {MARKET_LABEL[s.market] ?? s.market}</div>
                </div>
                {taken ? <span className="pill">Added</span> : !s.is_open ? <span className="pill warn">Closed now</span> : sel === s.symbol ? <span className="pill accent">Selected</span> : null}
              </div>
            );
          })}
          {data && list.length === 0 && <p className="small muted center">No matches.</p>}
        </div>
        <label className="field">Chart timeframe
          <div className="seg" role="group">
            {TIMEFRAMES.map((t) => <button key={t} aria-pressed={tf === t} onClick={() => setTf(t)}>{t}</button>)}
          </div>
        </label>
        <ErrorLine error={err} />
        <button className="btn block" onClick={add} disabled={!sel || busy}><Icon name="plus" /> {busy ? "Adding…" : sel ? `Watch ${sel} on ${tf}` : "Pick a market"}</button>
        <p className="tiny faint" style={{ margin: 0 }}>All four strategies are switched on. Forex and other markets that close at weekends pause automatically.</p>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ market detail */
function MarketDetail({ symbol, timeframe, rows, itemId, inst, onBack, onChanged }: {
  symbol: string; timeframe: string; rows: Snapshot[]; itemId: number | undefined; inst: InstrumentRow | undefined; onBack: () => void; onChanged: () => void;
}) {
  const [tf, setTf] = useState(timeframe);
  const { data, error } = useApi<CandleResp>(`/api/candles?symbol=${symbol}&timeframe=${tf}&count=300`, 60000);
  const first = rows[0];
  async function remove() {
    if (!itemId || !confirm(`Stop watching ${symbol} ${timeframe}? Past signals are kept.`)) return;
    await api(`/api/watchlists/items/${itemId}`, { method: "DELETE" });
    onChanged();
    onBack();
  }
  return (
    <section className="stack">
      <Back onClick={onBack} label="All markets" />
      <PageHead
        title={inst?.display_name ?? symbol}
        lead={`${symbol}${first ? ` · ${TREND_LABEL[first.regime.trend] ?? first.regime.trend}, ${VOL_LABEL[first.regime.volatility] ?? first.regime.volatility}` : ""}`}
        action={data && <ProvBadge p={data.provenance} />}
      />
      <div className="seg" role="group" aria-label="Chart timeframe">
        {TIMEFRAMES.map((t) => <button key={t} aria-pressed={tf === t} onClick={() => setTf(t)}>{t}</button>)}
      </div>
      <ErrorLine error={error} />
      {data && !data.data_quality.ok && <div className="note warn"><b>Data issue:</b> {data.data_quality.issues.join("; ")}</div>}
      <div className="card" style={{ padding: 4 }}>
        {data ? <CandleChart candles={data.candles} signals={data.signals} label={`${symbol} ${tf} price chart`} /> : <div className="chart" />}
      </div>
      <p className="tiny faint center" style={{ margin: 0 }}>Completed candles only · arrows mark past signals · dashed lines show the open signal’s stop and target</p>

      <div className="grid-2">
        <div>
          <h3 className="section" style={{ marginTop: 0 }}>Strategies on the {timeframe} chart</h3>
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
        </div>
        <div>
          <h3 className="section" style={{ marginTop: 0 }}>Product</h3>
          <ProductPanel inst={inst} onChanged={onChanged} />
          {itemId && <button className="btn ghost block" style={{ marginTop: 12 }} onClick={remove}><Icon name="trash" /> Stop watching</button>}
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ list */
export default function Markets() {
  const snaps = useApi<Snapshot[]>("/api/scan/snapshots", 30000);
  const wls = useApi<Watchlist[]>("/api/watchlists");
  const insts = useApi<InstrumentRow[]>("/api/instruments");
  const [open, setOpen] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const items = (wls.data ?? []).flatMap((w) => w.items.filter((i) => i.enabled).map((i) => ({ ...i, wl: w.id })));
  const snapBy = new Map<string, Snapshot[]>();
  for (const s of snaps.data ?? []) {
    const k = `${s.symbol}|${s.timeframe}`;
    snapBy.set(k, [...(snapBy.get(k) ?? []), s]);
  }
  const instBy = new Map((insts.data ?? []).map((i) => [i.symbol, i]));
  const refresh = () => {
    wls.reload();
    insts.reload();
    snaps.reload();
  };

  if (open) {
    const [symbol, timeframe] = open.split("|");
    const item = items.find((i) => i.symbol === symbol && i.timeframe === timeframe);
    return <MarketDetail symbol={symbol} timeframe={timeframe} rows={snapBy.get(open) ?? []} itemId={item?.id} inst={instBy.get(symbol)} onBack={() => setOpen(null)} onChanged={refresh} />;
  }
  const wlId = wls.data?.[0]?.id;
  return (
    <section className="stack">
      <PageHead
        title="Markets"
        lead="Your watchlist and what the strategies see right now."
        action={wlId !== undefined && <button className="btn sm" onClick={() => setAdding(true)}><Icon name="plus" /> Add market</button>}
      />
      <ErrorLine error={snaps.error ?? wls.error} />
      {wls.data && items.length === 0 && <Empty icon="markets" title="Your watchlist is empty">Tap “Add market” to start watching one.</Empty>}
      {items.length > 0 && (
        <div className="list">
          {items.map((it) => {
            const k = `${it.symbol}|${it.timeframe}`;
            const sum = summarize(snapBy.get(k) ?? []);
            const inst = instBy.get(it.symbol);
            return (
              <div key={it.id} className="tap" role="button" tabIndex={0} onClick={() => setOpen(k)} onKeyDown={(e) => e.key === "Enter" && setOpen(k)}>
                <div className="sig-head">
                  <DirIcon d={sum.dir} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="sig-title">{inst?.display_name ?? it.symbol} <span className="ticker">· {it.timeframe}</span></div>
                    <div className="small muted">{sum.text}</div>
                  </div>
                  {inst && !inst.adapter?.confirmed && inst.market !== "simulated" && <span className="pill warn" title="MT5 specs not confirmed">Specs</span>}
                  <Icon name="chevron" className="faint" />
                </div>
              </div>
            );
          })}
        </div>
      )}
      {snaps.data && snaps.data[0] && <div className="row" style={{ justifyContent: "center" }}><ProvBadge p={snaps.data[0].provenance} /></div>}
      {adding && wlId !== undefined && <AddMarket watchlistId={wlId} existing={new Set(items.map((i) => `${i.symbol}|${i.timeframe}`))} onClose={() => setAdding(false)} onAdded={refresh} />}
    </section>
  );
}
