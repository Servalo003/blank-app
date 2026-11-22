import { useState } from "react";
import { api, getToken, setToken, type Status } from "../api";
import { ErrorLine, Help, Icon, PageHead, ProvBadge, timeAgo, useApi } from "../common";
import { useTheme, type ThemeMode } from "../theme";

function urlBase64ToUint8Array(base64: string): Uint8Array {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + padding).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

const RISK_FIELDS: [string, string, string][] = [
  ["risk_per_trade_pct", "Risk per trade (%)", "Share of the paper balance a single idea may lose if its stop is hit."],
  ["max_daily_loss_pct", "Daily loss limit (%)", "After losing this much in a day, new ideas are blocked until tomorrow."],
  ["max_open_positions", "Max open ideas", "How many paper positions can be open at once."],
  ["min_reward_risk", "Min reward ÷ risk", "Ideas whose target is not at least this many times the risk are skipped."],
];

type Instrument = {
  symbol: string;
  market: string;
  product_spec: Record<string, unknown> | null;
  mt5_available: boolean;
  adapter: { product_type: string; confirmed: boolean } | null;
  fundamentals: { status: string };
};

export default function Settings({ status, reloadStatus }: { status: Status | null; reloadStatus: () => void }) {
  const [tok, setTok] = useState(getToken());
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const audit = useApi<{ chain: { ok: boolean; entries: number } }>("/api/audit?limit=1");
  const instruments = useApi<Instrument[]>("/api/instruments");
  const risk = status?.risk ?? {};
  const { mode, setMode } = useTheme();
  const mt5src = useApi<{ source: string; retrieved_at: string }>("/api/mt5/source");
  const killed = Boolean(risk.kill_switch);

  const done = (m: string) => {
    setErr(null);
    setMsg(m);
    reloadStatus();
  };
  async function saveRisk() {
    const changes: Record<string, number> = {};
    for (const [k, v] of Object.entries(draft)) if (v.trim() !== "") changes[k] = Number(v);
    try {
      await api("/api/risk/config", { method: "PUT", body: JSON.stringify({ changes }) });
      setDraft({});
      done("Risk limits saved.");
    } catch (e) {
      setErr((e as Error).message);
    }
  }
  async function setFlag(key: string, value: boolean) {
    try {
      if (key === "kill_switch") await api(`/api/risk/kill-switch?engaged=${value}`, { method: "POST" });
      else await api("/api/risk/config", { method: "PUT", body: JSON.stringify({ changes: { [key]: value } }) });
      done("Saved.");
    } catch (e) {
      setErr((e as Error).message);
    }
  }
  async function applyMt5All() {
    try {
      const r = await api<{ applied: string[]; skipped: string[] }>("/api/instruments/apply-mt5-all", { method: "POST" });
      instruments.reload();
      done(`MT5 specs applied to ${r.applied.length} market(s)${r.skipped.length ? `; no MT5 match for ${r.skipped.join(", ")}` : ""}.`);
    } catch (e) {
      setErr((e as Error).message);
    }
  }
  async function importCsv(file: File) {
    try {
      const text = await file.text();
      const r = await api<{ imported: number }>("/api/events/csv", { method: "POST", body: JSON.stringify({ csv: text }) });
      instruments.reload();
      done(`Imported ${r.imported} calendar event(s).`);
    } catch (e) {
      setErr((e as Error).message);
    }
  }
  async function testNotify() {
    try {
      const r = await api<Record<string, string>>("/api/notifications/test", { method: "POST" });
      done(Object.entries(r).map(([k, v]) => `${k === "webpush" ? "Phone push" : "Telegram"}: ${v}`).join(" · "));
    } catch (e) {
      setErr((e as Error).message);
    }
  }
  async function enablePush() {
    try {
      if (!("serviceWorker" in navigator) || !("PushManager" in window)) throw new Error("This browser can’t receive push notifications. On iPhone, first add the app to your Home Screen.");
      const { key } = await api<{ key: string | null }>("/api/push/vapid-public-key");
      if (!key) throw new Error("Push isn’t set up on the server yet (VAPID keys missing).");
      if ((await Notification.requestPermission()) !== "granted") throw new Error("Notifications were not allowed.");
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(key) as BufferSource });
      const j = sub.toJSON();
      await api("/api/push/subscribe", { method: "POST", body: JSON.stringify({ endpoint: j.endpoint, keys: j.keys }) });
      done("This phone will now get alerts.");
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  return (
    <section className="stack">
      <PageHead title="Settings" lead="Alerts, safety limits and connection." />
      {msg && <div className="note info">{msg}</div>}
      <ErrorLine error={err} />

      <h3 className="section" style={{ marginTop: 0 }}>Appearance</h3>
      <div className="seg" role="group" aria-label="Theme">
        {(["system", "dark", "light"] as ThemeMode[]).map((m) => (
          <button key={m} aria-pressed={mode === m} onClick={() => setMode(m)}>
            <span className="row" style={{ justifyContent: "center", gap: 6 }}><Icon name={m === "system" ? "monitor" : m === "dark" ? "moon" : "sun"} className="ic14" /> {m[0].toUpperCase() + m.slice(1)}</span>
          </button>
        ))}
      </div>

      {/* ---- emergency stop ---- */}
      <div className={`card hero`}>
        <div style={{ flex: 1 }}>
          <b>{killed ? "Kill switch is ON" : "Emergency stop"}</b>
          <span className="small muted">{killed ? "All new signals are blocked." : "Instantly block all new signals and alerts."}</span>
        </div>
        <button className={`btn ${killed ? "ghost" : "danger"}`} onClick={() => setFlag("kill_switch", !killed)}>{killed ? "Turn off" : "Stop all"}</button>
      </div>

      {/* ---- notifications ---- */}
      <h3 className="section">Phone alerts</h3>
      <div className="list">
        <div className="row between">
          <div><b>Push notifications</b><div className="small muted">{status?.notifications.webpush ? "Ready — enable on each phone" : "Not set up on the server"}</div></div>
          <button className="btn ghost" onClick={enablePush}>Enable here</button>
        </div>
        <div className="row between">
          <div><b>Telegram</b><div className="small muted">{status?.notifications.telegram ? "Connected" : "Not connected (set bot token and chat ID on the server)"}</div></div>
        </div>
        <div className="row between">
          <div className="small muted">Check that alerts reach you</div>
          <button className="btn ghost" onClick={testNotify}>Send test</button>
        </div>
      </div>

      {/* ---- risk ---- */}
      <h3 className="section">Safety limits</h3>
      <div className="card stack">
        <div className="form-grid">
          {RISK_FIELDS.map(([k, label, help]) => (
            <label className="field" key={k}>
              <span>{label} <Help text={help} /></span>
              <input inputMode="decimal" value={draft[k] ?? ""} placeholder={String(risk[k] ?? "")} onChange={(e) => setDraft({ ...draft, [k]: e.target.value })} />
            </label>
          ))}
        </div>
        <button className="btn" onClick={saveRisk} disabled={Object.values(draft).every((v) => v.trim() === "")}>Save limits</button>
        <p className="tiny faint" style={{ margin: 0 }}>
          Never more than {String(risk.hard_max_risk_pct ?? 2)}% per trade. After {String(risk.loss_streak_reduce)} losses in a row risk is halved; after {String(risk.loss_streak_pause)} new ideas pause for a day. Risk is never increased to “win it back”.
        </p>
        <label className="switch">
          <input type="checkbox" checked={Boolean(risk.require_eligibility)} onChange={(e) => setFlag("require_eligibility", e.target.checked)} />
          <span><b>Only alert on tested strategies</b><br /><span className="small muted">A strategy must pass a full test in Research for that market before it can send paper alerts.</span></span>
        </label>
        <label className="switch">
          <input type="checkbox" checked={Boolean(risk.research_only_alerts)} onChange={(e) => setFlag("research_only_alerts", e.target.checked)} />
          <span><b>Also send research-only alerts</b><br /><span className="small muted">Includes untested strategies, simulated data and products where risk can’t be calculated. Clearly labelled.</span></span>
        </label>
      </div>

      {/* ---- markets / MT5 ---- */}
      <h3 className="section">MT5 contracts</h3>
      <div className="card stack-sm">
        <p className="small muted" style={{ margin: 0 }}>
          Signals are sized as Deriv MT5 CFDs. Specs come from Deriv’s published trading specifications
          {mt5src.data && <> (<a href={mt5src.data.source} target="_blank" rel="noreferrer">source</a>, {mt5src.data.retrieved_at})</>}.
          Confirm them against your MT5 terminal’s <b>Specification</b> window. Until a market is confirmed, its risk can’t be calculated and alerts stay research-only.
        </p>
        <button className="btn" onClick={applyMt5All}>Use MT5 specs for all my markets</button>
      </div>
      <div className="list">
        {instruments.data?.filter((i) => i.market !== "simulated").map((i) => (
          <div key={i.symbol} className="row between">
            <div>
              <b>{i.symbol}</b>
              <div className="small muted">
                {i.product_spec && (i.product_spec as Record<string, unknown>).platform === "mt5" ? `MT5 · ${String((i.product_spec as Record<string, unknown>).mt5_name)}` : i.mt5_available ? "MT5 match available" : "no MT5 match"} · fundamentals {i.fundamentals.status.toLowerCase().replace(/_/g, " ")}
              </div>
            </div>
            {i.adapter?.confirmed ? <span className="pill long">Ready</span> : <span className="pill warn">Not confirmed</span>}
          </div>
        ))}
      </div>

      {/* ---- calendar ---- */}
      <h3 className="section">Economic calendar</h3>
      <div className="card stack-sm">
        <p className="small muted" style={{ margin: 0 }}>
          For forex and commodity markets: import a CSV of scheduled events. New ideas are blocked within 30 minutes of a <b>high</b>-impact event for that currency.
          Derived indices have no economic news, so this doesn’t apply to them.
        </p>
        <details>
          <summary>CSV format</summary>
          <pre className="mono" style={{ whiteSpace: "pre-wrap", margin: "6px 0 0" }}>{`currency,title,impact,scheduled_at,known_at,source
USD,Nonfarm Payrolls,high,2026-12-04T13:30:00Z,2026-11-20T00:00:00Z,my-calendar`}</pre>
          <p className="tiny faint">impact: low / medium / high. known_at = when you first knew about the event (keeps tests honest). Times in UTC.</p>
        </details>
        <label className="btn ghost" style={{ cursor: "pointer" }}>
          <Icon name="upload" /> Import CSV
          <input type="file" accept=".csv,text/csv" style={{ display: "none" }} onChange={(e) => e.target.files?.[0] && importCsv(e.target.files[0])} />
        </label>
      </div>

      {/* ---- system ---- */}
      <h3 className="section">System</h3>
      {status && (
        <div className="list">
          <div className="row between"><span>Data source</span><ProvBadge p={status.provenance} /></div>
          <div className="row between"><span>Database</span><span className={status.database ? "ok" : "bad"}>{status.database ? "OK" : "Down"}</span></div>
          <div className="row between"><span>Background scanner</span><span className="small">{status.worker_heartbeat ? `alive (${timeAgo(JSON.parse(status.worker_heartbeat).ts)})` : <span className="warn-t">not running</span>}</span></div>
          <div className="row between"><span>Audit log <Help text="Every signal, setting change and test is recorded in a tamper-evident log." /></span><span className={audit.data?.chain.ok ? "ok" : "bad"}>{audit.data ? (audit.data.chain.ok ? `Intact · ${audit.data.chain.entries} entries` : "BROKEN") : "…"}</span></div>
          <div className="row between"><span>Version</span><span className="small muted">{status.code_version}</span></div>
        </div>
      )}

      <h3 className="section">Access</h3>
      <div className="card stack-sm">
        <label className="field">Access token <small>Stored only on this device.</small>
          <input type="password" value={tok} onChange={(e) => setTok(e.target.value)} autoComplete="off" />
        </label>
        <button className="btn ghost" onClick={() => { setToken(tok); done("Token saved."); }}>Save token</button>
      </div>
    </section>
  );
}
