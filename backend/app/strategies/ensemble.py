"""Fixed-weight ensemble.

Weights are declared up front (``FIXED_WEIGHTS``) and are NOT fitted to any
data, so the ensemble cannot leak evaluation information. Rules:

* Any LONG together with any SHORT -> NO_TRADE (conflicting evidence).
* Otherwise the agreeing members' weighted score is the ensemble strength,
  and price levels come from the highest-weighted, highest-scored member.
* Strength is an ordinal 0..1 ranking aid, NOT a calibrated probability.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pandas as pd

from app.core.types import Direction, StrategyDecision
from app.strategies.base import Strategy

FIXED_WEIGHTS: dict[str, float] = {
    "trend_pullback": 1.0,
    "donchian_breakout": 1.0,
    "mean_reversion": 1.0,
    "momentum_continuation": 1.0,
}


class Ensemble(Strategy):
    name = "ensemble"
    description = "Fixed-weight agreement ensemble (weights not fitted)"
    default_params: ClassVar[dict[str, Any]] = {"min_agree": 1, "weights": FIXED_WEIGHTS}
    min_history = 0

    def __init__(self, members: list[Strategy], params: dict[str, Any] | None = None) -> None:
        super().__init__(params)
        self.members = members

    def _evaluate(self, f: pd.DataFrame, i: int) -> StrategyDecision:
        decisions = [m.evaluate(f, i) for m in self.members]
        return combine(decisions, self.params)

    def required_columns(self) -> list[str]:
        return ["close"]


def combine(decisions: list[StrategyDecision], params: dict[str, Any]) -> StrategyDecision:
    weights: dict[str, float] = params.get("weights", FIXED_WEIGHTS)
    longs = [d for d in decisions if d.direction is Direction.LONG]
    shorts = [d for d in decisions if d.direction is Direction.SHORT]
    member_summary = [f"{d.strategy}: {d.direction.value}" for d in decisions]
    base: dict[str, float | str | None] = {"members": ", ".join(member_summary)}
    if longs and shorts:
        return StrategyDecision(
            strategy="ensemble",
            direction=Direction.NO_TRADE,
            reasons=["Conflicting evidence between strategies -> NO_TRADE", *member_summary],
            indicators=base,
            params=dict(params),
        )
    agree = longs or shorts
    if len(agree) < int(params.get("min_agree", 1)):
        return StrategyDecision(
            strategy="ensemble",
            direction=Direction.NO_TRADE,
            reasons=[f"Fewer than {params.get('min_agree', 1)} agreeing strategies", *member_summary],
            indicators=base,
            params=dict(params),
        )
    total_w = sum(weights.get(d.strategy, 0.0) for d in decisions) or 1.0
    strength = sum(weights.get(d.strategy, 0.0) * d.score for d in agree) / total_w
    lead = max(agree, key=lambda d: (weights.get(d.strategy, 0.0), d.score))
    return StrategyDecision(
        strategy="ensemble",
        direction=lead.direction,
        reasons=[f"{len(agree)} of {len(decisions)} strategies agree ({lead.direction.value}); levels from {lead.strategy}"]
        + [f"[{d.strategy}] {r}" for d in agree for r in d.reasons],
        indicators={**lead.indicators, **base},
        params=dict(params),
        entry_ref=lead.entry_ref,
        stop=lead.stop,
        target=lead.target,
        invalidation=lead.invalidation,
        expiry_bars=lead.expiry_bars,
        max_hold_bars=lead.max_hold_bars,
        score=round(strength, 3),
    )
