"""Deriv MT5 CFD product specs, from Deriv's published trading specifications.

The bundled table (``deriv_mt5_specs.json``) was extracted from
https://deriv.com/trading-specifications. Instruments are matched to Deriv API
symbols by their display name (``underlying_symbol_name``), normalised. No
match means no suggestion; nothing is guessed.

Suggestions are always returned with ``confirmed: false``. The operator applies
them with one tap, after checking them against the Specification window in
their MT5 terminal, because values can change and can differ by account type.

Modelling notes:
* The table gives no volume step, so the minimum size is used as the step.
  This only ever rounds a position down, never up.
* MT5 synthetic indices charge no commission; costs come from the spread.
  ``target-spread-in`` (a percentage) and ``min-spread`` (price units) are both
  kept, and the cost model uses whichever is larger.
* Swap (overnight financing) is shown but NOT modelled in backtests.
* MT5 prices are assumed to follow the same index as Deriv's public market-data
  feed, which this app uses for charts, signals and backtests. Deriv does not
  state this explicitly, so verify that the prices match in your terminal.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_FILE = Path(__file__).with_name("deriv_mt5_specs.json")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


@lru_cache(maxsize=1)
def _table() -> dict[str, Any]:
    data = json.loads(_FILE.read_text())
    data["by_name"] = {_norm(r["name"]): r for r in data["instruments"]}
    return data


def _num(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        return float(str(v).replace("%", "").replace(",", "").strip())
    except ValueError:
        return None


def find(display_name: str) -> dict[str, Any] | None:
    return _table()["by_name"].get(_norm(display_name))


def source() -> dict[str, str]:
    t = _table()
    return {"source": t["source"], "retrieved_at": t["retrieved_at"], "note": t["note"]}


def suggest_spec(display_name: str) -> dict[str, Any] | None:
    """Build a product spec suggestion (unconfirmed) for a Deriv API display name."""
    row = find(display_name)
    if row is None:
        return None
    min_size = _num(row.get("min-size"))
    contract = _num(row.get("contract-size"))
    if not min_size or not contract:
        return None
    target_pct = _num(row.get("target-spread-in")) or 0.0
    return {
        "product_type": "linear",
        "platform": "mt5",
        "mt5_name": row["name"],
        "contract_size": contract,
        "min_units": min_size,
        "unit_step": min_size,
        "max_units": _num(row.get("vol-limit")),
        "confirmed": False,
        "swap_long_pts": _num(row.get("swap-long-pts")),
        "swap_short_pts": _num(row.get("swap-short-pts")),
        "max_leverage": row.get("max-effective-leverage"),
        "trading_hours": row.get("trading-hours-gmt"),
        "costs": {
            "spread": _num(row.get("min-spread")) or 0.0,
            "spread_bps": target_pct * 100,
            "slippage_atr": 0.05,
            "commission_bps": 0.0,
            "source": f"Deriv MT5 trading specifications ({_table()['retrieved_at']}); swap not modelled",
        },
    }
