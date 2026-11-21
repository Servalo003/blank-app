"""Market-regime classification (trend/range x high/low volatility).

Causal by construction: uses ADX, EMA slope and a trailing ATR% percentile.
"""

from __future__ import annotations

from enum import StrEnum

import numpy as np
import pandas as pd

from app.indicators.core import adx, atr, ema, expanding_percentile_rank


class TrendRegime(StrEnum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    UNKNOWN = "UNKNOWN"


class VolRegime(StrEnum):
    HIGH_VOL = "HIGH_VOL"
    NORMAL_VOL = "NORMAL_VOL"
    LOW_VOL = "LOW_VOL"
    UNKNOWN = "UNKNOWN"


def classify_regimes(
    df: pd.DataFrame,
    adx_n: int = 14,
    adx_trend: float = 25.0,
    adx_range: float = 20.0,
    ema_n: int = 50,
    vol_window: int = 500,
    vol_high_pct: float = 0.8,
    vol_low_pct: float = 0.2,
) -> pd.DataFrame:
    a = adx(df, adx_n)
    e = ema(df["close"], ema_n)
    slope = e.diff(5)
    atr_pct = atr(df, 14) / df["close"]
    vol_rank = expanding_percentile_rank(atr_pct, min_periods=min(100, vol_window), window=vol_window)

    trend = np.full(len(df), TrendRegime.UNKNOWN.value, dtype=object)
    adx_v = a["adx"].to_numpy()
    slope_v = slope.to_numpy()
    close_v = df["close"].to_numpy()
    ema_v = e.to_numpy()
    for i in range(len(df)):
        if np.isnan(adx_v[i]) or np.isnan(slope_v[i]) or np.isnan(ema_v[i]):
            continue
        if adx_v[i] >= adx_trend and slope_v[i] > 0 and close_v[i] > ema_v[i]:
            trend[i] = TrendRegime.TREND_UP.value
        elif adx_v[i] >= adx_trend and slope_v[i] < 0 and close_v[i] < ema_v[i]:
            trend[i] = TrendRegime.TREND_DOWN.value
        elif adx_v[i] <= adx_range:
            trend[i] = TrendRegime.RANGE.value
        else:
            # transitional zone: neither clearly trending nor ranging
            trend[i] = TrendRegime.UNKNOWN.value

    vol = np.full(len(df), VolRegime.UNKNOWN.value, dtype=object)
    vr = vol_rank.to_numpy()
    for i in range(len(df)):
        if np.isnan(vr[i]):
            continue
        vol[i] = (
            VolRegime.HIGH_VOL.value
            if vr[i] >= vol_high_pct
            else VolRegime.LOW_VOL.value
            if vr[i] <= vol_low_pct
            else VolRegime.NORMAL_VOL.value
        )
    return pd.DataFrame(
        {"trend_regime": trend, "vol_regime": vol, "adx": a["adx"], "vol_rank": vol_rank, "atr_pct": atr_pct},
        index=df.index,
    )
