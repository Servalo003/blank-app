"""Deriv adapter contract tests.

* Parsers are tested against responses RECORDED from the public endpoint.
* Requests are validated against the official JSON schemas when
  ``DERIV_SCHEMAS_DIR`` points at an extracted ``schemas.zip`` (CI downloads it).
* ``DSL_LIVE_TESTS=1`` enables a live smoke test against the public endpoint.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.core.types import DataProvenance
from app.data.deriv.client import DerivWSClient, RateLimiter, backoff_delays
from app.data.deriv.provider import DerivProvider
from app.data.deriv.schema import (
    PUBLIC_WS_URL,
    DerivSchemaError,
    active_symbols_request,
    candles_request,
    error_of,
    parse_active_symbols,
    parse_candles,
    parse_tick,
    ticks_subscribe_request,
)

FIX = json.loads((Path(__file__).parent / "fixtures" / "deriv_public_recorded.json").read_text())
SCHEMAS = os.environ.get("DERIV_SCHEMAS_DIR")


def test_parse_recorded_candles() -> None:
    cs = parse_candles(FIX["candles"], 300)
    assert len(cs) == 5 and all(c.granularity == 300 and c.high >= c.low for c in cs)
    assert all(c.ts % 300 == 0 for c in cs)


def test_parse_recorded_active_symbols() -> None:
    syms = parse_active_symbols(FIX["active_symbols"], DataProvenance.LIVE_DATA)
    assert syms and all(s.symbol and s.market for s in syms)


def test_parse_recorded_tick_and_error() -> None:
    t = parse_tick(FIX["tick"])
    assert t is not None and t["ask"] >= t["bid"]
    assert error_of(FIX["error"]) == ("InvalidSymbol", "Invalid symbol.")


def test_request_builders_validate() -> None:
    with pytest.raises(DerivSchemaError):
        candles_request("R_100", 250, 10)  # granularity not in schema enum
    with pytest.raises(DerivSchemaError):
        candles_request("bad symbol!", 300, 10)
    assert candles_request("R_100", 300, 5000)["count"] == 1000


@pytest.mark.skipif(not SCHEMAS, reason="DERIV_SCHEMAS_DIR not set")
def test_requests_match_official_schemas() -> None:
    import jsonschema

    def check(name: str, payload: dict[str, Any]) -> None:
        schema = json.loads((Path(SCHEMAS) / f"{name}_request.schema.json").read_text())  # type: ignore[arg-type]
        jsonschema.validate({**payload, "req_id": 1}, schema)

    check("ticks_history", candles_request("R_100", 300, 10))
    check("ticks_history", candles_request("R_100", 300, 10, end=1795286700))
    check("active_symbols", active_symbols_request())
    check("ticks", ticks_subscribe_request("R_100"))
    check("ping", {"ping": 1})
    check("time", {"time": 1})


async def test_client_refuses_trading_calls() -> None:
    client = DerivWSClient(PUBLIC_WS_URL)
    for call in ({"buy": 1, "price": 10}, {"proposal": 1}, {"sell": 1}, {"authorize": "x"}):
        with pytest.raises(PermissionError):
            await client.call(call)


async def test_rate_limiter_blocks_when_budget_used() -> None:
    t = [0.0]
    rl = RateLimiter(per_minute=3, per_hour=100, clock=lambda: t[0])
    for _ in range(3):
        await rl.acquire()
    assert rl.wait_time() == pytest.approx(60.0)
    t[0] = 61.0
    assert rl.wait_time() == 0.0


def test_backoff_is_capped_and_grows() -> None:
    import random

    d = backoff_delays(base=1, cap=8, rng=random.Random(0))
    vals = [next(d) for _ in range(7)]
    assert vals[0] < vals[3] and all(v <= 8 * 1.25 for v in vals)


class _FakeClient:
    """Serves 2,500 synthetic candle epochs in pages, like ticks_history with `end`."""

    connected = True
    last_message_at = None
    url = "fake"
    reconnects = 0
    last_error = None

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def call_with_retry(self, p: dict[str, Any], attempts: int = 4) -> dict[str, Any]:
        self.calls.append(p)
        g = p["granularity"]
        last = 2500 * g if p["end"] == "latest" else int(p["end"]) - int(p["end"]) % g
        first = max(g, last - (p["count"] - 1) * g)
        cs = [{"epoch": e, "open": 1, "high": 2, "low": 0.5, "close": 1.5} for e in range(first, last + 1, g)]
        return {"msg_type": "candles", "candles": cs}


async def test_pagination_walks_backwards() -> None:
    fake = _FakeClient()
    prov = DerivProvider(fake)  # type: ignore[arg-type]
    cs = await prov.get_candles("R_100", 60, 2300)
    assert len(cs) == 2300 and len(fake.calls) == 3
    assert [c.ts for c in cs] == sorted({c.ts for c in cs})
    assert prov.provenance is DataProvenance.DELAYED_DATA  # no fresh stream -> not LIVE


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("DSL_LIVE_TESTS") != "1", reason="live test opt-in")
async def test_live_public_endpoint() -> None:
    client = DerivWSClient(PUBLIC_WS_URL)
    prov = DerivProvider(client)
    try:
        cs = await asyncio.wait_for(prov.get_candles("R_100", 300, 50), 30)
        assert len(cs) == 50
        assert (await prov.server_time()) > 1_700_000_000
    finally:
        await prov.close()
