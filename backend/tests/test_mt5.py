from __future__ import annotations

from app.core.types import DataProvenance, Direction, RiskStatus, StrategyDecision
from app.products import mt5
from app.products.adapter import build_adapter
from app.risk.engine import RiskConfig, RiskContext, RiskEngine


def test_official_specs_load_and_match_api_names() -> None:
    assert mt5.source()["source"].startswith("https://deriv.com/")
    spec = mt5.suggest_spec("Volatility 100 Index")
    assert spec is not None and spec["confirmed"] is False  # suggestions always need operator confirmation
    assert spec["contract_size"] > 0 and spec["min_units"] > 0
    assert mt5.suggest_spec("EUR/USD") is not None  # API "EUR/USD" matches MT5 "EURUSD"
    assert mt5.suggest_spec("Definitely Not An Instrument") is None  # no guessing


def test_confirmed_mt5_spec_sizes_within_limits() -> None:
    spec = mt5.suggest_spec("Volatility 100 Index")
    assert spec is not None
    spec["confirmed"] = True
    ad = build_adapter(spec)
    d = StrategyDecision("x", Direction.LONG, [], {}, {}, entry_ref=500.0, stop=495.0, target=510.0)
    ctx = RiskContext(equity=10_000, provenance=DataProvenance.LIVE_DATA, strategy_eligible=True)
    rd = RiskEngine(RiskConfig()).assess(d, "R_100", ad, ctx)
    assert rd.status is RiskStatus.APPROVED
    assert rd.units >= spec["min_units"] and rd.units <= spec["max_units"]
    assert rd.units % spec["unit_step"] == 0
    assert rd.risk_amount <= 50.0 + 1e-6
