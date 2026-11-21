"""Idempotent seed data: instruments with product specs and a default watchlist.

Simulated instruments get a confirmed linear research spec so the full
pipeline (including risk sizing) is demonstrable offline - their alerts are
still RESEARCH_ONLY because the data is simulated.

Deriv instruments are seeded with ``confirmed: false`` specs: their contract
size / minimum volume / commission must be verified by the operator for the
product they actually use. Until then the risk engine returns RISK_UNAVAILABLE.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Instrument, Watchlist, WatchlistItem
from app.services import audit

SIM_SPEC = {
    "product_type": "linear",
    "contract_size": 1.0,
    "min_units": 0.001,
    "unit_step": 0.001,
    "confirmed": True,
    "costs": {"spread_bps": 2.0, "slippage_atr": 0.05, "commission_bps": 0.0, "source": "assumption (simulated)"},
}
DERIV_UNCONFIRMED_SPEC = {
    "product_type": "linear",
    "contract_size": None,
    "min_units": None,
    "confirmed": False,
    "costs": {
        "spread_bps": 3.0,
        "slippage_atr": 0.05,
        "commission_bps": 0.0,
        "source": "placeholder - verify spread/commission for your product",
    },
}

DEFAULT_STRATEGIES = {
    "trend_pullback": {"enabled": True},
    "donchian_breakout": {"enabled": True},
    "mean_reversion": {"enabled": True},
    "momentum_continuation": {"enabled": True},
}


def seed(s: Session) -> None:
    st = get_settings()
    if st.data_provider == "simulator":
        instruments = [
            ("SIM_TREND", "Simulated Trending Index (SIMULATED)"),
            ("SIM_RANGE", "Simulated Ranging Index (SIMULATED)"),
            ("SIM_MIXED", "Simulated Mixed-Regime Index (SIMULATED)"),
        ]
        for sym, name in instruments:
            if s.get(Instrument, sym) is None:
                s.add(
                    Instrument(
                        symbol=sym,
                        display_name=name,
                        market="simulated",
                        submarket="simulated",
                        provider="simulator",
                        product_spec=SIM_SPEC,
                        periods_per_year=365,
                    )
                )
        items = [("SIM_TREND", "15m"), ("SIM_MIXED", "15m"), ("SIM_RANGE", "1h")]
        wl_name = "Simulated demo"
    else:
        instruments = [
            ("R_100", "Volatility 100 Index"),
            ("R_50", "Volatility 50 Index"),
            ("1HZ100V", "Volatility 100 (1s) Index"),
        ]
        for sym, name in instruments:
            if s.get(Instrument, sym) is None:
                s.add(
                    Instrument(
                        symbol=sym,
                        display_name=name,
                        market="synthetic_index",
                        submarket="random_index",
                        provider="deriv",
                        product_spec=DERIV_UNCONFIRMED_SPEC,
                        periods_per_year=365,
                    )
                )
        items = [("R_100", "15m"), ("R_50", "1h"), ("1HZ100V", "15m")]
        wl_name = "Deriv synthetics"
    s.flush()
    if s.scalars(select(Watchlist).where(Watchlist.name == wl_name)).first() is None:
        wl = Watchlist(name=wl_name)
        s.add(wl)
        s.flush()
        for sym, tf in items:
            s.add(WatchlistItem(watchlist_id=wl.id, symbol=sym, timeframe=tf, strategies=DEFAULT_STRATEGIES, enabled=True))
        audit.record(s, "watchlist.seeded", "watchlist", str(wl.id), {"name": wl_name, "items": items})
