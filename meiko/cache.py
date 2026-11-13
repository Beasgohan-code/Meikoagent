"""Redis adapter with a fully functional in-memory fallback.

Redis provides caching, rate limiting, distributed locks, queues and worker
coordination in production.  When ``REDIS_URL`` is empty or unreachable the
platform keeps working on a local asyncio implementation (single process only),
which keeps the sandbox, tests and `python start.py` usable without services.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from meiko.config import get_settings

logger = logging.getLogger(__name__)


class MemoryBackend:
    """Minimal async Redis-compatible backend used as fallback."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[Any, float | None]] = {}
        self._counters: defaultdict[str, deque[float]] = defaultdict(deque)
        self._locks: dict[str, asyncio.Lock] = {}
        self._queues: dict[str, asyncio.Queue[str]] = defaultdict(asyncio.Queue)
        self._lock_guard = asyncio.Lock()

    async def get(self, key: str) -> Any:
        item = self._data.get(key)
        if item is None:
            return None
        value, expires = item
        if expires is not None and expires < time.time():
            self._data.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        expires = time.time() + ttl if ttl else None
        self._data[key] = (value, expires)

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self._data.pop(key, None)

    async def incr(self, key: str, ttl: int | None = None) -> int:
        current = await self.get(key)
        value = int(current or 0) + 1
        await self.set(key, value, ttl)
        return value

    async def rate_limit(self, key: str, limit: int, window: int) -> tuple[bool, int]:
        now = time.time()
        bucket = self._counters[key]
        while bucket and bucket[0] <= now - window:
            bucket.popleft()
        if len(bucket) >= limit:
            return False, max(0, int(window - (now - bucket[0])))
        bucket.append(now)
        return True, 0

    async def push(self, queue: str, payload: str) -> None:
        await self._queues[queue].put(payload)

    async def pop(self, queue: str, timeout: float = 0.0) -> str | None:
        try:
            if timeout:
                return await asyncio.wait_for(self._queues[queue].get(), timeout)
            return self._queues[queue].get_nowait()
        except (asyncio.TimeoutError, asyncio.QueueEmpty):
            return None

    async def queue_size(self, queue: str) -> int:
        return self._queues[queue].qsize()

    async def ping(self) -> bool:
        return True

    @asynccontextmanager
    async def lock(self, name: str, timeout: float = 10.0) -> AsyncIterator[bool]:
        async with self._lock_guard:
            lock = self._locks.setdefault(name, asyncio.Lock())
        acquired = False
        try:
            await asyncio.wait_for(lock.acquire(), timeout)
            acquired = True
            yield True
        except asyncio.TimeoutError:
            yield False
        finally:
            if acquired:
                lock.release()

    def flush(self) -> None:
        self._data.clear()
        self._counters.clear()
        self._queues.clear()


class RedisBackend:
    """Thin wrapper around redis.asyncio keeping a uniform API."""

    def __init__(self, client: Any) -> None:
        self.client = client

    async def get(self, key: str) -> Any:
        raw = await self.client.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return raw

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        payload = json.dumps(value, default=str)
        if ttl:
            await self.client.set(key, payload, ex=ttl)
        else:
            await self.client.set(key, payload)

    async def delete(self, *keys: str) -> None:
        if keys:
            await self.client.delete(*keys)

    async def incr(self, key: str, ttl: int | None = None) -> int:
        value = int(await self.client.incr(key))
        if ttl and value == 1:
            await self.client.expire(key, ttl)
        return value

    async def rate_limit(self, key: str, limit: int, window: int) -> tuple[bool, int]:
        bucket = f"{key}:{int(time.time() // window)}"
        count = await self.incr(bucket, ttl=window)
        if count > limit:
            return False, window
        return True, 0

    async def push(self, queue: str, payload: str) -> None:
        await self.client.rpush(queue, payload)

    async def pop(self, queue: str, timeout: float = 0.0) -> str | None:
        if timeout:
            result = await self.client.blpop(queue, timeout=int(timeout) or 1)
            if result:
                return result[1].decode() if isinstance(result[1], bytes) else result[1]
            return None
        raw = await self.client.lpop(queue)
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, bytes) else raw

    async def queue_size(self, queue: str) -> int:
        return int(await self.client.llen(queue))

    async def ping(self) -> bool:
        return bool(await self.client.ping())

    @asynccontextmanager
    async def lock(self, name: str, timeout: float = 10.0) -> AsyncIterator[bool]:
        acquired = await self.client.set(f"lock:{name}", "1", nx=True, ex=int(timeout) or 10)
        try:
            yield bool(acquired)
        finally:
            if acquired:
                await self.client.delete(f"lock:{name}")


class Cache:
    """Facade used by every service – never touches the backend directly."""

    def __init__(self, backend: MemoryBackend | RedisBackend, namespace: str = "meiko") -> None:
        self.backend = backend
        self.namespace = namespace

    def key(self, *parts: object) -> str:
        return ":".join([self.namespace, *[str(p) for p in parts]])

    async def get(self, key: str, default: Any = None) -> Any:
        value = await self.backend.get(self.key(key))
        return default if value is None else value

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        await self.backend.set(self.key(key), value, ttl)

    async def delete(self, *keys: str) -> None:
        await self.backend.delete(*[self.key(k) for k in keys])

    async def incr(self, key: str, ttl: int | None = None) -> int:
        return await self.backend.incr(self.key(key), ttl)

    async def rate_limit(self, key: str, limit: int, window: int = 60) -> tuple[bool, int]:
        return await self.backend.rate_limit(self.key("rl", key), limit, window)

    async def push(self, queue: str, payload: dict[str, Any] | str) -> None:
        data = payload if isinstance(payload, str) else json.dumps(payload, default=str)
        await self.backend.push(self.key("queue", queue), data)

    async def pop(self, queue: str, timeout: float = 0.0) -> dict[str, Any] | None:
        raw = await self.backend.pop(self.key("queue", queue), timeout)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return {"raw": raw}

    async def queue_size(self, queue: str) -> int:
        return await self.backend.queue_size(self.key("queue", queue))

    def lock(self, name: str, timeout: float = 10.0):
        return self.backend.lock(self.key("lock", name), timeout)

    async def ping(self) -> dict[str, Any]:
        try:
            ok = await self.backend.ping()
            return {"status": "ok" if ok else "error", "backend": type(self.backend).__name__}
        except Exception as exc:  # pragma: no cover
            return {"status": "error", "error": str(exc), "backend": type(self.backend).__name__}


_cache: Cache | None = None


class RedisUnavailable(RuntimeError):
    """Redis is required by this deployment but did not answer."""


async def init_cache() -> Cache:
    """Connect to Redis when configured, otherwise fall back to memory.

    Falling back is only safe when the cache is *local by design*.  With more than
    one process (``WEB_CONCURRENCY``/replicas) or when ``REQUIRE_REDIS`` is set, an
    in-memory cache silently breaks the things Redis exists for: rate limits
    become per-process, job deduplication stops working and the worker heartbeat
    registry splits into islands.  In those cases this raises instead of degrading
    quietly — a loud failure at boot is better than a platform that only *looks*
    consistent.
    """
    global _cache
    if _cache is not None:
        return _cache
    settings = get_settings()
    strict = bool(getattr(settings, "require_redis", False)) or settings.is_production
    if settings.redis_url:
        try:
            from redis.asyncio import from_url

            client = from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
            await asyncio.wait_for(client.ping(), timeout=3)
            _cache = Cache(RedisBackend(client), settings.redis_namespace)
            logger.info("connected to redis")
            return _cache
        except Exception as exc:
            if strict:
                raise RedisUnavailable(
                    f"redis is required in this deployment but {settings.redis_url!r} "
                    f"did not answer ({exc}); refusing to fall back to per-process memory"
                ) from exc
            logger.warning("redis unavailable (%s) – using in-memory cache", exc)
    elif strict:
        raise RedisUnavailable(
            "REDIS_URL is not set but this deployment requires shared state "
            "(production or REQUIRE_REDIS=1); set REDIS_URL or run a single process"
        )
    _cache = Cache(MemoryBackend(), settings.redis_namespace)
    return _cache


def get_cache() -> Cache:
    if _cache is None:
        return Cache(MemoryBackend(), get_settings().redis_namespace)
    return _cache


async def reset_cache() -> None:
    global _cache
    _cache = None
