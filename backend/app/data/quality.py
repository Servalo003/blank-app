"""Data normalisation and quality checks.

* UTC epoch normalisation and sort
* de-duplication (last observation wins for the same open time)
* OHLC sanity validation
* removal of the still-forming candle (signals use CLOSED candles only)
* gap detection and stale-data detection
"""

from __future__ import annotations

import math

from app.core.types import Candle, DataQualityReport


def normalize_candles(
    raw: list[Candle],
    granularity: int,
    now: int,
    *,
    stale_after_bars: float = 2.0,
    recent_window: int = 50,
    max_recent_gap_bars: int = 3,
    expect_continuous: bool = True,
    min_bars: int = 0,
) -> tuple[list[Candle], DataQualityReport]:
    issues: list[str] = []
    by_ts: dict[int, Candle] = {}
    dups = 0
    invalid = 0
    for c in raw:
        ts = int(c.ts)
        if ts % granularity != 0:
            invalid += 1
            continue
        vals = (c.open, c.high, c.low, c.close)
        if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in vals):
            invalid += 1
            continue
        if c.high < max(c.open, c.close) or c.low > min(c.open, c.close) or c.high < c.low:
            invalid += 1
            continue
        if ts in by_ts:
            dups += 1
        by_ts[ts] = Candle(ts, float(c.open), float(c.high), float(c.low), float(c.close), granularity)
    ordered = [by_ts[k] for k in sorted(by_ts)]
    closed = [c for c in ordered if c.close_ts <= now]
    unclosed = len(ordered) - len(closed)

    n_gaps = 0
    largest = 0
    recent_gap = 0
    for idx in range(1, len(closed)):
        missing = (closed[idx].ts - closed[idx - 1].ts) // granularity - 1
        if missing > 0:
            n_gaps += 1
            largest = max(largest, missing)
            if idx >= len(closed) - recent_window:
                recent_gap = max(recent_gap, missing)

    last_close = closed[-1].close_ts if closed else None
    stale_by = 0 if last_close is None else max(0, now - last_close)
    # A closed candle is expected at most one granularity after the last close.
    is_stale = last_close is None or stale_by > stale_after_bars * granularity

    if dups:
        issues.append(f"{dups} duplicate candles removed")
    if invalid:
        issues.append(f"{invalid} invalid candles dropped")
    if n_gaps:
        issues.append(f"{n_gaps} gaps (largest {largest} bars)")
    if is_stale:
        issues.append(f"data stale by {stale_by}s (> {stale_after_bars} bars)")
    if len(closed) < min_bars:
        issues.append(f"insufficient history: {len(closed)} < {min_bars} bars")

    ok = (
        not is_stale
        and len(closed) >= min_bars
        and (invalid <= max(1, len(raw) // 100))
        and (not expect_continuous or recent_gap <= max_recent_gap_bars)
    )
    if expect_continuous and recent_gap > max_recent_gap_bars:
        issues.append(f"recent gap of {recent_gap} bars within last {recent_window} bars")
    return closed, DataQualityReport(
        n_candles=len(closed),
        n_duplicates_removed=dups,
        n_gaps=n_gaps,
        largest_gap_bars=largest,
        n_invalid_ohlc=invalid,
        n_unclosed_dropped=unclosed,
        last_close_ts=last_close,
        is_stale=is_stale,
        stale_by_seconds=stale_by,
        ok=ok,
        issues=issues,
    )
