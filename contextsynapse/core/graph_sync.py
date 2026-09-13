"""
Graph Sync — Redis pub/sub for cross-worker graph invalidation.

When Worker A writes to a graph, it publishes a message. Worker B
receives it and reloads the graph from disk on next access.

This gives near-realtime consistency across workers without polling.

Usage:
    from contextsynapse.core.graph_sync import get_graph_sync

    sync = get_graph_sync(graph_registry)
    sync.notify_write("my_graph")           # after writing
    # Other workers auto-invalidate their cached copy
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

CHANNEL = "context:sync"


class GraphSync:
    """Redis pub/sub for cross-worker graph cache invalidation."""

    def __init__(self, graph_registry, redis_url: str = None):
        import redis
        self._url = redis_url or os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL", "redis://localhost:6379/0")
        self._registry = graph_registry
        self._publisher = redis.from_url(self._url, decode_responses=True)
        self._worker_id = f"w-{os.getpid()}"
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def notify_write(self, graph_name: str):
        """Publish a graph-modified event. Called after save_graph."""
        try:
            msg = json.dumps({
                "type": "graph_modified",
                "graph": graph_name,
                "worker": self._worker_id,
                "ts": time.time(),
            })
            self._publisher.publish(CHANNEL, msg)
            logger.debug("[SYNC] Published graph_modified: %s", graph_name)
        except Exception as e:
            logger.debug("[SYNC] Publish failed: %s", e)

    def notify_flush(self, graph_name: str):
        """Notify that a graph was flushed to disk."""
        try:
            msg = json.dumps({
                "type": "graph_flushed",
                "graph": graph_name,
                "worker": self._worker_id,
                "ts": time.time(),
            })
            self._publisher.publish(CHANNEL, msg)
        except Exception:
            pass

    def start_listener(self):
        """Start background thread that listens for sync events."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True, name="graph-sync")
        self._thread.start()
        logger.info("[SYNC] Graph sync listener started (worker=%s)", self._worker_id)

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    def _listen_loop(self):
        import redis
        try:
            r = redis.from_url(self._url, decode_responses=True)
            pubsub = r.pubsub()
            pubsub.subscribe(CHANNEL)

            for message in pubsub.listen():
                if not self._running:
                    break
                if message["type"] != "message":
                    continue

                try:
                    data = json.loads(message["data"])
                except Exception:
                    continue

                # Ignore our own messages
                if data.get("worker") == self._worker_id:
                    continue

                graph_name = data.get("graph", "")
                event_type = data.get("type", "")

                if event_type in ("graph_modified", "graph_flushed") and graph_name:
                    # Invalidate our cached copy so next access reloads from disk
                    if hasattr(self._registry, "graphs") and graph_name in self._registry.graphs:
                        del self._registry.graphs[graph_name]
                        logger.debug("[SYNC] Invalidated cached graph '%s' (event from %s)",
                                     graph_name, data.get("worker", "?"))

                    # Also invalidate query cache for this graph
                    try:
                        from ..aiql.engine.executor import _query_cache
                        _query_cache.invalidate(graph_name)
                    except Exception:
                        pass

        except Exception as e:
            logger.warning("[SYNC] Listener error: %s", e)
            if self._running:
                time.sleep(5)
                self._listen_loop()  # reconnect


# Singleton
_sync: Optional[GraphSync] = None


def get_graph_sync(graph_registry=None) -> Optional[GraphSync]:
    """Get or create the graph sync instance."""
    global _sync
    if _sync is not None:
        return _sync

    url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
    if not url:
        return None

    if graph_registry is None:
        try:
            from .registry_factory import create_graph_registry
            graph_registry = create_graph_registry()
        except Exception:
            return None

    try:
        _sync = GraphSync(graph_registry, redis_url=url)
        _sync.start_listener()
        return _sync
    except Exception as e:
        logger.debug("[SYNC] Graph sync not available: %s", e)
        return None
