"""Centralised, non-bypassable risk engine.

Every directional strategy decision - live scan or backtest - passes through
``RiskEngine.assess``. ``RiskDecision`` objects can only be constructed by the
engine (guarded by a module-private token), and the alert dispatcher refuses to
send anything that does not carry an APPROVED decision (or an explicitly
enabled research-only flag).

Design rules:
* Risk per trade is a fixed fraction of equity, hard-capped by ``HARD_MAX_RISK_PCT``.
* Risk NEVER increases after losses. After ``loss_streak_reduce`` consecutive
  losses it is halved; after ``loss_streak_pause`` the engine pauses new signals
  for ``pause_hours`` and then resumes at the reduced risk until a winning trade.
  There is no martingale, averaging down, grid or "recovery" sizing anywhere.
* Daily loss limit, max open positions, per-symbol limit, aggregate open risk.
* Data gates: stale/poor-quality data, simulated data without opt-in, and
  strategies not yet eligible (out-of-sample) cannot produce actionable alerts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.core.types import DataProvenance, Direction, RiskStatus, StrategyDecision, stable_hash
from app.products.adapter import ProductAdapter

HARD_MAX_RISK_PCT = 2.0  # absolute ceiling, not configurable at runtime
_ENGINE_TOKEN = object()


class AlertLevel(StrEnum):
    ACTIONABLE = "ACTIONABLE"  # paper-trade alert, all checks passed
    RESEARCH_ONLY = "RESEARCH_ONLY"  # informational; risk unavailable / not eligible
    NONE = "NONE"


@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade_pct: float = 0.5
    max_daily_loss_pct: float = 2.0
    max_open_positions: int = 3
    max_positions_per_symbol: int = 1
    max_total_open_risk_pct: float = 1.5
    loss_streak_reduce: int = 3
    loss_streak_pause: int = 6
    pause_hours: float = 24.0
    min_score: float = 0.0
    min_reward_risk: float = 1.0
    allow_simulated_actionable: bool = False
    research_only_alerts: bool = False
    require_eligibility: bool = True
    kill_switch: bool = False

    def __post_init__(self) -> None:
        if not 0 < self.risk_per_trade_pct <= HARD_MAX_RISK_PCT:
            raise ValueError(f"risk_per_trade_pct must be in (0, {HARD_MAX_RISK_PCT}]")
        if self.loss_streak_reduce > self.loss_streak_pause:
            raise ValueError("loss_streak_reduce must be <= loss_streak_pause")

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class OpenPosition:
    symbol: str
    direction: Direction
    risk_amount: float


@dataclass
class RiskContext:
    equity: float
    open_positions: list[OpenPosition] = field(default_factory=list)
    realized_pnl_today: float = 0.0
    consecutive_losses: int = 0
    last_loss_ts: int | None = None
    now_ts: int | None = None
    data_ok: bool = True
    data_issues: list[str] = field(default_factory=list)
    provenance: DataProvenance = DataProvenance.SIMULATED_DATA
    strategy_eligible: bool = False
    fundamentals_block: str | None = None  # e.g. high-impact event window


@dataclass(frozen=True)
class RiskDecision:
    status: RiskStatus
    alert_level: AlertLevel
    units: float
    risk_amount: float
    risk_pct: float
    checks: tuple[tuple[str, bool, str], ...]
    reasons: tuple[str, ...]
    config_hash: str
    _token: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._token is not _ENGINE_TOKEN:
            raise PermissionError("RiskDecision can only be created by RiskEngine")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "alert_level": self.alert_level.value,
            "units": self.units,
            "risk_amount": self.risk_amount,
            "risk_pct": self.risk_pct,
            "checks": [{"name": n, "passed": p, "detail": d} for n, p, d in self.checks],
            "reasons": list(self.reasons),
            "config_hash": self.config_hash,
        }


class RiskEngine:
    def __init__(self, config: RiskConfig) -> None:
        self.config = config
        self.config_hash = stable_hash(config.to_dict())

    def effective_risk_pct(self, consecutive_losses: int) -> float:
        """Risk fraction after loss-streak de-risking. Monotonically non-increasing in losses."""
        pct = self.config.risk_per_trade_pct
        if consecutive_losses >= self.config.loss_streak_reduce:
            pct *= 0.5
        return min(pct, HARD_MAX_RISK_PCT)

    def _make(self, status: RiskStatus, level: AlertLevel, checks: list[tuple[str, bool, str]], **kw: Any) -> RiskDecision:
        reasons = tuple(f"{n}: {d}" for n, ok, d in checks if not ok)
        return RiskDecision(
            status=status,
            alert_level=level,
            units=kw.get("units", 0.0),
            risk_amount=kw.get("risk_amount", 0.0),
            risk_pct=kw.get("risk_pct", 0.0),
            checks=tuple(checks),
            reasons=reasons,
            config_hash=self.config_hash,
            _token=_ENGINE_TOKEN,
        )

    def assess(
        self,
        decision: StrategyDecision,
        symbol: str,
        adapter: ProductAdapter | None,
        ctx: RiskContext,
        *,
        backtest: bool = False,
    ) -> RiskDecision:
        cfg = self.config
        checks: list[tuple[str, bool, str]] = []

        def chk(name: str, ok: bool, detail: str) -> bool:
            checks.append((name, ok, detail))
            return ok

        if decision.direction is Direction.NO_TRADE:
            chk("direction", False, "Strategy returned NO_TRADE")
            return self._make(RiskStatus.REJECTED, AlertLevel.NONE, checks)

        hard_ok = True
        hard_ok &= chk("kill_switch", not cfg.kill_switch, "Kill switch engaged" if cfg.kill_switch else "off")
        hard_ok &= chk("data_quality", ctx.data_ok, "; ".join(ctx.data_issues) if not ctx.data_ok else "ok")
        hard_ok &= chk(
            "levels",
            decision.entry_ref is not None and decision.stop is not None and decision.target is not None,
            "entry/stop/target present" if decision.stop is not None else "missing stop or target",
        )
        if decision.entry_ref is not None and decision.stop is not None and decision.target is not None:
            risk = abs(decision.entry_ref - decision.stop)
            reward = abs(decision.target - decision.entry_ref)
            rr = reward / risk if risk > 0 else 0.0
            stop_side_ok = (
                (decision.stop < decision.entry_ref)
                if decision.direction is Direction.LONG
                else (decision.stop > decision.entry_ref)
            )
            hard_ok &= chk("stop_side", stop_side_ok, "stop on the loss side of entry" if stop_side_ok else "stop on wrong side")
            hard_ok &= chk("reward_risk", rr >= cfg.min_reward_risk, f"R/R {rr:.2f} vs min {cfg.min_reward_risk}")
        hard_ok &= chk("min_score", decision.score >= cfg.min_score, f"score {decision.score} vs min {cfg.min_score}")
        if ctx.fundamentals_block:
            hard_ok &= chk("event_window", False, ctx.fundamentals_block)

        pause = ctx.consecutive_losses >= cfg.loss_streak_pause and (
            ctx.last_loss_ts is None or ctx.now_ts is None or ctx.now_ts - ctx.last_loss_ts < cfg.pause_hours * 3600
        )
        hard_ok &= chk(
            "loss_streak",
            not pause,
            f"{ctx.consecutive_losses} consecutive losses (pause {cfg.pause_hours}h at {cfg.loss_streak_pause}; "
            "risk stays halved until a win)",
        )
        daily_limit = ctx.equity * cfg.max_daily_loss_pct / 100
        hard_ok &= chk(
            "daily_loss",
            -ctx.realized_pnl_today < daily_limit,
            f"today's P&L {ctx.realized_pnl_today:.2f} vs limit -{daily_limit:.2f}",
        )
        hard_ok &= chk(
            "max_open_positions",
            len(ctx.open_positions) < cfg.max_open_positions,
            f"{len(ctx.open_positions)} open (max {cfg.max_open_positions})",
        )
        same_symbol = sum(1 for p in ctx.open_positions if p.symbol == symbol)
        hard_ok &= chk(
            "per_symbol",
            same_symbol < cfg.max_positions_per_symbol,
            f"{same_symbol} open on {symbol} (max {cfg.max_positions_per_symbol}); no averaging down",
        )

        risk_pct = self.effective_risk_pct(ctx.consecutive_losses)
        budget = ctx.equity * risk_pct / 100
        open_risk = sum(p.risk_amount for p in ctx.open_positions)
        max_open_risk = ctx.equity * cfg.max_total_open_risk_pct / 100
        if open_risk + budget > max_open_risk:
            budget = max(0.0, max_open_risk - open_risk)
        hard_ok &= chk("aggregate_risk", budget > 0, f"open risk {open_risk:.2f} of max {max_open_risk:.2f}")

        if not hard_ok:
            return self._make(RiskStatus.REJECTED, AlertLevel.NONE, checks)

        # ---- product-specific risk ----
        if adapter is None:
            chk("product", False, "No product adapter configured for symbol")
            return self._unavailable(checks)
        if decision.direction is Direction.SHORT and not adapter.supports_short:
            chk("product_short", False, "Product does not support short exposure")
            return self._make(RiskStatus.REJECTED, AlertLevel.NONE, checks)
        if not adapter.supports_price_stops:
            chk("product_stops", False, "Product has no price stop; loss at stop cannot be bounded as modelled")
            return self._unavailable(checks)
        assert decision.entry_ref is not None and decision.stop is not None
        calc = adapter.size(decision.direction, decision.entry_ref, decision.stop, budget)
        if not calc.ok:
            chk("position_risk", False, "; ".join(calc.notes))
            return self._unavailable(checks)
        chk("position_risk", True, f"units {calc.units:.6g}, est. loss at stop {calc.risk_amount:.2f}")

        if backtest:
            return self._make(
                RiskStatus.APPROVED, AlertLevel.NONE, checks, units=calc.units, risk_amount=calc.risk_amount, risk_pct=risk_pct
            )

        level = AlertLevel.ACTIONABLE
        if ctx.provenance is DataProvenance.SIMULATED_DATA and not cfg.allow_simulated_actionable:
            chk("provenance", False, "Simulated data: alert is research-only")
            level = AlertLevel.RESEARCH_ONLY
        elif ctx.provenance is DataProvenance.DELAYED_DATA:
            chk("provenance", False, "Delayed data: alert is research-only")
            level = AlertLevel.RESEARCH_ONLY
        if cfg.require_eligibility and not ctx.strategy_eligible:
            chk("eligibility", False, "Strategy not eligible for paper alerts (no passing out-of-sample evaluation)")
            level = AlertLevel.RESEARCH_ONLY
        if level is AlertLevel.RESEARCH_ONLY and not cfg.research_only_alerts:
            level = AlertLevel.NONE
        return self._make(RiskStatus.APPROVED, level, checks, units=calc.units, risk_amount=calc.risk_amount, risk_pct=risk_pct)

    def _unavailable(self, checks: list[tuple[str, bool, str]]) -> RiskDecision:
        level = AlertLevel.RESEARCH_ONLY if self.config.research_only_alerts else AlertLevel.NONE
        return self._make(RiskStatus.RISK_UNAVAILABLE, level, checks)
