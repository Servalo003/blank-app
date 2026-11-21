import { useEffect, useState } from "react";
import type { Status, Watchlist } from "./api";
import { Disclaimer, ErrorLine, ProvBadge, useApi } from "./common";
import Backtests from "./pages/Backtests";
import ChartView from "./pages/ChartView";
import Scanner from "./pages/Scanner";
import Settings from "./pages/Settings";
import Signals from "./pages/Signals";

const TABS = ["signals", "scanner", "chart", "backtests", "settings"] as const;
type Tab = (typeof TABS)[number];

function parseHash(): { tab: Tab; id: string | null } {
  const [, tab, id] = window.location.hash.split("/");
  return { tab: (TABS as readonly string[]).includes(tab) ? (tab as Tab) : "signals", id: id ?? null };
}

export default function App() {
  const [{ tab, id }, setRoute] = useState(parseHash());
  useEffect(() => {
    const on = () => setRoute(parseHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const go = (t: Tab, sid: string | null = null) => {
    window.location.hash = sid ? `/${t}/${sid}` : `/${t}`;
  };
  const status = useApi<Status>("/api/status", 30000);
  const wls = useApi<Watchlist[]>("/api/watchlists");
  const symbols = [...new Set((wls.data ?? []).flatMap((w) => w.items.map((i) => i.symbol)))];
  const unauth = status.error?.includes("access token");

  return (
    <>
      <header className="top">
        <h1>Deriv Signal Lab</h1>
        <div className="spacer" />
        {status.data && <ProvBadge p={status.data.provenance} />}
        {status.data?.risk.kill_switch && <span className="badge bad">KILL SWITCH</span>}
      </header>
      <nav className="tabs" aria-label="Sections">
        {TABS.map((t) => (
          <button key={t} aria-current={tab === t ? "page" : undefined} onClick={() => go(t)}>
            {t[0].toUpperCase() + t.slice(1)}
          </button>
        ))}
      </nav>
      <main className="stack">
        <Disclaimer />
        {unauth && tab !== "settings" && <p className="error">Enter your access token under Settings.</p>}
        {!unauth && <ErrorLine error={status.error} />}
        {tab === "signals" && <Signals openId={id} setOpenId={(sid) => go("signals", sid)} />}
        {tab === "scanner" && <Scanner />}
        {tab === "chart" && <ChartView symbols={symbols} />}
        {tab === "backtests" && <Backtests symbols={symbols} />}
        {tab === "settings" && <Settings status={status.data} reloadStatus={status.reload} />}
      </main>
    </>
  );
}
