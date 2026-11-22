from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.core.types import Candle, DataProvenance, InstrumentInfo


class DataUnavailable(RuntimeError):
    """Raised when a provider cannot serve data; callers must degrade gracefully."""


class MarketDataProvider(ABC):
    """Read-only market data source. No provider may expose order/trade methods."""

    name: str
    provenance: DataProvenance

    @abstractmethod
    async def list_symbols(self) -> list[InstrumentInfo]: ...

    @abstractmethod
    async def get_candles(self, symbol: str, granularity: int, count: int) -> list[Candle]:
        """Return up to ``count`` most recent candles (may include the forming candle)."""

    @abstractmethod
    async def server_time(self) -> int: ...

    async def latest_quote(self, symbol: str) -> dict[str, Any] | None:
        """Most recent streamed tick {epoch, quote, bid, ask} if available."""
        return None

    def spread_stats(self, symbol: str) -> dict[str, Any] | None:
        """Observed bid/ask spread from the streamed feed, if the provider has one."""
        return None

    async def health(self) -> dict[str, Any]:
        return {"provider": self.name, "provenance": self.provenance.value}

    async def close(self) -> None:  # pragma: no cover - default no-op
        return None
