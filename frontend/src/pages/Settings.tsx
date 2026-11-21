import { useState } from "react";
import { api, getToken, setToken, type Status } from "../api";
import { ErrorLine, ProvBadge, useApi } from "../common";

function urlBase64ToUint8Array(base64: string): Uint8Array {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + padding).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

const EDITABLE: [string, string][] = [
  ["risk_per_trade_pct", "Risk per trade (% of paper equity)"],
  ["max_daily_loss_pct", "Max daily loss %"],
  ["max_open_positions", "Max open positions"],
  ["max_total_open_risk_pct", "Max total open risk %"],
  ["loss_streak_reduce", "Halve risk after N losses"],
  ["loss_streak_pause", "Pause after N losses"],
  ["min_reward_risk", "Min reward/risk"],
];
const FLAGS: [string, string][] = [
  ["research_only_alerts", "Send research-only alerts (risk unavailable / not eligible / simulated)"],
  ["require_eligibility", "Require out-of-sample eligibility for paper alerts"],
];

export default function Settings({ status, reloadStatus }: { status: Status | null; reloadStatus: () => void }) {
  const [tok, setTok] = useState(getToken());
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const audit = useApi<{ chain: { ok: boolean; entries: number } }>("/api/audit?limit=1");
  const instruments = useApi<{ symbol: string; market: string; adapter: { product_type: string; confirmed: boolean; backtestable: boolean } | null; fundamentals: { status: string; reason?: string } }[]>("/api/instruments");
  const risk = status?.risk ?? {};

  async function saveRisk() {
    setErr(null);
    const changes: Record<string, number | boolean> = {};
    for (const [k, v] of Object.entries(draft)) if (v !== "") changes[k] = Number(v);
    try {
      await api("/api/risk/config", { method: "PUT", body: JSON.stringify({ changes }) });
      setDraft({});
      setMsg("Risk settings saved (audited).");
      reloadStatus();
    } catch (e) {
      setErr((e as Error).message);
    }
  }
  async function toggle(key: string, value: boolean) {
    setErr(null);
    try {
      if (key === "kill_switch") await api(`/api/risk/kill-switch?engaged=${value}`, { method: "POST" });
      else await api("/api/risk/config", { method: "PUT", body: JSON.stringify({ changes: { [key]: value } }) });
      reloadStatus();
    } catch (e) {
      setErr((e as Error).message);
    }
  }
  async function testNotify() {
    try {
      setMsg(JSON.stringify(await api("/api/notifications/test", { method: "POST" })));
    } catch (e) {
      setErr((e as Error).message);
    }
  }
  async function enablePush() {
    setErr(null);
    try {
      if (!("serviceWorker" in navigator) || !("PushManager" in window)) throw new Error("Web Push is not supported in this browser");
      const { key } = await api<{ key: string | null }>("/api/push/vapid-public-key");
      if (!key) throw new Error("Server has no VAPID key configured");
      const perm = await Notification.requestPermission();
      if (perm !== "granted") throw new Error("Notification permission denied");
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(key) as BufferSource });
      const j = sub.toJSON();
      await api("/api/push/subscribe", { method: "POST", body: JSON.stringify({ endpoint: j.endpoint, keys: j.keys }) });
      setMsg("Push notifications enabled on this device.");
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  return (
    <section className="stack">
      <h2>Settings &amp; status</h2>
      <div className="card stack">
        <h3>Access</h3>
        <label className="field">App access token (APP_ACCESS_TOKEN — stored only on this device)
          <input type="password" value={tok} onChange={(e) => setTok(e.target.value)} autoComplete="off" />
        </label>
        <div><button className="btn" onClick={() => { setToken(tok); reloadStatus(); setMsg("Token saved on this device."); }}>Save token</button></div>
      </div>

      {status && (
        <div className="card stack">
          <h3>System</h3>
          <div className="row"><ProvBadge p={status.provenance} /> <span className="small">{status.mode}</span></div>
          <div className="small">
            Provider <b>{String(status.provider.provider)}</b> · DB {status.database ? <span className="ok">ok</span> : <span className="bad">down</span>} · Redis{" "}
            {status.redis === null ? "not configured" : status.redis ? <span className="ok">ok</span> : <span className="bad">down</span>} · active signals {status.active_signals}
          </div>
          <div className="small muted">Worker heartbeat: {status.worker_heartbeat ?? "none (worker not running?)"}</div>
          <div className="small muted">Last scan: {status.last_scan ?? "—"}</div>
          <div className="small muted">Audit chain: {audit.data ? (audit.data.chain.ok ? `intact (${audit.data.chain.entries} entries)` : "BROKEN") : "…"}</div>
          <div className="small muted">Version {status.code_version}</div>
        </div>
      )}

      {status && (
        <div className="card stack">
          <h3>Risk engine</h3>
          <div className="row">
            <button className={`btn ${risk.kill_switch ? "secondary" : "danger"}`} onClick={() => toggle("kill_switch", !risk.kill_switch)}>
              {risk.kill_switch ? "Release kill switch" : "Engage kill switch"}
            </button>
            {risk.kill_switch && <span className="bad small">All new signals are blocked.</span>}
          </div>
          <div className="grid">
            {EDITABLE.map(([k, label]) => (
              <label className="field" key={k}>{label} (now {String(risk[k])})
                <input inputMode="decimal" value={draft[k] ?? ""} placeholder={String(risk[k])} onChange={(e) => setDraft({ ...draft, [k]: e.target.value })} />
              </label>
            ))}
          </div>
          <div><button className="btn" onClick={saveRisk} disabled={Object.keys(draft).length === 0}>Save risk settings</button></div>
          {FLAGS.map(([k, label]) => (
            <label key={k} className="row small"><input type="checkbox" checked={Boolean(risk[k])} onChange={(e) => toggle(k, e.target.checked)} style={{ minHeight: 0 }} /> {label}</label>
          ))}
          <p className="small muted">Hard cap {String(risk.hard_max_risk_pct)}% per trade. Risk never increases after losses (no martingale, averaging down or grids).</p>
        </div>
      )}

      <div className="card stack">
        <h3>Notifications</h3>
        <div className="small">Telegram: {status?.notifications.telegram ? <span className="ok">configured</span> : "not configured (set TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID on the server)"}</div>
        <div className="small">Web Push: {status?.notifications.webpush ? <span className="ok">server configured</span> : "server VAPID keys not set"}</div>
        <div className="row">
          <button className="btn secondary" onClick={enablePush}>Enable push on this device</button>
          <button className="btn secondary" onClick={testNotify}>Send test</button>
        </div>
      </div>

      <div className="card stack">
        <h3>Instruments &amp; product adapters</h3>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Symbol</th><th>Market</th><th>Product</th><th>Spec</th><th>Fundamentals</th></tr></thead>
            <tbody>
              {instruments.data?.map((i) => (
                <tr key={i.symbol}>
                  <td>{i.symbol}</td><td>{i.market}</td><td>{i.adapter?.product_type ?? "none"}</td>
                  <td>{i.adapter?.confirmed ? <span className="ok">confirmed</span> : <span className="warn">unconfirmed → RISK_UNAVAILABLE</span>}</td>
                  <td className="muted">{i.fundamentals.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="small muted">Set a verified product spec with <span className="mono">PUT /api/instruments/&lt;symbol&gt;</span> (see README).</p>
      </div>
      {msg && <p className="small">{msg}</p>}
      <ErrorLine error={err} />
    </section>
  );
}
