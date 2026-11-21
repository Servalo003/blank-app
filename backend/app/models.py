from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


class Instrument(Base):
    __tablename__ = "instruments"
    symbol: Mapped[str] = mapped_column(String(40), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120))
    market: Mapped[str] = mapped_column(String(60), default="")
    submarket: Mapped[str] = mapped_column(String(60), default="")
    provider: Mapped[str] = mapped_column(String(20))
    # Product spec drives the product adapter. Null -> RISK_UNAVAILABLE.
    product_spec: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    periods_per_year: Mapped[int] = mapped_column(Integer, default=365)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Watchlist(Base):
    __tablename__ = "watchlists"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    items: Mapped[list[WatchlistItem]] = relationship(back_populates="watchlist", cascade="all, delete-orphan")


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (UniqueConstraint("watchlist_id", "symbol", "timeframe"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    watchlist_id: Mapped[int] = mapped_column(ForeignKey("watchlists.id", ondelete="CASCADE"))
    symbol: Mapped[str] = mapped_column(String(40))
    timeframe: Mapped[str] = mapped_column(String(8))
    # {strategy_name: {"enabled": bool, "params": {...}}}
    strategies: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    watchlist: Mapped[Watchlist] = relationship(back_populates="items")


class CandleRow(Base):
    __tablename__ = "candles"
    __table_args__ = (UniqueConstraint("provider", "symbol", "granularity", "ts"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(20))
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    granularity: Mapped[int] = mapped_column(Integer)
    ts: Mapped[int] = mapped_column(BigInteger, index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScanSnapshot(Base):
    """Latest evaluation per (symbol, timeframe, strategy) - including NO_TRADE."""

    __tablename__ = "scan_snapshots"
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "strategy"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(40))
    timeframe: Mapped[str] = mapped_column(String(8))
    strategy: Mapped[str] = mapped_column(String(40))
    bar_ts: Mapped[int] = mapped_column(BigInteger)
    direction: Mapped[str] = mapped_column(String(10))
    provenance: Mapped[str] = mapped_column(String(20))
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    indicators: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    data_quality: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    regime: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class SignalRow(Base):
    __tablename__ = "signals"
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "strategy", "signal_ts", name="uq_signal_bar"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    provider: Mapped[str] = mapped_column(String(20))
    provenance: Mapped[str] = mapped_column(String(20))
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    timeframe: Mapped[str] = mapped_column(String(8))
    strategy: Mapped[str] = mapped_column(String(40))
    direction: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), index=True)
    signal_ts: Mapped[int] = mapped_column(BigInteger)  # close time of the signal bar
    entry_window_until: Mapped[int] = mapped_column(BigInteger)
    expires_at: Mapped[int] = mapped_column(BigInteger)
    entry_ref: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop: Mapped[float | None] = mapped_column(Float, nullable=True)
    target: Mapped[float | None] = mapped_column(Float, nullable=True)
    invalidation: Mapped[float | None] = mapped_column(Float, nullable=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    indicators: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    params_hash: Mapped[str] = mapped_column(String(64))
    risk_config_hash: Mapped[str] = mapped_column(String(64))
    dataset_hash: Mapped[str] = mapped_column(String(64))
    data_quality: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    risk: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    risk_status: Mapped[str] = mapped_column(String(20))
    alert_level: Mapped[str] = mapped_column(String(20))
    regime: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    fundamentals: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    code_version: Mapped[str] = mapped_column(String(40))
    closed_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    outcome_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    events: Mapped[list[SignalEvent]] = relationship(
        back_populates="signal", cascade="all, delete-orphan", order_by="SignalEvent.id"
    )


class SignalEvent(Base):
    __tablename__ = "signal_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[str] = mapped_column(ForeignKey("signals.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    kind: Mapped[str] = mapped_column(String(20))
    detail: Mapped[str] = mapped_column(Text, default="")
    signal: Mapped[SignalRow] = relationship(back_populates="events")


class AlertDelivery(Base):
    __tablename__ = "alert_deliveries"
    __table_args__ = (UniqueConstraint("signal_id", "event_kind", "channel", name="uq_alert_once"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[str] = mapped_column(ForeignKey("signals.id", ondelete="CASCADE"), index=True)
    event_kind: Mapped[str] = mapped_column(String(20))
    channel: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))  # SENT | FAILED | SKIPPED
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(20), default="backtest")  # backtest | evaluation
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    symbol: Mapped[str] = mapped_column(String(40))
    timeframe: Mapped[str] = mapped_column(String(8))
    strategy: Mapped[str] = mapped_column(String(40))
    provenance: Mapped[str] = mapped_column(String(20), default="")
    request: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    config_hash: Mapped[str] = mapped_column(String(64), default="")
    dataset_hash: Mapped[str] = mapped_column(String(64), default="")
    dataset_range: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    equity: Mapped[list[Any]] = mapped_column(JSON, default=list)
    label: Mapped[str | None] = mapped_column(String(40), nullable=True)
    code_version: Mapped[str] = mapped_column(String(40), default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    trades: Mapped[list[BacktestTrade]] = relationship(cascade="all, delete-orphan", order_by="BacktestTrade.trade_no")


class BacktestTrade(Base):
    __tablename__ = "backtest_trades"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("backtest_runs.id", ondelete="CASCADE"), index=True)
    trade_no: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)


class StrategyEligibility(Base):
    __tablename__ = "strategy_eligibility"
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "strategy"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(40))
    timeframe: Mapped[str] = mapped_column(String(8))
    strategy: Mapped[str] = mapped_column(String(40))
    eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    run_id: Mapped[str] = mapped_column(String(32))
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    provenance: Mapped[str] = mapped_column(String(20), default="")
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EconomicEvent(Base):
    """Point-in-time calendar: each row is what was KNOWN at ``known_at`` (revisions are new rows)."""

    __tablename__ = "economic_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    currency: Mapped[str] = mapped_column(String(8), index=True)
    title: Mapped[str] = mapped_column(String(200))
    impact: Mapped[str] = mapped_column(String(10))  # low | medium | high
    scheduled_at: Mapped[int] = mapped_column(BigInteger, index=True)
    known_at: Mapped[int] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(120))
    actual: Mapped[str | None] = mapped_column(String(40), nullable=True)
    forecast: Mapped[str | None] = mapped_column(String(40), nullable=True)
    previous: Mapped[str | None] = mapped_column(String(40), nullable=True)


class AuditLog(Base):
    """Append-only, hash-chained audit trail."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor: Mapped[str] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    keys: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
