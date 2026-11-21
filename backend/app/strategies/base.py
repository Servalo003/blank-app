"""Strategy interface.

A strategy is a pure, deterministic function of (feature frame, bar index,
params). It may read rows ``<= i`` only. It never sees the next bar, so a
decision made at bar ``i`` is executable no earlier than bar ``i+1``.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import Any, ClassVar

import pandas as pd

from app.core.types import Direction, StrategyDecision


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else round(v, 8)


class Strategy(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    default_params: ClassVar[dict[str, Any]]
    # Small, pre-declared grid for walk-forward parameter selection on TRAIN data only.
    param_grid: ClassVar[dict[str, list[Any]]] = {}
    min_history: ClassVar[int] = 250

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        merged = dict(self.default_params)
        if params:
            unknown = set(params) - set(self.default_params)
            if unknown:
                raise ValueError(f"unknown params for {self.name}: {sorted(unknown)}")
            merged.update(params)
        self.params = merged

    def no_trade(self, reasons: list[str], snap: dict[str, Any] | None = None) -> StrategyDecision:
        return StrategyDecision(
            strategy=self.name,
            direction=Direction.NO_TRADE,
            reasons=reasons,
            indicators=snap or {},
            params=dict(self.params),
        )

    @staticmethod
    def snapshot(row: pd.Series, cols: list[str]) -> dict[str, float | str | None]:
        out: dict[str, float | str | None] = {}
        for c in cols:
            v = row.get(c)
            out[c] = v if isinstance(v, str) else _f(v)
        return out

    def evaluate(self, f: pd.DataFrame, i: int) -> StrategyDecision:
        if i < self.min_history:
            return self.no_trade([f"Insufficient history: {i + 1} bars < {self.min_history} required"])
        row = f.iloc[i]
        needed = self.required_columns()
        missing = [c for c in needed if _f(row.get(c)) is None]
        if missing:
            return self.no_trade([f"Indicators not yet available: {', '.join(missing)}"])
        return self._evaluate(f, i)

    def required_columns(self) -> list[str]:
        return ["close", "atr14"]

    @abstractmethod
    def _evaluate(self, f: pd.DataFrame, i: int) -> StrategyDecision: ...

    # ---- exit rules evaluated for an OPEN position at bar i (closed bar) ----
    def exit_signal(self, f: pd.DataFrame, i: int, direction: Direction) -> str | None:
        """Return a reason string if the strategy's discretionary exit fires."""
        return None
