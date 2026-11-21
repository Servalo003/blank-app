// Thin API client. The only credential the browser ever holds is the app's own
// access token (entered by the operator). Deriv/Telegram secrets stay on the server.

const TOKEN_KEY = "dsl.accessToken";
const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}
export function setToken(t: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, t);
  } catch {
    /* storage unavailable */
  }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const tok = getToken();
  if (tok) headers.Authorization = `Bearer ${tok}`;
  const r = await fetch(`${BASE}${path}`, { ...init, headers: { ...headers, ...(init.headers as Record<string, string>) } });
  if (!r.ok) {
    let msg = r.statusText;
    try {
      const body = await r.json();
      msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* non-JSON */
    }
    throw new ApiError(r.status, msg);
  }
  return (await r.json()) as T;
}

export type Provenance = "LIVE_DATA" | "DELAYED_DATA" | "SIMULATED_DATA";

export interface Signal {
  id: string;
  provenance: Provenance;
  symbol: string;
  timeframe: string;
  strategy: string;
  direction: "LONG" | "SHORT";
  status: string;
  signal_ts: number;
  entry_window_until: number;
  expires_at: number;
  entry_ref: number | null;
  stop: number | null;
  target: number | null;
  invalidation: number | null;
  score: number;
  reasons: string[];
  risk_status: string;
  alert_level: string;
  regime: Record<string, string>;
  closed_at: number | null;
  close_reason: string | null;
  outcome_r: number | null;
  created_at: string;
  // detail-only
  indicators?: Record<string, number | string | null>;
  params?: Record<string, unknown>;
  params_hash?: string;
  dataset_hash?: string;
  risk_config_hash?: string;
  risk?: { checks: { name: string; passed: boolean; detail: string }[]; units: number; risk_amount: number; risk_pct: number };
  fundamentals?: Record<string, unknown>;
  data_quality?: Record<string, unknown>;
  events?: { ts: string; kind: string; detail: string }[];
  deliveries?: { event: string; channel: string; status: string; attempts: number; error: string | null }[];
  code_version?: string;
}

export interface Snapshot {
  symbol: string;
  timeframe: string;
  strategy: string;
  bar_ts: number;
  direction: string;
  provenance: Provenance;
  reasons: string[];
  indicators: Record<string, number | string | null>;
  data_quality: { ok: boolean; issues: string[]; stale_by_seconds: number };
  regime: { trend: string; volatility: string };
  updated_at: string;
}

export interface Status {
  disclaimer: string;
  mode: string;
  env: string;
  provider: Record<string, unknown>;
  provenance: Provenance;
  database: boolean;
  redis: boolean | null;
  worker_heartbeat: string | null;
  last_scan: string | null;
  risk: Record<string, number | boolean>;
  notifications: { telegram: boolean; webpush: boolean };
  active_signals: number;
  code_version: string;
}

export interface Metrics {
  trades: number;
  net_pnl: number;
  expectancy: number | null;
  expectancy_r: number | null;
  total_r: number;
  win_rate: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  payoff_ratio: number | null;
  profit_factor: number | null;
  max_consecutive_losses: number;
  final_equity: number;
  net_return_pct: number;
  max_drawdown_pct: number;
  max_drawdown_amount: number;
  max_drawdown_duration_hours: number;
  sharpe: number | null;
  sortino: number | null;
}

export interface BacktestRun {
  id: string;
  kind: string;
  status: string;
  symbol: string;
  timeframe: string;
  strategy: string;
  provenance: Provenance;
  label: string | null;
  config_hash: string;
  dataset_hash: string;
  error: string | null;
  created_at: string;
  summary: Partial<Metrics>;
  metrics?: { net: Metrics; gross: Metrics; exposure_pct: number; turnover: number; total_costs: number; ambiguous_bar_trades: number; assumptions: Record<string, string> };
  report?: Record<string, unknown> & { notes?: string[]; eligibility_reasons?: string[]; benchmarks?: Record<string, Record<string, number>>; folds?: unknown[]; disclaimer?: string; chosen_params?: Record<string, unknown> };
  equity?: { net: [number, number][]; gross: [number, number][] };
  dataset_range?: Record<string, unknown>;
  trades?: Record<string, unknown>[];
}

export interface Watchlist {
  id: number;
  name: string;
  items: { id: number; symbol: string; timeframe: string; strategies: Record<string, { enabled: boolean }>; enabled: boolean }[];
}

export const STRATEGIES = ["trend_pullback", "donchian_breakout", "mean_reversion", "momentum_continuation", "ensemble"];
export const TIMEFRAMES = ["5m", "15m", "1h", "4h"];

export const fmtTs = (ts: number) =>
  new Date(ts * 1000).toISOString().replace("T", " ").slice(0, 16) + " UTC";
export const fmtNum = (v: number | null | undefined, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "—" : v.toFixed(d));
export const fmtPrice = (v: number | null | undefined) => (v === null || v === undefined ? "—" : Number(v.toPrecision(6)).toString());
