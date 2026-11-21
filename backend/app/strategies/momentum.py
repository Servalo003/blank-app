"""Strategy 4: momentum continuation with strict invalidation and cooldown.

Entry (LONG; SHORT symmetric):
  * MACD histogram > 0 and rising for ``hist_rising`` consecutive bars.
  * Close > EMA20 > EMA50; ADX >= ``adx_min`` with +DI > -DI.
  * RSI in [``rsi_min``, ``rsi_max``] - momentum without exhaustion.
  * Signal bar closes in the top ``close_loc`` fraction of its range.
  * Cooldown: the full condition set must have been FALSE for the previous
    ``cooldown_bars`` bars (stateless, so live and backtest behave identically).
Stop: ``stop_atr`` * ATR. Target ``rr`` * risk. Max hold ``max_hold_bars``.
Strict invalidation (before entry): any close below EMA20.
Exit (open position): MACD histogram flips sign.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pandas as pd

from app.core.types import Direction, StrategyDecision
from app.strategies.base import Strategy


class MomentumContinuation(Strategy):
    name = "momentum_continuation"
    description = "MACD/ADX momentum continuation with cooldown"
    default_params: ClassVar[dict[str, Any]] = {
        "hist_rising": 3,
        "adx_min": 25.0,
        "rsi_min": 55.0,
        "rsi_max": 75.0,
        "close_loc": 0.3,
        "cooldown_bars": 10,
        "stop_atr": 1.5,
        "rr": 2.0,
        "expiry_bars": 1,
        "max_hold_bars": 20,
    }
    param_grid: ClassVar[dict[str, list[Any]]] = {"stop_atr": [1.5, 2.0], "cooldown_bars": [10, 20]}
    COLS: ClassVar[list[str]] = ["close", "ema20", "ema50", "macd_hist", "adx", "plus_di", "minus_di", "rsi14", "atr14"]

    def required_columns(self) -> list[str]:
        return ["close", "ema20", "ema50", "macd_hist", "adx", "plus_di", "minus_di", "rsi14", "atr14"]

    def _cond(self, f: pd.DataFrame, i: int) -> tuple[Direction, dict[str, bool]]:
        p = self.params
        r = f.iloc[i]
        k = int(p["hist_rising"])
        h = f["macd_hist"].iloc[i - k : i + 1].to_numpy()
        rng = float(r["high"] - r["low"]) or 1e-12
        loc_up = (float(r["close"]) - float(r["low"])) / rng
        long_c = {
            f"MACD hist > 0 and rising {k} bars": bool((h[1:] > 0).all() and (h[1:] > h[:-1]).all()),
            "Close > EMA20 > EMA50": r["close"] > r["ema20"] > r["ema50"],
            f"ADX >= {p['adx_min']} with +DI > -DI": r["adx"] >= p["adx_min"] and r["plus_di"] > r["minus_di"],
            f"RSI in [{p['rsi_min']}, {p['rsi_max']}]": p["rsi_min"] <= r["rsi14"] <= p["rsi_max"],
            f"Close in top {p['close_loc']:.0%} of bar": loc_up >= 1 - p["close_loc"],
        }
        short_c = {
            f"MACD hist < 0 and falling {k} bars": bool((h[1:] < 0).all() and (h[1:] < h[:-1]).all()),
            "Close < EMA20 < EMA50": r["close"] < r["ema20"] < r["ema50"],
            f"ADX >= {p['adx_min']} with -DI > +DI": r["adx"] >= p["adx_min"] and r["minus_di"] > r["plus_di"],
            f"RSI in [{100 - p['rsi_max']}, {100 - p['rsi_min']}]": 100 - p["rsi_max"] <= r["rsi14"] <= 100 - p["rsi_min"],
            f"Close in bottom {p['close_loc']:.0%} of bar": loc_up <= p["close_loc"],
        }
        if all(long_c.values()):
            return Direction.LONG, long_c
        if all(short_c.values()):
            return Direction.SHORT, short_c
        return Direction.NO_TRADE, long_c if r["close"] >= r["ema50"] else short_c

    def _evaluate(self, f: pd.DataFrame, i: int) -> StrategyDecision:
        p = self.params
        r = f.iloc[i]
        snap = self.snapshot(r, self.COLS)
        direction, conds = self._cond(f, i)
        if direction is Direction.NO_TRADE:
            failed = [k for k, v in conds.items() if not v]
            return self.no_trade(["Momentum conditions unmet: " + "; ".join(failed)], snap)
        cd = int(p["cooldown_bars"])
        for j in range(max(self.min_history, i - cd), i):
            if self._cond(f, j)[0] is not Direction.NO_TRADE:
                return self.no_trade([f"Cooldown: momentum condition already fired within last {cd} bars"], snap)
        a = float(r["atr14"])
        sign = 1 if direction is Direction.LONG else -1
        entry = float(r["close"])
        dist = p["stop_atr"] * a
        return StrategyDecision(
            strategy=self.name,
            direction=direction,
            reasons=[*list(conds.keys()), f"No momentum signal in prior {cd} bars (cooldown clear)"],
            indicators=snap,
            params=dict(p),
            entry_ref=entry,
            stop=entry - sign * dist,
            target=entry + sign * p["rr"] * dist,
            invalidation=float(r["ema20"]),
            expiry_bars=int(p["expiry_bars"]),
            max_hold_bars=int(p["max_hold_bars"]),
            score=round(min(1.0, 0.35 + (float(r["adx"]) - p["adx_min"]) / 50.0), 3),
        )

    def exit_signal(self, f: pd.DataFrame, i: int, direction: Direction) -> str | None:
        h = f["macd_hist"].iloc[i]
        if direction is Direction.LONG and h < 0:
            return "MACD histogram turned negative"
        if direction is Direction.SHORT and h > 0:
            return "MACD histogram turned positive"
        return None
