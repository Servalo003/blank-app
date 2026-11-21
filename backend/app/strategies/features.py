"""Feature frame shared by all strategies.

All columns are causal (see indicator tests). Features are computed once per
series; strategies then read row ``i`` only, plus earlier rows.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.types import Candle
from app.indicators.core import (
    adx,
    align_higher_timeframe,
    atr,
    bollinger,
    candles_to_frame,
    confirmed_swings,
    donchian,
    ema,
    macd,
    resample_closed,
    rsi,
)
from app.indicators.regime import classify_regimes

FEATURE_VERSION = "features-v1"


def _htf_frame(htf_candles: list[Candle]) -> pd.DataFrame:
    h = candles_to_frame(htf_candles)
    if h.empty:
        return h
    h["htf_ema20"] = ema(h["close"], 20)
    h["htf_ema50"] = ema(h["close"], 50)
    h["htf_close"] = h["close"]
    trend = np.where(
        (h["htf_close"] > h["htf_ema50"]) & (h["htf_ema20"] > h["htf_ema50"]),
        1,
        np.where((h["htf_close"] < h["htf_ema50"]) & (h["htf_ema20"] < h["htf_ema50"]), -1, 0),
    )
    h["htf_trend"] = np.where(h["htf_ema50"].isna(), np.nan, trend)
    return h


def compute_features(candles: list[Candle], htf_candles: list[Candle] | None = None) -> pd.DataFrame:
    """Compute the full feature frame.

    ``htf_candles`` may be omitted; then the higher timeframe is resampled from
    ``candles`` itself using only complete buckets (see ``resample_closed``).
    """
    df = candles_to_frame(candles)
    if df.empty:
        return df
    c = df["close"]
    df["ema20"] = ema(c, 20)
    df["ema50"] = ema(c, 50)
    df["ema200"] = ema(c, 200)
    df["atr14"] = atr(df, 14)
    df["rsi14"] = rsi(c, 14)
    m = macd(c)
    df["macd"], df["macd_signal"], df["macd_hist"] = m["macd"], m["signal"], m["hist"]
    a = adx(df, 14)
    df["adx"], df["plus_di"], df["minus_di"] = a["adx"], a["plus_di"], a["minus_di"]
    bb = bollinger(c, 20, 2.0)
    for col in bb.columns:
        df[f"bb_{col}"] = bb[col]
    dc20 = donchian(df, 20, exclude_current=True)
    df["dc20_upper"], df["dc20_lower"], df["dc20_mid"] = dc20["upper"], dc20["lower"], dc20["mid"]
    dc10 = donchian(df, 10, exclude_current=True)
    df["dc10_upper"], df["dc10_lower"] = dc10["upper"], dc10["lower"]
    sw = confirmed_swings(df, 3, 3)
    df["swing_high"], df["swing_low"] = sw["swing_high"], sw["swing_low"]
    reg = classify_regimes(df)
    df["trend_regime"], df["vol_regime"], df["vol_rank"] = reg["trend_regime"], reg["vol_regime"], reg["vol_rank"]

    if htf_candles is None:
        from app.core.types import HIGHER_TIMEFRAME, Timeframe

        g = candles[0].granularity
        htf_g = HIGHER_TIMEFRAME[Timeframe.from_seconds(g)].seconds
        htf_candles = resample_closed(candles, htf_g) if htf_g > g else []
    h = _htf_frame(htf_candles)
    if h.empty:
        df["htf_trend"] = np.nan
        df["htf_close"] = np.nan
        df["htf_ema50"] = np.nan
    else:
        al = align_higher_timeframe(df, h, ["htf_trend", "htf_close", "htf_ema50"])
        df["htf_trend"], df["htf_close"], df["htf_ema50"] = al["htf_trend"], al["htf_close"], al["htf_ema50"]
    return df
