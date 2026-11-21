from __future__ import annotations

import itertools

import pytest

from app.core.types import DataProvenance, Direction, RiskStatus, StrategyDecision
from app.products.adapter import build_adapter
from app.risk.engine import HARD_MAX_RISK_PCT, AlertLevel, OpenPosition, RiskConfig, RiskContext, RiskDecision, RiskEngine


def _long(score: float = 0.5) -> StrategyDecision:
    return StrategyDecision(
        "trend_pullback", Direction.LONG, ["r"], {}, {}, entry_ref=100.0, stop=98.0, target=104.0, score=score
    )


def _ctx(**kw) -> RiskContext:  # type: ignore[no-untyped-def]
    base = {"equity": 10_000.0, "provenance": DataProvenance.LIVE_DATA, "strategy_eligible": True}
    base.update(kw)
    return RiskContext(**base)


def test_decision_cannot_be_forged() -> None:
    with pytest.raises(PermissionError):
        RiskDecision(RiskStatus.APPROVED, AlertLevel.ACTIONABLE, 1, 1, 1, (), (), "x")


def test_hard_cap_on_risk() -> None:
    with pytest.raises(ValueError):
        RiskConfig(risk_per_trade_pct=HARD_MAX_RISK_PCT + 0.1)


def test_risk_never_increases_after_losses() -> None:
    eng = RiskEngine(RiskConfig(risk_per_trade_pct=1.0, loss_streak_reduce=2, loss_streak_pause=5))
    seq = [eng.effective_risk_pct(n) for n in range(10)]
    assert all(b <= a for a, b in itertools.pairwise(seq))  # anti-martingale
    assert seq[0] == 1.0 and seq[3] == 0.5


def test_approved_live_actionable(linear_adapter) -> None:  # type: ignore[no-untyped-def]
    rd = RiskEngine(RiskConfig()).assess(_long(), "X", linear_adapter, _ctx())
    assert rd.status is RiskStatus.APPROVED and rd.alert_level is AlertLevel.ACTIONABLE
    assert rd.risk_amount <= 10_000 * 0.5 / 100 + 1e-9


def test_simulated_is_research_only(linear_adapter) -> None:  # type: ignore[no-untyped-def]
    eng = RiskEngine(RiskConfig(research_only_alerts=True))
    rd = eng.assess(_long(), "X", linear_adapter, _ctx(provenance=DataProvenance.SIMULATED_DATA))
    assert rd.alert_level is AlertLevel.RESEARCH_ONLY
    rd2 = RiskEngine(RiskConfig()).assess(_long(), "X", linear_adapter, _ctx(provenance=DataProvenance.SIMULATED_DATA))
    assert rd2.alert_level is AlertLevel.NONE  # research alerts off by default


def test_not_eligible_is_not_actionable(linear_adapter) -> None:  # type: ignore[no-untyped-def]
    rd = RiskEngine(RiskConfig()).assess(_long(), "X", linear_adapter, _ctx(strategy_eligible=False))
    assert rd.alert_level is not AlertLevel.ACTIONABLE


def test_unconfirmed_product_is_risk_unavailable() -> None:
    ad = build_adapter({"product_type": "linear", "confirmed": False})
    rd = RiskEngine(RiskConfig()).assess(_long(), "X", ad, _ctx())
    assert rd.status is RiskStatus.RISK_UNAVAILABLE and rd.alert_level is AlertLevel.NONE


def test_rise_fall_is_risk_unavailable() -> None:
    rd = RiskEngine(RiskConfig()).assess(_long(), "X", build_adapter({"product_type": "rise_fall"}), _ctx())
    assert rd.status is RiskStatus.RISK_UNAVAILABLE


def test_no_adapter_is_risk_unavailable() -> None:
    assert RiskEngine(RiskConfig()).assess(_long(), "X", None, _ctx()).status is RiskStatus.RISK_UNAVAILABLE


@pytest.mark.parametrize(
    ("ctx_kw", "cfg_kw", "check"),
    [
        ({}, {"kill_switch": True}, "kill_switch"),
        ({"data_ok": False, "data_issues": ["stale"]}, {}, "data_quality"),
        ({"realized_pnl_today": -250.0}, {}, "daily_loss"),
        ({"open_positions": [OpenPosition("X", Direction.LONG, 10)]}, {}, "per_symbol"),
        ({"open_positions": [OpenPosition(f"S{i}", Direction.LONG, 10) for i in range(3)]}, {}, "max_open_positions"),
        ({"consecutive_losses": 6, "last_loss_ts": 1000, "now_ts": 2000}, {}, "loss_streak"),
        ({"fundamentals_block": "High-impact event"}, {}, "event_window"),
    ],
)
def test_hard_rejections(linear_adapter, ctx_kw, cfg_kw, check) -> None:  # type: ignore[no-untyped-def]
    rd = RiskEngine(RiskConfig(**cfg_kw)).assess(_long(), "X", linear_adapter, _ctx(**ctx_kw))
    assert rd.status is RiskStatus.REJECTED
    assert any(n == check and not ok for n, ok, _ in rd.checks)


def test_loss_streak_pause_expires(linear_adapter) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(consecutive_losses=6, last_loss_ts=0, now_ts=25 * 3600)
    rd = RiskEngine(RiskConfig()).assess(_long(), "X", linear_adapter, ctx)
    assert rd.status is RiskStatus.APPROVED and rd.risk_pct == pytest.approx(0.25)  # resumed, at halved risk


def test_bad_reward_risk_rejected(linear_adapter) -> None:  # type: ignore[no-untyped-def]
    d = StrategyDecision("x", Direction.LONG, [], {}, {}, entry_ref=100, stop=98, target=101)
    assert RiskEngine(RiskConfig()).assess(d, "X", linear_adapter, _ctx()).status is RiskStatus.REJECTED


def test_aggregate_risk_budget_shrinks_size(linear_adapter) -> None:  # type: ignore[no-untyped-def]
    eng = RiskEngine(RiskConfig(max_total_open_risk_pct=1.0, max_open_positions=5))
    rd = eng.assess(_long(), "X", linear_adapter, _ctx(open_positions=[OpenPosition("Y", Direction.LONG, 80.0)]))
    assert rd.status is RiskStatus.APPROVED and rd.risk_amount <= 20.0 + 1e-6
