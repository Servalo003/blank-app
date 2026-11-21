import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { api, type Provenance } from "./api";

/* ------------------------------------------------------------------ data hook */
export function useApi<T>(path: string | null, refreshMs = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const alive = useRef(true);
  const load = useCallback(async () => {
    if (!path) return;
    setLoading(true);
    try {
      const d = await api<T>(path);
      if (alive.current) {
        setData(d);
        setError(null);
      }
    } catch (e) {
      if (alive.current) setError((e as Error).message);
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [path]);
  useEffect(() => {
    alive.current = true;
    load();
    if (!refreshMs) return () => { alive.current = false; };
    const t = setInterval(load, refreshMs);
    return () => {
      alive.current = false;
      clearInterval(t);
    };
  }, [load, refreshMs]);
  return { data, error, loading, reload: load };
}

/* ------------------------------------------------------------------ icons (inline, no deps) */
const PATHS: Record<string, ReactNode> = {
  home: <path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z" />,
  bell: <><path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9" /><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" /></>,
  markets: <><path d="M3 3v18h18" /><path d="m7 15 4-4 3 3 5-6" /></>,
  flask: <><path d="M9 3h6" /><path d="M10 3v6L4.5 19A1.5 1.5 0 0 0 5.8 21h12.4a1.5 1.5 0 0 0 1.3-2L14 9V3" /><path d="M7 15h10" /></>,
  gear: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></>,
  up: <path d="m6 15 6-6 6 6" />,
  down: <path d="m6 9 6 6 6-6" />,
  minus: <path d="M5 12h14" />,
  check: <path d="m5 12 5 5 9-10" />,
  alert: <><path d="M12 9v4" /><path d="M12 17h.01" /><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" /></>,
  stop: <><circle cx="12" cy="12" r="9" /><path d="m15 9-6 6M9 9l6 6" /></>,
  refresh: <><path d="M21 12a9 9 0 1 1-3-6.7L21 8" /><path d="M21 3v5h-5" /></>,
  back: <path d="m15 18-6-6 6-6" />,
  chevron: <path d="m9 18 6-6-6-6" />,
  inbox: <><path d="M22 12h-6l-2 3h-4l-2-3H2" /><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" /></>,
  play: <path d="M7 4v16l13-8z" />,
};

export function Icon({ name, className }: { name: keyof typeof PATHS | string; className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {PATHS[name]}
    </svg>
  );
}

/* ------------------------------------------------------------------ plain-language labels */
export const PROV_LABEL: Record<Provenance, string> = {
  LIVE_DATA: "Live data",
  DELAYED_DATA: "Delayed data",
  SIMULATED_DATA: "Simulated data",
};

export const STATUS_LABEL: Record<string, string> = {
  ACTIVE: "Open",
  TARGET_HIT: "Target reached",
  STOP_HIT: "Stopped out",
  INVALIDATED: "Cancelled",
  EXPIRED: "Expired",
  SUPPRESSED: "Blocked by risk rules",
};

export const DIR_LABEL: Record<string, string> = { LONG: "Buy idea", SHORT: "Sell idea", NO_TRADE: "No trade" };

export const ALERT_LABEL: Record<string, string> = {
  ACTIONABLE: "Paper alert sent",
  RESEARCH_ONLY: "Research only",
  NONE: "No alert",
};

export const STRATEGY_LABEL: Record<string, string> = {
  trend_pullback: "Trend pullback",
  donchian_breakout: "Breakout",
  mean_reversion: "Mean reversion",
  momentum_continuation: "Momentum",
  ensemble: "Combined (all strategies)",
};

export const STRATEGY_HELP: Record<string, string> = {
  trend_pullback: "Follows the bigger trend and waits for a small dip (or bounce) before joining it.",
  donchian_breakout: "Looks for price breaking out of its recent range after a quiet period.",
  mean_reversion: "In sideways markets, fades moves that stretched too far from the average.",
  momentum_continuation: "Joins strong, accelerating moves, with a cool-down to avoid chasing.",
  ensemble: "Only acts when strategies agree; stays out when they disagree.",
};

export const TREND_LABEL: Record<string, string> = {
  TREND_UP: "Trending up",
  TREND_DOWN: "Trending down",
  RANGE: "Sideways",
  UNKNOWN: "Unclear",
  None: "Unclear",
};
export const VOL_LABEL: Record<string, string> = {
  HIGH_VOL: "high volatility",
  NORMAL_VOL: "normal volatility",
  LOW_VOL: "low volatility",
  UNKNOWN: "volatility unknown",
  None: "volatility unknown",
};

export const strategyName = (s: string) => STRATEGY_LABEL[s] ?? s;

export function timeAgo(ts: number | string): string {
  const t = typeof ts === "number" ? ts * 1000 : Date.parse(ts);
  const s = Math.round((Date.now() - t) / 1000);
  if (s < 0) {
    const f = -s;
    if (f < 3600) return `in ${Math.max(1, Math.round(f / 60))} min`;
    if (f < 86400) return `in ${Math.round(f / 3600)} h`;
    return `in ${Math.round(f / 86400)} d`;
  }
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

/* ------------------------------------------------------------------ small components */
export function ProvBadge({ p }: { p: Provenance }) {
  return (
    <span className={`pill prov-${p}`} title={p}>
      <span className="dotc" /> {PROV_LABEL[p] ?? p}
    </span>
  );
}

export function DirIcon({ d }: { d: string }) {
  return (
    <div className={`sig-icon ${d}`}>
      <Icon name={d === "LONG" ? "up" : d === "SHORT" ? "down" : "minus"} />
    </div>
  );
}

export function DirPill({ d }: { d: string }) {
  const cls = d === "LONG" ? "long" : d === "SHORT" ? "short" : "";
  return <span className={`pill ${cls}`}>{DIR_LABEL[d] ?? d}</span>;
}

export function ErrorLine({ error }: { error: string | null }) {
  return error ? <p className="error" role="alert">{error}</p> : null;
}

export function Empty({ icon = "inbox", title, children }: { icon?: string; title: string; children?: ReactNode }) {
  return (
    <div className="empty card flat">
      <Icon name={icon} />
      <p style={{ margin: "8px 0 4px", fontWeight: 650, color: "var(--text)" }}>{title}</p>
      {children && <div className="small">{children}</div>}
    </div>
  );
}

export function Help({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className="help" aria-label="What does this mean?" aria-expanded={open} onClick={() => setOpen(!open)}>?</button>
      {open && <span className="tiny muted" style={{ display: "block", marginTop: 4 }}>{text}</span>}
    </>
  );
}

export function PageHead({ title, lead, action }: { title: string; lead?: string; action?: ReactNode }) {
  return (
    <div className="row between" style={{ alignItems: "flex-end" }}>
      <div>
        <h1 className="page">{title}</h1>
        {lead && <p className="lead">{lead}</p>}
      </div>
      {action}
    </div>
  );
}

export function Back({ onClick, label = "Back" }: { onClick: () => void; label?: string }) {
  return (
    <button className="back" onClick={onClick}>
      <Icon name="back" /> {label}
    </button>
  );
}

export function Footer() {
  return <p className="foot">Paper &amp; research only · not financial advice · no orders are ever placed</p>;
}
