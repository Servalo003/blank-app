"""Core domain types shared across the application.

Conventions
-----------
* All timestamps are integer UTC epoch seconds unless a field name says otherwise.
* A candle's ``ts`` is its OPEN time (this matches Deriv's ``candles[].epoch``).
  ``close_ts = ts + granularity``. A candle is only "closed" when ``close_ts <= now``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Direction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    NO_TRADE = "NO_TRADE"


class DataProvenance(StrEnum):
    """Where market data came from. Shown on every signal, chart and alert."""

    LIVE_DATA = "LIVE_DATA"
    DELAYED_DATA = "DELAYED_DATA"
    SIMULATED_DATA = "SIMULATED_DATA"


class Timeframe(StrEnum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"

    @property
    def seconds(self) -> int:
        return _TF_SECONDS[self]

    @classmethod
    def from_seconds(cls, seconds: int) -> Timeframe:
        for tf, s in _TF_SECONDS.items():
            if s == seconds:
                return tf
        raise ValueError(f"unsupported granularity {seconds}")


_TF_SECONDS: dict[Timeframe, int] = {
    Timeframe.M1: 60,
    Timeframe.M5: 300,
    Timeframe.M15: 900,
    Timeframe.M30: 1800,
    Timeframe.H1: 3600,
    Timeframe.H4: 14400,
    Timeframe.D1: 86400,
}

# Default higher timeframe used for multi-timeframe trend alignment.
HIGHER_TIMEFRAME: dict[Timeframe, Timeframe] = {
    Timeframe.M1: Timeframe.M15,
    Timeframe.M5: Timeframe.H1,
    Timeframe.M15: Timeframe.H1,
    Timeframe.M30: Timeframe.H4,
    Timeframe.H1: Timeframe.H4,
    Timeframe.H4: Timeframe.D1,
    Timeframe.D1: Timeframe.D1,
}


@dataclass(frozen=True, slots=True)
class Candle:
    ts: int  # open time, UTC epoch seconds
    open: float
    high: float
    low: float
    close: float
    granularity: int

    @property
    def close_ts(self) -> int:
        return self.ts + self.granularity


@dataclass(frozen=True, slots=True)
class InstrumentInfo:
    symbol: str
    display_name: str
    market: str
    submarket: str
    symbol_type: str
    pip_size: float
    is_open: bool
    is_suspended: bool
    provenance: DataProvenance


class RiskStatus(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    RISK_UNAVAILABLE = "RISK_UNAVAILABLE"


class SignalStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    STOP_HIT = "STOP_HIT"
    TARGET_HIT = "TARGET_HIT"
    SUPPRESSED = "SUPPRESSED"  # generated but blocked by the risk engine


@dataclass(slots=True)
class StrategyDecision:
    """Output of one strategy evaluation on one fully closed bar."""

    strategy: str
    direction: Direction
    reasons: list[str]
    indicators: dict[str, float | str | None]
    params: dict[str, Any]
    entry_ref: float | None = None  # reference price (close of signal bar)
    stop: float | None = None
    target: float | None = None
    invalidation: float | None = None  # price that voids the idea before entry
    expiry_bars: int = 0  # signal validity in bars after the signal bar
    max_hold_bars: int = 0
    score: float = 0.0  # ordinal strength 0..1 - NOT a probability
    exit_long: bool = False  # strategy-specific exit rule for an open long
    exit_short: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DataQualityReport:
    n_candles: int
    n_duplicates_removed: int
    n_gaps: int
    largest_gap_bars: int
    n_invalid_ohlc: int
    n_unclosed_dropped: int
    last_close_ts: int | None
    is_stale: bool
    stale_by_seconds: int
    ok: bool
    issues: list[str] = field(default_factory=list)


def stable_hash(obj: Any) -> str:
    """Deterministic SHA-256 of a JSON-serialisable object (sorted keys)."""
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def dataset_hash(candles: list[Candle]) -> str:
    """Hash of an exact candle dataset - used to version backtests and signals."""
    h = hashlib.sha256()
    for c in candles:
        h.update(f"{c.ts},{c.open!r},{c.high!r},{c.low!r},{c.close!r},{c.granularity};".encode())
    return h.hexdigest()
