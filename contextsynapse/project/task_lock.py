"""
Task Lock & Event Bus
======================
Atomic per-task locking for safe multi-agent coordination.

Uses Redis SETNX for distributed deployments, falls back to
threading locks for single-worker / no-Redis mode.

Usage:
    lock = TaskLock(namespace="my-project")
    if lock.acquire_claim("task-123", "agent-claude"):
        # do work
        lock.release_claim("task-123", "agent-claude")
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class TaskLock:
    """Per-task atomic locking. Redis-backed or threading fallback."""

    def __init__(self, namespace: str, redis_url: Optional[str] = None):
        self._namespace = namespace
        self._redis = None
        self._prefix = "contextcore:task:lock"

        url = redis_url or os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
        if url:
            try:
                import redis as _redis
                self._redis = _redis.Redis.from_url(url, decode_responses=True)
                self._redis.ping()
            except Exception:
                self._redis = None

        # Fallback: per-task threading locks + holder tracking
        self._local_lock = threading.Lock()
        self._holders: Dict[str, str] = {}  # task_id -> agent_id

    def _key(self, task_id: str) -> str:
        return f"{self._prefix}:{self._namespace}:{task_id}"

    def acquire_claim(self, task_id: str, agent_id: str, ttl: int = 300) -> bool:
        """Atomically claim a task. Returns True if acquired, False if held by another."""
        if self._redis:
            return self._acquire_redis(task_id, agent_id, ttl)
        return self._acquire_local(task_id, agent_id)

    def release_claim(self, task_id: str, agent_id: str) -> bool:
        """Release a claim. Only succeeds if agent_id matches holder."""
        if self._redis:
            return self._release_redis(task_id, agent_id)
        return self._release_local(task_id, agent_id)

    def get_holder(self, task_id: str) -> Optional[str]:
        """Return the agent_id holding the lock, or None."""
        if self._redis:
            return self._redis.get(self._key(task_id))
        return self._holders.get(task_id)

    def force_release(self, task_id: str) -> bool:
        """Admin override to release a stuck lock."""
        if self._redis:
            return bool(self._redis.delete(self._key(task_id)))
        with self._local_lock:
            return self._holders.pop(task_id, None) is not None

    # ── Redis implementation ────────────────────────────────────

    def _acquire_redis(self, task_id: str, agent_id: str, ttl: int) -> bool:
        key = self._key(task_id)
        acquired = self._redis.set(key, agent_id, nx=True, ex=ttl)
        if acquired:
            return True
        holder = self._redis.get(key)
        if holder == agent_id:
            self._redis.expire(key, ttl)
            return True
        return False

    def _release_redis(self, task_id: str, agent_id: str) -> bool:
        """Release via Lua script for atomicity (GET + compare + DELETE)."""
        key = self._key(task_id)
        lua = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        result = self._redis.eval(lua, 1, key, agent_id)
        return bool(result)

    # ── Threading fallback ──────────────────────────────────────

    def _acquire_local(self, task_id: str, agent_id: str) -> bool:
        with self._local_lock:
            holder = self._holders.get(task_id)
            if holder is None or holder == agent_id:
                self._holders[task_id] = agent_id
                return True
            return False

    def _release_local(self, task_id: str, agent_id: str) -> bool:
        with self._local_lock:
            if self._holders.get(task_id) == agent_id:
                del self._holders[task_id]
                return True
            return False


class TaskEventBus:
    """Lightweight event emission for task state changes.

    Publishes to Redis pub/sub (if available) and in-memory subscribers.
    Keeps a ring buffer of recent events for late joiners.
    """

    def __init__(self, namespace: str, redis_url: Optional[str] = None, max_history: int = 50):
        self._namespace = namespace
        self._channel = f"contextcore:task:events:{namespace}"
        self._redis = None
        self._subscribers: List[Callable[[Dict[str, Any]], None]] = []
        self._history: List[Dict[str, Any]] = []
        self._max_history = max_history

        url = redis_url or os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
        if url:
            try:
                import redis as _redis
                self._redis = _redis.Redis.from_url(url, decode_responses=True)
                self._redis.ping()
            except Exception:
                self._redis = None

    def emit(self, event_type: str, task_id: str, agent_id: str, **extra):
        """Publish a task event to all channels."""
        event = {
            "type": event_type,
            "task_id": task_id,
            "agent_id": agent_id,
            "namespace": self._namespace,
            "timestamp": time.time(),
            **extra,
        }

        # Ring buffer
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        # In-memory subscribers
        for sub in self._subscribers:
            try:
                sub(event)
            except Exception:
                pass  # subscriber errors must not break emission

        # Redis pub/sub
        if self._redis:
            try:
                import json
                self._redis.publish(self._channel, json.dumps(event))
            except Exception:
                pass

    def subscribe(self, callback: Callable[[Dict[str, Any]], None]):
        """Register an in-memory subscriber."""
        self._subscribers.append(callback)

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return recent events from ring buffer."""
        return self._history[-limit:]
