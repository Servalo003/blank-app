"""Instruments, MT5 specs, paper performance, calendar CSV import and batch evaluation."""

from __future__ import annotations

import asyncio
import csv
import io
import time
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.types import Timeframe
from app.data.provider import MarketDataProvider
from app.db import session_scope
from app.models import AppSetting, EconomicEvent, Instrument, SignalRow, WatchlistItem
from app.products import mt5
from app.services import audit
from app.strategies.registry import ALL_STRATEGY_NAMES

log = structlog.get_logger(__name__)

# ------------------------------------------------------------------ instruments


async def ensure_instrument(s: Session, provider: MarketDataProvider, symbol: str) -> Instrument:
    inst = s.get(Instrument, symbol)
    if inst is not None:
        return inst
    info = next((x for x in await provider.list_symbols() if x.symbol == symbol), None)
    if info is None:
        raise ValueError(f"{symbol} is not an active symbol on {provider.name}")
    inst = Instrument(
        symbol=symbol,
        display_name=info.display_name,
        market=info.market,
        submarket=info.submarket,
        provider=provider.name,
        product_spec=mt5.suggest_spec(info.display_name),
        periods_per_year=365 if info.market in ("synthetic_index", "simulated") else 252,
    )
    s.add(inst)
    s.flush()
    audit.record(
        s, "instrument.added", "instrument", symbol, {"market": info.market, "mt5_suggestion": inst.product_spec is not None}
    )
    return inst


def apply_mt5(s: Session, symbol: str, actor: str) -> dict[str, Any]:
    inst = s.get(Instrument, symbol)
    if inst is None:
        raise KeyError(symbol)
    spec = mt5.suggest_spec(inst.display_name)
    if spec is None:
        raise ValueError(f"No Deriv MT5 specification found for '{inst.display_name}'")
    spec["confirmed"] = True
    spec["confirmed_at"] = datetime.now(UTC).isoformat()
    inst.product_spec = spec
    audit.record(s, "instrument.mt5_applied", "instrument", symbol, {"spec": spec}, actor)
    return spec


# ------------------------------------------------------------------ paper performance


def performance(s: Session, days: int = 7) -> dict[str, Any]:
    since = int(time.time()) - days * 86400
    rows = s.scalars(select(SignalRow).where(SignalRow.closed_at >= since, SignalRow.outcome_r.is_not(None))).all()

    def agg(rs: list[SignalRow]) -> dict[str, Any]:
        rr = [r.outcome_r or 0.0 for r in rs]
        return {
            "closed": len(rr),
            "wins": sum(1 for x in rr if x > 0),
            "total_r": round(sum(rr), 3),
            "avg_r": round(sum(rr) / len(rr), 3) if rr else None,
        }

    by_strategy: dict[str, list[SignalRow]] = {}
    for r in rows:
        by_strategy.setdefault(r.strategy, []).append(r)
    return {
        "days": days,
        "overall": agg(list(rows)),
        "paper_alerts": agg([r for r in rows if r.alert_level == "ACTIONABLE"]),
        "by_strategy": {k: agg(v) for k, v in sorted(by_strategy.items())},
        "note": "Paper outcomes at the signal's reference price, stop and target. Not real fills.",
    }


# ------------------------------------------------------------------ calendar CSV

CSV_COLUMNS = ["currency", "title", "impact", "scheduled_at", "known_at", "source"]


def _ts(v: str) -> int:
    v = v.strip()
    if v.isdigit():
        return int(v)
    return int(datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(UTC).timestamp())


def import_calendar_csv(s: Session, text: str, actor: str) -> dict[str, Any]:
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in CSV_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"CSV is missing columns: {', '.join(missing)}. Required: {', '.join(CSV_COLUMNS)}")
    n = 0
    errors: list[str] = []
    for i, row in enumerate(reader, start=2):
        try:
            impact = row["impact"].strip().lower()
            if impact not in ("low", "medium", "high"):
                raise ValueError("impact must be low, medium or high")
            cur = row["currency"].strip().upper()
            if len(cur) != 3 or not cur.isalpha():
                raise ValueError("currency must be a 3-letter code")
            s.add(
                EconomicEvent(
                    currency=cur,
                    title=row["title"].strip()[:200],
                    impact=impact,
                    scheduled_at=_ts(row["scheduled_at"]),
                    known_at=_ts(row["known_at"]),
                    source=row["source"].strip()[:120],
                    actual=(row.get("actual") or None),
                    forecast=(row.get("forecast") or None),
                    previous=(row.get("previous") or None),
                )
            )
            n += 1
        except (ValueError, KeyError) as exc:
            errors.append(f"line {i}: {exc}")
        if len(errors) > 20:
            break
    if errors:
        raise ValueError("; ".join(errors[:5]) + (" …" if len(errors) > 5 else ""))
    audit.record(s, "events.imported_csv", "economic_events", str(n), {"count": n}, actor)
    return {"imported": n}


# ------------------------------------------------------------------ batch evaluation

BATCH_KEY = "batch_eval"
SIX_MONTHS_BARS = {"5m": 20000, "15m": 17280, "30m": 8640, "1h": 4320, "4h": 1080, "1d": 400}
_batch_task: asyncio.Task[None] | None = None


def _set(s: Session, value: dict[str, Any]) -> None:
    row = s.get(AppSetting, BATCH_KEY)
    if row is None:
        s.add(AppSetting(key=BATCH_KEY, value=value))
    else:
        row.value = value


def batch_status() -> dict[str, Any]:
    with session_scope() as s:
        row = s.get(AppSetting, BATCH_KEY)
        st = dict(row.value) if row else {"state": "idle"}
    if st.get("state") == "running" and (_batch_task is None or _batch_task.done()):
        st["state"] = "interrupted"
    return st


async def _run_batch(provider: MarketDataProvider, jobs: list[dict[str, Any]]) -> None:
    from app.services.backtests import run_and_store

    state: dict[str, Any] = {"state": "running", "started_at": int(time.time()), "total": len(jobs), "done": 0, "results": []}
    with session_scope() as s:
        _set(s, state)
    for job in jobs:
        state["current"] = f"{job['strategy']} on {job['symbol']} {job['timeframe']}"
        with session_scope() as s:
            _set(s, dict(state))
        try:
            with session_scope() as s:
                run = await run_and_store(s, provider, {**job, "kind": "evaluation"}, "evaluation")
                res = {
                    "id": run.id,
                    **job,
                    "status": run.status,
                    "label": run.label,
                    "error": run.error,
                    "summary": {
                        k: (run.metrics or {}).get("net", {}).get(k)
                        for k in ("trades", "expectancy_r", "profit_factor", "max_drawdown_pct", "net_return_pct")
                    },
                }
        except Exception as exc:  # keep going with the rest
            log.exception("batch.job_failed", job=job)
            res = {**job, "status": "FAILED", "error": f"{type(exc).__name__}: {exc}"}
        state["results"].append(res)
        state["done"] += 1
        with session_scope() as s:
            _set(s, dict(state))
    state["state"] = "finished"
    state["finished_at"] = int(time.time())
    state.pop("current", None)
    with session_scope() as s:
        _set(s, dict(state))
        audit.record(
            s,
            "batch_evaluation.finished",
            "batch",
            str(state["started_at"]),
            {"total": len(jobs), "passed": sum(1 for r in state["results"] if r.get("label") == "ELIGIBLE_FOR_PAPER_ALERTS")},
        )


def start_batch(provider: MarketDataProvider, strategies: list[str] | None = None) -> dict[str, Any]:
    global _batch_task
    if _batch_task is not None and not _batch_task.done():
        raise RuntimeError("a batch of tests is already running")
    strategies = strategies or ALL_STRATEGY_NAMES
    jobs: list[dict[str, Any]] = []
    with session_scope() as s:
        seen: set[tuple[str, str]] = set()
        for it in s.scalars(select(WatchlistItem).where(WatchlistItem.enabled.is_(True))):
            if (it.symbol, it.timeframe) in seen:
                continue
            seen.add((it.symbol, it.timeframe))
            Timeframe(it.timeframe)
            for st in strategies:
                jobs.append(
                    {
                        "symbol": it.symbol,
                        "timeframe": it.timeframe,
                        "strategy": st,
                        "bars": SIX_MONTHS_BARS.get(it.timeframe, 5000),
                    }
                )
    if not jobs:
        raise ValueError("no enabled markets on the watchlist")
    _batch_task = asyncio.create_task(_run_batch(provider, jobs), name="batch-eval")
    return {"state": "running", "total": len(jobs)}
