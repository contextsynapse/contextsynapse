"""Temporal Memory -- microsecond working memory + time-anchored persistence.

Two tiers:
  HOT:  Redis hash per agent. Sub-millisecond reads. TTL auto-expire.
        For: current task context, active preferences, live state.

  COLD: DuckDB via StorageRouter. Persistent, queryable by time range.
        For: historical memory, "what did we know at time T?", decay.

Every memory has a microsecond-precision timestamp for temporal ordering.

Usage:
    from contextsynapse.memory.temporal import TemporalMemory, get_temporal_memory

    tmem = get_temporal_memory()

    # Hot memory (Redis, ~0.1ms read)
    tmem.set_hot("agent-1", "current_task", "Analyzing TCS Q1 report")
    tmem.set_hot("agent-1", "user_preference", "dark mode", ttl_seconds=3600)
    value = tmem.get_hot("agent-1", "current_task")  # ~0.1ms

    # Time-anchored cold memory
    tmem.store("agent-1", "TCS revenue grew 15%", memory_type="fact", entity_id="ent_TCS")

    # Recall at a point in time
    memories = tmem.recall_at("agent-1", timestamp="2026-09-10T10:00:00")

    # Recall in a time range
    memories = tmem.recall_range("agent-1", start="2026-09-01", end="2026-09-10")

    # Decay old memories
    tmem.decay(max_age_hours=720, decay_rate=0.001)
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_global_tmem: Optional["TemporalMemory"] = None
_tmem_lock = threading.Lock()

# Microsecond precision timestamp
def _now_us() -> str:
    """Current time with microsecond precision as ISO string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")

def _now_epoch_us() -> int:
    """Current time as microseconds since epoch."""
    return int(time.time() * 1_000_000)


class HotMemory:
    """Redis-backed microsecond working memory.

    Each agent gets a Redis hash: contextcore:hot:{agent_id}
    Fields are key-value pairs with optional TTL.

    Fallback: in-memory dict when Redis is unavailable.
    """

    def __init__(self, redis_url: str = None):
        self._redis = None
        self._fallback: Dict[str, Dict[str, Any]] = {}
        self._ttls: Dict[str, Dict[str, float]] = {}  # agent -> key -> expire_at

        url = redis_url or os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
        if url:
            try:
                import redis
                host = url.replace("redis://", "").split("/")[0]
                if "localhost" in host:
                    host = host.replace("localhost", "127.0.0.1")
                self._redis = redis.from_url(f"redis://{host}", decode_responses=True)
                self._redis.ping()
                logger.info("HotMemory: Redis connected")
            except Exception as e:
                logger.warning("HotMemory: Redis unavailable (%s), using in-memory", e)
                self._redis = None

    def set(self, agent_id: str, key: str, value: Any, ttl_seconds: int = None) -> None:
        """Set a hot memory value. ~0.1ms with Redis."""
        ts = _now_us()
        payload = json.dumps({"v": value, "ts": ts}, default=str)

        if self._redis:
            rkey = f"contextcore:hot:{agent_id}"
            self._redis.hset(rkey, key, payload)
            if ttl_seconds:
                # Set field-level expiry via a separate sorted set
                expire_at = time.time() + ttl_seconds
                self._redis.zadd(f"contextcore:hot:ttl:{agent_id}", {key: expire_at})
                # Also set key-level TTL as safety net
                self._redis.expire(rkey, max(ttl_seconds, 3600))
        else:
            self._fallback.setdefault(agent_id, {})[key] = payload
            if ttl_seconds:
                self._ttls.setdefault(agent_id, {})[key] = time.time() + ttl_seconds

    def get(self, agent_id: str, key: str) -> Optional[Any]:
        """Get a hot memory value. ~0.05ms with Redis."""
        raw = None
        if self._redis:
            raw = self._redis.hget(f"contextcore:hot:{agent_id}", key)
            # Check field TTL
            if raw:
                expire = self._redis.zscore(f"contextcore:hot:ttl:{agent_id}", key)
                if expire and time.time() > expire:
                    self._redis.hdel(f"contextcore:hot:{agent_id}", key)
                    return None
        else:
            raw = self._fallback.get(agent_id, {}).get(key)
            if raw:
                expire = self._ttls.get(agent_id, {}).get(key)
                if expire and time.time() > expire:
                    del self._fallback[agent_id][key]
                    return None

        if not raw:
            return None
        try:
            data = json.loads(raw)
            return data.get("v")
        except (json.JSONDecodeError, TypeError):
            return raw

    def get_all(self, agent_id: str) -> Dict[str, Any]:
        """Get all hot memory for an agent. ~0.2ms with Redis."""
        result = {}
        if self._redis:
            raw = self._redis.hgetall(f"contextcore:hot:{agent_id}")
            now = time.time()
            for key, val in raw.items():
                expire = self._redis.zscore(f"contextcore:hot:ttl:{agent_id}", key)
                if expire and now > expire:
                    continue
                try:
                    data = json.loads(val)
                    result[key] = data.get("v")
                except (json.JSONDecodeError, TypeError):
                    result[key] = val
        else:
            now = time.time()
            for key, val in self._fallback.get(agent_id, {}).items():
                expire = self._ttls.get(agent_id, {}).get(key)
                if expire and now > expire:
                    continue
                try:
                    data = json.loads(val)
                    result[key] = data.get("v")
                except (json.JSONDecodeError, TypeError):
                    result[key] = val
        return result

    def delete(self, agent_id: str, key: str) -> None:
        if self._redis:
            self._redis.hdel(f"contextcore:hot:{agent_id}", key)
            self._redis.zrem(f"contextcore:hot:ttl:{agent_id}", key)
        else:
            self._fallback.get(agent_id, {}).pop(key, None)

    def clear(self, agent_id: str) -> None:
        if self._redis:
            self._redis.delete(f"contextcore:hot:{agent_id}", f"contextcore:hot:ttl:{agent_id}")
        else:
            self._fallback.pop(agent_id, None)
            self._ttls.pop(agent_id, None)

    def benchmark(self, iterations: int = 1000) -> Dict[str, float]:
        """Benchmark hot memory read/write latency."""
        agent = "__benchmark__"

        # Write benchmark
        t0 = time.perf_counter()
        for i in range(iterations):
            self.set(agent, f"key_{i}", f"value_{i}")
        write_total = time.perf_counter() - t0

        # Read benchmark
        t0 = time.perf_counter()
        for i in range(iterations):
            self.get(agent, f"key_{i}")
        read_total = time.perf_counter() - t0

        self.clear(agent)

        return {
            "write_avg_us": round((write_total / iterations) * 1_000_000, 2),
            "read_avg_us": round((read_total / iterations) * 1_000_000, 2),
            "write_ops_sec": round(iterations / write_total),
            "read_ops_sec": round(iterations / read_total),
            "backend": "redis" if self._redis else "memory",
        }


class TemporalMemory:
    """Two-tier temporal memory: hot (Redis) + cold (DuckDB).

    Hot: microsecond working memory for active agents.
    Cold: persistent, time-anchored, searchable via MemoryEngine.
    """

    def __init__(self, redis_url: str = None, engine=None):
        self.hot = HotMemory(redis_url)
        self._engine = engine

    @property
    def engine(self):
        if self._engine is None:
            from contextsynapse.engine import get_engine
            self._engine = get_engine()
        return self._engine

    def set_engine(self, engine):
        self._engine = engine

    # ── Hot memory (microsecond) ──

    def set_hot(self, agent_id: str, key: str, value: Any, ttl_seconds: int = None) -> None:
        """Set hot working memory. Sub-millisecond."""
        self.hot.set(agent_id, key, value, ttl_seconds)

    def get_hot(self, agent_id: str, key: str) -> Optional[Any]:
        """Get hot working memory. Sub-millisecond."""
        return self.hot.get(agent_id, key)

    def get_hot_all(self, agent_id: str) -> Dict[str, Any]:
        """Get all hot memory for an agent."""
        return self.hot.get_all(agent_id)

    # ── Cold memory (persistent, time-anchored) ──

    def store(self, agent_id: str, content: str, memory_type: str = "general",
              entity_id: str = None, confidence: float = 1.0,
              timestamp: str = None, **kwargs) -> str:
        """Store a time-anchored memory. Persistent in DuckDB."""
        ts = timestamp or _now_us()
        return self.engine.remember(
            agent_id, content,
            memory_type=memory_type,
            entity_id=entity_id,
            confidence=confidence,
            metadata={"timestamp": ts, **kwargs},
        )

    def recall(self, agent_id: str = None, query: str = None,
               limit: int = 10, **kwargs) -> List[Dict[str, Any]]:
        """Recall cold memories."""
        return self.engine.recall(agent_id=agent_id, query=query, limit=limit, **kwargs)

    def recall_at(self, agent_id: str, timestamp: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Recall memories that existed at a specific point in time."""
        try:
            all_mems = self.engine.recall(agent_id=agent_id, limit=1000)
            return [m for m in all_mems if m.get("created_at", "") <= timestamp][:limit]
        except Exception:
            return []

    def recall_range(self, agent_id: str = None, start: str = "", end: str = "",
                     limit: int = 50) -> List[Dict[str, Any]]:
        """Recall memories created within a time range."""
        try:
            all_mems = self.engine.recall(agent_id=agent_id, limit=1000)
            filtered = []
            for m in all_mems:
                created = m.get("created_at", "")
                if start and created < start:
                    continue
                if end and created > end:
                    continue
                filtered.append(m)
            return filtered[:limit]
        except Exception:
            return []

    # ── Promote hot -> cold ──

    def promote(self, agent_id: str, key: str, memory_type: str = "general") -> Optional[str]:
        """Promote a hot memory to cold (persistent) storage."""
        value = self.get_hot(agent_id, key)
        if value is None:
            return None
        content = f"{key}: {value}" if not isinstance(value, str) else value
        mem_id = self.store(agent_id, content, memory_type=memory_type)
        self.hot.delete(agent_id, key)
        return mem_id

    # ── Combined recall (hot + cold) ──

    def recall_all(self, agent_id: str, query: str = None, limit: int = 20) -> Dict[str, Any]:
        """Recall from both hot and cold memory."""
        hot = self.get_hot_all(agent_id)
        cold = self.recall(agent_id=agent_id, query=query, limit=limit)
        return {
            "hot": hot,
            "hot_count": len(hot),
            "cold": cold,
            "cold_count": len(cold),
            "total": len(hot) + len(cold),
        }

    # ── Benchmark ──

    def benchmark(self) -> Dict[str, Any]:
        """Benchmark both tiers."""
        return {
            "hot": self.hot.benchmark(),
            "tier": "redis" if self.hot._redis else "memory",
        }


def get_temporal_memory(redis_url: str = None, engine=None) -> TemporalMemory:
    """Get or create the global temporal memory."""
    global _global_tmem
    if _global_tmem is None:
        with _tmem_lock:
            if _global_tmem is None:
                _global_tmem = TemporalMemory(redis_url=redis_url, engine=engine)
    if engine and _global_tmem._engine is None:
        _global_tmem.set_engine(engine)
    return _global_tmem
