import { useEffect, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  LineSeries,
  type IChartApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { TIMEFRAMES, fmtTs, type Provenance, type Signal } from "../api";
import { ErrorLine, ProvBadge, useApi } from "../common";

interface CandleResp {
  symbol: string;
  timeframe: string;
  provenance: Provenance;
  candles: [number, number, number, number, number][];
  data_quality: { ok: boolean; issues: string[] };
  signals: Signal[];
}

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function themeChartOptions() {
  return {
    layout: { background: { type: ColorType.Solid, color: cssVar("--surface") }, textColor: cssVar("--muted") },
    grid: { vertLines: { color: cssVar("--border") }, horzLines: { color: cssVar("--border") } },
    timeScale: { timeVisible: true, secondsVisible: false, borderColor: cssVar("--border") },
    rightPriceScale: { borderColor: cssVar("--border") },
    autoSize: true,
  };
}

export function EquityChart({ net, gross }: { net: [number, number][]; gross: [number, number][] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, themeChartOptions());
    const g = chart.addSeries(LineSeries, { color: cssVar("--muted"), lineWidth: 1, title: "before costs" });
    const n = chart.addSeries(LineSeries, { color: cssVar("--accent"), lineWidth: 2, title: "after costs" });
    g.setData(gross.map(([t, v]) => ({ time: t as UTCTimestamp, value: v })));
    n.setData(net.map(([t, v]) => ({ time: t as UTCTimestamp, value: v })));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [net, gross]);
  return <div className="chart" ref={ref} style={{ height: 260 }} aria-label="Equity curve before and after costs" />;
}

export default function ChartView({ symbols }: { symbols: string[] }) {
  const [symbol, setSymbol] = useState(symbols[0] ?? "");
  const [tf, setTf] = useState("15m");
  const [sel, setSel] = useState<string | null>(null);
  useEffect(() => {
    if (!symbol && symbols[0]) setSymbol(symbols[0]);
  }, [symbols, symbol]);
  const { data, error } = useApi<CandleResp>(symbol ? `/api/candles?symbol=${symbol}&timeframe=${tf}&count=400` : null, 60000);
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!ref.current || !data) return;
    const chart = createChart(ref.current, themeChartOptions());
    chartRef.current = chart;
    const series = chart.addSeries(CandlestickSeries, {
      upColor: cssVar("--long"), downColor: cssVar("--short"), borderVisible: false,
      wickUpColor: cssVar("--long"), wickDownColor: cssVar("--short"),
    });
    series.setData(data.candles.map(([t, o, h, l, c]) => ({ time: t as UTCTimestamp, open: o, high: h, low: l, close: c })));
    const g = Number(data.candles.length > 1 ? data.candles[1][0] - data.candles[0][0] : 900);
    const first = data.candles[0]?.[0] ?? 0;
    const markers: SeriesMarker<Time>[] = data.signals
      .filter((s) => s.signal_ts - g >= first)
      .map((s) => ({
        time: (s.signal_ts - g) as UTCTimestamp, // marker on the signal bar (open time)
        position: s.direction === "LONG" ? "belowBar" : "aboveBar",
        color: s.direction === "LONG" ? cssVar("--long") : cssVar("--short"),
        shape: s.direction === "LONG" ? "arrowUp" : "arrowDown",
      }));
    markers.sort((a, b) => Number(a.time) - Number(b.time));
    createSeriesMarkers(series, markers);
    const s = data.signals.find((x) => x.id === sel) ?? data.signals.find((x) => x.status === "ACTIVE");
    if (s) {
      if (s.stop) series.createPriceLine({ price: s.stop, color: cssVar("--short"), lineStyle: 2, title: "stop" });
      if (s.target) series.createPriceLine({ price: s.target, color: cssVar("--long"), lineStyle: 2, title: "target" });
      if (s.invalidation) series.createPriceLine({ price: s.invalidation, color: cssVar("--warn"), lineStyle: 3, title: "invalid." });
    }
    chart.timeScale().fitContent();
    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [data, sel]);

  return (
    <section className="stack">
      <div className="row between">
        <h2>Chart</h2>
        {data && <ProvBadge p={data.provenance} />}
      </div>
      <div className="row">
        <select value={symbol} onChange={(e) => setSymbol(e.target.value)} aria-label="Symbol">
          {symbols.map((s) => <option key={s}>{s}</option>)}
        </select>
        <select value={tf} onChange={(e) => setTf(e.target.value)} aria-label="Timeframe">
          {TIMEFRAMES.map((t) => <option key={t}>{t}</option>)}
        </select>
      </div>
      <ErrorLine error={error} />
      {data && !data.data_quality.ok && <p className="small warn">Data quality: {data.data_quality.issues.join("; ")}</p>}
      <div className="card" style={{ padding: 6 }}>
        <div className="chart" ref={ref} aria-label={`${symbol} ${tf} candles, closed bars only`} />
      </div>
      <p className="small muted">Closed candles only. Arrows mark signal bars; dashed lines show stop/target of the selected (or latest active) signal.</p>
      {data && data.signals.length > 0 && (
        <div className="table-wrap card">
          <table>
            <thead><tr><th>Signal bar close</th><th>Strategy</th><th>Dir</th><th>Status</th><th /></tr></thead>
            <tbody>
              {data.signals.slice(0, 20).map((s) => (
                <tr key={s.id}>
                  <td>{fmtTs(s.signal_ts)}</td><td>{s.strategy}</td><td className={`dir-${s.direction}`}>{s.direction}</td><td>{s.status}</td>
                  <td><button className="btn secondary" onClick={() => setSel(s.id)}>Show levels</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
