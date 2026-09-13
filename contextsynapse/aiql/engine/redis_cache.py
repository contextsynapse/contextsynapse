"""
Redis-backed Query Cache — shared across all uvicorn workers.

Drop-in replacement for the in-process _QueryCache. Uses Redis strings
with TTL for automatic expiration and namespace-prefixed keys for
isolation and bulk invalidation.

Falls back to the in-process _QueryCache if Redis is unavailable.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


class RedisQueryCache:
    """Query cache backed by Redis — shared across all API workers."""

    KEY_PREFIX = "qcache:"

    def __init__(self, redis_url: str = None, ttl_seconds: int = 30, max_size: int = 10000):
        import redis
        self._redis_url = redis_url or os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL", "redis://localhost:6379/0")
        self._r = redis.from_url(self._redis_url, decode_responses=True)
        self._ttl = ttl_seconds
        self._max_size = max_size

    def _make_key(self, namespace: str, query: str) -> str:
        qhash = hashlib.sha256(query.strip().encode()).hexdigest()[:16]
        return f"{self.KEY_PREFIX}{namespace}:{qhash}"

    def get(self, namespace: str, query: str) -> Optional[Any]:
        key = self._make_key(namespace, query)
        try:
            data = self._r.get(key)
            if data is not None:
                return json.loads(data)
        except Exception:
            pass
        return None

    def put(self, namespace: str, query: str, result: Any):
        key = self._make_key(namespace, query)
        try:
            self._r.setex(key, self._ttl, json.dumps(result, default=str))
        except Exception:
            pass

    def invalidate(self, namespace: str):
        """Delete all cached queries for a namespace."""
        pattern = f"{self.KEY_PREFIX}{namespace}:*"
        try:
            cursor = 0
            while True:
                cursor, keys = self._r.scan(cursor, match=pattern, count=100)
                if keys:
                    self._r.delete(*keys)
                if cursor == 0:
                    break
        except Exception:
            pass

    def clear(self):
        pattern = f"{self.KEY_PREFIX}*"
        try:
            cursor = 0
            while True:
                cursor, keys = self._r.scan(cursor, match=pattern, count=100)
                if keys:
                    self._r.delete(*keys)
                if cursor == 0:
                    break
        except Exception:
            pass

    @property
    def size(self) -> int:
        try:
            cursor, keys = self._r.scan(0, match=f"{self.KEY_PREFIX}*", count=10000)
            return len(keys)
        except Exception:
            return 0


def create_query_cache(redis_url: str = None, ttl: int = 30):
    """Create query cache — Redis if available, in-process fallback."""
    url = redis_url or os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
    if url:
        try:
            cache = RedisQueryCache(redis_url=url, ttl_seconds=ttl)
            cache._r.ping()
            logger.info("Query cache: Redis (TTL=%ds)", ttl)
            return cache
        except Exception as e:
            logger.warning("Redis query cache unavailable (%s), using in-process", e)

    from .executor import _QueryCache
    logger.info("Query cache: in-process (TTL=%ds)", ttl)
    return _QueryCache(ttl_seconds=ttl)
