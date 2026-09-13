"""
RAG Answer Cache — cache LLM-generated answers to avoid redundant API calls.

Two tiers:
  1. Redis (shared across workers, 5-min TTL)
  2. In-process LRU (per-worker fallback, 2-min TTL)

Cache key = hash(graph_name + question_normalised + top_node_ids)
This means the cache invalidates when:
  - The question changes
  - The graph data changes (different nodes retrieved)
  - TTL expires
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from collections import OrderedDict
from threading import Lock
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-process LRU cache (fallback when Redis unavailable)
# ---------------------------------------------------------------------------

class _InProcessRAGCache:
    def __init__(self, max_size: int = 200, ttl: float = 120.0):
        self._max = max_size
        self._ttl = ttl
        self._cache: OrderedDict = OrderedDict()
        self._lock = Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Dict]:
        with self._lock:
            if key in self._cache:
                val, ts = self._cache[key]
                if time.time() - ts < self._ttl:
                    self._cache.move_to_end(key)
                    self._hits += 1
                    return val
                del self._cache[key]
        self._misses += 1
        return None

    def put(self, key: str, value: Dict):
        with self._lock:
            self._cache[key] = (value, time.time())
            self._cache.move_to_end(key)
            while len(self._cache) > self._max:
                self._cache.popitem(last=False)

    def invalidate(self, graph_name: str):
        with self._lock:
            keys = [k for k in self._cache if k.startswith(f"{graph_name}:")]
            for k in keys:
                del self._cache[k]

    @property
    def stats(self):
        return {"size": len(self._cache), "hits": self._hits, "misses": self._misses}


# ---------------------------------------------------------------------------
# Redis-backed RAG cache
# ---------------------------------------------------------------------------

class _RedisRAGCache:
    PREFIX = "rag:answer:"

    def __init__(self, redis_url: str, ttl: int = 300):
        import redis
        self._r = redis.from_url(redis_url, decode_responses=True)
        self._ttl = ttl
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Dict]:
        try:
            data = self._r.get(f"{self.PREFIX}{key}")
            if data:
                self._hits += 1
                return json.loads(data)
        except Exception:
            pass
        self._misses += 1
        return None

    def put(self, key: str, value: Dict):
        try:
            self._r.setex(f"{self.PREFIX}{key}", self._ttl, json.dumps(value, default=str))
        except Exception:
            pass

    def invalidate(self, graph_name: str):
        try:
            cursor = 0
            while True:
                cursor, keys = self._r.scan(cursor, match=f"{self.PREFIX}{graph_name}:*", count=100)
                if keys:
                    self._r.delete(*keys)
                if cursor == 0:
                    break
        except Exception:
            pass

    @property
    def stats(self):
        return {"hits": self._hits, "misses": self._misses}


# ---------------------------------------------------------------------------
# Cache key generation
# ---------------------------------------------------------------------------

def _normalise_question(q: str) -> str:
    """Normalise question for cache key — lowercase, strip punctuation, collapse whitespace."""
    q = q.lower().strip()
    q = re.sub(r"[^\w\s]", "", q)
    q = re.sub(r"\s+", " ", q)
    return q


def make_cache_key(graph_name: str, question: str, top_node_ids: list = None) -> str:
    """Build a deterministic cache key from graph + question + retrieved nodes."""
    parts = [graph_name or "", _normalise_question(question)]
    if top_node_ids:
        parts.append(",".join(sorted(top_node_ids[:10])))
    raw = "|".join(parts)
    return f"{graph_name}:{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_cache = None


def get_rag_cache():
    """Get or create the RAG answer cache (Redis if available, in-process fallback)."""
    global _cache
    if _cache is not None:
        return _cache

    url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
    if url:
        try:
            cache = _RedisRAGCache(url, ttl=300)
            cache._r.ping()
            logger.info("RAG answer cache: Redis (TTL=300s)")
            _cache = cache
            return _cache
        except Exception:
            pass

    _cache = _InProcessRAGCache(max_size=200, ttl=120)
    logger.info("RAG answer cache: in-process (TTL=120s)")
    return _cache
