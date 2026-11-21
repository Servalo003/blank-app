"""Process-wide singletons: data provider, coordination (Redis or in-memory)."""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

import structlog

from app.config import Settings, get_settings
from app.data.deriv.client import DerivWSClient
from app.data.deriv.provider import DerivProvider
from app.data.provider import MarketDataProvider
from app.data.simulator import SimulatedProvider

log = structlog.get_logger(__name__)
_provider: MarketDataProvider | None = None


def build_provider(settings: Settings | None = None) -> MarketDataProvider:
    st = settings or get_settings()
    if st.data_provider == "deriv":
        client = DerivWSClient(st.deriv_ws_url, per_minute=st.deriv_rate_per_minute, per_hour=st.deriv_rate_per_hour)
        return DerivProvider(client)
    return SimulatedProvider(seed=st.simulator_seed)


def get_provider() -> MarketDataProvider:
    global _provider
    if _provider is None:
        _provider = build_provider()
    return _provider


def set_provider(p: MarketDataProvider | None) -> None:
    global _provider
    _provider = p


class Coordinator:
    """Distributed lock + heartbeat. Uses Redis when configured, else process-local."""

    def __init__(self, redis_url: str | None) -> None:
        self._redis: Any = None
        self._local_locks: dict[str, float] = {}
        self._local_kv: dict[str, Any] = {}
        if redis_url:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(redis_url, decode_responses=True)

    async def acquire(self, key: str, ttl: int) -> bool:
        if self._redis is not None:
            try:
                return bool(await self._redis.set(f"lock:{key}", "1", nx=True, ex=ttl))
            except Exception as exc:
                log.warning("redis.unavailable", error=str(exc))
        now = time.time()
        if self._local_locks.get(key, 0) > now:
            return False
        self._local_locks[key] = now + ttl
        return True

    async def release(self, key: str) -> None:
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.delete(f"lock:{key}")
        self._local_locks.pop(key, None)

    async def set(self, key: str, value: str, ttl: int = 600) -> None:
        if self._redis is not None:
            try:
                await self._redis.set(key, value, ex=ttl)
                return
            except Exception as exc:
                log.warning("redis.set_failed", error=str(exc))
        self._local_kv[key] = value

    async def get(self, key: str) -> str | None:
        if self._redis is not None:
            try:
                return await self._redis.get(key)  # type: ignore[no-any-return]
            except Exception:
                return None
        return self._local_kv.get(key)

    async def ping(self) -> bool | None:
        if self._redis is None:
            return None
        try:
            return bool(await asyncio.wait_for(self._redis.ping(), 2))
        except Exception:
            return False
