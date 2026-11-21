"""Resilient, read-only Deriv WebSocket client.

* one shared connection, requests multiplexed and matched by ``req_id``
* keep-alive ``ping`` every 30 s; silent disconnect detection
* reconnect with capped exponential backoff + jitter; tick subscriptions are
  re-established after reconnect
* client-side sliding-window rate limiter below the documented budgets
* refuses any call not in ``READ_ONLY_CALLS`` (no trading endpoints, ever)
* never logs credentials; the public endpoint needs none
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import structlog
import websockets

from app.data.deriv.schema import READ_ONLY_CALLS, error_of, parse_tick

log = structlog.get_logger(__name__)


class DerivAPIError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class RateLimiter:
    """Sliding windows: per-minute and per-hour budgets."""

    def __init__(self, per_minute: int, per_hour: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_minute, self.per_hour = per_minute, per_hour
        self._clock = clock
        self._m: deque[float] = deque()
        self._h: deque[float] = deque()
        self._lock = asyncio.Lock()

    def _prune(self, now: float) -> None:
        while self._m and now - self._m[0] >= 60:
            self._m.popleft()
        while self._h and now - self._h[0] >= 3600:
            self._h.popleft()

    def wait_time(self) -> float:
        now = self._clock()
        self._prune(now)
        w = 0.0
        if len(self._m) >= self.per_minute:
            w = max(w, 60 - (now - self._m[0]))
        if len(self._h) >= self.per_hour:
            w = max(w, 3600 - (now - self._h[0]))
        return w

    async def acquire(self) -> None:
        async with self._lock:
            while (w := self.wait_time()) > 0:
                await asyncio.sleep(min(w, 5))
            now = self._clock()
            self._m.append(now)
            self._h.append(now)


def backoff_delays(base: float = 1.0, cap: float = 60.0, rng: random.Random | None = None):  # type: ignore[no-untyped-def]
    r = rng or random.Random()
    attempt = 0
    while True:
        d = min(cap, base * (2**attempt))
        yield d + r.uniform(0, d * 0.25)
        attempt += 1


class DerivWSClient:
    def __init__(
        self,
        url: str,
        per_minute: int = 180,
        per_hour: int = 12_000,
        request_timeout: float = 20.0,
        ping_interval: float = 30.0,
    ) -> None:
        self.url = url
        self.limiter = RateLimiter(per_minute, per_hour)
        self.request_timeout = request_timeout
        self.ping_interval = ping_interval
        self._ws: Any = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._req = 0
        self._connected = asyncio.Event()
        self._closing = False
        self._runner: asyncio.Task[None] | None = None
        self._tick_subs: set[str] = set()
        self.last_ticks: dict[str, dict[str, Any]] = {}
        self.last_message_at: float | None = None
        self.reconnects = 0
        self.last_error: str | None = None

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if self._runner is None:
            self._closing = False
            self._runner = asyncio.create_task(self._run(), name="deriv-ws")

    async def close(self) -> None:
        self._closing = True
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
        if self._runner:
            self._runner.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._runner
        self._runner = None

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    async def _run(self) -> None:
        delays = backoff_delays()
        while not self._closing:
            try:
                async with websockets.connect(self.url, open_timeout=15, ping_interval=None, max_size=2**23) as ws:
                    self._ws = ws
                    self._connected.set()
                    delays = backoff_delays()
                    log.info("deriv.connected", url=self.url)
                    reader = asyncio.create_task(self._reader(ws))
                    keeper = asyncio.create_task(self._keepalive())
                    resubs = [asyncio.create_task(self._subscribe_ticks_now(sym)) for sym in list(self._tick_subs)]
                    _done, _ = await asyncio.wait({reader, keeper}, return_when=asyncio.FIRST_COMPLETED)
                    for t in (reader, keeper, *resubs):
                        t.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # network errors -> reconnect
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.warning("deriv.connection_error", error=self.last_error)
            finally:
                self._connected.clear()
                self._ws = None
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(ConnectionError("deriv connection lost"))
                self._pending.clear()
            if self._closing:
                break
            self.reconnects += 1
            d = next(delays)
            log.info("deriv.reconnecting", in_seconds=round(d, 2), attempt=self.reconnects)
            await asyncio.sleep(d)

    async def _reader(self, ws: Any) -> None:
        async for raw in ws:
            self.last_message_at = time.time()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("deriv.bad_json")
                continue
            tick = parse_tick(msg)
            if tick and tick.get("symbol"):
                self.last_ticks[tick["symbol"]] = {**tick, "received_at": self.last_message_at}
            rid = msg.get("req_id")
            if isinstance(rid, int) and rid in self._pending:
                fut = self._pending.pop(rid)
                if not fut.done():
                    fut.set_result(msg)

    async def _keepalive(self) -> None:
        while True:
            await asyncio.sleep(self.ping_interval)
            try:
                await self.call({"ping": 1}, timeout=10, count_budget=False)
            except Exception as exc:
                log.warning("deriv.ping_failed", error=str(exc))
                if self._ws is not None:
                    await self._ws.close()
                return

    # ------------------------------------------------------------------ requests
    async def call(self, payload: dict[str, Any], timeout: float | None = None, count_budget: bool = True) -> dict[str, Any]:
        method = next(iter(payload))
        if method not in READ_ONLY_CALLS:
            raise PermissionError(f"Deriv call {method!r} is not allowed (read-only client)")
        await self.start()
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=timeout or self.request_timeout)
        except TimeoutError as exc:
            raise ConnectionError(f"deriv not connected: {self.last_error}") from exc
        if count_budget:
            await self.limiter.acquire()
        self._req += 1
        rid = self._req
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._ws.send(json.dumps({**payload, "req_id": rid}))
        try:
            msg = await asyncio.wait_for(fut, timeout=timeout or self.request_timeout)
        finally:
            self._pending.pop(rid, None)
        err = error_of(msg)
        if err:
            raise DerivAPIError(*err)
        return msg

    async def call_with_retry(self, payload: dict[str, Any], attempts: int = 4) -> dict[str, Any]:
        delays = backoff_delays(base=1.0, cap=20.0)
        last: Exception | None = None
        for _ in range(attempts):
            try:
                return await self.call(payload)
            except DerivAPIError as exc:
                if exc.code not in ("RateLimit", "ServiceUnavailable", "InternalServerError"):
                    raise
                last = exc
            except (ConnectionError, TimeoutError) as exc:
                last = exc
            await asyncio.sleep(next(delays))
        raise ConnectionError(f"deriv request failed after {attempts} attempts: {last}")

    async def _subscribe_ticks_now(self, symbol: str) -> None:
        try:
            await self.call({"ticks": symbol, "subscribe": 1})
        except Exception as exc:
            log.warning("deriv.tick_subscribe_failed", symbol=symbol, error=str(exc))

    async def subscribe_ticks(self, symbol: str) -> None:
        if symbol in self._tick_subs:
            return
        self._tick_subs.add(symbol)
        if self.connected:
            await self._subscribe_ticks_now(symbol)
