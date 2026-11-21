"""Point-in-time fundamentals / event analysis.

* Derived/synthetic indices (and simulated instruments) have NO conventional
  economic fundamentals -> status ``NOT_APPLICABLE``. Nothing is invented.
* For FX-style symbols (``frxEURUSD``) we look up user-imported calendar events
  for the two currencies, using only rows whose ``known_at <= as_of`` (so later
  revisions are never visible to earlier decisions).
* No calendar data shipped with the app: without imported data the status is
  ``NO_DATA`` (not a fabricated "all clear").
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EconomicEvent

NOT_APPLICABLE_MARKETS = {"synthetic_index", "simulated"}
_FX = re.compile(r"^frx([A-Z]{3})([A-Z]{3})$")


def currencies_for(symbol: str) -> list[str]:
    m = _FX.match(symbol)
    return [m.group(1), m.group(2)] if m else []


def assess(
    s: Session, symbol: str, market: str, as_of: int, window_minutes: int = 30, block_impact: str = "high"
) -> dict[str, Any]:
    if market in NOT_APPLICABLE_MARKETS:
        return {"status": "NOT_APPLICABLE", "reason": "Derived/synthetic instrument: no economic fundamentals", "block": None}
    ccys = currencies_for(symbol)
    if not ccys:
        return {"status": "NOT_SUPPORTED", "reason": f"No fundamentals mapping for market '{market}'", "block": None}
    any_rows = s.scalars(select(EconomicEvent.id).where(EconomicEvent.currency.in_(ccys)).limit(1)).first()
    if any_rows is None:
        return {"status": "NO_DATA", "reason": f"No calendar imported for {', '.join(ccys)}", "block": None, "currencies": ccys}
    lo, hi = as_of - window_minutes * 60, as_of + window_minutes * 60
    rows = s.scalars(
        select(EconomicEvent)
        .where(
            EconomicEvent.currency.in_(ccys),
            EconomicEvent.known_at <= as_of,
            EconomicEvent.scheduled_at >= lo,
            EconomicEvent.scheduled_at <= hi,
        )
        .order_by(EconomicEvent.scheduled_at, EconomicEvent.known_at.desc())
    ).all()
    # keep the latest *known* revision per (currency, title, scheduled_at)
    latest: dict[tuple[str, str, int], EconomicEvent] = {}
    for r in rows:
        latest.setdefault((r.currency, r.title, r.scheduled_at), r)
    events = [
        {"currency": r.currency, "title": r.title, "impact": r.impact, "scheduled_at": r.scheduled_at, "source": r.source}
        for r in latest.values()
    ]
    blocking = [e for e in events if e["impact"] == block_impact]
    block = (
        f"High-impact event within ±{window_minutes}m: " + ", ".join(f"{e['currency']} {e['title']}" for e in blocking)
        if blocking
        else None
    )
    return {"status": "OK", "currencies": ccys, "events_in_window": events, "block": block}
