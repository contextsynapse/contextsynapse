"""
CacheManager — In-memory and Redis cache backends for AIContextDB.

Usage:
    from contextsynapse.cache import create_cache

    cache = await create_cache()
    await cache.set("user:1", {"name": "Alice"}, ttl_seconds=300)
    val = await cache.get("user:1")
    await cache.delete("user:1")
    await cache.invalidate_prefix("user:")
"""

import asyncio
import logging
import os
import time
import threading
from abc import ABC, abstractmethod
from typing import Any, Optional

logger = logging.getLogger(__name__)


class CacheBackend(ABC):
    """Abstract base class for cache backends."""

    @abstractmethod
    async def get(self, key: str) -> Optional[Any]:
        ...

    @abstractmethod
    async def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        ...

    @abstractmethod
    async def delete(self, key: str) -> None:
        ...

    @abstractmethod
    async def invalidate_prefix(self, prefix: str) -> int:
        """Delete all keys starting with `prefix`. Returns count of deleted keys."""
        ...


class MemoryBackend(CacheBackend):
    """Thread-safe in-memory cache with TTL expiry."""

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}
        self._expiry: dict[str, float] = {}
        self._lock = threading.Lock()

    def _is_expired(self, key: str) -> bool:
        exp = self._expiry.get(key)
        if exp is None:
            return False
        return time.monotonic() > exp

    def _evict_expired(self) -> None:
        now = time.monotonic()
        expired = [k for k, exp in self._expiry.items() if now > exp]
        for k in expired:
            self._store.pop(k, None)
            self._expiry.pop(k, None)

    async def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key not in self._store:
                return None
            if self._is_expired(key):
                self._store.pop(key, None)
                self._expiry.pop(key, None)
                return None
            return self._store[key]

    async def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        with self._lock:
            self._store[key] = value
            if ttl_seconds is not None:
                self._expiry[key] = time.monotonic() + ttl_seconds
            else:
                self._expiry.pop(key, None)
            # Opportunistic eviction every 100 writes
            if len(self._store) % 100 == 0:
                self._evict_expired()

    async def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)
            self._expiry.pop(key, None)

    async def invalidate_prefix(self, prefix: str) -> int:
        with self._lock:
            keys = [k for k in self._store if k.startswith(prefix)]
            for k in keys:
                self._store.pop(k, None)
                self._expiry.pop(k, None)
            return len(keys)


class RedisBackend(CacheBackend):
    """Async Redis cache backend wrapping redis-py (redis.asyncio)."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._redis: Any = None

    async def _connect(self) -> Any:
        if self._redis is None:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(
                self._redis_url,
                decode_responses=False,
            )
        return self._redis

    async def get(self, key: str) -> Optional[Any]:
        import pickle
        r = await self._connect()
        raw = await r.get(key)
        if raw is None:
            return None
        return pickle.loads(raw)

    async def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        import pickle
        r = await self._connect()
        data = pickle.dumps(value)
        if ttl_seconds is not None:
            await r.setex(key, ttl_seconds, data)
        else:
            await r.set(key, data)

    async def delete(self, key: str) -> None:
        r = await self._connect()
        await r.delete(key)

    async def invalidate_prefix(self, prefix: str) -> int:
        r = await self._connect()
        count = 0
        cursor = 0
        while True:
            cursor, keys = await r.scan(cursor=cursor, match=f"{prefix}*", count=200)
            if keys:
                count += len(keys)
                await r.delete(*keys)
            if cursor == 0:
                break
        return count

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None


class CacheManager:
    """Unified cache interface that delegates to a backend."""

    def __init__(self, backend: CacheBackend) -> None:
        self._backend = backend

    @property
    def backend(self) -> CacheBackend:
        return self._backend

    async def get(self, key: str) -> Optional[Any]:
        return await self._backend.get(key)

    async def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        await self._backend.set(key, value, ttl_seconds)

    async def delete(self, key: str) -> None:
        await self._backend.delete(key)

    async def invalidate_prefix(self, prefix: str) -> int:
        return await self._backend.invalidate_prefix(prefix)

    async def close(self) -> None:
        if hasattr(self._backend, "close"):
            await self._backend.close()


async def create_cache(redis_url: Optional[str] = None) -> CacheManager:
    """Factory: create a CacheManager.

    If `redis_url` is provided or `AICONTEXTDB_REDIS_URL` env var is set,
    attempt to use Redis. Falls back to in-memory on failure.
    """
    url = redis_url or os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")

    if url:
        try:
            backend = RedisBackend(url)
            # Verify connectivity
            r = await backend._connect()
            await r.ping()
            logger.info("CacheManager: using Redis backend (%s)", url)
            return CacheManager(backend)
        except Exception as exc:
            logger.warning(
                "CacheManager: Redis unavailable (%s), falling back to in-memory cache.",
                exc,
            )

    logger.info("CacheManager: using in-memory backend.")
    return CacheManager(MemoryBackend())
