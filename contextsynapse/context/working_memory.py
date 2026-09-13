"""
WorkingMemory — Per-Agent Context Cache
========================================
Redis-backed (with in-memory fallback) short-lived cache that stores
pre-built context payloads per agent+namespace.  Designed to make
repeated ``orient()`` calls return in sub-5 ms when the underlying
graph has not changed.

Key layout (Redis):
    contextcore:wm:{namespace}:{agent_id}  →  Hash
        content         JSON-encoded payload
        topic_keywords  JSON list of strings
        task_id         optional current task id
        _ts             Unix timestamp (float)

TTL is enforced both via Redis EXPIRE and a client-side timestamp
check so the in-memory fallback behaves identically.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PREFIX = "contextcore:wm"

# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_instance: Optional["WorkingMemory"] = None


def get_working_memory(redis_client=None, ttl_s: int = 60) -> "WorkingMemory":
    """Return (or create) the singleton WorkingMemory instance."""
    global _instance
    if _instance is None:
        _instance = WorkingMemory(redis_client=redis_client, ttl_s=ttl_s)
    return _instance


# ---------------------------------------------------------------------------
# WorkingMemory
# ---------------------------------------------------------------------------


class WorkingMemory:
    """Per-agent working-memory cache with Redis or in-memory backend."""

    def __init__(self, redis_client=None, ttl_s: int = 60):
        self._redis = redis_client
        self._ttl_s = ttl_s
        # In-memory fallback: {redis_key: {content, topic_keywords, task_id, _ts}}
        self._local: Dict[str, Dict[str, Any]] = {}
        self._hits = 0
        self._misses = 0

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _key(namespace: str, agent_id: str) -> str:
        return f"{_PREFIX}:{namespace}:{agent_id}"

    def _is_expired(self, ts: float) -> bool:
        return (time.time() - ts) > self._ttl_s

    # -- public API ----------------------------------------------------------

    def get(self, agent_id: str, namespace: str) -> Optional[Any]:
        """Return cached content or ``None`` on miss / expiry."""
        key = self._key(namespace, agent_id)

        if self._redis is not None:
            return self._get_redis(key)
        return self._get_local(key)

    def set(
        self,
        agent_id: str,
        namespace: str,
        content: Any,
        topic_keywords: List[str],
        task_id: Optional[str] = None,
    ) -> None:
        """Store *content* in the cache with the configured TTL."""
        key = self._key(namespace, agent_id)
        entry = {
            "content": json.dumps(content),
            "topic_keywords": json.dumps(topic_keywords),
            "task_id": task_id or "",
            "_ts": str(time.time()),
        }

        if self._redis is not None:
            self._redis.hset(key, mapping=entry)
            self._redis.expire(key, self._ttl_s)
        else:
            self._local[key] = entry

    def invalidate(self, agent_id: str, namespace: str) -> None:
        """Remove a specific agent's cached context."""
        key = self._key(namespace, agent_id)
        if self._redis is not None:
            self._redis.delete(key)
        else:
            self._local.pop(key, None)

    def invalidate_by_keywords(self, namespace: str, keywords: List[str]) -> None:
        """Invalidate all agents in *namespace* whose topic_keywords overlap.

        Also emits ``context_invalidated`` events so connected agents
        re-orient immediately (CMA Phase 2 — reactive push).
        """
        kw_set = set(keywords)
        prefix = f"{_PREFIX}:{namespace}:"
        invalidated_agents: List[str] = []

        if self._redis is not None:
            cursor = "0"
            while True:
                cursor, keys = self._redis.scan(
                    cursor=cursor, match=f"{prefix}*", count=200
                )
                for k in keys:
                    raw = self._redis.hget(k, "topic_keywords")
                    if raw:
                        stored = set(json.loads(raw))
                        if stored & kw_set:
                            self._redis.delete(k)
                            # Extract agent_id from key: contextcore:wm:{ns}:{agent_id}
                            k_str = k.decode() if isinstance(k, bytes) else k
                            parts = k_str.split(":")
                            if len(parts) >= 4:
                                invalidated_agents.append(parts[-1])
                if cursor == 0 or cursor == b"0":
                    break
        else:
            to_remove = []
            for k, entry in self._local.items():
                if not k.startswith(prefix):
                    continue
                stored = set(json.loads(entry["topic_keywords"]))
                if stored & kw_set:
                    to_remove.append(k)
                    parts = k.split(":")
                    if len(parts) >= 4:
                        invalidated_agents.append(parts[-1])
            for k in to_remove:
                del self._local[k]

        # Reactive push: notify invalidated agents to re-orient
        if invalidated_agents:
            self._emit_invalidation(namespace, invalidated_agents, keywords)

    def invalidate_namespace(self, namespace: str) -> None:
        """Remove all cached entries for a namespace."""
        prefix = f"{_PREFIX}:{namespace}:"

        if self._redis is not None:
            cursor = "0"
            while True:
                cursor, keys = self._redis.scan(
                    cursor=cursor, match=f"{prefix}*", count=200
                )
                if keys:
                    self._redis.delete(*keys)
                if cursor == 0 or cursor == b"0":
                    break
        else:
            self._local = {
                k: v for k, v in self._local.items() if not k.startswith(prefix)
            }

    def _emit_invalidation(self, namespace: str, agent_ids: List[str], keywords: List[str]) -> None:
        """Emit context_invalidated event so agents re-orient immediately."""
        try:
            from ..api.events import event_bus
            for agent_id in agent_ids:
                event_bus.emit("context_invalidated", {
                    "namespace": namespace,
                    "agent_id": agent_id,
                    "keywords": keywords[:10],
                    "reason": "graph_write",
                })
            logger.debug("[WM] Reactive push: %d agent(s) notified in %s", len(agent_ids), namespace)
        except Exception:
            pass  # event bus not available (e.g. during tests)

    def stats(self) -> Dict[str, int]:
        """Return hit/miss counters."""
        return {"hits": self._hits, "misses": self._misses}

    # -- internals -----------------------------------------------------------

    def _get_redis(self, key: str) -> Optional[Any]:
        raw = self._redis.hgetall(key)
        if not raw:
            self._misses += 1
            return None
        ts = float(raw.get(b"_ts") or raw.get("_ts") or 0)
        if self._is_expired(ts):
            self._redis.delete(key)
            self._misses += 1
            return None
        self._hits += 1
        content_raw = raw.get(b"content") or raw.get("content")
        return json.loads(content_raw)

    def _get_local(self, key: str) -> Optional[Any]:
        entry = self._local.get(key)
        if entry is None:
            self._misses += 1
            return None
        ts = float(entry["_ts"])
        if self._is_expired(ts):
            del self._local[key]
            self._misses += 1
            return None
        self._hits += 1
        return json.loads(entry["content"])
