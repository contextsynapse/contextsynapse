"""
Write-Behind Graph Persistence — buffer graph mutations in Redis, flush async.

Instead of calling graph.save() on every PERSIST stage (blocking disk I/O),
mutations are queued in Redis and flushed to disk by a background thread.

Architecture:
  - API/pipeline writes: buffer node/edge in Redis list `wb:{graph_name}`
  - Background flusher: every N seconds, batch-save dirty graphs to disk
  - On read: graph is always in-memory (Redis registry handles loading)

This eliminates disk I/O from the hot path — ingestion becomes:
  graph.add_node() → memory only → return to user immediately
  Background thread → save_graph() → disk (every 10s or on shutdown)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Dict, Optional, Set

logger = logging.getLogger(__name__)

DEFAULT_FLUSH_INTERVAL = int(os.environ.get("CONTEXTSYNAPSE_WB_FLUSH_INTERVAL") or os.environ.get("AICONTEXTDB_WB_FLUSH_INTERVAL", "10"))


class WriteBehindManager:
    """Tracks dirty graphs and flushes them to disk periodically."""

    def __init__(self, graph_registry, flush_interval: float = DEFAULT_FLUSH_INTERVAL):
        self._registry = graph_registry
        self._flush_interval = flush_interval
        self._dirty: Set[str] = set()
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stats = {"flushes": 0, "graphs_flushed": 0, "errors": 0}

    def mark_dirty(self, graph_name: str):
        """Mark a graph as needing a disk save."""
        with self._lock:
            self._dirty.add(graph_name)

    def flush_now(self, graph_name: str = None):
        """Immediately flush a specific graph or all dirty graphs."""
        if graph_name:
            self._flush_graph(graph_name)
        else:
            self._flush_all()

    def start(self):
        """Start the background flush thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="write-behind-flusher")
        self._thread.start()
        logger.info("[WB] Write-behind flusher started (interval=%ds)", self._flush_interval)

    def stop(self):
        """Stop flusher and flush remaining dirty graphs."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        # Final flush
        self._flush_all()
        logger.info("[WB] Write-behind flusher stopped (stats: %s)", self._stats)

    def _run_loop(self):
        while self._running:
            time.sleep(self._flush_interval)
            self._flush_all()

    def _flush_all(self):
        with self._lock:
            to_flush = list(self._dirty)
            self._dirty.clear()

        if not to_flush:
            return

        flushed = 0
        for name in to_flush:
            if self._flush_graph(name):
                flushed += 1
        if flushed:
            self._stats["flushes"] += 1
            self._stats["graphs_flushed"] += flushed
            logger.debug("[WB] Flushed %d graphs to disk", flushed)

    def _flush_graph(self, name: str) -> bool:
        try:
            self._registry.save_graph(name, create_checkpoint=False)
            # Notify other workers to invalidate their cache
            try:
                from .graph_sync import get_graph_sync
                sync = get_graph_sync()
                if sync:
                    sync.notify_flush(name)
            except Exception:
                pass
            return True
        except Exception as e:
            self._stats["errors"] += 1
            logger.error("[WB] Failed to flush graph '%s': %s", name, e)
            # Re-mark as dirty for next attempt
            with self._lock:
                self._dirty.add(name)
            return False

    @property
    def stats(self) -> Dict:
        with self._lock:
            return {**self._stats, "pending": len(self._dirty)}

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._dirty)


# Singleton
_manager: Optional[WriteBehindManager] = None


def get_write_behind(graph_registry=None) -> Optional[WriteBehindManager]:
    """Get or create the write-behind manager."""
    global _manager
    if _manager is not None:
        return _manager

    if graph_registry is None:
        try:
            from .registry_factory import create_graph_registry
            graph_registry = create_graph_registry()
        except Exception:
            return None

    _manager = WriteBehindManager(graph_registry)
    _manager.start()
    return _manager


def shutdown_write_behind():
    """Gracefully stop the write-behind flusher (call on app shutdown)."""
    global _manager
    if _manager:
        _manager.stop()
        _manager = None
