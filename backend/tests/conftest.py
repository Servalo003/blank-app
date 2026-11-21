from __future__ import annotations

import asyncio
import os
import time

import pytest

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATA_PROVIDER", "simulator")

from app.core.types import Candle
from app.data.quality import normalize_candles
from app.data.simulator import SimulatedProvider
from app.products.adapter import build_adapter

FIXED_NOW = 1_790_000_000  # deterministic clock for tests


@pytest.fixture(scope="session")
def sim_candles() -> list[Candle]:
    p = SimulatedProvider(seed=7, clock=lambda: FIXED_NOW)
    raw = asyncio.run(p.get_candles("SIM_MIXED", 900, 3000))
    closed, _ = normalize_candles(raw, 900, FIXED_NOW)
    return closed


@pytest.fixture
def linear_adapter():  # type: ignore[no-untyped-def]
    return build_adapter(
        {"product_type": "linear", "contract_size": 1.0, "min_units": 0.001, "unit_step": 0.001, "confirmed": True}
    )


def flat_candles(n: int, price: float = 100.0, g: int = 900, start: int = 1_700_000_100) -> list[Candle]:
    start -= start % g
    return [Candle(start + i * g, price, price + 0.5, price - 0.5, price, g) for i in range(n)]


@pytest.fixture
def now() -> int:
    return int(time.time())
