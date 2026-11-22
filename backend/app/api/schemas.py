from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.core.types import Timeframe
from app.strategies.registry import ALL_STRATEGY_NAMES


class StrategyToggle(BaseModel):
    enabled: bool = True
    params: dict[str, Any] | None = None


class WatchlistItemIn(BaseModel):
    symbol: str = Field(pattern=r"^\w{2,30}$")
    timeframe: Timeframe
    strategies: dict[str, StrategyToggle] = Field(default_factory=dict)
    enabled: bool = True

    @field_validator("strategies")
    @classmethod
    def _known(cls, v: dict[str, StrategyToggle]) -> dict[str, StrategyToggle]:
        unknown = set(v) - set(ALL_STRATEGY_NAMES)
        if unknown:
            raise ValueError(f"unknown strategies {sorted(unknown)}")
        return v


class WatchlistIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    items: list[WatchlistItemIn] = Field(default_factory=list, max_length=50)


class BacktestIn(BaseModel):
    symbol: str = Field(pattern=r"^\w{2,30}$")
    timeframe: Timeframe
    strategy: str
    params: dict[str, Any] | None = None
    bars: int = Field(default=3000, ge=400, le=20000)
    initial_equity: float = Field(default=10_000, gt=0)
    seed: int = 42
    product_spec: dict[str, Any] | None = None
    research_override_unconfirmed: bool = False
    kind: Literal["backtest", "evaluation"] = "backtest"

    @field_validator("strategy")
    @classmethod
    def _strategy(cls, v: str) -> str:
        if v not in ALL_STRATEGY_NAMES:
            raise ValueError(f"unknown strategy {v}")
        return v


class InstrumentSpecIn(BaseModel):
    product_spec: dict[str, Any]
    periods_per_year: int = Field(default=365, ge=1, le=366)


class RiskConfigIn(BaseModel):
    changes: dict[str, Any]


class PushSubscriptionIn(BaseModel):
    endpoint: str = Field(pattern=r"^https://")
    keys: dict[str, str]


class EconomicEventIn(BaseModel):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    title: str = Field(max_length=200)
    impact: Literal["low", "medium", "high"]
    scheduled_at: int
    known_at: int
    source: str = Field(max_length=120)
    actual: str | None = None
    forecast: str | None = None
    previous: str | None = None


class CsvIn(BaseModel):
    csv: str = Field(max_length=2_000_000)
