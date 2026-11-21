"""FastAPI application. Read-only research API - there are no order endpoints."""

from __future__ import annotations

import asyncio
import hmac
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any

import structlog
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.schemas import (
    BacktestIn,
    EconomicEventIn,
    InstrumentSpecIn,
    PushSubscriptionIn,
    RiskConfigIn,
    WatchlistIn,
    WatchlistItemIn,
)
from app.config import get_settings
from app.core.types import Timeframe
from app.data.provider import DataUnavailable
from app.db import Base, get_engine, get_session, session_scope
from app.fundamentals import service as fundamentals
from app.logging import configure_logging
from app.models import (
    AuditLog,
    BacktestRun,
    EconomicEvent,
    Instrument,
    PushSubscription,
    ScanSnapshot,
    SignalRow,
    StrategyEligibility,
    Watchlist,
    WatchlistItem,
)
from app.products.adapter import ProductSpec, build_adapter
from app.risk.engine import HARD_MAX_RISK_PCT
from app.services import audit
from app.services.backtests import run_and_store
from app.services.runtime import Coordinator, get_provider
from app.services.seed import seed
from app.services.settings_store import load_risk_config, save_risk_config
from app.strategies.registry import describe_strategies

log = structlog.get_logger(__name__)
DISCLAIMER = (
    "Decision-support and research software. Not financial advice. No strategy is guaranteed to be profitable. "
    "No orders are placed by this application."
)


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    st = get_settings()
    configure_logging(st.log_level)
    if st.app_env != "production":
        Base.metadata.create_all(get_engine())  # production uses `alembic upgrade head`
    with session_scope() as s:
        seed(s)
    app.state.coordinator = Coordinator(st.redis_url)
    app.state.started_at = time.time()
    if not st.app_access_token:
        log.warning("api.auth_disabled", note="APP_ACCESS_TOKEN unset - allowed only outside production")
    yield
    await get_provider().close()


app = FastAPI(title="Deriv Signal Lab", version="0.1.0", lifespan=lifespan)
_st = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_st.cors_origin_list,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    allow_credentials=False,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    return resp


def require_auth(request: Request) -> str:
    st = get_settings()
    if not st.app_access_token:
        return "anonymous-dev"
    header = request.headers.get("authorization", "")
    token = header[7:] if header.lower().startswith("bearer ") else ""
    if not hmac.compare_digest(token.encode(), st.app_access_token.get_secret_value().encode()):
        raise HTTPException(status_code=401, detail="invalid or missing access token")
    return "operator"


Auth = Depends(require_auth)
DB = Depends(get_session)


def _sig(r: SignalRow, full: bool = False) -> dict[str, Any]:
    d = {
        c: getattr(r, c)
        for c in (
            "id",
            "provider",
            "provenance",
            "symbol",
            "timeframe",
            "strategy",
            "direction",
            "status",
            "signal_ts",
            "entry_window_until",
            "expires_at",
            "entry_ref",
            "stop",
            "target",
            "invalidation",
            "score",
            "reasons",
            "risk_status",
            "alert_level",
            "regime",
            "closed_at",
            "close_reason",
            "outcome_r",
        )
    }
    d["created_at"] = r.created_at.isoformat()
    d["score_note"] = "ordinal strength, not a probability"
    if full:
        d.update(
            {
                c: getattr(r, c)
                for c in (
                    "indicators",
                    "params",
                    "params_hash",
                    "risk_config_hash",
                    "dataset_hash",
                    "data_quality",
                    "risk",
                    "fundamentals",
                    "code_version",
                )
            }
        )
        d["events"] = [{"ts": e.ts.isoformat(), "kind": e.kind, "detail": e.detail} for e in r.events]
    return d


# ------------------------------------------------------------------ health / status
@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"status": "ok"}


@app.get("/api/status")
async def status(request: Request, s: Session = DB, _: str = Auth) -> dict[str, Any]:
    st = get_settings()
    provider = get_provider()
    try:
        s.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    coord: Coordinator = request.app.state.coordinator
    try:
        p_health = await asyncio.wait_for(provider.health(), 5)
    except Exception as exc:
        p_health = {"provider": provider.name, "error": str(exc)}
    risk = load_risk_config(s)
    return {
        "disclaimer": DISCLAIMER,
        "mode": "PAPER/RESEARCH (no order placement)",
        "env": st.app_env,
        "provider": p_health,
        "provenance": provider.provenance.value,
        "database": db_ok,
        "redis": await coord.ping(),
        "worker_heartbeat": await coord.get("worker:heartbeat"),
        "last_scan": await coord.get("worker:last_scan"),
        "risk": {**risk.to_dict(), "hard_max_risk_pct": HARD_MAX_RISK_PCT},
        "notifications": {
            "telegram": bool(st.telegram_bot_token and st.telegram_chat_id),
            "webpush": bool(st.vapid_public_key and st.vapid_private_key),
        },
        "active_signals": s.scalar(select(func.count()).select_from(SignalRow).where(SignalRow.status == "ACTIVE")),
        "code_version": st.git_sha,
        "uptime_s": round(time.time() - request.app.state.started_at),
    }


# ------------------------------------------------------------------ instruments / data
@app.get("/api/symbols")
async def symbols(_: str = Auth) -> dict[str, Any]:
    p = get_provider()
    try:
        syms = await p.list_symbols()
    except DataUnavailable as exc:
        raise HTTPException(503, f"provider unavailable: {exc}") from exc
    return {"provenance": p.provenance.value, "symbols": [asdict(s) | {"provenance": s.provenance.value} for s in syms]}


@app.get("/api/instruments")
def instruments(s: Session = DB, _: str = Auth) -> list[dict[str, Any]]:
    out = []
    for i in s.scalars(select(Instrument).order_by(Instrument.symbol)):
        adapter = build_adapter(i.product_spec)
        out.append(
            {
                "symbol": i.symbol,
                "display_name": i.display_name,
                "market": i.market,
                "provider": i.provider,
                "product_spec": i.product_spec,
                "periods_per_year": i.periods_per_year,
                "adapter": adapter.describe() if adapter else None,
                "fundamentals": fundamentals.assess(s, i.symbol, i.market, int(time.time())),
            }
        )
    return out


@app.put("/api/instruments/{symbol}")
def upsert_instrument(symbol: str, body: InstrumentSpecIn, s: Session = DB, actor: str = Auth) -> dict[str, Any]:
    ProductSpec.from_dict(body.product_spec)  # validate
    inst = s.get(Instrument, symbol)
    if inst is None:
        inst = Instrument(symbol=symbol, display_name=symbol, provider=get_provider().name, market="unknown")
        s.add(inst)
    inst.product_spec, inst.periods_per_year = body.product_spec, body.periods_per_year
    audit.record(s, "instrument.spec_updated", "instrument", symbol, body.model_dump(), actor)
    return {"ok": True}


@app.get("/api/candles")
async def candles(
    symbol: str, timeframe: Timeframe, count: int = Query(300, ge=10, le=2000), s: Session = DB, _: str = Auth
) -> dict[str, Any]:
    from app.data.quality import normalize_candles

    p = get_provider()
    try:
        now = await p.server_time()
        raw = await p.get_candles(symbol, timeframe.seconds, count + 1)
    except DataUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    closed, dq = normalize_candles(raw, timeframe.seconds, now, expect_continuous=False)
    sigs = s.scalars(
        select(SignalRow)
        .where(SignalRow.symbol == symbol, SignalRow.timeframe == timeframe.value)
        .order_by(SignalRow.signal_ts.desc())
        .limit(100)
    ).all()
    return {
        "symbol": symbol,
        "timeframe": timeframe.value,
        "provenance": p.provenance.value,
        "candles": [[c.ts, c.open, c.high, c.low, c.close] for c in closed],
        "data_quality": asdict(dq),
        "signals": [_sig(x) for x in sigs],
    }


# ------------------------------------------------------------------ watchlists
def _wl(w: Watchlist) -> dict[str, Any]:
    return {
        "id": w.id,
        "name": w.name,
        "items": [
            {"id": i.id, "symbol": i.symbol, "timeframe": i.timeframe, "strategies": i.strategies, "enabled": i.enabled}
            for i in w.items
        ],
    }


@app.get("/api/watchlists")
def list_watchlists(s: Session = DB, _: str = Auth) -> list[dict[str, Any]]:
    return [_wl(w) for w in s.scalars(select(Watchlist).order_by(Watchlist.id))]


@app.post("/api/watchlists")
def create_watchlist(body: WatchlistIn, s: Session = DB, actor: str = Auth) -> dict[str, Any]:
    w = Watchlist(name=body.name)
    s.add(w)
    s.flush()
    for it in body.items:
        s.add(
            WatchlistItem(
                watchlist_id=w.id,
                symbol=it.symbol,
                timeframe=it.timeframe.value,
                strategies={k: v.model_dump() for k, v in it.strategies.items()},
                enabled=it.enabled,
            )
        )
    s.flush()
    s.refresh(w)
    audit.record(s, "watchlist.created", "watchlist", str(w.id), body.model_dump(mode="json"), actor)
    return _wl(w)


@app.post("/api/watchlists/{wid}/items")
def add_item(wid: int, body: WatchlistItemIn, s: Session = DB, actor: str = Auth) -> dict[str, Any]:
    w = s.get(Watchlist, wid)
    if w is None:
        raise HTTPException(404, "watchlist not found")
    if len(w.items) >= 50:
        raise HTTPException(400, "watchlist limit is 50 items (bounded scanning)")
    s.add(
        WatchlistItem(
            watchlist_id=wid,
            symbol=body.symbol,
            timeframe=body.timeframe.value,
            strategies={k: v.model_dump() for k, v in body.strategies.items()},
            enabled=body.enabled,
        )
    )
    audit.record(s, "watchlist.item_added", "watchlist", str(wid), body.model_dump(mode="json"), actor)
    s.flush()
    s.refresh(w)
    return _wl(w)


@app.put("/api/watchlists/items/{item_id}")
def update_item(item_id: int, body: WatchlistItemIn, s: Session = DB, actor: str = Auth) -> dict[str, Any]:
    it = s.get(WatchlistItem, item_id)
    if it is None:
        raise HTTPException(404, "item not found")
    it.symbol, it.timeframe, it.enabled = body.symbol, body.timeframe.value, body.enabled
    it.strategies = {k: v.model_dump() for k, v in body.strategies.items()}
    audit.record(s, "watchlist.item_updated", "watchlist_item", str(item_id), body.model_dump(mode="json"), actor)
    return {"ok": True}


@app.delete("/api/watchlists/items/{item_id}")
def delete_item(item_id: int, s: Session = DB, actor: str = Auth) -> dict[str, Any]:
    it = s.get(WatchlistItem, item_id)
    if it is None:
        raise HTTPException(404, "item not found")
    s.delete(it)
    audit.record(s, "watchlist.item_deleted", "watchlist_item", str(item_id), {}, actor)
    return {"ok": True}


# ------------------------------------------------------------------ strategies / scans / signals
@app.get("/api/strategies")
def strategies(s: Session = DB, _: str = Auth) -> dict[str, Any]:
    elig = [
        {
            "symbol": e.symbol,
            "timeframe": e.timeframe,
            "strategy": e.strategy,
            "eligible": e.eligible,
            "run_id": e.run_id,
            "params": e.params,
            "provenance": e.provenance,
            "evaluated_at": e.evaluated_at.isoformat(),
        }
        for e in s.scalars(select(StrategyEligibility))
    ]
    return {"strategies": describe_strategies(), "eligibility": elig}


@app.get("/api/scan/snapshots")
def snapshots(s: Session = DB, _: str = Auth) -> list[dict[str, Any]]:
    return [
        {
            "symbol": r.symbol,
            "timeframe": r.timeframe,
            "strategy": r.strategy,
            "bar_ts": r.bar_ts,
            "direction": r.direction,
            "provenance": r.provenance,
            "reasons": r.reasons,
            "indicators": r.indicators,
            "data_quality": r.data_quality,
            "regime": r.regime,
            "updated_at": r.updated_at.isoformat(),
        }
        for r in s.scalars(select(ScanSnapshot).order_by(ScanSnapshot.symbol, ScanSnapshot.timeframe))
    ]


@app.post("/api/scan/run")
async def run_scan_now(request: Request, _: str = Auth) -> dict[str, Any]:
    from app.worker import scan_all

    coord: Coordinator = request.app.state.coordinator
    if not await coord.acquire("scan", ttl=300):
        raise HTTPException(409, "a scan is already running")
    try:
        return {"results": await scan_all(get_provider())}
    finally:
        await coord.release("scan")


@app.get("/api/signals")
def signals(
    status: str | None = None, symbol: str | None = None, limit: int = Query(100, le=500), s: Session = DB, _: str = Auth
) -> list[dict[str, Any]]:
    q = select(SignalRow).order_by(SignalRow.created_at.desc()).limit(limit)
    if status:
        q = q.where(SignalRow.status == status)
    if symbol:
        q = q.where(SignalRow.symbol == symbol)
    return [_sig(r) for r in s.scalars(q)]


@app.get("/api/signals/{sid}")
def signal_detail(sid: str, s: Session = DB, _: str = Auth) -> dict[str, Any]:
    r = s.get(SignalRow, sid)
    if r is None:
        raise HTTPException(404, "signal not found")
    from app.models import AlertDelivery

    d = _sig(r, full=True)
    d["deliveries"] = [
        {"event": a.event_kind, "channel": a.channel, "status": a.status, "attempts": a.attempts, "error": a.error}
        for a in s.scalars(select(AlertDelivery).where(AlertDelivery.signal_id == sid))
    ]
    return d


# ------------------------------------------------------------------ backtests
@app.post("/api/backtests")
async def create_backtest(body: BacktestIn, s: Session = DB, actor: str = Auth) -> dict[str, Any]:
    req = body.model_dump(mode="json")
    kind = req.pop("kind")
    run = await run_and_store(s, get_provider(), req, kind)
    audit.record(s, "backtest.requested", "backtest", run.id, {"actor": actor}, actor)
    return _bt(run, full=True)


def _bt(r: BacktestRun, full: bool = False) -> dict[str, Any]:
    d = {
        c: getattr(r, c)
        for c in (
            "id",
            "kind",
            "status",
            "symbol",
            "timeframe",
            "strategy",
            "provenance",
            "label",
            "config_hash",
            "dataset_hash",
            "error",
            "code_version",
        )
    }
    d["created_at"] = r.created_at.isoformat()
    net = (r.metrics or {}).get("net", {})
    d["summary"] = {
        k: net.get(k) for k in ("trades", "net_return_pct", "expectancy_r", "profit_factor", "max_drawdown_pct", "sharpe")
    }
    if full:
        d.update(
            {
                "request": r.request,
                "config": r.config,
                "dataset_range": r.dataset_range,
                "metrics": r.metrics,
                "report": r.report,
                "equity": r.equity,
            }
        )
    return d


@app.get("/api/backtests")
def list_backtests(s: Session = DB, _: str = Auth) -> list[dict[str, Any]]:
    return [_bt(r) for r in s.scalars(select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(100))]


@app.get("/api/backtests/{rid}")
def get_backtest(rid: str, s: Session = DB, _: str = Auth) -> dict[str, Any]:
    r = s.get(BacktestRun, rid)
    if r is None:
        raise HTTPException(404, "run not found")
    d = _bt(r, full=True)
    d["trades"] = [t.data for t in r.trades]
    return d


# ------------------------------------------------------------------ risk / audit / notifications / events
@app.get("/api/risk/config")
def get_risk(s: Session = DB, _: str = Auth) -> dict[str, Any]:
    return {**load_risk_config(s).to_dict(), "hard_max_risk_pct": HARD_MAX_RISK_PCT}


@app.put("/api/risk/config")
def put_risk(body: RiskConfigIn, s: Session = DB, actor: str = Auth) -> dict[str, Any]:
    try:
        return save_risk_config(s, body.changes, actor).to_dict()
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/risk/kill-switch")
def kill_switch(engaged: bool, s: Session = DB, actor: str = Auth) -> dict[str, Any]:
    return save_risk_config(s, {"kill_switch": engaged}, actor).to_dict()


@app.get("/api/audit")
def audit_log(limit: int = Query(200, le=1000), s: Session = DB, _: str = Auth) -> dict[str, Any]:
    rows = s.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)).all()
    return {
        "chain": audit.verify_chain(s),
        "entries": [
            {
                "id": r.id,
                "ts": r.ts.isoformat(),
                "actor": r.actor,
                "action": r.action,
                "entity": r.entity,
                "entity_id": r.entity_id,
                "payload": r.payload,
                "hash": r.hash,
            }
            for r in rows
        ],
    }


@app.get("/api/push/vapid-public-key")
def vapid_key(_: str = Auth) -> dict[str, Any]:
    return {"key": get_settings().vapid_public_key}


@app.post("/api/push/subscribe")
def push_subscribe(body: PushSubscriptionIn, s: Session = DB, actor: str = Auth) -> dict[str, Any]:
    if s.scalars(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint)).first() is None:
        s.add(PushSubscription(endpoint=body.endpoint, keys=body.keys))
        audit.record(s, "push.subscribed", "push", body.endpoint[-16:], {}, actor)
    return {"ok": True}


@app.post("/api/notifications/test")
async def test_notification(_: str = Auth) -> dict[str, Any]:
    from app.worker import build_dispatcher

    results = {}
    for ch in build_dispatcher().channels:
        if not ch.configured():
            results[ch.name] = "not configured"
            continue
        try:
            await ch.send("Deriv Signal Lab test", "Test notification - no signal. " + DISCLAIMER, {"event": "TEST"})
            results[ch.name] = "sent"
        except Exception as exc:
            results[ch.name] = f"failed: {str(exc)[:200]}"
    return results


@app.post("/api/events")
def import_events(events: list[EconomicEventIn], s: Session = DB, actor: str = Auth) -> dict[str, Any]:
    for e in events:
        s.add(EconomicEvent(**e.model_dump()))
    audit.record(s, "events.imported", "economic_events", str(len(events)), {"count": len(events)}, actor)
    return {"imported": len(events)}


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.error("api.unhandled", path=request.url.path, error=type(exc).__name__)
    return JSONResponse({"detail": "internal error"}, status_code=500)
