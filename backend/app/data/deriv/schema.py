"""Deriv WebSocket message builders/parsers - READ-ONLY market data calls only.

Verified against the official documentation and published JSON schemas
(``deriv-com/deriv-api-schemas`` ``schemas.zip``) on 2026-11-21:

* Public endpoint (no auth): ``wss://api.derivws.com/trading/v1/options/ws/public``
  https://developers.deriv.com/docs/options/ws-public/
* ``active_symbols``: request ``{"active_symbols": "brief"|"full"}``; each item has
  ``underlying_symbol``, ``underlying_symbol_name``, ``underlying_symbol_type``,
  ``market``, ``submarket``, ``subgroup``, ``pip_size``, ``exchange_is_open``,
  ``is_trading_suspended``, ``trade_count``.
  https://developers.deriv.com/docs/data/active-symbols/
* ``ticks_history``: required ``ticks_history`` and ``end`` ("latest" or epoch string);
  optional ``start``, ``count`` (default 1000), ``style`` ("candles"|"ticks"),
  ``granularity`` in {60,120,180,300,600,900,1800,3600,7200,14400,28800,86400},
  ``adjust_start_time``, ``subscribe``. Response ``candles: [{epoch, open, high, low, close}]``.
  https://developers.deriv.com/docs/data/ticks-history/
* ``ticks``: ``{"ticks": sym, "subscribe": 1}``; ``tick`` has ``epoch, quote, bid, ask, symbol, pip_size, id``.
* ``ping`` -> ``{"ping": "pong"}``; ``time`` -> ``{"time": <epoch>}``; ``forget`` / ``forget_all``.
* Correlation via integer ``req_id``. Errors are returned in an ``error`` object with
  ``code`` and ``message`` (observed live: ``{"code": "InvalidSymbol", "message": "Invalid symbol."}``).
* Rate limits (https://developers.deriv.com/docs/limits/): "all other calls" share
  220/minute and 14,400/hour per connection budget; ping is outside budgets (10/s).

The ``ohlc`` streaming message is listed as a ``msg_type`` but its payload shape
is not described in the published schema, so this adapter deliberately does NOT
depend on it: closed candles are re-fetched with ``ticks_history`` after each
bar close instead.
"""

from __future__ import annotations

from typing import Any

from app.core.types import Candle, DataProvenance, InstrumentInfo

PUBLIC_WS_URL = "wss://api.derivws.com/trading/v1/options/ws/public"
ALLOWED_GRANULARITIES = frozenset({60, 120, 180, 300, 600, 900, 1800, 3600, 7200, 14400, 28800, 86400})
MAX_COUNT_PER_REQUEST = 1000  # documented default; no documented maximum, so stay at the default
# Calls this adapter is permitted to send. Anything else is refused client-side.
READ_ONLY_CALLS = frozenset({"active_symbols", "ticks_history", "ticks", "ping", "time", "forget", "forget_all"})


class DerivSchemaError(ValueError):
    pass


def active_symbols_request(mode: str = "brief") -> dict[str, Any]:
    if mode not in ("brief", "full"):
        raise DerivSchemaError("active_symbols must be 'brief' or 'full'")
    return {"active_symbols": mode}


def candles_request(symbol: str, granularity: int, count: int, end: int | str = "latest") -> dict[str, Any]:
    if granularity not in ALLOWED_GRANULARITIES:
        raise DerivSchemaError(f"granularity {granularity} not allowed by Deriv schema")
    if not (2 <= len(symbol) <= 30) or not symbol.replace("_", "").isalnum():
        raise DerivSchemaError(f"invalid symbol {symbol!r}")
    return {
        "ticks_history": symbol,
        "end": str(end),
        "count": int(min(count, MAX_COUNT_PER_REQUEST)),
        "style": "candles",
        "granularity": granularity,
        "adjust_start_time": 1,
    }


def ticks_subscribe_request(symbol: str) -> dict[str, Any]:
    return {"ticks": symbol, "subscribe": 1}


def parse_candles(msg: dict[str, Any], granularity: int) -> list[Candle]:
    if msg.get("msg_type") != "candles" or not isinstance(msg.get("candles"), list):
        raise DerivSchemaError(f"unexpected ticks_history response msg_type={msg.get('msg_type')}")
    out: list[Candle] = []
    for c in msg["candles"]:
        try:
            out.append(
                Candle(
                    ts=int(c["epoch"]),
                    open=float(c["open"]),
                    high=float(c["high"]),
                    low=float(c["low"]),
                    close=float(c["close"]),
                    granularity=granularity,
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DerivSchemaError(f"malformed candle {c!r}") from exc
    return out


def parse_active_symbols(msg: dict[str, Any], provenance: DataProvenance) -> list[InstrumentInfo]:
    if msg.get("msg_type") != "active_symbols":
        raise DerivSchemaError("unexpected active_symbols response")
    out = []
    for s in msg.get("active_symbols") or []:
        out.append(
            InstrumentInfo(
                symbol=s["underlying_symbol"],
                display_name=s.get("underlying_symbol_name", s["underlying_symbol"]),
                market=s.get("market", ""),
                submarket=s.get("submarket", ""),
                symbol_type=s.get("underlying_symbol_type", ""),
                pip_size=float(s.get("pip_size", 0.01)),
                is_open=bool(s.get("exchange_is_open", 0)),
                is_suspended=bool(s.get("is_trading_suspended", 0)),
                provenance=provenance,
            )
        )
    return out


def parse_tick(msg: dict[str, Any]) -> dict[str, Any] | None:
    t = msg.get("tick")
    if msg.get("msg_type") != "tick" or not isinstance(t, dict):
        return None
    return {k: t.get(k) for k in ("symbol", "epoch", "quote", "bid", "ask", "pip_size")}


def error_of(msg: dict[str, Any]) -> tuple[str, str] | None:
    err = msg.get("error")
    if not err:
        return None
    if isinstance(err, dict):
        return str(err.get("code", "UnknownError")), str(err.get("message", ""))
    return "UnknownError", str(err)
