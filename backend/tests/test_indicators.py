"""Indicator correctness and no-look-ahead (truncation invariance) tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core.types import Candle
from app.indicators.core import (
    align_higher_timeframe,
    atr,
    bollinger,
    candles_to_frame,
    confirmed_swings,
    donchian,
    ema,
    resample_closed,
    rsi,
    sma,
)
from app.strategies.features import compute_features


def test_sma_ema_known_values() -> None:
    s = pd.Series([1.0, 2, 3, 4, 5])
    assert sma(s, 3).tolist()[2:] == [2.0, 3.0, 4.0]
    e = ema(s, 3)
    assert np.isnan(e.iloc[1])
    # recursive EMA with alpha = 2/(n+1) = 0.5, seeded at first value
    manual = [1.0]
    for x in [2, 3, 4, 5]:
        manual.append(0.5 * x + 0.5 * manual[-1])
    assert e.iloc[2:].tolist() == pytest.approx(manual[2:])


def test_rsi_bounds_and_extremes() -> None:
    up = pd.Series(np.arange(1, 60, dtype=float))
    assert rsi(up, 14).iloc[-1] == pytest.approx(100.0)
    flat = pd.Series(np.full(40, 5.0))
    assert rsi(flat, 14).iloc[-1] == pytest.approx(50.0)
    rnd = pd.Series(np.cumsum(np.random.default_rng(0).standard_normal(500)) + 100)
    r = rsi(rnd, 14).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_atr_and_bollinger_shapes(sim_candles: list[Candle]) -> None:
    df = candles_to_frame(sim_candles[:300])
    a = atr(df, 14)
    assert (a.dropna() > 0).all()
    bb = bollinger(df["close"], 20)
    ok = bb.dropna()
    assert (ok["upper"] >= ok["mid"]).all() and (ok["mid"] >= ok["lower"]).all()


def test_donchian_excludes_current_bar(sim_candles: list[Candle]) -> None:
    df = candles_to_frame(sim_candles[:100])
    dc = donchian(df, 20, exclude_current=True)
    i = 50
    assert dc["upper"].iloc[i] == df["high"].iloc[i - 20 : i].max()


def test_feature_frame_is_causal(sim_candles: list[Candle]) -> None:
    """Truncating the future must not change any past feature value (no repainting)."""
    cs = sim_candles[:1200]
    full = compute_features(cs)
    numeric = full.select_dtypes(include=[float, int]).columns
    for cut in (400, 777, 1100):
        part = compute_features(cs[: cut + 1])
        a = full.iloc[: cut + 1][numeric]
        b = part[numeric]
        pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=1e-9, atol=1e-9)
        assert (full["trend_regime"].iloc[: cut + 1] == part["trend_regime"]).all()
        assert (full["vol_regime"].iloc[: cut + 1] == part["vol_regime"]).all()


def test_swings_confirmed_only_after_right_bars() -> None:
    g = 60
    highs = [1, 2, 3, 10, 3, 2, 1, 1, 1]
    cs = [Candle(i * g, h - 0.5, h, h - 1, h - 0.5, g) for i, h in enumerate(highs)]
    sw = confirmed_swings(candles_to_frame(cs), left=3, right=3)
    # pivot at index 3 is only known at index 6
    assert np.isnan(sw["swing_high"].iloc[5])
    assert sw["swing_high"].iloc[6] == 10


def test_htf_alignment_never_uses_forming_bar() -> None:
    g = 900
    ltf = [Candle(i * g, 1, 2, 0.5, 1 + i, g) for i in range(12)]
    htf = resample_closed(ltf, 3600)
    assert [c.ts for c in htf] == [0, 3600, 7200]  # 12 bars -> 3 complete hours
    lf, hf = candles_to_frame(ltf), candles_to_frame(htf)
    hf["v"] = hf["close"]
    al = align_higher_timeframe(lf, hf, ["v"])
    # LTF bar 4 (closes at 4500) may only see the first HTF bar (closes at 3600)
    assert al["v"].iloc[4] == htf[0].close
    # LTF bars 0..2 close before any HTF bar closes -> unknown
    assert al["v"].iloc[:3].isna().all()
    # LTF bar 3 closes exactly when HTF bar 0 closes -> allowed
    assert al["v"].iloc[3] == htf[0].close


def test_resample_drops_incomplete_bucket() -> None:
    g = 900
    ltf = [Candle(i * g, 1, 2, 0.5, 1, g) for i in range(6)]  # 1.5 hours
    assert len(resample_closed(ltf, 3600)) == 1
