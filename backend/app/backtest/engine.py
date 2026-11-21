"""Event-driven, leak-free bar backtester.

Timeline per bar ``i`` (two events):

1. **OPEN event of bar i** - orders created at the *close of bar i-1* fill here,
   at bar i's open plus half-spread plus slippage. This is the earliest
   tradable price after the signal (signal_ts = close of bar i-1 <= entry_ts).
   Then the bar's high/low is checked against stop/target of the open position.
   If both are inside the bar the order is unknown, so we assume the **stop**
   was hit first (conservative) and flag the trade ``ambiguous_bar``.
   A gap through the stop fills at the (worse) open price.
2. **CLOSE event of bar i** - the strategy sees rows ``0..i`` only and may
   create an entry (flat) or exit (open position) for bar i+1's open.

No order ever fills on the bar that generated it, and strategies never read
row ``i+1``. ``strict=True`` recomputes all features on ``candles[:i+1]`` at
every bar to prove the pre-computed causal features give identical results
(see tests).

Costs: half-spread on each fill, ATR-scaled adverse slippage on market fills
(entries, stops, discretionary exits; not on target limit fills), commission
in bps of notional per side. Gross P&L is computed on the same trades at mid
prices without costs so results can be shown before and after costs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from app.core.types import Candle, DataProvenance, Direction, RiskStatus, dataset_hash, stable_hash
from app.products.adapter import CostModel, ProductAdapter
from app.risk.engine import RiskConfig, RiskContext, RiskEngine
from app.strategies.base import Strategy
from app.strategies.features import FEATURE_VERSION, compute_features

ENGINE_VERSION = "bt-engine-v1"


@dataclass
class Trade:
    trade_id: int
    symbol: str
    strategy: str
    direction: str
    signal_ts: int
    entry_ts: int
    entry_price: float
    entry_mid: float
    stop: float
    target: float
    units: float
    risk_amount: float
    exit_ts: int | None = None
    exit_price: float | None = None
    exit_mid: float | None = None
    exit_reason: str | None = None
    gross_pnl: float = 0.0
    costs: float = 0.0
    net_pnl: float = 0.0
    r_net: float = 0.0
    r_gross: float = 0.0
    bars_held: int = 0
    ambiguous_bar: bool = False
    trend_regime: str = ""
    vol_regime: str = ""
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BacktestConfig:
    symbol: str
    strategy: str
    params: dict[str, Any]
    timeframe: str
    initial_equity: float = 10_000.0
    costs: CostModel = field(default_factory=CostModel)
    risk: RiskConfig = field(default_factory=RiskConfig)
    start_index: int = 0
    end_index: int | None = None  # exclusive
    seed: int = 42
    strict: bool = False
    provenance: str = DataProvenance.SIMULATED_DATA.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "strategy": self.strategy,
            "params": self.params,
            "timeframe": self.timeframe,
            "initial_equity": self.initial_equity,
            "costs": self.costs.to_dict(),
            "risk": self.risk.to_dict(),
            "start_index": self.start_index,
            "end_index": self.end_index,
            "seed": self.seed,
            "engine_version": ENGINE_VERSION,
            "feature_version": FEATURE_VERSION,
            "provenance": self.provenance,
        }


@dataclass
class BacktestResult:
    config: dict[str, Any]
    config_hash: str
    dataset_hash: str
    trades: list[Trade]
    equity: list[tuple[int, float]]  # (close_ts, equity)
    equity_gross: list[tuple[int, float]]
    rejected_signals: int
    risk_unavailable_signals: int
    bars: int
    start_ts: int
    end_ts: int


@dataclass
class _Pending:
    kind: str  # "entry" | "exit"
    direction: Direction
    signal_ts: int
    stop: float = 0.0
    target: float = 0.0
    invalidation: float | None = None
    units: float = 0.0
    risk_amount: float = 0.0
    reason: str = ""
    max_hold: int = 0
    trend_regime: str = ""
    vol_regime: str = ""
    reasons: list[str] = field(default_factory=list)


def _day(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d")


def run_backtest(
    candles: list[Candle],
    strategy: Strategy,
    adapter: ProductAdapter,
    cfg: BacktestConfig,
    features: pd.DataFrame | None = None,
) -> BacktestResult:
    if not adapter.backtestable:
        raise ValueError("Product adapter is not backtestable (P&L cannot be reproduced from OHLC)")
    if len(candles) < 2:
        raise ValueError("need at least 2 candles")
    f = features if features is not None and not cfg.strict else compute_features(candles)
    end = len(candles) if cfg.end_index is None else min(cfg.end_index, len(candles))
    start = max(cfg.start_index, 1)
    engine = RiskEngine(cfg.risk)
    costs = cfg.costs

    equity = cfg.initial_equity
    equity_gross = cfg.initial_equity
    curve: list[tuple[int, float]] = []
    curve_g: list[tuple[int, float]] = []
    trades: list[Trade] = []
    pos: Trade | None = None
    pos_dir: Direction | None = None
    pos_max_hold = 0
    pending: _Pending | None = None
    consecutive_losses = 0
    last_loss_ts: int | None = None
    daily_pnl: dict[str, float] = {}
    rejected = 0
    unavailable = 0

    def slip(i: int) -> float:
        a = f["atr14"].iloc[i - 1] if i > 0 else float("nan")
        return 0.0 if pd.isna(a) else costs.slippage_atr * float(a)

    def commission(price: float, units: float) -> float:
        return adapter.notional(price, units) * costs.commission_bps / 1e4

    def close_position(i: int, ts: int, fill: float, mid: float, reason: str, is_market: bool) -> None:
        nonlocal pos, pos_dir, equity, equity_gross, consecutive_losses, last_loss_ts
        assert pos is not None and pos_dir is not None
        pos.exit_ts, pos.exit_price, pos.exit_mid, pos.exit_reason = ts, fill, mid, reason
        pos.gross_pnl = adapter.pnl(pos_dir, pos.entry_mid, mid, pos.units)
        net = adapter.pnl(pos_dir, pos.entry_price, fill, pos.units)
        net -= commission(pos.entry_price, pos.units) + commission(fill, pos.units)
        pos.net_pnl = net
        pos.costs = pos.gross_pnl - net
        pos.r_net = net / pos.risk_amount if pos.risk_amount else 0.0
        pos.r_gross = pos.gross_pnl / pos.risk_amount if pos.risk_amount else 0.0
        equity += net
        equity_gross += pos.gross_pnl
        d = _day(ts)
        daily_pnl[d] = daily_pnl.get(d, 0.0) + net
        consecutive_losses = consecutive_losses + 1 if net < 0 else 0
        if net < 0:
            last_loss_ts = ts
        trades.append(pos)
        pos, pos_dir = None, None

    for i in range(start, end):
        c = candles[i]
        hs = costs.half_spread(c.open)
        # ---------------- OPEN event ----------------
        if pending is not None:
            sign = 1 if pending.direction is Direction.LONG else -1
            if pending.kind == "exit" and pos is not None:
                fill = c.open - sign * (hs + slip(i))
                close_position(i, c.ts, fill, c.open, pending.reason, True)
            elif pending.kind == "entry" and pos is None:
                gapped = (sign > 0 and c.open - hs <= pending.stop) or (sign < 0 and c.open + hs >= pending.stop)
                inv = pending.invalidation
                invalid = inv is not None and ((sign > 0 and c.open < inv) or (sign < 0 and c.open > inv))
                if gapped or invalid:
                    rejected += 1
                else:
                    fill = c.open + sign * (hs + slip(i))
                    pos = Trade(
                        trade_id=len(trades) + 1,
                        symbol=cfg.symbol,
                        strategy=strategy.name,
                        direction=pending.direction.value,
                        signal_ts=pending.signal_ts,
                        entry_ts=c.ts,
                        entry_price=fill,
                        entry_mid=c.open,
                        stop=pending.stop,
                        target=pending.target,
                        units=pending.units,
                        risk_amount=pending.risk_amount,
                        trend_regime=pending.trend_regime,
                        vol_regime=pending.vol_regime,
                        reasons=pending.reasons,
                    )
                    pos_dir = pending.direction
                    pos_max_hold = pending.max_hold
            pending = None

        # ------------- intrabar stop/target -------------
        if pos is not None and pos_dir is not None:
            sign = 1 if pos_dir is Direction.LONG else -1
            hs_bar = costs.half_spread(pos.stop)
            if sign > 0:
                stop_hit = c.low - hs_bar <= pos.stop
                tgt_hit = c.high - hs_bar >= pos.target
            else:
                stop_hit = c.high + hs_bar >= pos.stop
                tgt_hit = c.low + hs_bar <= pos.target
            if stop_hit:
                pos.ambiguous_bar = bool(tgt_hit)
                gap = c.ts != pos.entry_ts and ((sign > 0 and c.open - hs <= pos.stop) or (sign < 0 and c.open + hs >= pos.stop))
                fill = (c.open - sign * hs if gap else pos.stop) - sign * slip(i)
                mid = c.open if gap else pos.stop + sign * hs_bar
                why = "stop (gap through stop)" if gap else "stop"
                if tgt_hit:
                    why += " (ambiguous bar: stop assumed first)"
                close_position(i, c.close_ts, fill, mid, why, True)
            elif tgt_hit:
                close_position(i, c.close_ts, pos.target, pos.target + sign * hs_bar, "target", False)

        # ---------------- CLOSE event ----------------
        if pos is not None and pos_dir is not None:
            pos.bars_held += 1
            reason = strategy.exit_signal(f if not cfg.strict else compute_features(candles[: i + 1]), i, pos_dir)
            if reason is None and pos_max_hold and pos.bars_held >= pos_max_hold:
                reason = f"max hold {pos_max_hold} bars"
            if reason and i + 1 < end:
                pending = _Pending(kind="exit", direction=pos_dir, signal_ts=c.close_ts, reason=reason)
        elif pending is None and i + 1 < end:
            fi = compute_features(candles[: i + 1]) if cfg.strict else f
            d = strategy.evaluate(fi, i)
            if d.direction is not Direction.NO_TRADE:
                ctx = RiskContext(
                    equity=equity,
                    open_positions=[],
                    realized_pnl_today=daily_pnl.get(_day(c.close_ts), 0.0),
                    consecutive_losses=consecutive_losses,
                    last_loss_ts=last_loss_ts,
                    now_ts=c.close_ts,
                    data_ok=True,
                    strategy_eligible=True,
                )
                rd = engine.assess(d, cfg.symbol, adapter, ctx, backtest=True)
                if rd.status is RiskStatus.APPROVED:
                    row = fi.iloc[i]
                    pending = _Pending(
                        kind="entry",
                        direction=d.direction,
                        signal_ts=c.close_ts,
                        stop=float(d.stop),  # type: ignore[arg-type]
                        target=float(d.target),  # type: ignore[arg-type]
                        invalidation=d.invalidation,
                        units=rd.units,
                        risk_amount=rd.risk_amount,
                        max_hold=d.max_hold_bars,
                        trend_regime=str(row.get("trend_regime", "")),
                        vol_regime=str(row.get("vol_regime", "")),
                        reasons=d.reasons[:6],
                    )
                elif rd.status is RiskStatus.RISK_UNAVAILABLE:
                    unavailable += 1
                else:
                    rejected += 1

        # mark to market at close (mid)
        unreal = adapter.pnl(pos_dir, pos.entry_price, c.close, pos.units) if pos is not None and pos_dir else 0.0
        unreal_g = adapter.pnl(pos_dir, pos.entry_mid, c.close, pos.units) if pos is not None and pos_dir else 0.0
        curve.append((c.close_ts, equity + unreal))
        curve_g.append((c.close_ts, equity_gross + unreal_g))

    if pos is not None and pos_dir is not None:
        last = candles[end - 1]
        sign = 1 if pos_dir is Direction.LONG else -1
        close_position(
            end - 1, last.close_ts, last.close - sign * costs.half_spread(last.close), last.close, "end of test window", True
        )
        curve[-1] = (last.close_ts, equity)
        curve_g[-1] = (last.close_ts, equity_gross)

    window = candles[start:end]
    config_dict = cfg.to_dict()
    return BacktestResult(
        config=config_dict,
        config_hash=stable_hash(config_dict),
        dataset_hash=dataset_hash(candles[:end]),
        trades=trades,
        equity=curve,
        equity_gross=curve_g,
        rejected_signals=rejected,
        risk_unavailable_signals=unavailable,
        bars=len(window),
        start_ts=window[0].ts if window else 0,
        end_ts=window[-1].close_ts if window else 0,
    )
