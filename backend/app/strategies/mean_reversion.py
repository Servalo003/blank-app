"""Strategy 3: mean reversion with RSI + Bollinger Bands, range regimes only.

Enabled ONLY when the regime classifier reports RANGE and volatility is not HIGH.
Entry (LONG; SHORT symmetric):
  * Previous close was below the lower band, signal bar closes back inside (re-entry).
  * RSI on the previous bar <= ``rsi_os`` (oversold), current RSI rising.
Target: the middle band (SMA20) at signal time. Stop: ``stop_atr`` * ATR beyond the signal bar's extreme.
Trades whose reward/risk to the mid band is < ``min_rr`` are rejected.
Invalidation (before entry): close below the signal bar low.
Exit (open position): regime turns into a trend (ADX >= ``exit_adx``).
"""

from __future__ import annotations

from typing import Any, ClassVar

import pandas as pd

from app.core.types import Direction, StrategyDecision
from app.indicators.regime import TrendRegime, VolRegime
from app.strategies.base import Strategy


class MeanReversion(Strategy):
    name = "mean_reversion"
    description = "RSI/Bollinger re-entry fade, range regimes only"
    default_params: ClassVar[dict[str, Any]] = {
        "rsi_os": 35.0,
        "rsi_ob": 65.0,
        "stop_atr": 1.0,
        "min_rr": 1.0,
        "exit_adx": 28.0,
        "expiry_bars": 1,
        "max_hold_bars": 20,
    }
    param_grid: ClassVar[dict[str, list[Any]]] = {"stop_atr": [1.0, 1.5], "min_rr": [0.8, 1.2]}
    COLS: ClassVar[list[str]] = ["close", "bb_lower", "bb_mid", "bb_upper", "rsi14", "atr14", "adx", "trend_regime", "vol_regime"]

    def required_columns(self) -> list[str]:
        return ["close", "bb_lower", "bb_mid", "bb_upper", "rsi14", "atr14"]

    def _evaluate(self, f: pd.DataFrame, i: int) -> StrategyDecision:
        p = self.params
        r = f.iloc[i]
        prev = f.iloc[i - 1]
        snap = self.snapshot(r, self.COLS)
        if r["trend_regime"] != TrendRegime.RANGE.value:
            return self.no_trade([f"Regime is {r['trend_regime']}, mean reversion only runs in RANGE"], snap)
        if r["vol_regime"] == VolRegime.HIGH_VOL.value:
            return self.no_trade(["High-volatility regime: fading moves is disabled"], snap)
        a = float(r["atr14"])
        long_setup = prev["close"] < prev["bb_lower"] and r["close"] > r["bb_lower"] and prev["rsi14"] <= p["rsi_os"]
        short_setup = prev["close"] > prev["bb_upper"] and r["close"] < r["bb_upper"] and prev["rsi14"] >= p["rsi_ob"]
        if not (long_setup or short_setup):
            return self.no_trade(["No band re-entry after an RSI extreme"], snap)
        direction = Direction.LONG if long_setup else Direction.SHORT
        sign = 1 if long_setup else -1
        if (r["rsi14"] - prev["rsi14"]) * sign <= 0:
            return self.no_trade(["RSI not turning in the trade direction"], snap)
        entry = float(r["close"])
        stop = float(r["low"]) - p["stop_atr"] * a if long_setup else float(r["high"]) + p["stop_atr"] * a
        target = float(r["bb_mid"])
        risk = abs(entry - stop)
        reward = (target - entry) * sign
        if risk <= 0 or reward / risk < p["min_rr"]:
            return self.no_trade([f"Reward/risk to mid band {reward / risk if risk else 0:.2f} < {p['min_rr']}"], snap)
        return StrategyDecision(
            strategy=self.name,
            direction=direction,
            reasons=[
                "RANGE regime, volatility not high",
                f"Previous close outside {'lower' if long_setup else 'upper'} band with RSI {prev['rsi14']:.1f}",
                "Signal bar closed back inside the band; RSI turning",
                f"Target = band midline {target:.5g} (R/R {reward / risk:.2f})",
            ],
            indicators=snap,
            params=dict(p),
            entry_ref=entry,
            stop=stop,
            target=target,
            invalidation=float(r["low"] if long_setup else r["high"]),
            expiry_bars=int(p["expiry_bars"]),
            max_hold_bars=int(p["max_hold_bars"]),
            score=round(min(1.0, 0.3 + 0.2 * reward / risk), 3),
        )

    def exit_signal(self, f: pd.DataFrame, i: int, direction: Direction) -> str | None:
        r = f.iloc[i]
        if pd.notna(r["adx"]) and r["adx"] >= self.params["exit_adx"]:
            return f"ADX {r['adx']:.1f} >= {self.params['exit_adx']}: range regime ended"
        return None
