import { useEffect, useState } from "react";
import { setToken, type Signal, type Status, type Watchlist } from "./api";
import { Footer, Icon, ProvBadge, useApi } from "./common";
import Home from "./pages/Home";
import Markets from "./pages/Markets";
import Research from "./pages/Research";
import Settings from "./pages/Settings";
import Signals from "./pages/Signals";

const TABS = [
  { id: "home", label: "Today", icon: "home" },
  { id: "signals", label: "Signals", icon: "bell" },
  { id: "markets", label: "Markets", icon: "markets" },
  { id: "research", label: "Research", icon: "flask" },
  { id: "settings", label: "Settings", icon: "gear" },
] as const;
type Tab = (typeof TABS)[number]["id"];

function parseHash(): { tab: Tab; id: string | null } {
  const [, tab, id] = window.location.hash.split("/");
  const known = TABS.some((t) => t.id === tab);
  return { tab: known ? (tab as Tab) : "home", id: id ?? null };
}

function Welcome({ onSaved }: { onSaved: () => void }) {
  const [tok, setTok] = useState("");
  return (
    <main className="stack" style={{ paddingTop: 48 }}>
      <div className="center">
        <img src="/icon.svg" alt="" width={64} height={64} style={{ borderRadius: 16 }} />
        <h1 className="page" style={{ marginTop: 12 }}>Deriv Signal Lab</h1>
        <p className="lead">Research and paper-trading alerts. Nothing is ever traded for you.</p>
      </div>
      <form className="card stack" onSubmit={(e) => { e.preventDefault(); setToken(tok.trim()); onSaved(); }}>
        <label className="field">Access token
          <input type="password" value={tok} onChange={(e) => setTok(e.target.value)} autoFocus autoComplete="off" placeholder="Paste the APP_ACCESS_TOKEN from your server" />
          <small>Saved only on this device. Ask whoever runs the server if you don’t have it.</small>
        </label>
        <button className="btn block" type="submit" disabled={!tok.trim()}>Continue</button>
      </form>
      <Footer />
    </main>
  );
}

export default function App() {
  const [{ tab, id }, setRoute] = useState(parseHash());
  useEffect(() => {
    const on = () => {
      setRoute(parseHash());
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const go = (t: string, sid: string | null = null) => {
    window.location.hash = sid ? `/${t}/${sid}` : `/${t}`;
  };
  const status = useApi<Status>("/api/status", 30000);
  const wls = useApi<Watchlist[]>("/api/watchlists");
  const open = useApi<Signal[]>("/api/signals?status=ACTIVE&limit=50", 30000);
  const symbols = [...new Set((wls.data ?? []).flatMap((w) => w.items.map((i) => i.symbol)))];

  if (status.error?.includes("access token")) {
    return <Welcome onSaved={() => { status.reload(); wls.reload(); open.reload(); }} />;
  }
  const openCount = open.data?.length ?? 0;

  return (
    <>
      <header className="top">
        <div className="brand"><img src="/icon.svg" alt="" /> Signal Lab</div>
        <div className="spacer" />
        {status.data?.risk.kill_switch && <span className="pill short">Paused</span>}
        {status.data && <ProvBadge p={status.data.provenance} />}
      </header>
      <nav className="tabs" aria-label="Main">
        {TABS.map((t) => (
          <button key={t.id} aria-current={tab === t.id ? "page" : undefined} onClick={() => go(t.id)}>
            <Icon name={t.icon} />
            {t.id === "signals" && openCount > 0 && <span className="dot">{openCount}</span>}
            {t.label}
          </button>
        ))}
      </nav>
      <main className={`stack ${tab === "signals" || tab === "research" ? "wide" : ""}`}>
        {status.error && <p className="error">Can’t reach the server: {status.error}</p>}
        {tab === "home" && <Home status={status.data} goSignal={(sid) => go("signals", sid)} goTab={go} />}
        {tab === "signals" && <Signals openId={id} setOpenId={(sid) => go("signals", sid)} />}
        {tab === "markets" && <Markets />}
        {tab === "research" && <Research symbols={symbols} />}
        {tab === "settings" && <Settings status={status.data} reloadStatus={status.reload} />}
        <Footer />
      </main>
    </>
  );
}
