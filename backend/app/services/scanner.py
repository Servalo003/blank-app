"""Scan pipeline: data -> quality -> features -> strategies -> fundamentals -> RISK ENGINE -> signal -> alert.

The risk engine sits on the only path from a strategy decision to a stored,
alertable signal. Signals are generated from fully CLOSED candles only.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.types import (
    HIGHER_TIMEFRAME,
    Candle,
    Direction,
    RiskStatus,
    SignalStatus,
    StrategyDecision,
    Timeframe,
    dataset_hash,
    stable_hash,
)
from app.data.provider import DataUnavailable, MarketDataProvider
from app.data.quality import normalize_candles
from app.fundamentals import service as fundamentals
from app.models import AppSetting, CandleRow, Instrument, ScanSnapshot, SignalEvent, SignalRow, StrategyEligibility, WatchlistItem
from app.notifications.dispatcher import Dispatcher
from app.products.adapter import build_adapter
from app.risk.engine import AlertLevel, OpenPosition, RiskContext, RiskEngine
from app.services import audit
from app.services.settings_store import load_risk_config
from app.strategies.features import compute_features
from app.strategies.registry import build_strategy

log = structlog.get_logger(__name__)


def persist_candles(s: Session, provider: str, symbol: str, candles: list[Candle]) -> None:
    if not candles:
        return
    rows = [
        {
            "provider": provider,
            "symbol": symbol,
            "granularity": c.granularity,
            "ts": c.ts,
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "ingested_at": datetime.now(UTC),
        }
        for c in candles
    ]
    if s.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    else:
        dialect_insert = sqlite_insert  # type: ignore[assignment]
    for k in range(0, len(rows), 500):
        s.execute(dialect_insert(CandleRow).values(rows[k : k + 500]).on_conflict_do_nothing())


async def load_closed_candles(
    provider: MarketDataProvider,
    symbol: str,
    tf: Timeframe,
    count: int,
    now: int,
    min_bars: int = 0,
    expect_continuous: bool = True,
) -> tuple[list[Candle], Any]:
    raw = await provider.get_candles(symbol, tf.seconds, count + 1)
    return normalize_candles(
        raw,
        tf.seconds,
        now,
        stale_after_bars=get_settings().stale_after_bars,
        min_bars=min_bars,
        expect_continuous=expect_continuous,
    )


def _open_positions(s: Session) -> list[OpenPosition]:
    rows = s.scalars(
        select(SignalRow).where(
            SignalRow.status == SignalStatus.ACTIVE.value, SignalRow.alert_level == AlertLevel.ACTIONABLE.value
        )
    ).all()
    return [OpenPosition(r.symbol, Direction(r.direction), float(r.risk.get("risk_amount", 0.0))) for r in rows]


def _paper_stats(s: Session, now: int) -> tuple[float, int, int | None]:
    """Today's realised paper P&L and current loss streak from closed ACTIONABLE signals."""
    day_start = now - now % 86400
    closed = s.scalars(
        select(SignalRow)
        .where(SignalRow.alert_level == AlertLevel.ACTIONABLE.value, SignalRow.outcome_r.is_not(None))
        .order_by(SignalRow.closed_at.desc())
        .limit(50)
    ).all()
    pnl_today = sum((r.outcome_r or 0) * float(r.risk.get("risk_amount", 0.0)) for r in closed if (r.closed_at or 0) >= day_start)
    streak = 0
    last_loss: int | None = None
    for r in closed:
        if (r.outcome_r or 0) < 0:
            streak += 1
            last_loss = last_loss or r.closed_at
        else:
            break
    return pnl_today, streak, last_loss


def _eligible(s: Session, symbol: str, tf: str, strategy: str) -> bool:
    row = s.scalars(
        select(StrategyEligibility).where(
            StrategyEligibility.symbol == symbol, StrategyEligibility.timeframe == tf, StrategyEligibility.strategy == strategy
        )
    ).first()
    return bool(row and row.eligible)


def update_lifecycle(s: Session, sig: SignalRow, bars: list[Candle]) -> list[tuple[str, str]]:
    """Advance an ACTIVE signal through new CLOSED bars. Returns (event, detail) transitions."""
    if sig.status != SignalStatus.ACTIVE.value:
        return []
    long = sig.direction == Direction.LONG.value
    for b in bars:
        if b.ts < sig.signal_ts:  # bars strictly after the signal bar only
            continue
        kind = detail = None
        stop_hit = sig.stop is not None and (b.low <= sig.stop if long else b.high >= sig.stop)
        tgt_hit = sig.target is not None and (b.high >= sig.target if long else b.low <= sig.target)
        risk = abs((sig.entry_ref or 0) - (sig.stop or 0)) or None
        if (
            b.close_ts <= sig.entry_window_until
            and sig.invalidation is not None
            and (b.close < sig.invalidation if long else b.close > sig.invalidation)
            and not stop_hit
        ):
            kind, detail = (
                "INVALIDATED",
                f"Bar closed {b.close:.5g} beyond invalidation {sig.invalidation:.5g} inside entry window",
            )
        elif stop_hit:
            kind = "STOP_HIT"
            detail = "Stop touched" + (" (target also touched in same bar; stop assumed first)" if tgt_hit else "")
            sig.outcome_r = -1.0
        elif tgt_hit:
            kind, detail = "TARGET_HIT", "Target touched"
            sig.outcome_r = abs((sig.target or 0) - (sig.entry_ref or 0)) / risk if risk else None
        elif b.close_ts >= sig.expires_at:
            kind, detail = "EXPIRED", "Maximum holding period reached (paper)"
            if risk:
                sig.outcome_r = ((b.close - (sig.entry_ref or 0)) * (1 if long else -1)) / risk
        if kind:
            sig.status = kind
            sig.closed_at = b.close_ts
            sig.close_reason = detail
            s.add(SignalEvent(signal_id=sig.id, kind=kind, detail=detail or ""))
            audit.record(
                s, f"signal.{kind.lower()}", "signal", sig.id, {"bar_ts": b.ts, "detail": detail, "outcome_r": sig.outcome_r}
            )
            return [(kind, detail or "")]
    return []


def _snapshot(
    s: Session, item: WatchlistItem, d: StrategyDecision, bar_ts: int, prov: str, dq: dict[str, Any], regime: dict[str, Any]
) -> None:
    row = s.scalars(
        select(ScanSnapshot).where(
            ScanSnapshot.symbol == item.symbol, ScanSnapshot.timeframe == item.timeframe, ScanSnapshot.strategy == d.strategy
        )
    ).first()
    if row is None:
        row = ScanSnapshot(symbol=item.symbol, timeframe=item.timeframe, strategy=d.strategy)
        s.add(row)
    row.bar_ts, row.direction, row.provenance = bar_ts, d.direction.value, prov
    row.reasons, row.indicators, row.data_quality, row.regime = d.reasons, d.indicators, dq, regime


async def scan_item(
    s: Session, provider: MarketDataProvider, item: WatchlistItem, dispatcher: Dispatcher | None, now: int | None = None
) -> dict[str, Any]:
    st = get_settings()
    tf = Timeframe(item.timeframe)
    now = now or await provider.server_time()
    inst = s.get(Instrument, item.symbol)
    market = inst.market if inst else "unknown"
    continuous = market in ("synthetic_index", "simulated")
    prov = provider.provenance
    summary: dict[str, Any] = {"symbol": item.symbol, "timeframe": item.timeframe, "signals": [], "events": []}
    try:
        candles, dq = await load_closed_candles(
            provider, item.symbol, tf, st.scan_lookback_bars, now, min_bars=260, expect_continuous=continuous
        )
    except DataUnavailable as exc:
        summary["error"] = str(exc)
        log.warning("scan.data_unavailable", symbol=item.symbol, tf=item.timeframe, error=str(exc))
        return summary
    persist_candles(s, provider.name, item.symbol, candles[-50:])
    try:  # live bid/ask stream for spread statistics (no-op for providers without one)
        await provider.latest_quote(item.symbol)
        stats = provider.spread_stats(item.symbol)
        if stats:
            srow = s.get(AppSetting, f"spread:{item.symbol}")
            if srow is None:
                s.add(AppSetting(key=f"spread:{item.symbol}", value=stats))
            else:
                srow.value = stats
    except Exception as exc:
        log.warning("scan.spread_stats_failed", symbol=item.symbol, error=str(exc))
    dq_d = asdict(dq)
    summary["data_quality"] = dq_d

    # lifecycle for existing ACTIVE signals on this symbol/timeframe
    for sig in s.scalars(
        select(SignalRow).where(
            SignalRow.symbol == item.symbol, SignalRow.timeframe == item.timeframe, SignalRow.status == SignalStatus.ACTIVE.value
        )
    ).all():
        new_bars = [c for c in candles if c.close_ts > sig.signal_ts]
        for kind, detail in update_lifecycle(s, sig, new_bars):
            summary["events"].append({"signal": sig.id, "event": kind})
            if dispatcher:
                await dispatcher.dispatch(s, sig, kind, detail)

    if not candles:
        return summary
    htf_tf = HIGHER_TIMEFRAME[tf]
    htf: list[Candle] | None = None
    if htf_tf.seconds > tf.seconds:
        try:
            htf, _ = await load_closed_candles(provider, item.symbol, htf_tf, 300, now, expect_continuous=False)
        except DataUnavailable:
            htf = None  # fall back to resampling closed LTF bars
    f = compute_features(candles, htf)
    i = len(candles) - 1
    bar = candles[i]
    row = f.iloc[i]
    regime = {"trend": str(row.get("trend_regime")), "volatility": str(row.get("vol_regime"))}
    risk_cfg = load_risk_config(s)
    engine = RiskEngine(risk_cfg)
    adapter = build_adapter(inst.product_spec if inst and inst.product_spec else None)
    ds_hash = dataset_hash(candles)

    for name, conf in (item.strategies or {}).items():
        if not conf.get("enabled", True):
            continue
        strat = build_strategy(name, conf.get("params") or None)
        d = strat.evaluate(f, i)
        if not dq.ok:
            d = StrategyDecision(
                strategy=name,
                direction=Direction.NO_TRADE,
                reasons=["Data quality gate: " + "; ".join(dq.issues)],
                indicators=d.indicators,
                params=strat.params,
            )
        _snapshot(s, item, d, bar.ts, prov.value, dq_d, regime)
        if d.direction is Direction.NO_TRADE:
            continue
        exists = s.scalar(
            select(func.count())
            .select_from(SignalRow)
            .where(
                SignalRow.symbol == item.symbol,
                SignalRow.timeframe == item.timeframe,
                SignalRow.strategy == name,
                SignalRow.signal_ts == bar.close_ts,
            )
        )
        if exists:
            continue  # idempotent per closed bar
        fund = fundamentals.assess(s, item.symbol, market, bar.close_ts)
        pnl_today, streak, last_loss = _paper_stats(s, now)
        ctx = RiskContext(
            equity=st.paper_equity + 0.0,
            open_positions=_open_positions(s),
            realized_pnl_today=pnl_today,
            consecutive_losses=streak,
            last_loss_ts=last_loss,
            now_ts=now,
            data_ok=dq.ok,
            data_issues=dq.issues,
            provenance=prov,
            strategy_eligible=_eligible(s, item.symbol, item.timeframe, name),
            fundamentals_block=fund.get("block"),
        )
        rd = engine.assess(d, item.symbol, adapter, ctx)
        status = SignalStatus.SUPPRESSED.value if rd.status is RiskStatus.REJECTED else SignalStatus.ACTIVE.value
        sig = SignalRow(
            provider=provider.name,
            provenance=prov.value,
            symbol=item.symbol,
            timeframe=item.timeframe,
            strategy=name,
            direction=d.direction.value,
            status=status,
            signal_ts=bar.close_ts,
            entry_window_until=bar.close_ts + max(1, d.expiry_bars) * tf.seconds,
            expires_at=bar.close_ts + max(1, d.max_hold_bars) * tf.seconds,
            entry_ref=d.entry_ref,
            stop=d.stop,
            target=d.target,
            invalidation=d.invalidation,
            score=d.score,
            reasons=d.reasons,
            indicators=d.indicators,
            params=d.params,
            params_hash=stable_hash(d.params),
            risk_config_hash=rd.config_hash,
            dataset_hash=ds_hash,
            data_quality=dq_d,
            risk=rd.to_dict(),
            risk_status=rd.status.value,
            alert_level=rd.alert_level.value,
            regime=regime,
            fundamentals=fund,
            code_version=st.git_sha,
        )
        s.add(sig)
        s.flush()
        s.add(SignalEvent(signal_id=sig.id, kind="CREATED", detail=f"risk={rd.status.value} alert={rd.alert_level.value}"))
        audit.record(
            s,
            "signal.created",
            "signal",
            sig.id,
            {
                "symbol": sig.symbol,
                "timeframe": sig.timeframe,
                "strategy": name,
                "direction": sig.direction,
                "signal_ts": sig.signal_ts,
                "provenance": sig.provenance,
                "params_hash": sig.params_hash,
                "dataset_hash": ds_hash,
                "risk": rd.to_dict(),
                "code_version": st.git_sha,
            },
        )
        summary["signals"].append(
            {"id": sig.id, "strategy": name, "direction": sig.direction, "risk": rd.status.value, "alert": rd.alert_level.value}
        )
        if dispatcher and status == SignalStatus.ACTIVE.value:
            await dispatcher.dispatch(s, sig, "CREATED")
    return summary
