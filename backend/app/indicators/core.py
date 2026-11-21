"""Causal technical indicators.

Every function here is *causal*: the value at index ``i`` depends only on rows
``0..i``. That property is asserted in ``tests/test_indicators.py`` by checking
that truncating the input never changes earlier outputs (no repainting).

Inputs are pandas Series/DataFrames indexed by candle open time, columns
``open, high, low, close``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.types import Candle


def candles_to_frame(candles: list[Candle]) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "ts": [c.ts for c in candles],
            "open": [c.open for c in candles],
            "high": [c.high for c in candles],
            "low": [c.low for c in candles],
            "close": [c.close for c in candles],
        }
    )
    if candles:
        df["close_ts"] = df["ts"] + candles[0].granularity
    else:
        df["close_ts"] = pd.Series(dtype="int64")
    return df.set_index("ts", drop=False)


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    # adjust=False gives the recursive (causal) EMA; first n-1 values masked.
    out = s.ewm(span=n, adjust=False, min_periods=n).mean()
    return out


def wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    tr.iloc[0] = df["high"].iloc[0] - df["low"].iloc[0] if len(df) else np.nan
    return tr


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return wilder(true_range(df), n)


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    delta = s.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = wilder(gain, n)
    avg_loss = wilder(loss, n)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    # avg_loss == 0 and avg_gain > 0 -> RSI 100; both zero -> 50.
    out = out.where(~((avg_loss == 0) & (avg_gain > 0)), 100.0)
    out = out.where(~((avg_loss == 0) & (avg_gain == 0)), 50.0)
    return out


def macd(s: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(s, fast) - ema(s, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def adx(df: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr_s = wilder(true_range(df), n)
    plus_di = 100 * wilder(plus_dm, n) / tr_s.replace(0.0, np.nan)
    minus_di = 100 * wilder(minus_dm, n) / tr_s.replace(0.0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    return pd.DataFrame({"adx": wilder(dx, n), "plus_di": plus_di, "minus_di": minus_di})


def bollinger(s: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(s, n)
    sd = s.rolling(n, min_periods=n).std(ddof=0)
    upper, lower = mid + k * sd, mid - k * sd
    width = (upper - lower) / mid
    pct_b = (s - lower) / (upper - lower).replace(0.0, np.nan)
    return pd.DataFrame({"mid": mid, "upper": upper, "lower": lower, "width": width, "pct_b": pct_b})


def donchian(df: pd.DataFrame, n: int = 20, exclude_current: bool = True) -> pd.DataFrame:
    """Donchian channel.

    With ``exclude_current=True`` (default for breakout logic) the channel at bar
    ``i`` uses bars ``i-n..i-1`` so that "close above upper" is a genuine
    breakout of *prior* range rather than a tautology.
    """
    hi = df["high"].shift(1) if exclude_current else df["high"]
    lo = df["low"].shift(1) if exclude_current else df["low"]
    upper = hi.rolling(n, min_periods=n).max()
    lower = lo.rolling(n, min_periods=n).min()
    return pd.DataFrame({"upper": upper, "lower": lower, "mid": (upper + lower) / 2})


def realized_vol(s: pd.Series, n: int = 20) -> pd.Series:
    """Rolling standard deviation of log returns (per bar, not annualised)."""
    return np.log(s).diff().rolling(n, min_periods=n).std(ddof=0)


def expanding_percentile_rank(s: pd.Series, min_periods: int = 50, window: int | None = 500) -> pd.Series:
    """Percentile rank of each value vs. its own *past* values (inclusive).

    Uses a trailing window so it is causal. Returns values in [0, 1].
    """
    vals = s.to_numpy(dtype=float)
    out = np.full(len(vals), np.nan)
    for i in range(len(vals)):
        if np.isnan(vals[i]):
            continue
        start = 0 if window is None else max(0, i - window + 1)
        hist = vals[start : i + 1]
        hist = hist[~np.isnan(hist)]
        if len(hist) < min_periods:
            continue
        out[i] = float((hist <= vals[i]).sum()) / len(hist)
    return pd.Series(out, index=s.index)


def confirmed_swings(df: pd.DataFrame, left: int = 3, right: int = 3) -> pd.DataFrame:
    """Swing highs/lows *as known at each bar*, without future leakage.

    A pivot at bar ``p`` needs ``right`` bars after it to be confirmed, so it
    only becomes visible at bar ``p + right``. The returned frame carries, for
    every bar ``i``, the most recent swing high/low that was *confirmed* at or
    before ``i`` (forward-filled). Using raw pivot detection (centered windows)
    would repaint, which is exactly what this avoids.
    """
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    n = len(df)
    last_sh = np.full(n, np.nan)
    last_sl = np.full(n, np.nan)
    cur_sh = np.nan
    cur_sl = np.nan
    for i in range(n):
        p = i - right  # pivot candidate that becomes confirmed at bar i
        if p - left >= 0:
            window_h = highs[p - left : p + right + 1]
            window_l = lows[p - left : p + right + 1]
            if highs[p] == window_h.max() and (window_h == highs[p]).sum() == 1:
                cur_sh = highs[p]
            if lows[p] == window_l.min() and (window_l == lows[p]).sum() == 1:
                cur_sl = lows[p]
        last_sh[i] = cur_sh
        last_sl[i] = cur_sl
    return pd.DataFrame({"swing_high": last_sh, "swing_low": last_sl}, index=df.index)


def align_higher_timeframe(ltf: pd.DataFrame, htf: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Attach higher-timeframe columns to lower-timeframe bars without look-ahead.

    For each LTF bar, use the most recent HTF bar whose ``close_ts`` is
    ``<=`` the LTF bar's ``close_ts``. A still-forming HTF bar is never used.
    """
    if htf.empty or ltf.empty:
        return pd.DataFrame(index=ltf.index, columns=columns, dtype=float)
    h = htf[["close_ts", *columns]].sort_values("close_ts")
    left = ltf[["close_ts"]].reset_index(drop=True).sort_values("close_ts")
    merged = pd.merge_asof(left, h, on="close_ts", direction="backward", allow_exact_matches=True)
    merged.index = ltf.index
    return merged[columns]


def resample_closed(candles: list[Candle], target_granularity: int) -> list[Candle]:
    """Build higher-timeframe candles from lower-timeframe ones.

    Only buckets that are *complete* (contain the bar that ends exactly at the
    bucket close) are emitted, so a forming HTF candle is never produced.
    Buckets are aligned to the UTC epoch.
    """
    if not candles:
        return []
    g = candles[0].granularity
    if target_granularity % g != 0:
        raise ValueError("target granularity must be a multiple of source granularity")
    out: list[Candle] = []
    bucket: list[Candle] = []
    bucket_start: int | None = None
    for c in candles:
        b = c.ts - (c.ts % target_granularity)
        if bucket_start is not None and b != bucket_start:
            bucket = []
        bucket_start = b
        bucket.append(c)
        if c.close_ts == b + target_granularity:
            out.append(
                Candle(
                    ts=b,
                    open=bucket[0].open,
                    high=max(x.high for x in bucket),
                    low=min(x.low for x in bucket),
                    close=bucket[-1].close,
                    granularity=target_granularity,
                )
            )
            bucket = []
            bucket_start = None
    return out
