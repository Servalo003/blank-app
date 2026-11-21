"""Deterministic SIMULATED market data.

Clearly labelled ``SIMULATED_DATA`` everywhere. It exists so the whole app is
runnable and testable offline. It is a regime-switching random process: it
contains NO real market information and any edge found on it is meaningless
for live markets.

Prices for a given (symbol, granularity, seed) are a pure function of the bar
index from a fixed anchor, so repeated calls and "live" polling are consistent.
"""

from __future__ import annotations

import hashlib
import time
from functools import lru_cache
from typing import Any

import numpy as np

from app.core.types import Candle, DataProvenance, InstrumentInfo
from app.data.provider import DataUnavailable, MarketDataProvider

ANCHOR_TS = 1_735_689_600  # 2025-01-01T00:00:00Z

SIM_SYMBOLS: dict[str, dict[str, Any]] = {
    "SIM_TREND": {"name": "Simulated Trending Index", "start": 1000.0, "trend_bias": 0.7},
    "SIM_RANGE": {"name": "Simulated Ranging Index", "start": 500.0, "trend_bias": 0.2},
    "SIM_MIXED": {"name": "Simulated Mixed-Regime Index", "start": 250.0, "trend_bias": 0.45},
}


def _seed(symbol: str, granularity: int, seed: int) -> int:
    h = hashlib.sha256(f"{symbol}|{granularity}|{seed}".encode()).digest()
    return int.from_bytes(h[:8], "big")


@lru_cache(maxsize=32)
def _series(symbol: str, granularity: int, n_bars: int, seed: int) -> tuple[np.ndarray, ...]:
    spec = SIM_SYMBOLS[symbol]
    rng = np.random.default_rng(_seed(symbol, granularity, seed))
    scale = np.sqrt(granularity / 300.0)
    base_vol = 0.0012 * scale
    closes = np.empty(n_bars)
    opens = np.empty(n_bars)
    highs = np.empty(n_bars)
    lows = np.empty(n_bars)
    logp = np.log(spec["start"])
    anchor = logp
    i = 0
    while i < n_bars:
        length = int(rng.integers(300, 1500))
        trending = rng.random() < spec["trend_bias"]
        vol = base_vol * float(rng.choice([0.6, 1.0, 1.8]))
        drift = (rng.choice([-1, 1]) * vol * float(rng.uniform(0.08, 0.2))) if trending else 0.0
        kappa = 0.0 if trending else float(rng.uniform(0.02, 0.08))
        anchor = logp
        eps = rng.standard_normal((min(length, n_bars - i), 3))
        for k in range(eps.shape[0]):
            o = logp
            logp = logp + drift - kappa * (logp - anchor) + vol * eps[k, 0]
            wick = vol * 0.6
            hi = max(o, logp) + abs(eps[k, 1]) * wick
            lo = min(o, logp) - abs(eps[k, 2]) * wick
            opens[i], closes[i], highs[i], lows[i] = o, logp, hi, lo
            i += 1
    return np.exp(opens), np.exp(highs), np.exp(lows), np.exp(closes)


class SimulatedProvider(MarketDataProvider):
    name = "simulator"
    provenance = DataProvenance.SIMULATED_DATA

    def __init__(self, seed: int = 7, clock: Any = None, max_bars: int = 400_000) -> None:
        self.seed = seed
        self._clock = clock or time.time
        self.max_bars = max_bars

    async def list_symbols(self) -> list[InstrumentInfo]:
        return [
            InstrumentInfo(
                symbol=s,
                display_name=f"{v['name']} (SIMULATED)",
                market="simulated",
                submarket="simulated",
                symbol_type="simulated",
                pip_size=0.01,
                is_open=True,
                is_suspended=False,
                provenance=self.provenance,
            )
            for s, v in SIM_SYMBOLS.items()
        ]

    async def server_time(self) -> int:
        return int(self._clock())

    def candles_until(self, symbol: str, granularity: int, now: int, count: int) -> list[Candle]:
        if symbol not in SIM_SYMBOLS:
            raise DataUnavailable(f"unknown simulated symbol {symbol}")
        last_idx = (now - ANCHOR_TS) // granularity  # index of forming bar
        n = int(min(last_idx + 1, self.max_bars))
        if n <= 0:
            return []
        # Build enough bars from the fixed anchor; cached per (symbol, g, n bucket).
        bucket = int(np.ceil(n / 5000.0) * 5000)
        o, h, lo, c = _series(symbol, granularity, bucket, self.seed)
        first = max(0, n - count)
        return [
            Candle(
                ts=ANCHOR_TS + k * granularity,
                open=round(float(o[k]), 4),
                high=round(float(h[k]), 4),
                low=round(float(lo[k]), 4),
                close=round(float(c[k]), 4),
                granularity=granularity,
            )
            for k in range(first, n)
        ]

    async def get_candles(self, symbol: str, granularity: int, count: int) -> list[Candle]:
        return self.candles_until(symbol, granularity, int(self._clock()), count)

    async def latest_quote(self, symbol: str) -> dict[str, Any] | None:
        cs = self.candles_until(symbol, 60, int(self._clock()), 1)
        if not cs:
            return None
        q = cs[-1].close
        return {"epoch": int(self._clock()), "quote": q, "bid": q * 0.9999, "ask": q * 1.0001, "simulated": True}

    async def health(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "provenance": self.provenance.value,
            "connected": True,
            "note": "Deterministic synthetic process - not market data",
        }
