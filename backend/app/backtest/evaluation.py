"""Out-of-sample evaluation: chronological splits, walk-forward, benchmarks, eligibility.

Protocol
--------
1. Data is split chronologically into TRAIN | VALIDATION | TEST (default 60/20/20).
   The TEST segment is a final hold-out that is never used for any choice.
2. Walk-forward over TRAIN+VALIDATION: in each fold, parameters are chosen from
   the strategy's small pre-declared grid using ONLY that fold's training
   window, then evaluated on the following unseen window.
3. The parameter set chosen most often across folds (ties -> defaults) is
   evaluated once on TEST.
4. A strategy is labelled ``ELIGIBLE_FOR_PAPER_ALERTS`` only if pre-declared
   criteria hold on TEST *and* across walk-forward folds. This label means
   "passed a research screen", not "will be profitable".

The selection objective is net expectancy in R with a minimum trade count -
not maximum profit - to discourage selecting fragile, few-trade parameter sets.
"""

from __future__ import annotations

import itertools
import random
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
import pandas as pd

from app.backtest.engine import BacktestConfig, BacktestResult, run_backtest
from app.backtest.metrics import breakdown, compute_metrics, trade_stats
from app.core.types import Candle
from app.products.adapter import ProductAdapter
from app.strategies.features import compute_features
from app.strategies.registry import build_strategy


@dataclass(frozen=True)
class EligibilityCriteria:
    min_test_trades: int = 30
    min_test_expectancy_r: float = 0.05
    min_test_profit_factor: float = 1.1
    max_test_drawdown_pct: float = 20.0
    min_positive_fold_ratio: float = 0.6
    min_fold_trades: int = 5

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class SplitPlan:
    train: tuple[int, int]
    validation: tuple[int, int]
    test: tuple[int, int]


def chronological_split(n: int, warmup: int, train: float = 0.6, validation: float = 0.2) -> SplitPlan:
    usable = n - warmup
    if usable < 300:
        raise ValueError(f"not enough bars after warm-up ({usable}); need >= 300")
    a = warmup
    b = a + int(usable * train)
    c = b + int(usable * validation)
    return SplitPlan(train=(a, b), validation=(b, c), test=(c, n))


def walk_forward_windows(start: int, end: int, n_folds: int = 4, train_frac: float = 0.5) -> list[tuple[int, int, int]]:
    """Anchored-start rolling windows: returns (train_start, test_start, test_end)."""
    span = end - start
    first_train = int(span * train_frac)
    step = (span - first_train) // n_folds
    out = []
    for k in range(n_folds):
        ts = start + first_train + k * step
        te = ts + step if k < n_folds - 1 else end
        out.append((start, ts, te))
    return out


def _fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.3f}"


def _grid(strategy_name: str) -> list[dict[str, Any]]:
    s = build_strategy(strategy_name)
    grid = s.param_grid
    if not grid:
        return [{}]
    keys = sorted(grid)
    return [dict(zip(keys, vals, strict=True)) for vals in itertools.product(*(grid[k] for k in keys))]


def _objective(res: BacktestResult, min_trades: int) -> float:
    st = trade_stats(res.trades)
    if st["trades"] < min_trades or st["expectancy_r"] is None:
        return -np.inf
    return float(st["expectancy_r"])


def random_entry_benchmark(
    candles: list[Candle],
    f: pd.DataFrame,
    n_trades: int,
    hold: int,
    start: int,
    end: int,
    cost_r: float,
    seed: int,
    runs: int = 200,
) -> dict[str, Any]:
    """Expectancy (R) of random-direction entries with the same count and hold, seeded.

    1R = 1.5 ATR at entry; costs approximated in R from the strategy's average cost per trade.
    """
    rng = random.Random(seed)
    if n_trades == 0 or end - start <= hold + 2:
        return {"runs": 0}
    exps = []
    for _ in range(runs):
        rs = []
        for _ in range(n_trades):
            i = rng.randrange(start, end - hold - 1)
            a = f["atr14"].iloc[i]
            if pd.isna(a) or a <= 0:
                continue
            d = rng.choice((1, -1))
            entry = candles[i + 1].open
            exit_ = candles[min(i + 1 + hold, end - 1)].close
            rs.append(d * (exit_ - entry) / (1.5 * a) - cost_r)
        if rs:
            exps.append(float(np.mean(rs)))
    arr = np.array(exps)
    return {"runs": len(arr), "mean_expectancy_r": float(arr.mean()), "p95_expectancy_r": float(np.percentile(arr, 95))}


def buy_and_hold(candles: list[Candle], start: int, end: int, cost_frac: float) -> dict[str, Any]:
    if end - start < 2:
        return {}
    first, last = candles[start].open, candles[end - 1].close
    return {"return_pct": ((last / first) - 1 - 2 * cost_frac) * 100, "note": "1 unit long, one round-trip cost"}


@dataclass
class EvaluationReport:
    strategy: str
    symbol: str
    timeframe: str
    split: dict[str, Any]
    folds: list[dict[str, Any]] = field(default_factory=list)
    chosen_params: dict[str, Any] = field(default_factory=dict)
    test_metrics: dict[str, Any] = field(default_factory=dict)
    test_breakdowns: dict[str, Any] = field(default_factory=dict)
    benchmarks: dict[str, Any] = field(default_factory=dict)
    eligible: bool = False
    eligibility_reasons: list[str] = field(default_factory=list)
    criteria: dict[str, Any] = field(default_factory=dict)
    test_result: BacktestResult | None = None
    dataset_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "test_result"}
        d["label"] = "ELIGIBLE_FOR_PAPER_ALERTS" if self.eligible else "NOT_ELIGIBLE"
        d["disclaimer"] = "Past/simulated performance does not predict future results. Eligibility is a research screen only."
        return d


def evaluate_strategy(
    candles: list[Candle],
    strategy_name: str,
    adapter: ProductAdapter,
    base_cfg: BacktestConfig,
    criteria: EligibilityCriteria | None = None,
    n_folds: int = 4,
    periods_per_year: int = 365,
) -> EvaluationReport:
    criteria = criteria or EligibilityCriteria()
    f = compute_features(candles)
    warmup = build_strategy(strategy_name).min_history if strategy_name != "ensemble" else 250
    plan = chronological_split(len(candles), warmup)
    report = EvaluationReport(
        strategy=strategy_name,
        symbol=base_cfg.symbol,
        timeframe=base_cfg.timeframe,
        split={
            "train": plan.train,
            "validation": plan.validation,
            "test": plan.test,
            "train_ts": (candles[plan.train[0]].ts, candles[plan.train[1] - 1].close_ts),
            "test_ts": (candles[plan.test[0]].ts, candles[plan.test[1] - 1].close_ts),
        },
        criteria=criteria.to_dict(),
    )
    grid = _grid(strategy_name)
    chosen: list[str] = []
    positive = 0
    counted = 0
    for tr_start, te_start, te_end in walk_forward_windows(plan.train[0], plan.validation[1], n_folds):
        best, best_score = grid[0], -np.inf
        for params in grid:
            cfg = replace(base_cfg, strategy=strategy_name, params=params, start_index=tr_start, end_index=te_start)
            res = run_backtest(candles, build_strategy(strategy_name, params), adapter, cfg, features=f)
            score = _objective(res, min_trades=10)
            if score > best_score:
                best, best_score = params, score
        cfg = replace(base_cfg, strategy=strategy_name, params=best, start_index=te_start, end_index=te_end)
        oos = run_backtest(candles, build_strategy(strategy_name, best), adapter, cfg, features=f)
        st = trade_stats(oos.trades)
        report.folds.append(
            {
                "train": (tr_start, te_start),
                "test": (te_start, te_end),
                "params": best,
                "train_objective_r": None if best_score == -np.inf else best_score,
                "oos": st,
            }
        )
        chosen.append(repr(sorted(best.items())))
        if st["trades"] >= criteria.min_fold_trades:
            counted += 1
            positive += int((st["expectancy_r"] or 0) > 0)
    most = Counter(chosen).most_common()
    final_params = grid[0] if not most else next(p for p in grid if repr(sorted(p.items())) == most[0][0])
    report.chosen_params = final_params

    cfg = replace(base_cfg, strategy=strategy_name, params=final_params, start_index=plan.test[0], end_index=plan.test[1])
    test = run_backtest(candles, build_strategy(strategy_name, final_params), adapter, cfg, features=f)
    report.test_result = test
    report.dataset_hash = test.dataset_hash
    m = compute_metrics(test, periods_per_year)
    report.test_metrics = m
    report.test_breakdowns = {k: breakdown(test.trades, k) for k in ("strategy", "symbol", "year", "regime")}
    report.test_breakdowns["timeframe"] = {base_cfg.timeframe: trade_stats(test.trades)}
    n = m["net"]["trades"]
    avg_cost_r = float(np.mean([t.costs / t.risk_amount for t in test.trades if t.risk_amount])) if n else 0.0
    avg_hold = int(np.mean([t.bars_held for t in test.trades])) if n else 10
    report.benchmarks = {
        "buy_and_hold": buy_and_hold(candles, plan.test[0], plan.test[1], base_cfg.costs.spread_bps / 1e4),
        "random_entry": random_entry_benchmark(
            candles, f, n, max(1, avg_hold), plan.test[0], plan.test[1], avg_cost_r, base_cfg.seed
        ),
        "no_trade": {"return_pct": 0.0},
    }

    net = m["net"]
    checks = [
        (n >= criteria.min_test_trades, f"Enough trades to judge: {n} (needs at least {criteria.min_test_trades})"),
        (
            (net["expectancy_r"] or -1) >= criteria.min_test_expectancy_r,
            f"Average per trade after costs: {_fmt(net['expectancy_r'])} R (needs {criteria.min_test_expectancy_r} R)",
        ),
        (
            (net["profit_factor"] or 0) >= criteria.min_test_profit_factor,
            f"Profit factor (gains ÷ losses): {_fmt(net['profit_factor'])} (needs at least {criteria.min_test_profit_factor})",
        ),
        (
            net["max_drawdown_pct"] <= criteria.max_test_drawdown_pct,
            f"Worst drop from peak: {net['max_drawdown_pct']:.1f}% (must stay under {criteria.max_test_drawdown_pct:.0f}%)",
        ),
        (
            counted > 0 and positive / counted >= criteria.min_positive_fold_ratio,
            f"Profitable in {positive} of {counted} earlier test periods (needs {criteria.min_positive_fold_ratio:.0%})",
        ),
    ]
    rb = report.benchmarks["random_entry"]
    if rb.get("runs"):
        checks.append(
            (
                (net["expectancy_r"] or -1) > rb["p95_expectancy_r"],
                f"Better than random entries (must beat {rb['p95_expectancy_r']:.3f} R, the top 5% of random results)",
            )
        )
    report.eligible = all(ok for ok, _ in checks)
    report.eligibility_reasons = [("PASS: " if ok else "FAIL: ") + msg for ok, msg in checks]
    return report
