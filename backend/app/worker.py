"""Background worker: scans enabled watchlist items shortly after each bar closes.

Run with ``python -m app.worker``. Uses a Redis lock (when configured) so only
one worker scans at a time; writes a heartbeat the API exposes on /api/status.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import signal
import time
from typing import Any

import structlog
from sqlalchemy import select

from app.config import get_settings
from app.core.types import Timeframe
from app.data.provider import MarketDataProvider
from app.db import Base, get_engine, session_scope
from app.logging import configure_logging
from app.models import WatchlistItem
from app.notifications.dispatcher import Dispatcher, TelegramChannel, WebPushChannel
from app.services.runtime import Coordinator, get_provider
from app.services.scanner import scan_item
from app.services.seed import seed

log = structlog.get_logger(__name__)


def build_dispatcher() -> Dispatcher:
    st = get_settings()
    return Dispatcher([TelegramChannel(st), WebPushChannel(st, session_scope)])


def next_close(now: float, granularity: int) -> int:
    return int(now - now % granularity + granularity)


async def scan_all(provider: MarketDataProvider, timeframes: set[str] | None = None) -> list[dict[str, Any]]:
    dispatcher = build_dispatcher()
    results = []
    with session_scope() as s:
        items = s.scalars(select(WatchlistItem).where(WatchlistItem.enabled.is_(True))).all()
        seen: set[tuple[str, str]] = set()
        for item in items:
            key = (item.symbol, item.timeframe)
            if key in seen or (timeframes and item.timeframe not in timeframes):
                continue
            seen.add(key)
            try:
                with s.begin_nested():
                    results.append(await scan_item(s, provider, item, dispatcher))
            except Exception as exc:  # one bad symbol must not stop the scan
                log.exception("scan.item_failed", symbol=item.symbol, tf=item.timeframe, error=str(exc))
                results.append({"symbol": item.symbol, "timeframe": item.timeframe, "error": type(exc).__name__})
    return results


async def run_forever() -> None:
    st = get_settings()
    configure_logging(st.log_level)
    if st.app_env != "production":
        Base.metadata.create_all(get_engine())
    with session_scope() as s:
        seed(s)
    provider = get_provider()
    coord = Coordinator(st.redis_url)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    log.info("worker.started", provider=provider.name, provenance=provider.provenance.value)
    while not stop.is_set():
        with session_scope() as s:
            tfs = {i.timeframe for i in s.scalars(select(WatchlistItem).where(WatchlistItem.enabled.is_(True)))}
        now = time.time()
        if not tfs:
            due_at, due_tfs = now + 60, set()
        else:
            closes = {tf: next_close(now, Timeframe(tf).seconds) for tf in tfs}
            due_at = min(closes.values())
            due_tfs = {tf for tf, c in closes.items() if c == due_at}
        await coord.set("worker:heartbeat", json.dumps({"ts": int(now), "next_scan_at": int(due_at + st.scan_delay_seconds)}))
        wait = max(0.0, due_at + st.scan_delay_seconds - time.time())
        try:
            await asyncio.wait_for(stop.wait(), timeout=min(wait, 30))
            break
        except TimeoutError:
            pass
        if time.time() < due_at + st.scan_delay_seconds or not due_tfs:
            continue  # heartbeat tick only
        if not await coord.acquire("scan", ttl=300):
            log.info("worker.scan_skipped_locked")
            continue
        try:
            t0 = time.time()
            res = await scan_all(provider, due_tfs)
            n_sig = sum(len(r.get("signals", [])) for r in res)
            await coord.set(
                "worker:last_scan",
                json.dumps(
                    {
                        "ts": int(time.time()),
                        "timeframes": sorted(due_tfs),
                        "items": len(res),
                        "signals": n_sig,
                        "duration_s": round(time.time() - t0, 2),
                    }
                ),
                ttl=86400,
            )
            log.info("worker.scan_done", timeframes=sorted(due_tfs), items=len(res), signals=n_sig)
        finally:
            await coord.release("scan")
    await provider.close()
    log.info("worker.stopped")


if __name__ == "__main__":
    asyncio.run(run_forever())
