"""Product adapters encapsulate product-specific behaviour.

Deriv offers several very different product families on the same underlying
(e.g. options/rise-fall, multipliers, CFDs on other platforms). They differ in
whether shorting exists, whether price stops exist and how P&L is computed.
Signal logic is product-agnostic; *risk* is not. When an adapter cannot compute
position risk reliably it returns ``RISK_UNAVAILABLE`` and actionable alerts
are suppressed by the risk engine.

Nothing here places orders. All numbers are research estimates.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.core.types import Direction


@dataclass(frozen=True)
class CostModel:
    """Per-fill transaction cost assumptions (price units unless noted).

    ``spread`` is the full bid/ask spread in price units; a fill pays half of it.
    ``slippage_atr`` is extra adverse movement per fill as a fraction of ATR.
    ``commission_bps`` is charged on notional per side.
    """

    spread: float = 0.0
    spread_bps: float = 0.0
    slippage_atr: float = 0.0
    commission_bps: float = 0.0
    source: str = "assumption"

    def half_spread(self, price: float) -> float:
        return max(self.spread, price * self.spread_bps / 1e4) / 2.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "spread": self.spread,
            "spread_bps": self.spread_bps,
            "slippage_atr": self.slippage_atr,
            "commission_bps": self.commission_bps,
            "source": self.source,
        }


@dataclass(frozen=True)
class RiskCalc:
    ok: bool
    units: float = 0.0
    risk_amount: float = 0.0  # estimated loss at stop incl. exit costs
    notional: float = 0.0
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProductSpec:
    product_type: str  # linear | multiplier | rise_fall
    contract_size: float | None = None
    min_units: float | None = None
    unit_step: float | None = None
    multiplier: float | None = None
    max_stake: float | None = None
    confirmed: bool = False  # operator verified the spec against the broker
    costs: CostModel = field(default_factory=CostModel)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ProductSpec:
        costs = CostModel(**d.get("costs", {}))
        return cls(
            product_type=d.get("product_type", "linear"),
            contract_size=d.get("contract_size"),
            min_units=d.get("min_units"),
            unit_step=d.get("unit_step"),
            multiplier=d.get("multiplier"),
            max_stake=d.get("max_stake"),
            confirmed=bool(d.get("confirmed", False)),
            costs=costs,
        )


class ProductAdapter(ABC):
    def __init__(self, spec: ProductSpec) -> None:
        self.spec = spec

    @property
    @abstractmethod
    def supports_short(self) -> bool: ...

    @property
    @abstractmethod
    def supports_price_stops(self) -> bool: ...

    @property
    def backtestable(self) -> bool:
        """True when P&L can be reproduced from OHLC paths."""
        return False

    @abstractmethod
    def size(self, direction: Direction, entry: float, stop: float, risk_budget: float) -> RiskCalc: ...

    def pnl(self, direction: Direction, entry: float, exit_: float, units: float) -> float:
        raise NotImplementedError

    def notional(self, price: float, units: float) -> float:
        return abs(price * units)

    def describe(self) -> dict[str, Any]:
        return {
            "product_type": self.spec.product_type,
            "supports_short": self.supports_short,
            "supports_price_stops": self.supports_price_stops,
            "backtestable": self.backtestable,
            "confirmed": self.spec.confirmed,
            "costs": self.spec.costs.to_dict(),
        }


class LinearAdapter(ProductAdapter):
    """Linear P&L = units * contract_size * price change (CFD-like research model)."""

    supports_short = True
    supports_price_stops = True

    @property
    def backtestable(self) -> bool:
        return True

    def _cs(self) -> float:
        return self.spec.contract_size or 1.0

    def size(self, direction: Direction, entry: float, stop: float, risk_budget: float) -> RiskCalc:
        notes: list[str] = []
        if not self.spec.confirmed:
            return RiskCalc(False, notes=["Product spec not operator-confirmed (contract size/min volume unknown)"])
        if self.spec.contract_size is None or self.spec.min_units is None:
            return RiskCalc(False, notes=["contract_size/min_units missing"])
        dist = abs(entry - stop)
        if dist <= 0:
            return RiskCalc(False, notes=["Stop distance is zero"])
        exit_cost = self.spec.costs.half_spread(stop) + entry * self.spec.costs.commission_bps / 1e4 * 2
        per_unit = (dist + exit_cost) * self._cs()
        units = risk_budget / per_unit
        step = self.spec.unit_step or self.spec.min_units
        units = (units // step) * step if step else units
        if units < self.spec.min_units:
            return RiskCalc(
                False,
                notes=[f"Required size {units:.4g} below broker minimum {self.spec.min_units}: risk budget too small"],
            )
        notes.append("Linear model; excludes overnight financing/swap")
        return RiskCalc(True, units=units, risk_amount=units * per_unit, notional=units * self._cs() * entry, notes=notes)

    def pnl(self, direction: Direction, entry: float, exit_: float, units: float) -> float:
        sign = 1 if direction is Direction.LONG else -1
        return sign * (exit_ - entry) * units * self._cs()

    def notional(self, price: float, units: float) -> float:
        return abs(price * units * self._cs())


class MultiplierAdapter(ProductAdapter):
    """Multiplier-style product: P&L ~= stake * multiplier * relative price change, loss capped at stake.

    The exact Deriv commission formula for multipliers was not verified for this
    build, so sizing requires an operator-confirmed spec (``confirmed=true``,
    ``multiplier`` and ``costs.commission_bps``). Otherwise RISK_UNAVAILABLE.
    """

    supports_short = True  # MULTUP / MULTDOWN contract types exist per active_symbols schema enum
    supports_price_stops = True

    @property
    def backtestable(self) -> bool:
        return self.spec.confirmed and self.spec.multiplier is not None

    def size(self, direction: Direction, entry: float, stop: float, risk_budget: float) -> RiskCalc:
        if not self.spec.confirmed or not self.spec.multiplier:
            return RiskCalc(False, notes=["Multiplier/commission not operator-confirmed"])
        m = self.spec.multiplier
        rel = abs(entry - stop) / entry
        comm = self.spec.costs.commission_bps / 1e4
        loss_per_stake = min(1.0, m * rel + m * comm)
        stake = risk_budget / loss_per_stake
        if self.spec.max_stake:
            stake = min(stake, self.spec.max_stake)
        return RiskCalc(
            True,
            units=stake,
            risk_amount=stake * loss_per_stake,
            notional=stake * m,
            notes=["Units are stake in account currency; loss capped at stake"],
        )

    def pnl(self, direction: Direction, entry: float, exit_: float, units: float) -> float:
        sign = 1 if direction is Direction.LONG else -1
        m = self.spec.multiplier or 0.0
        return max(-units, sign * units * m * (exit_ - entry) / entry)

    def notional(self, price: float, units: float) -> float:
        return abs(units * (self.spec.multiplier or 0.0))


class RiseFallAdapter(ProductAdapter):
    """Fixed-expiry rise/fall options: no price stops; payout depends on a live proposal.

    Without a verified payout, position risk in R-multiples cannot be computed
    reliably -> always RISK_UNAVAILABLE (research-only signals).
    """

    supports_short = True  # direction expressed by buying the opposite contract, not by shorting
    supports_price_stops = False

    def size(self, direction: Direction, entry: float, stop: float, risk_budget: float) -> RiskCalc:
        return RiskCalc(False, notes=["Rise/fall options have no price stop; payout requires a live proposal (not used)"])


def build_adapter(spec: ProductSpec | dict[str, Any] | None) -> ProductAdapter | None:
    if spec is None:
        return None
    if isinstance(spec, dict):
        spec = ProductSpec.from_dict(spec)
    classes: dict[str, type[ProductAdapter]] = {
        "linear": LinearAdapter,
        "multiplier": MultiplierAdapter,
        "rise_fall": RiseFallAdapter,
    }
    if spec.product_type not in classes:
        raise ValueError(f"unknown product_type {spec.product_type!r}")
    return classes[spec.product_type](spec)
