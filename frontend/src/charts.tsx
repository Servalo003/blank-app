import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  LineSeries,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import type { Signal } from "./api";

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function themeOptions() {
  return {
    layout: { background: { type: ColorType.Solid, color: cssVar("--surface") }, textColor: cssVar("--muted") },
    grid: { vertLines: { visible: false }, horzLines: { color: cssVar("--border") } },
    timeScale: { timeVisible: true, secondsVisible: false, borderColor: cssVar("--border") },
    rightPriceScale: { borderColor: cssVar("--border") },
    crosshair: { mode: 0 },
    autoSize: true,
  };
}

export function CandleChart({
  candles,
  signals,
  selected,
  label,
}: {
  candles: [number, number, number, number, number][];
  signals: Signal[];
  selected?: Signal | null;
  label: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || candles.length === 0) return;
    const chart = createChart(ref.current, themeOptions());
    const series = chart.addSeries(CandlestickSeries, {
      upColor: cssVar("--long"), downColor: cssVar("--short"), borderVisible: false,
      wickUpColor: cssVar("--long"), wickDownColor: cssVar("--short"),
    });
    series.setData(candles.map(([t, o, h, l, c]) => ({ time: t as UTCTimestamp, open: o, high: h, low: l, close: c })));
    const g = candles.length > 1 ? candles[1][0] - candles[0][0] : 900;
    const first = candles[0][0];
    const markers: SeriesMarker<Time>[] = signals
      .filter((s) => s.signal_ts - g >= first)
      .map((s) => ({
        time: (s.signal_ts - g) as UTCTimestamp, // the signal bar (open time)
        position: s.direction === "LONG" ? "belowBar" : "aboveBar",
        color: s.direction === "LONG" ? cssVar("--long") : cssVar("--short"),
        shape: s.direction === "LONG" ? "arrowUp" : "arrowDown",
      }));
    markers.sort((a, b) => Number(a.time) - Number(b.time));
    createSeriesMarkers(series, markers);
    const s = selected ?? signals.find((x) => x.status === "ACTIVE");
    if (s) {
      if (s.stop) series.createPriceLine({ price: s.stop, color: cssVar("--short"), lineStyle: 2, title: "Stop" });
      if (s.target) series.createPriceLine({ price: s.target, color: cssVar("--long"), lineStyle: 2, title: "Target" });
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [candles, signals, selected]);
  return <div className="chart" ref={ref} role="img" aria-label={label} />;
}

export function EquityChart({ net, gross }: { net: [number, number][]; gross: [number, number][] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, themeOptions());
    const g = chart.addSeries(LineSeries, { color: cssVar("--faint"), lineWidth: 1, title: "Before costs" });
    const n = chart.addSeries(LineSeries, { color: cssVar("--accent"), lineWidth: 2, title: "After costs" });
    g.setData(gross.map(([t, v]) => ({ time: t as UTCTimestamp, value: v })));
    n.setData(net.map(([t, v]) => ({ time: t as UTCTimestamp, value: v })));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [net, gross]);
  return <div className="chart" ref={ref} style={{ height: 240 }} role="img" aria-label="Paper balance over time, before and after costs" />;
}
