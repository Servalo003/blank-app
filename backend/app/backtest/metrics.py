"""Performance metrics.

Assumptions (also returned in ``assumptions`` so the UI can disclose them):
* Sharpe/Sortino use **daily** (UTC) returns of the mark-to-market equity
  curve, risk-free rate 0, annualised with ``periods_per_year``
  (365 for 24/7 synthetic indices, 252 otherwise). Sortino uses downside
  deviation relative to 0.
* Exposure = fraction of bars with an open position.
* Turnover = total traded notional / average equity.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd

from app.backtest.engine import BacktestResult, Trade


def _max_drawdown(eq: np.ndarray, ts: np.ndarray) -> tuple[float, float, int]:
    """Return (max dd fraction, max dd amount, longest dd duration in seconds)."""
    if len(eq) == 0:
        return 0.0, 0.0, 0
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / np.where(peak == 0, 1, peak)
    max_dd = float(-dd.min())
    max_dd_amt = float((peak - eq).max())
    longest = 0
    start = None
    for i in range(len(eq)):
        if eq[i] < peak[i]:
            if start is None:
                start = ts[i - 1] if i > 0 else ts[i]
            longest = max(longest, int(ts[i] - start))
        else:
            start = None
    return max_dd, max_dd_amt, longest


def _daily_returns(curve: list[tuple[int, float]], initial: float) -> pd.Series:
    if not curve:
        return pd.Series(dtype=float)
    s = pd.Series([e for _, e in curve], index=pd.to_datetime([t for t, _ in curve], unit="s", utc=True))
    daily = s.resample("1D").last().dropna()
    prev = daily.shift(1)
    prev.iloc[0] = initial
    return daily / prev - 1.0


def trade_stats(trades: list[Trade], use_gross: bool = False) -> dict[str, Any]:
    pnls = np.array([t.gross_pnl if use_gross else t.net_pnl for t in trades], dtype=float)
    rs = np.array([t.r_gross if use_gross else t.r_net for t in trades], dtype=float)
    n = len(pnls)
    if n == 0:
        return {
            "trades": 0,
            "net_pnl": 0.0,
            "expectancy": None,
            "expectancy_r": None,
            "win_rate": None,
            "avg_win": None,
            "avg_loss": None,
            "payoff_ratio": None,
            "profit_factor": None,
            "max_consecutive_losses": 0,
            "total_r": 0.0,
        }
    wins, losses = pnls[pnls > 0], pnls[pnls <= 0]
    gross_win, gross_loss = wins.sum(), -losses.sum()
    streak = best = 0
    for p in pnls:
        streak = streak + 1 if p <= 0 else 0
        best = max(best, streak)
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    return {
        "trades": n,
        "net_pnl": float(pnls.sum()),
        "expectancy": float(pnls.mean()),
        "expectancy_r": float(rs.mean()),
        "total_r": float(rs.sum()),
        "win_rate": float(len(wins) / n),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": float(avg_win / -avg_loss) if avg_loss < 0 else None,
        "profit_factor": float(gross_win / gross_loss) if gross_loss > 0 else None,
        "max_consecutive_losses": int(best),
    }


def compute_metrics(res: BacktestResult, periods_per_year: int = 365) -> dict[str, Any]:
    initial = float(res.config["initial_equity"])
    out: dict[str, Any] = {}
    for label, curve, gross in (("net", res.equity, False), ("gross", res.equity_gross, True)):
        eq = np.array([e for _, e in curve], dtype=float)
        ts = np.array([t for t, _ in curve], dtype=np.int64)
        mdd, mdd_amt, dd_dur = _max_drawdown(np.concatenate([[initial], eq]), np.concatenate([[res.start_ts], ts]))
        dr = _daily_returns(curve, initial)
        sharpe = sortino = None
        if len(dr) >= 10 and dr.std(ddof=1) > 0:
            sharpe = float(dr.mean() / dr.std(ddof=1) * math.sqrt(periods_per_year))
            downside = np.sqrt(np.mean(np.minimum(dr.to_numpy(), 0.0) ** 2))
            sortino = float(dr.mean() / downside * math.sqrt(periods_per_year)) if downside > 0 else None
        final = float(eq[-1]) if len(eq) else initial
        stats = trade_stats(res.trades, use_gross=gross)
        out[label] = {
            **stats,
            "final_equity": final,
            "net_return_pct": (final / initial - 1) * 100,
            "max_drawdown_pct": mdd * 100,
            "max_drawdown_amount": mdd_amt,
            "max_drawdown_duration_hours": dd_dur / 3600,
            "sharpe": sharpe,
            "sortino": sortino,
            "daily_return_obs": len(dr),
        }
    in_pos = sum(t.bars_held for t in res.trades)
    notional = sum(abs(t.entry_price * t.units) + abs((t.exit_price or 0) * t.units) for t in res.trades)
    avg_eq = float(np.mean([e for _, e in res.equity])) if res.equity else initial
    out["exposure_pct"] = in_pos / res.bars * 100 if res.bars else 0.0
    out["turnover"] = notional / avg_eq if avg_eq else 0.0
    out["total_costs"] = float(sum(t.costs for t in res.trades))
    out["ambiguous_bar_trades"] = int(sum(t.ambiguous_bar for t in res.trades))
    out["rejected_signals"] = res.rejected_signals
    out["risk_unavailable_signals"] = res.risk_unavailable_signals
    out["assumptions"] = {
        "sharpe_sortino": f"daily UTC returns of MTM equity, rf=0, annualised x sqrt({periods_per_year})",
        "same_bar_stop_and_target": "stop assumed first (conservative)",
        "execution": "next bar open after signal-bar close, + half spread + ATR-scaled slippage",
        "gross": "same trades at mid prices without spread/slippage/commission",
        "excluded": "overnight financing/swap, partial fills, liquidity limits",
    }
    return out


def breakdown(trades: list[Trade], key: str) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        if key == "year":
            k = str(datetime.fromtimestamp(t.entry_ts, tz=UTC).year)
        elif key == "regime":
            k = f"{t.trend_regime}/{t.vol_regime}"
        else:
            k = str(getattr(t, key))
        groups[k].append(t)
    return {k: trade_stats(v) for k, v in sorted(groups.items())}
