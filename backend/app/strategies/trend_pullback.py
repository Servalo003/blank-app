"""Strategy 1: higher-timeframe trend following with a pullback entry.

Entry (LONG; SHORT is symmetric):
  * HTF trend is up (HTF close > HTF EMA50 and HTF EMA20 > HTF EMA50, closed HTF bars only).
  * LTF EMA20 > EMA50 and ADX >= ``adx_min`` (trend is present on the signal timeframe).
  * Within the last ``pullback_lookback`` bars price traded back to EMA20 (low <= EMA20 + 0.25*ATR).
  * Signal bar closes back above EMA20 and is a bullish bar (close > open).
  * RSI between ``rsi_low`` and ``rsi_high`` (not overextended).
Stop: ``stop_atr`` * ATR below the signal close, or below the last confirmed swing low if that is closer
to a structural level but still >= 0.5 ATR away. Target: ``rr`` * risk.
Invalidation (before entry): price trades below EMA50.
Exit (open position): close below EMA50.
No-trade: HTF trend neutral/missing, vol percentile > ``max_vol_rank`` (disorderly market).
"""

from __future__ import annotations

from typing import Any, ClassVar

import pandas as pd

from app.core.types import Direction, StrategyDecision
from app.strategies.base import Strategy


class TrendPullback(Strategy):
    name = "trend_pullback"
    description = "HTF trend following with pullback-to-EMA20 entry"
    default_params: ClassVar[dict[str, Any]] = {
        "adx_min": 20.0,
        "pullback_lookback": 5,
        "rsi_low": 40.0,
        "rsi_high": 70.0,
        "stop_atr": 1.5,
        "rr": 2.0,
        "max_vol_rank": 0.95,
        "expiry_bars": 2,
        "max_hold_bars": 48,
    }
    param_grid: ClassVar[dict[str, list[Any]]] = {"stop_atr": [1.5, 2.0], "rr": [1.5, 2.0]}

    COLS: ClassVar[list[str]] = ["close", "ema20", "ema50", "atr14", "adx", "rsi14", "htf_trend", "vol_rank"]

    def required_columns(self) -> list[str]:
        return ["close", "ema20", "ema50", "atr14", "adx", "rsi14", "htf_trend"]

    def _evaluate(self, f: pd.DataFrame, i: int) -> StrategyDecision:
        p = self.params
        r = f.iloc[i]
        snap = self.snapshot(r, self.COLS)
        a = float(r["atr14"])
        if a <= 0:
            return self.no_trade(["ATR is zero - no volatility information"], snap)
        if pd.notna(r["vol_rank"]) and r["vol_rank"] > p["max_vol_rank"]:
            return self.no_trade([f"Volatility percentile {r['vol_rank']:.2f} > {p['max_vol_rank']} (disorderly)"], snap)
        htf = int(r["htf_trend"])
        if htf == 0:
            return self.no_trade(["Higher-timeframe trend is neutral"], snap)
        lb = int(p["pullback_lookback"])
        win = f.iloc[max(0, i - lb) : i]
        if htf > 0:
            conds = {
                "LTF EMA20 > EMA50": r["ema20"] > r["ema50"],
                f"ADX {r['adx']:.1f} >= {p['adx_min']}": r["adx"] >= p["adx_min"],
                "Pullback to EMA20 within lookback": bool((win["low"] <= win["ema20"] + 0.25 * win["atr14"]).any()),
                "Close back above EMA20 on bullish bar": r["close"] > r["ema20"] and r["close"] > r["open"],
                f"RSI {r['rsi14']:.1f} in [{p['rsi_low']}, {p['rsi_high']}]": p["rsi_low"] <= r["rsi14"] <= p["rsi_high"],
            }
            direction = Direction.LONG
        else:
            conds = {
                "LTF EMA20 < EMA50": r["ema20"] < r["ema50"],
                f"ADX {r['adx']:.1f} >= {p['adx_min']}": r["adx"] >= p["adx_min"],
                "Pullback to EMA20 within lookback": bool((win["high"] >= win["ema20"] - 0.25 * win["atr14"]).any()),
                "Close back below EMA20 on bearish bar": r["close"] < r["ema20"] and r["close"] < r["open"],
                f"RSI {r['rsi14']:.1f} in [{100 - p['rsi_high']}, {100 - p['rsi_low']}]": 100 - p["rsi_high"]
                <= r["rsi14"]
                <= 100 - p["rsi_low"],
            }
            direction = Direction.SHORT
        failed = [k for k, v in conds.items() if not v]
        if failed:
            return self.no_trade([f"HTF trend {'up' if htf > 0 else 'down'}; unmet: " + "; ".join(failed)], snap)

        entry = float(r["close"])
        dist = p["stop_atr"] * a
        sw = r["swing_low"] if direction is Direction.LONG else r["swing_high"]
        if pd.notna(sw):
            sdist = abs(entry - float(sw)) + 0.1 * a
            if 0.5 * a <= sdist < dist:
                dist = sdist
        sign = 1 if direction is Direction.LONG else -1
        score = min(1.0, 0.4 + (float(r["adx"]) - p["adx_min"]) / 50.0)
        return StrategyDecision(
            strategy=self.name,
            direction=direction,
            reasons=[f"HTF trend {'up' if htf > 0 else 'down'}", *conds.keys()],
            indicators=snap,
            params=dict(p),
            entry_ref=entry,
            stop=entry - sign * dist,
            target=entry + sign * p["rr"] * dist,
            invalidation=float(r["ema50"]),
            expiry_bars=int(p["expiry_bars"]),
            max_hold_bars=int(p["max_hold_bars"]),
            score=round(max(0.0, score), 3),
        )

    def exit_signal(self, f: pd.DataFrame, i: int, direction: Direction) -> str | None:
        r = f.iloc[i]
        if direction is Direction.LONG and r["close"] < r["ema50"]:
            return "Close below EMA50 (trend failure)"
        if direction is Direction.SHORT and r["close"] > r["ema50"]:
            return "Close above EMA50 (trend failure)"
        return None
