"""Redis Streams task delivery — push-based worker consumption.

Publisher: XADD tasks to per-namespace streams when created/unblocked.
Consumer: XREADGROUP with consumer groups for exactly-once delivery.
Falls back to in-memory queue when Redis is unavailable.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class TaskStreamPublisher:
    """Publishes task events to Redis Streams."""

    def __init__(self, redis_client=None):
        self._redis = redis_client
        self._memory: Dict[str, List[Dict]] = {}
        self._groups_created: set = set()

    def publish(self, namespace: str, task_id: str, priority: int = 0,
                skills_required: List[str] = None, metadata: Dict = None):
        entry = {
            "task_id": task_id,
            "priority": str(priority),
            "skills_required": json.dumps(skills_required or []),
            "metadata": json.dumps(metadata or {}),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if self._redis:
            try:
                stream_key = f"tasks:{namespace}"
                self._ensure_group(stream_key)
                self._redis.xadd(stream_key, entry, maxlen=10000)
                return
            except Exception as e:
                logger.warning("[STREAM] Redis publish failed, using memory: %s", e)
        self._memory.setdefault(namespace, []).append(entry)

    def publish_batch(self, namespace: str, tasks: List[Dict]):
        if self._redis:
            try:
                stream_key = f"tasks:{namespace}"
                self._ensure_group(stream_key)
                pipe = self._redis.pipeline()
                for t in tasks:
                    entry = {
                        "task_id": t["task_id"],
                        "priority": str(t.get("priority", 0)),
                        "skills_required": json.dumps(t.get("skills_required", [])),
                        "metadata": json.dumps(t.get("metadata", {})),
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                    pipe.xadd(stream_key, entry, maxlen=10000)
                pipe.execute()
                return
            except Exception as e:
                logger.warning("[STREAM] Redis batch publish failed: %s", e)
        for t in tasks:
            self.publish(namespace, t["task_id"], t.get("priority", 0), metadata=t.get("metadata"))

    def get_pending(self, namespace: str) -> List[Dict]:
        return list(self._memory.get(namespace, []))

    def _ensure_group(self, stream_key: str):
        if stream_key in self._groups_created:
            return
        try:
            self._redis.xgroup_create(stream_key, "workers", id="0", mkstream=True)
        except Exception:
            pass
        self._groups_created.add(stream_key)


class TaskStreamConsumer:
    """Consumes tasks from Redis Streams with exactly-once delivery."""

    def __init__(self, redis_client=None, namespace: str = "default",
                 publisher: Optional[TaskStreamPublisher] = None,
                 on_task: Optional[Callable[[Dict], None]] = None,
                 worker_id: str = None, block_ms: int = 5000,
                 max_retries: int = 3, stale_ms: int = 60000):
        self._redis = redis_client
        self._namespace = namespace
        self._publisher = publisher
        self._on_task = on_task or (lambda t: None)
        self._worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self._block_ms = block_ms
        self._max_retries = max_retries
        self._stale_ms = stale_ms
        self._running = True
        self._retry_counts: Dict[str, int] = {}
        self.dead_letters: List[Dict] = []

    def start(self):
        logger.info("[STREAM] Consumer %s started on tasks:%s", self._worker_id, self._namespace)
        while self._running:
            try:
                self.poll_once()
            except Exception as e:
                logger.error("[STREAM] Consumer error: %s", e)
                if self._running:
                    import time
                    time.sleep(1)

    def stop(self):
        self._running = False

    def poll_once(self):
        if self._redis:
            self._poll_redis()
        elif self._publisher:
            self._poll_memory()

    def _poll_redis(self):
        stream_key = f"tasks:{self._namespace}"
        # Claim stale entries
        try:
            _, stale = self._redis.xautoclaim(
                stream_key, "workers", self._worker_id,
                min_idle_time=self._stale_ms, start_id="0-0", count=1,
            )
            for msg_id, fields in stale:
                self._handle_message(stream_key, msg_id, fields)
                return
        except Exception:
            pass
        # Read new messages
        try:
            results = self._redis.xreadgroup(
                groupname="workers", consumername=self._worker_id,
                streams={stream_key: ">"}, count=1, block=self._block_ms,
            )
            if results:
                for stream, messages in results:
                    for msg_id, fields in messages:
                        self._handle_message(stream_key, msg_id, fields)
        except Exception as e:
            logger.debug("[STREAM] xreadgroup error: %s", e)

    def _handle_message(self, stream_key: str, msg_id, fields: Dict):
        task_data = {}
        for k, v in fields.items():
            key = k.decode() if isinstance(k, bytes) else k
            val = v.decode() if isinstance(v, bytes) else v
            task_data[key] = val
        task_id = task_data.get("task_id", "")
        try:
            self._on_task(task_data)
            if self._redis:
                self._redis.xack(stream_key, "workers", msg_id)
            self._retry_counts.pop(task_id, None)
        except Exception as e:
            count = self._retry_counts.get(task_id, 0) + 1
            self._retry_counts[task_id] = count
            if count >= self._max_retries:
                self.dead_letters.append(task_data)
                if self._redis:
                    self._redis.xack(stream_key, "workers", msg_id)
                    try:
                        self._redis.lpush(f"tasks:{self._namespace}:dead", json.dumps(task_data))
                        self._redis.ltrim(f"tasks:{self._namespace}:dead", 0, 99)
                    except Exception:
                        pass
                self._retry_counts.pop(task_id, None)

    def _poll_memory(self):
        entries = self._publisher._memory.get(self._namespace, [])
        if not entries:
            return
        entry = entries.pop(0)
        task_id = entry.get("task_id", "")
        try:
            self._on_task(entry)
            self._retry_counts.pop(task_id, None)
        except Exception:
            count = self._retry_counts.get(task_id, 0) + 1
            self._retry_counts[task_id] = count
            if count >= self._max_retries:
                self.dead_letters.append(entry)
                self._retry_counts.pop(task_id, None)
            else:
                entries.append(entry)


def get_task_publisher() -> TaskStreamPublisher:
    """Get or create a singleton TaskStreamPublisher."""
    if not hasattr(get_task_publisher, "_instance"):
        redis_client = None
        try:
            import os, redis
            url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
            if url:
                redis_client = redis.from_url(url)
        except Exception:
            pass
        get_task_publisher._instance = TaskStreamPublisher(redis_client=redis_client)
    return get_task_publisher._instance
