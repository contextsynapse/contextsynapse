"""
Ingestion Queue — Bounded worker pool for concurrent document ingestion.

Replaces unlimited FastAPI BackgroundTasks with a controlled thread pool.
Prevents thread explosion under load while providing queue-based backpressure.

Usage:
    from contextsynapse.ingestion.queue import get_ingestion_queue
    q = get_ingestion_queue()
    position = q.submit(job_id, fn, *args)
"""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, Future
from collections import deque
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# Default max concurrent ingestion workers
DEFAULT_WORKERS = int(os.environ.get("CONTEXTSYNAPSE_INGEST_WORKERS") or os.environ.get("AICONTEXTDB_INGEST_WORKERS", "4"))
MAX_QUEUE_SIZE = int(os.environ.get("CONTEXTSYNAPSE_INGEST_QUEUE_SIZE") or os.environ.get("AICONTEXTDB_INGEST_QUEUE_SIZE", "100"))


class IngestionQueue:
    """Bounded worker pool with queue for ingestion jobs."""

    def __init__(self, max_workers: int = DEFAULT_WORKERS, max_queue: int = MAX_QUEUE_SIZE):
        self._max_workers = max_workers
        self._max_queue = max_queue
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="ingest-worker",
        )
        self._lock = threading.Lock()
        self._active: Dict[str, Future] = {}  # job_id → Future
        self._queue_order: deque = deque()     # ordered job_ids for position tracking
        self._completed_count = 0
        self._failed_count = 0

    def submit(self, job_id: str, fn: Callable, *args, **kwargs) -> Dict[str, Any]:
        """Submit an ingestion job. Returns queue info.

        Returns:
            {"accepted": True, "queue_position": N, "active_workers": M}
            or {"accepted": False, "reason": "queue full"} if at capacity
        """
        with self._lock:
            total_pending = len(self._queue_order)
            if total_pending >= self._max_queue:
                logger.warning("[QUEUE] Rejected job %s — queue full (%d/%d)",
                               job_id, total_pending, self._max_queue)
                return {"accepted": False, "reason": f"Queue full ({total_pending}/{self._max_queue})"}

            self._queue_order.append(job_id)
            position = len(self._queue_order)

        def _wrapped():
            try:
                fn(*args, **kwargs)
                with self._lock:
                    self._completed_count += 1
            except Exception as e:
                with self._lock:
                    self._failed_count += 1
                logger.error("[QUEUE] Job %s failed: %s", job_id, e)
            finally:
                with self._lock:
                    self._active.pop(job_id, None)
                    try:
                        self._queue_order.remove(job_id)
                    except ValueError:
                        pass

        future = self._executor.submit(_wrapped)
        with self._lock:
            self._active[job_id] = future

        logger.info("[QUEUE] Job %s submitted (position=%d, active=%d/%d)",
                     job_id, position, len(self._active), self._max_workers)

        return {
            "accepted": True,
            "queue_position": position,
            "active_workers": len(self._active),
            "max_workers": self._max_workers,
        }

    def get_status(self) -> Dict[str, Any]:
        """Get queue status for monitoring."""
        with self._lock:
            return {
                "active_workers": len(self._active),
                "max_workers": self._max_workers,
                "queue_depth": len(self._queue_order),
                "max_queue": self._max_queue,
                "completed": self._completed_count,
                "failed": self._failed_count,
                "active_jobs": list(self._active.keys()),
            }

    def get_position(self, job_id: str) -> int:
        """Get a job's position in the queue. Returns 0 if running or not found."""
        with self._lock:
            try:
                return list(self._queue_order).index(job_id) + 1
            except ValueError:
                return 0

    def shutdown(self, wait: bool = True):
        """Shut down the worker pool."""
        self._executor.shutdown(wait=wait)


# Global singleton
_queue: Optional[IngestionQueue] = None
_queue_lock = threading.Lock()


def get_ingestion_queue() -> IngestionQueue:
    """Get or create the global ingestion queue."""
    global _queue
    if _queue is None:
        with _queue_lock:
            if _queue is None:
                _queue = IngestionQueue()
                logger.info("[QUEUE] Ingestion queue initialized (workers=%d, max_queue=%d)",
                            _queue._max_workers, _queue._max_queue)
    return _queue
