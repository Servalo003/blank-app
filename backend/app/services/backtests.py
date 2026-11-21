"""Backtest/evaluation orchestration with full persistence for reproducibility."""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backtest.engine import BacktestConfig, run_backtest
from app.backtest.evaluation import EligibilityCriteria, evaluate_strategy
from app.backtest.metrics import breakdown, compute_metrics
from app.config import get_settings
from app.core.types import Candle, Timeframe
from app.data.provider import MarketDataProvider
from app.data.quality import normalize_candles
from app.models import BacktestRun, BacktestTrade, Instrument, StrategyEligibility
from app.products.adapter import ProductSpec, build_adapter
from app.services import audit
from app.services.scanner import persist_candles
from app.services.settings_store import load_risk_config
from app.strategies.registry import build_strategy


def _downsample(curve: list[tuple[int, float]], max_points: int = 1500) -> list[list[float]]:
    step = max(1, len(curve) // max_points)
    pts = [[t, round(e, 2)] for t, e in curve[::step]]
    if curve and pts[-1][0] != curve[-1][0]:
        pts.append([curve[-1][0], round(curve[-1][1], 2)])
    return pts


async def fetch_history(provider: MarketDataProvider, symbol: str, tf: Timeframe, bars: int) -> tuple[list[Candle], Any]:
    now = await provider.server_time()
    raw = await provider.get_candles(symbol, tf.seconds, bars + 1)
    return normalize_candles(raw, tf.seconds, now, stale_after_bars=1e9, expect_continuous=False)


def _spec(s: Session, symbol: str, overrides: dict[str, Any] | None) -> tuple[ProductSpec, int]:
    inst = s.get(Instrument, symbol)
    spec_d = dict(inst.product_spec) if inst and inst.product_spec else {}
    if overrides:
        spec_d.update({k: v for k, v in overrides.items() if k != "costs"})
        if "costs" in overrides:
            spec_d["costs"] = {**spec_d.get("costs", {}), **overrides["costs"]}
    if not spec_d:
        raise ValueError("No product spec for symbol; provide product_spec in the request")
    return ProductSpec.from_dict(spec_d), (inst.periods_per_year if inst else 365)


async def run_and_store(s: Session, provider: MarketDataProvider, req: dict[str, Any], kind: str = "backtest") -> BacktestRun:
    st = get_settings()
    tf = Timeframe(req["timeframe"])
    run = BacktestRun(
        kind=kind,
        status="RUNNING",
        symbol=req["symbol"],
        timeframe=tf.value,
        strategy=req["strategy"],
        request=req,
        provenance=provider.provenance.value,
        code_version=st.git_sha,
    )
    s.add(run)
    s.flush()
    t0 = time.time()
    try:
        candles, dq = await fetch_history(provider, req["symbol"], tf, int(req.get("bars", 3000)))
        if len(candles) < 400:
            raise ValueError(f"only {len(candles)} closed candles available; need >= 400")
        persist_candles(s, provider.name, req["symbol"], candles)
        spec, ppy = _spec(s, req["symbol"], req.get("product_spec"))
        notes: list[str] = []
        if not spec.confirmed and (spec.product_type == "linear" or req.get("research_override_unconfirmed")):
            # Research-only sizing for an unconfirmed product. R-multiples, win rate, profit factor and
            # drawdown % do not depend on contract size, so the test stays meaningful; currency amounts are
            # illustrative. Live alerts remain RISK_UNAVAILABLE until the operator confirms the spec.
            spec = replace(spec, confirmed=True, contract_size=spec.contract_size or 1.0, min_units=spec.min_units or 1e-6)
            notes.append(
                "Product details for this market are not confirmed yet, so the test assumes a contract size of 1. "
                "Results in R, win rate, profit factor and drawdown % are unaffected; currency amounts are illustrative."
            )
        adapter = build_adapter(spec)
        assert adapter is not None
        risk = load_risk_config(s)
        cfg = BacktestConfig(
            symbol=req["symbol"],
            strategy=req["strategy"],
            params=req.get("params") or {},
            timeframe=tf.value,
            initial_equity=float(req.get("initial_equity", st.paper_equity)),
            costs=spec.costs,
            risk=risk,
            seed=int(req.get("seed", 42)),
            provenance=provider.provenance.value,
        )
        run.dataset_range = {
            "first_ts": candles[0].ts,
            "last_close_ts": candles[-1].close_ts,
            "bars": len(candles),
            "data_quality": asdict(dq),
            "provider": provider.name,
        }
        if kind == "evaluation":
            rep = await asyncio.to_thread(
                evaluate_strategy, candles, req["strategy"], adapter, cfg, EligibilityCriteria(), 4, ppy
            )
            assert rep.test_result is not None
            res = rep.test_result
            run.report = rep.to_dict()
            run.metrics = rep.test_metrics
            run.label = run.report["label"]
            elig = s.scalars(
                select(StrategyEligibility).where(
                    StrategyEligibility.symbol == req["symbol"],
                    StrategyEligibility.timeframe == tf.value,
                    StrategyEligibility.strategy == req["strategy"],
                )
            ).first()
            if elig is None:
                elig = StrategyEligibility(symbol=req["symbol"], timeframe=tf.value, strategy=req["strategy"], run_id=run.id)
                s.add(elig)
            # Eligibility is per (symbol, timeframe, strategy) and records data provenance. Simulated
            # symbols only exist in simulator mode, and the risk engine keeps simulated alerts research-only.
            elig.eligible = rep.eligible
            elig.run_id, elig.params, elig.provenance = run.id, rep.chosen_params, provider.provenance.value
        else:
            strategy = build_strategy(req["strategy"], req.get("params") or None)
            start = int(req.get("start_index", strategy.min_history if req["strategy"] != "ensemble" else 250))
            cfg.start_index = start
            res = await asyncio.to_thread(run_backtest, candles, strategy, adapter, cfg)
            run.metrics = compute_metrics(res, ppy)
            run.report = {"breakdowns": {k: breakdown(res.trades, k) for k in ("strategy", "year", "regime", "direction")}}
        run.config, run.config_hash, run.dataset_hash = res.config, res.config_hash, res.dataset_hash
        run.equity = {"net": _downsample(res.equity), "gross": _downsample(res.equity_gross)}  # type: ignore[assignment]
        for t in res.trades:
            s.add(BacktestTrade(run_id=run.id, trade_no=t.trade_id, data=t.to_dict()))
        run.report["notes"] = notes
        run.status = "DONE"
        run.report["elapsed_s"] = round(time.time() - t0, 2)
    except Exception as exc:
        run.status, run.error = "FAILED", f"{type(exc).__name__}: {exc}"
    audit.record(
        s,
        f"{kind}.completed",
        "backtest",
        run.id,
        {
            "status": run.status,
            "request": req,
            "config_hash": run.config_hash,
            "dataset_hash": run.dataset_hash,
            "label": run.label,
            "error": run.error,
        },
    )
    return run
