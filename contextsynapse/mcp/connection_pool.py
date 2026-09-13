"""
MCP Connection Pool — LRU cache of session connections.

Avoids creating new graph connections for every MCP tool call.
Connections are cached by (session_id, agent_id) and evicted after idle timeout.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)


class ConnectionPool:
    """LRU cache of graph connections keyed by (session_id, agent_id)."""

    def __init__(
        self,
        max_sessions: int = None,
        idle_timeout: int = 1800,  # 30 minutes
    ):
        self.max_sessions = max_sessions or int(os.environ.get("CONTEXTSYNAPSE_MCP_MAX_SESSIONS") or os.environ.get("AICONTEXTDB_MCP_MAX_SESSIONS", "50"))
        self.idle_timeout = idle_timeout
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._lock = threading.Lock()

    def get_or_create(self, session_id: str, agent_id: str, graph_namespace: str) -> Any:
        """Get cached connection or create a new one. Returns AIContextDBConnection."""
        key = f"{session_id}:{agent_id}"

        with self._lock:
            if key in self._cache:
                entry = self._cache[key]
                entry.last_used = time.time()
                self._cache.move_to_end(key)
                return entry.conn

        # Create outside lock (may be slow)
        conn = self._build_connection(graph_namespace)

        with self._lock:
            # Evict if over capacity
            while len(self._cache) >= self.max_sessions:
                oldest_key, oldest = self._cache.popitem(last=False)
                logger.debug("[POOL] Evicted: %s", oldest_key)

            self._cache[key] = _CacheEntry(conn=conn, namespace=graph_namespace)
            logger.info("[POOL] Created connection: %s → %s (%d active)",
                        key, graph_namespace, len(self._cache))

        return conn

    def release(self, session_id: str, agent_id: str):
        """Mark a connection as idle (don't remove, just update timestamp)."""
        key = f"{session_id}:{agent_id}"
        with self._lock:
            if key in self._cache:
                self._cache[key].last_used = time.time()

    def evict_idle(self):
        """Remove connections idle longer than timeout."""
        now = time.time()
        with self._lock:
            expired = [k for k, v in self._cache.items()
                       if now - v.last_used > self.idle_timeout]
            for k in expired:
                del self._cache[k]
                logger.debug("[POOL] Evicted idle: %s", k)

    def stats(self) -> dict:
        with self._lock:
            return {
                "active": len(self._cache),
                "max": self.max_sessions,
                "sessions": list(self._cache.keys()),
            }

    def _build_connection(self, namespace: str):
        """Build an AIContextDBConnection for a namespace."""
        redis_url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
        if redis_url:
            try:
                from ..core.registry import GraphRegistry
                reg = GraphRegistry()
                db = reg.get_graph(namespace, load_if_missing=True)
                if db:
                    from ..adapters._base import AIContextDBConnection
                    conn = AIContextDBConnection(namespace=namespace)
                    conn.db = db
                    conn._namespace = namespace
                    try:
                        from ..aiql import AIQLExecutor
                        conn.executor = AIQLExecutor(contextcore=db)
                    except Exception:
                        pass
                    return conn
            except Exception as e:
                logger.warning("[POOL] Redis connection failed for %s: %s", namespace, e)

        from ..adapters._base import AIContextDBConnection
        return AIContextDBConnection(namespace=namespace)


class _CacheEntry:
    __slots__ = ("conn", "namespace", "last_used", "created_at")

    def __init__(self, conn, namespace: str):
        self.conn = conn
        self.namespace = namespace
        self.last_used = time.time()
        self.created_at = time.time()


# Module-level singleton
_pool: Optional[ConnectionPool] = None


def get_connection_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool()
    return _pool
