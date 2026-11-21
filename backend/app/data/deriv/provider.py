from __future__ import annotations

import time
from typing import Any

from app.core.types import Candle, DataProvenance, InstrumentInfo
from app.data.deriv.client import DerivWSClient
from app.data.deriv.schema import (
    MAX_COUNT_PER_REQUEST,
    active_symbols_request,
    candles_request,
    parse_active_symbols,
    parse_candles,
)
from app.data.provider import DataUnavailable, MarketDataProvider


class DerivProvider(MarketDataProvider):
    """Public, unauthenticated Deriv market data (LIVE_DATA while the stream is fresh)."""

    name = "deriv"

    def __init__(self, client: DerivWSClient, symbols_ttl: int = 3600, delayed_after: float = 30.0) -> None:
        self.client = client
        self._symbols: tuple[float, list[InstrumentInfo]] | None = None
        self.symbols_ttl = symbols_ttl
        self.delayed_after = delayed_after
        self._time_offset: float = 0.0

    @property
    def provenance(self) -> DataProvenance:  # type: ignore[override]
        last = self.client.last_message_at
        if not self.client.connected or last is None or time.time() - last > self.delayed_after:
            return DataProvenance.DELAYED_DATA
        return DataProvenance.LIVE_DATA

    async def list_symbols(self) -> list[InstrumentInfo]:
        if self._symbols and time.time() - self._symbols[0] < self.symbols_ttl:
            return self._symbols[1]
        try:
            msg = await self.client.call_with_retry(active_symbols_request("brief"))
        except Exception as exc:
            if self._symbols:
                return self._symbols[1]  # serve cached list on failure
            raise DataUnavailable(str(exc)) from exc
        syms = parse_active_symbols(msg, DataProvenance.LIVE_DATA)
        self._symbols = (time.time(), syms)
        return syms

    async def server_time(self) -> int:
        try:
            msg = await self.client.call_with_retry({"time": 1}, attempts=2)
            t = int(msg["time"])
            self._time_offset = t - time.time()
            return t
        except Exception:
            return int(time.time() + self._time_offset)

    async def get_candles(self, symbol: str, granularity: int, count: int) -> list[Candle]:
        """Paginate backwards in pages of <= 1000 candles until ``count`` reached or history ends."""
        out: dict[int, Candle] = {}
        end: int | str = "latest"
        remaining = count
        try:
            while remaining > 0:
                requested = min(remaining, MAX_COUNT_PER_REQUEST)
                msg = await self.client.call_with_retry(candles_request(symbol, granularity, requested, end))
                page = parse_candles(msg, granularity)
                new = [c for c in page if c.ts not in out]
                if not new:
                    break
                for c in new:
                    out[c.ts] = c
                remaining -= len(new)
                earliest = min(c.ts for c in page)
                end = earliest - 1
                if len(page) < requested:
                    break  # history exhausted
        except DataUnavailable:
            raise
        except Exception as exc:
            if not out:
                raise DataUnavailable(f"deriv candles {symbol}/{granularity}: {exc}") from exc
        return [out[k] for k in sorted(out)][-count:]

    async def latest_quote(self, symbol: str) -> dict[str, Any] | None:
        await self.client.subscribe_ticks(symbol)
        return self.client.last_ticks.get(symbol)

    async def health(self) -> dict[str, Any]:
        last = self.client.last_message_at
        return {
            "provider": self.name,
            "provenance": self.provenance.value,
            "connected": self.client.connected,
            "url": self.client.url,
            "seconds_since_last_message": None if last is None else round(time.time() - last, 1),
            "reconnects": self.client.reconnects,
            "last_error": self.client.last_error,
        }

    async def close(self) -> None:
        await self.client.close()
