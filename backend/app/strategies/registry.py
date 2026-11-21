from __future__ import annotations

from typing import Any

from app.strategies.base import Strategy
from app.strategies.breakout import DonchianBreakout
from app.strategies.ensemble import Ensemble
from app.strategies.mean_reversion import MeanReversion
from app.strategies.momentum import MomentumContinuation
from app.strategies.trend_pullback import TrendPullback

BASE_STRATEGIES: dict[str, type[Strategy]] = {
    TrendPullback.name: TrendPullback,
    DonchianBreakout.name: DonchianBreakout,
    MeanReversion.name: MeanReversion,
    MomentumContinuation.name: MomentumContinuation,
}

ALL_STRATEGY_NAMES = [*BASE_STRATEGIES, Ensemble.name]


def build_strategy(name: str, params: dict[str, Any] | None = None) -> Strategy:
    if name == Ensemble.name:
        members = [cls() for cls in BASE_STRATEGIES.values()]
        return Ensemble(members, params)
    if name not in BASE_STRATEGIES:
        raise KeyError(f"unknown strategy {name!r}; known: {ALL_STRATEGY_NAMES}")
    return BASE_STRATEGIES[name](params)


def describe_strategies() -> list[dict[str, Any]]:
    out = []
    for cls in BASE_STRATEGIES.values():
        out.append(
            {
                "name": cls.name,
                "description": cls.description,
                "default_params": cls.default_params,
                "param_grid": cls.param_grid,
                "rules_doc": ((cls.__module__ and __import__(cls.__module__, fromlist=["x"]).__doc__) or "").strip(),
            }
        )
    out.append(
        {
            "name": Ensemble.name,
            "description": Ensemble.description,
            "default_params": Ensemble.default_params,
            "param_grid": {},
            "rules_doc": (__import__("app.strategies.ensemble", fromlist=["x"]).__doc__ or "").strip(),
        }
    )
    return out
