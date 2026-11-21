"""API + pipeline integration on SQLite with the simulated provider."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import reset_engine, session_scope

TOKEN = "t" * 32


@pytest.fixture
def client(tmp_path, monkeypatch) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("APP_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("RESEARCH_ONLY_ALERTS", "true")
    get_settings.cache_clear()
    reset_engine()
    from app.services.runtime import set_provider

    set_provider(None)
    from app.api.main import app

    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        yield c
    reset_engine()
    get_settings.cache_clear()


def test_health_open_status_protected(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"status": "ok"}
    r = client.get("/api/status", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    st = client.get("/api/status").json()
    assert st["provenance"] == "SIMULATED_DATA" and "no order placement" in st["mode"]
    assert client.get("/api/health").headers["x-frame-options"] == "DENY"


def test_no_order_endpoints_exist(client: TestClient) -> None:
    paths = {r.path for r in client.app.routes}  # type: ignore[attr-defined]
    assert not any(word in p for p in paths for word in ("order", "buy", "sell", "trade"))


def test_scan_produces_snapshots_and_audit(client: TestClient) -> None:
    r = client.post("/api/scan/run")
    assert r.status_code == 200, r.text
    snaps = client.get("/api/scan/snapshots").json()
    assert snaps and all(s["provenance"] == "SIMULATED_DATA" for s in snaps)
    assert {s["direction"] for s in snaps} <= {"LONG", "SHORT", "NO_TRADE"}
    audit = client.get("/api/audit").json()
    assert audit["chain"]["ok"] is True
    # second scan on the same closed bar must not duplicate signals
    n1 = len(client.get("/api/signals").json())
    client.post("/api/scan/run")
    assert len(client.get("/api/signals").json()) == n1


def test_risk_config_validation_and_audit(client: TestClient) -> None:
    assert client.put("/api/risk/config", json={"changes": {"risk_per_trade_pct": 5}}).status_code == 400
    assert client.put("/api/risk/config", json={"changes": {"bogus": 1}}).status_code == 400
    r = client.post("/api/risk/kill-switch", params={"engaged": True})
    assert r.json()["kill_switch"] is True
    actions = [e["action"] for e in client.get("/api/audit").json()["entries"]]
    assert "risk_config.update" in actions


def test_backtest_endpoint_persists_full_record(client: TestClient) -> None:
    r = client.post(
        "/api/backtests", json={"symbol": "SIM_TREND", "timeframe": "15m", "strategy": "donchian_breakout", "bars": 1500}
    )
    body = r.json()
    assert body["status"] == "DONE", body
    assert body["dataset_hash"] and body["config_hash"]
    assert "gross" in body["metrics"] and "net" in body["metrics"]
    full = client.get(f"/api/backtests/{body['id']}").json()
    assert len(full["trades"]) == body["metrics"]["net"]["trades"]


def test_dispatcher_blocks_unvetted_and_is_idempotent(client: TestClient) -> None:
    from app.models import AlertDelivery, SignalRow
    from app.notifications.dispatcher import Dispatcher

    sent: list[str] = []

    class Ch:
        name = "fake"

        def configured(self) -> bool:
            return True

        async def send(self, title: str, body: str, data: dict[str, Any]) -> None:
            sent.append(title)

    common = dict(
        provider="simulator",
        provenance="SIMULATED_DATA",
        symbol="SIM_TREND",
        timeframe="15m",
        strategy="x",
        direction="LONG",
        status="ACTIVE",
        entry_window_until=2,
        expires_at=3,
        entry_ref=100.0,
        stop=99.0,
        target=102.0,
        params_hash="h",
        risk_config_hash="h",
        dataset_hash="h",
        code_version="t",
    )
    with session_scope() as s:
        blocked = SignalRow(signal_ts=1, risk_status="REJECTED", alert_level="NONE", **common)
        ok = SignalRow(signal_ts=2, risk_status="APPROVED", alert_level="RESEARCH_ONLY", **common)
        s.add_all([blocked, ok])
        s.flush()
        d = Dispatcher([Ch()], max_attempts=1)
        asyncio.run(d.dispatch(s, blocked, "CREATED"))
        asyncio.run(d.dispatch(s, ok, "CREATED"))
        asyncio.run(d.dispatch(s, ok, "CREATED"))  # duplicate
        assert len(sent) == 1 and "SIMULATED_DATA" in sent[0]
        assert s.query(AlertDelivery).count() == 1


def test_lifecycle_invalidation_stop_target() -> None:
    from app.core.types import Candle
    from app.models import SignalRow
    from app.services.scanner import update_lifecycle

    class S:
        def add(self, *_: Any) -> None: ...

    def sig() -> SignalRow:
        return SignalRow(
            id="a",
            direction="LONG",
            status="ACTIVE",
            signal_ts=900,
            entry_window_until=1800,
            expires_at=9000,
            entry_ref=100.0,
            stop=98.0,
            target=104.0,
            invalidation=99.0,
        )

    import app.services.scanner as sc

    orig = sc.audit.record
    sc.audit.record = lambda *a, **k: None  # type: ignore[assignment]
    try:
        g = 900
        s1 = sig()
        assert update_lifecycle(S(), s1, [Candle(900, 100, 100.5, 98.5, 98.8, g)])[0][0] == "INVALIDATED"  # type: ignore[arg-type]
        s2 = sig()
        assert update_lifecycle(S(), s2, [Candle(900, 100, 104.5, 97.5, 100, g)])[0][0] == "STOP_HIT"  # type: ignore[arg-type]
        s3 = sig()
        out = update_lifecycle(S(), s3, [Candle(900, 100, 101, 99.5, 100.5, g), Candle(1800, 100.5, 104.2, 100, 104, g)])  # type: ignore[arg-type]
        assert out[0][0] == "TARGET_HIT" and s3.outcome_r == pytest.approx(2.0)
    finally:
        sc.audit.record = orig  # type: ignore[assignment]
