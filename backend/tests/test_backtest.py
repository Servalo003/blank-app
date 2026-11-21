"""Backtester leak-freedom, execution timing, cost handling and reproducibility."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, ClassVar

import pandas as pd
import pytest

from app.backtest.engine import BacktestConfig, run_backtest
from app.backtest.evaluation import chronological_split, walk_forward_windows
from app.backtest.metrics import compute_metrics
from app.core.types import Candle, Direction, StrategyDecision
from app.products.adapter import CostModel
from app.strategies.base import Strategy
from app.strategies.registry import build_strategy

COSTS = CostModel(spread_bps=2.0, slippage_atr=0.05, commission_bps=0.5)


def _cfg(**kw: Any) -> BacktestConfig:
    base = {"symbol": "SIM_MIXED", "strategy": "donchian_breakout", "params": {}, "timeframe": "15m", "costs": COSTS}
    base.update(kw)
    return BacktestConfig(**base)


def test_strict_recompute_equals_precomputed(sim_candles: list[Candle], linear_adapter) -> None:  # type: ignore[no-untyped-def]
    """Recomputing features from candles[:i+1] at every bar gives identical trades -> no look-ahead."""
    cs = sim_candles[:900]
    strat = build_strategy("donchian_breakout")
    fast = run_backtest(cs, strat, linear_adapter, _cfg(start_index=260))
    strict = run_backtest(cs, strat, linear_adapter, _cfg(start_index=260, strict=True))
    assert [t.to_dict() for t in fast.trades] == [t.to_dict() for t in strict.trades]
    assert fast.equity == strict.equity


def test_future_data_does_not_change_past_trades(sim_candles: list[Candle], linear_adapter) -> None:  # type: ignore[no-untyped-def]
    cs = sim_candles[:2000]
    cut = 1500
    base = run_backtest(cs, build_strategy("trend_pullback"), linear_adapter, _cfg(start_index=260))
    # corrupt everything after `cut`
    corrupted = cs[:cut] + [Candle(c.ts, c.open * 3, c.high * 3, c.low * 3, c.close * 3, c.granularity) for c in cs[cut:]]
    alt = run_backtest(corrupted, build_strategy("trend_pullback"), linear_adapter, _cfg(start_index=260))
    done_before = [t.to_dict() for t in base.trades if t.exit_ts is not None and t.exit_ts <= cs[cut - 1].close_ts]
    alt_before = [t.to_dict() for t in alt.trades if t.exit_ts is not None and t.exit_ts <= cs[cut - 1].close_ts]
    assert done_before and done_before == alt_before


def test_execution_after_signal_and_costs(sim_candles: list[Candle], linear_adapter) -> None:  # type: ignore[no-untyped-def]
    res = run_backtest(sim_candles, build_strategy("donchian_breakout"), linear_adapter, _cfg(start_index=260))
    assert res.trades
    by_ts = {c.ts: c for c in sim_candles}
    for t in res.trades:
        assert t.entry_ts >= t.signal_ts  # signal bar close == next bar open at the earliest
        assert t.entry_mid == by_ts[t.entry_ts].open  # filled at the next bar's open
        assert t.costs > 0  # spread + commission always cost something
        assert t.net_pnl < t.gross_pnl
    m = compute_metrics(res)
    assert m["net"]["net_pnl"] < m["gross"]["net_pnl"]


class _Scripted(Strategy):
    """Goes long once at a given bar with fixed levels - for deterministic fill tests."""

    name = "scripted"
    description = "test"
    default_params: ClassVar[dict[str, Any]] = {"at": 5, "stop": 99.0, "target": 101.0}
    min_history = 0

    def _evaluate(self, f: pd.DataFrame, i: int) -> StrategyDecision:
        p = self.params
        if i != p["at"]:
            return self.no_trade(["not now"])
        return StrategyDecision(self.name, Direction.LONG, ["scripted"], {}, dict(p), 100.0, p["stop"], p["target"])


def _bars(spec: list[tuple[float, float, float, float]], g: int = 900) -> list[Candle]:
    return [Candle(i * g, o, h, lo, c, g) for i, (o, h, lo, c) in enumerate(spec)]


def _risk_free_cfg() -> BacktestConfig:
    from app.risk.engine import RiskConfig

    return _cfg(strategy="scripted", costs=CostModel(), risk=RiskConfig(min_reward_risk=0.5), start_index=1)


def test_same_bar_stop_and_target_assumes_stop(linear_adapter) -> None:  # type: ignore[no-untyped-def]
    flat = [(100, 100.2, 99.8, 100)] * 6
    bars = _bars([*flat, (100, 102, 98, 100), (100, 100.1, 99.9, 100)])
    feats = pd.DataFrame({"atr14": [0.4] * len(bars), "close": [c.close for c in bars]}, index=[c.ts for c in bars])
    res = run_backtest(bars, _Scripted(), linear_adapter, _risk_free_cfg(), features=feats)
    t = res.trades[0]
    assert t.entry_ts == bars[6].ts  # signal at close of bar 5 -> entry at bar 6 open
    assert t.exit_reason.startswith("stop") and t.ambiguous_bar
    assert t.exit_price == 99.0


def test_gap_through_stop_fills_at_open(linear_adapter) -> None:  # type: ignore[no-untyped-def]
    flat = [(100, 100.2, 99.8, 100)] * 7
    bars = _bars([*flat, (97, 97.5, 96.5, 97), (97, 97.1, 96.9, 97)])
    feats = pd.DataFrame({"atr14": [0.4] * len(bars), "close": [c.close for c in bars]}, index=[c.ts for c in bars])
    res = run_backtest(bars, _Scripted(), linear_adapter, _risk_free_cfg(), features=feats)
    t = res.trades[0]
    assert t.exit_price == 97.0 and "gap" in t.exit_reason
    assert t.r_net < -1.0  # gap loss worse than planned 1R


def test_reproducible_hashes(sim_candles: list[Candle], linear_adapter) -> None:  # type: ignore[no-untyped-def]
    a = run_backtest(sim_candles[:800], build_strategy("trend_pullback"), linear_adapter, _cfg(start_index=260))
    b = run_backtest(sim_candles[:800], build_strategy("trend_pullback"), linear_adapter, _cfg(start_index=260))
    assert a.config_hash == b.config_hash and a.dataset_hash == b.dataset_hash
    assert [t.to_dict() for t in a.trades] == [t.to_dict() for t in b.trades]
    c = run_backtest(sim_candles[:800], build_strategy("trend_pullback"), linear_adapter, replace(_cfg(start_index=260), seed=1))
    assert c.config_hash != a.config_hash


def test_splits_are_chronological_and_disjoint() -> None:
    plan = chronological_split(3000, 250)
    assert plan.train[0] == 250 and plan.train[1] == plan.validation[0] and plan.validation[1] == plan.test[0]
    assert plan.test[1] == 3000
    wins = walk_forward_windows(plan.train[0], plan.validation[1], 4)
    for tr_start, te_start, te_end in wins:
        assert tr_start < te_start < te_end <= plan.test[0]  # never touches the hold-out


def test_unbacktestable_adapter_refused(sim_candles: list[Candle]) -> None:
    from app.products.adapter import build_adapter

    rf = build_adapter({"product_type": "rise_fall"})
    with pytest.raises(ValueError):
        run_backtest(sim_candles[:400], build_strategy("trend_pullback"), rf, _cfg())
