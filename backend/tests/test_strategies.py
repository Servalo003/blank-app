from __future__ import annotations

import pytest

from app.core.types import Candle, Direction
from app.strategies.ensemble import combine
from app.strategies.features import compute_features
from app.strategies.registry import ALL_STRATEGY_NAMES, BASE_STRATEGIES, build_strategy


@pytest.mark.parametrize("name", ALL_STRATEGY_NAMES)
def test_insufficient_history_is_no_trade(name: str, sim_candles: list[Candle]) -> None:
    f = compute_features(sim_candles[:100])
    d = build_strategy(name).evaluate(f, 99)
    assert d.direction is Direction.NO_TRADE
    assert d.reasons


@pytest.mark.parametrize("name", list(BASE_STRATEGIES))
def test_decisions_do_not_repaint(name: str, sim_candles: list[Candle]) -> None:
    """A decision at bar i must be identical whether or not later bars exist."""
    cs = sim_candles[:1500]
    full = compute_features(cs)
    strat = build_strategy(name)
    for i in range(300, 1500, 97):
        a = strat.evaluate(full, i)
        b = strat.evaluate(compute_features(cs[: i + 1]), i)
        assert a.direction == b.direction
        assert a.stop == pytest.approx(b.stop) if a.stop is not None else b.stop is None
        assert a.reasons == b.reasons


@pytest.mark.parametrize("name", list(BASE_STRATEGIES))
def test_directional_decisions_are_well_formed(name: str, sim_candles: list[Candle]) -> None:
    f = compute_features(sim_candles)
    strat = build_strategy(name)
    for i in range(260, len(sim_candles)):
        d = strat.evaluate(f, i)
        if d.direction is Direction.NO_TRADE:
            continue
        assert d.entry_ref is not None and d.stop is not None and d.target is not None
        if d.direction is Direction.LONG:
            assert d.stop < d.entry_ref < d.target
        else:
            assert d.target < d.entry_ref < d.stop
        assert 0 <= d.score <= 1
        assert d.params == strat.params
        assert d.reasons


def test_unknown_params_rejected() -> None:
    with pytest.raises(ValueError):
        build_strategy("trend_pullback", {"nope": 1})


def test_ensemble_conflict_is_no_trade() -> None:
    from app.core.types import StrategyDecision

    long = StrategyDecision("a", Direction.LONG, ["x"], {}, {}, 1, 0.5, 2, score=0.8)
    short = StrategyDecision("b", Direction.SHORT, ["y"], {}, {}, 1, 1.5, 0, score=0.9)
    out = combine([long, short], {"weights": {"a": 1, "b": 1}})
    assert out.direction is Direction.NO_TRADE
    assert "Conflicting" in out.reasons[0]
