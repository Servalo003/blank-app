"""Strategy 2: Donchian breakout with volatility and regime filters.

Entry (LONG; SHORT symmetric):
  * Close > prior 20-bar Donchian upper + ``buffer_atr`` * ATR (channel excludes the signal bar).
  * Volatility was compressed before the break: vol percentile of the previous bar <= ``max_prior_vol_rank``.
  * ADX >= ``adx_min`` and rising over 3 bars (trend strength emerging).
  * Higher-timeframe trend is not opposite to the breakout.
Stop: ``stop_atr`` * ATR beyond entry. Target: ``rr`` * risk.
Invalidation (before entry): close back inside the channel (below prior upper).
Exit (open position): close beyond the opposite 10-bar Donchian band (trailing exit).
No-trade: RANGE regime with falling ADX, HTF opposite, or volatility already extreme.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pandas as pd

from app.core.types import Direction, StrategyDecision
from app.strategies.base import Strategy


class DonchianBreakout(Strategy):
    name = "donchian_breakout"
    description = "Donchian breakout after volatility compression"
    default_params: ClassVar[dict[str, Any]] = {
        "buffer_atr": 0.1,
        "max_prior_vol_rank": 0.6,
        "adx_min": 18.0,
        "stop_atr": 2.0,
        "rr": 2.0,
        "expiry_bars": 1,
        "max_hold_bars": 60,
    }
    param_grid: ClassVar[dict[str, list[Any]]] = {"stop_atr": [1.5, 2.0], "max_prior_vol_rank": [0.5, 0.7]}
    COLS: ClassVar[list[str]] = ["close", "dc20_upper", "dc20_lower", "atr14", "adx", "vol_rank", "htf_trend"]

    def required_columns(self) -> list[str]:
        return ["close", "dc20_upper", "dc20_lower", "atr14", "adx", "vol_rank"]

    def _evaluate(self, f: pd.DataFrame, i: int) -> StrategyDecision:
        p = self.params
        r = f.iloc[i]
        prev = f.iloc[i - 1]
        snap = self.snapshot(r, self.COLS)
        a = float(r["atr14"])
        if a <= 0:
            return self.no_trade(["ATR is zero"], snap)
        up_break = r["close"] > r["dc20_upper"] + p["buffer_atr"] * a
        dn_break = r["close"] < r["dc20_lower"] - p["buffer_atr"] * a
        if not (up_break or dn_break):
            return self.no_trade(["No close beyond the prior 20-bar channel"], snap)
        direction = Direction.LONG if up_break else Direction.SHORT
        adx_rising = f["adx"].iloc[i] > f["adx"].iloc[i - 3]
        htf = r.get("htf_trend")
        conds = {
            f"Prior-bar vol percentile {prev['vol_rank']:.2f} <= {p['max_prior_vol_rank']} (compression)": pd.notna(
                prev["vol_rank"]
            )
            and prev["vol_rank"] <= p["max_prior_vol_rank"],
            f"ADX {r['adx']:.1f} >= {p['adx_min']} and rising": r["adx"] >= p["adx_min"] and adx_rising,
            "HTF trend not opposite": not (
                pd.notna(htf) and ((direction is Direction.LONG and htf < 0) or (direction is Direction.SHORT and htf > 0))
            ),
        }
        failed = [k for k, v in conds.items() if not v]
        if failed:
            return self.no_trade([f"{direction.value} breakout rejected: " + "; ".join(failed)], snap)
        sign = 1 if direction is Direction.LONG else -1
        entry = float(r["close"])
        dist = p["stop_atr"] * a
        level = float(r["dc20_upper"] if direction is Direction.LONG else r["dc20_lower"])
        score = min(1.0, 0.3 + abs(entry - level) / a * 0.3 + (float(r["adx"]) - p["adx_min"]) / 60.0)
        return StrategyDecision(
            strategy=self.name,
            direction=direction,
            reasons=[f"Close {'above' if sign > 0 else 'below'} 20-bar channel {level:.5g}", *conds.keys()],
            indicators=snap,
            params=dict(p),
            entry_ref=entry,
            stop=entry - sign * dist,
            target=entry + sign * p["rr"] * dist,
            invalidation=level,
            expiry_bars=int(p["expiry_bars"]),
            max_hold_bars=int(p["max_hold_bars"]),
            score=round(max(0.0, score), 3),
        )

    def exit_signal(self, f: pd.DataFrame, i: int, direction: Direction) -> str | None:
        r = f.iloc[i]
        if direction is Direction.LONG and pd.notna(r["dc10_lower"]) and r["close"] < r["dc10_lower"]:
            return "Close below 10-bar low (trailing exit)"
        if direction is Direction.SHORT and pd.notna(r["dc10_upper"]) and r["close"] > r["dc10_upper"]:
            return "Close above 10-bar high (trailing exit)"
        return None
