from __future__ import annotations

from app.core.types import Candle
from app.data.quality import normalize_candles

G = 300


def c(i: int, close: float = 10.0) -> Candle:
    return Candle(i * G, 10, 11, 9, close, G)


def test_dedup_sort_and_drop_forming_bar() -> None:
    raw = [c(3), c(1), c(2), c(2, 10.5), c(4)]
    now = 4 * G + 10  # bar 4 still forming
    out, rep = normalize_candles(raw, G, now)
    assert [x.ts for x in out] == [G, 2 * G, 3 * G]
    assert out[1].close == 10.5  # last observation wins
    assert rep.n_duplicates_removed == 1 and rep.n_unclosed_dropped == 1


def test_invalid_ohlc_dropped() -> None:
    bad = Candle(5 * G, 10, 9, 11, 10, G)  # high < low
    misaligned = Candle(5 * G + 7, 10, 11, 9, 10, G)
    out, rep = normalize_candles([c(1), bad, misaligned, c(2)], G, 3 * G)
    assert rep.n_invalid_ohlc == 2 and len(out) == 2


def test_gaps_and_staleness() -> None:
    raw = [c(i) for i in range(10)] + [c(i) for i in range(20, 30)]
    _out, rep = normalize_candles(raw, G, 30 * G)
    assert rep.n_gaps == 1 and rep.largest_gap_bars == 10
    assert not rep.ok  # recent large gap on a continuous market
    _, rep2 = normalize_candles(raw, G, 30 * G, expect_continuous=False)
    assert rep2.ok
    _, stale = normalize_candles([c(i) for i in range(10)], G, 50 * G)
    assert stale.is_stale and not stale.ok
