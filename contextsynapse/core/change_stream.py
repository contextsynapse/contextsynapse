"""
Redis Streams change notification for multi-agent graph coordination.

When any worker mutates a graph (add/update/delete node or edge) it publishes
a ChangeEvent to a per-namespace Redis Stream. Other workers can subscribe via
consumer groups for exactly-once delivery.

Streams key pattern: "graph:{namespace}:changes"

Usage:
    from contextsynapse.core.change_stream import get_change_publisher, ChangeEvent

    pub = get_change_publisher()
    pub.publish(ChangeEvent(namespace="my_graph", operation="add_node",
                            node_id="n-123", label="Person", agent_id="agent-1"))

    # Subscriber (long-lived worker):
    from contextsynapse.core.change_stream import ChangeStreamSubscriber

    sub = ChangeStreamSubscriber()
    sub.subscribe("my_graph", lambda evt: print(evt))
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional Redis import
# ---------------------------------------------------------------------------

try:
    import redis as _redis_lib
    _REDIS_AVAILABLE = True
except ImportError:
    _redis_lib = None  # type: ignore[assignment]
    _REDIS_AVAILABLE = False

_DEFAULT_REDIS_URL = "redis://localhost:6379/0"
_STREAM_MAXLEN_DEFAULT = 100_000


# ---------------------------------------------------------------------------
# ChangeEvent
# ---------------------------------------------------------------------------

@dataclass
class ChangeEvent:
    """Represents a single mutation to a graph namespace."""

    namespace: str
    operation: str   # add_node | update_node | delete_node | add_edge | delete_edge
    node_id: str
    label: str = ""
    agent_id: str = ""
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # Serialization — Redis requires flat string dicts
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, str]:
        """Serialize to a flat string dict suitable for Redis XADD."""
        return {
            "ns": self.namespace,
            "op": self.operation,
            "nid": self.node_id,
            "lbl": self.label,
            "aid": self.agent_id,
            "ts": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict[str, str]) -> "ChangeEvent":
        """Deserialize from the flat string dict stored in Redis."""
        return cls(
            namespace=d.get("ns", ""),
            operation=d.get("op", ""),
            node_id=d.get("nid", ""),
            label=d.get("lbl", ""),
            agent_id=d.get("aid", ""),
            timestamp=d.get("ts", ""),
        )


# ---------------------------------------------------------------------------
# ChangeStreamPublisher
# ---------------------------------------------------------------------------

class ChangeStreamPublisher:
    """
    Publishes ChangeEvents to Redis Streams.

    If Redis is unavailable the publisher degrades gracefully: publish() is a
    silent no-op so callers never need to guard around it.
    """

    def __init__(self, redis_url: Optional[str] = None):
        self._redis: Optional[object] = None
        self._maxlen: int = int(
            os.environ.get("CONTEXTSYNAPSE_STREAM_MAXLEN") or os.environ.get("AICONTEXTDB_STREAM_MAXLEN", _STREAM_MAXLEN_DEFAULT)
        )

        if not _REDIS_AVAILABLE:
            logger.debug("redis package not installed — ChangeStreamPublisher disabled")
            return

        url = redis_url or os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL", _DEFAULT_REDIS_URL)
        try:
            client = _redis_lib.Redis.from_url(url, decode_responses=True)
            # Ping to verify connectivity now so we can set available = False early
            client.ping()
            self._redis = client
        except Exception as exc:
            logger.warning("ChangeStreamPublisher: Redis unavailable (%s) — operating in no-op mode", exc)
            self._redis = None

    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        """True when Redis is reachable and publishing will work."""
        return self._redis is not None

    def publish(self, event: ChangeEvent) -> None:
        """
        Publish a ChangeEvent to the namespace stream.

        Silently does nothing (with a warning log) when Redis is unavailable.
        """
        if self._redis is None:
            return

        stream_key = f"context:{event.namespace}:changes"
        try:
            self._redis.xadd(  # type: ignore[union-attr]
                stream_key,
                event.to_dict(),
                maxlen=self._maxlen,
                approximate=True,
            )
        except Exception as exc:
            logger.warning("ChangeStreamPublisher.publish failed: %s", exc)


# ---------------------------------------------------------------------------
# ChangeStreamSubscriber
# ---------------------------------------------------------------------------

class ChangeStreamSubscriber:
    """
    Subscribes to graph change streams using Redis consumer groups.

    Uses XREADGROUP for exactly-once delivery across multiple worker processes.
    """

    def __init__(
        self,
        redis_url: Optional[str] = None,
        consumer_group: str = "workers",
    ):
        self._redis: Optional[object] = None
        self._group = consumer_group
        self._consumer_name = f"worker_{os.getpid()}"
        self._running = False
        self._thread: Optional[threading.Thread] = None

        if not _REDIS_AVAILABLE:
            logger.debug("redis package not installed — ChangeStreamSubscriber disabled")
            return

        url = redis_url or os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL", _DEFAULT_REDIS_URL)
        try:
            client = _redis_lib.Redis.from_url(url, decode_responses=True)  # type: ignore[union-attr]
            client.ping()
            self._redis = client
        except Exception as exc:
            logger.warning(
                "ChangeStreamSubscriber: Redis unavailable (%s) — operating in no-op mode", exc
            )
            self._redis = None

    # ------------------------------------------------------------------

    def subscribe(self, namespace: str, callback: Callable[[ChangeEvent], None]) -> None:
        """
        Start listening to the change stream for *namespace*.

        Spawns a daemon thread. Returns immediately.
        Silent no-op when Redis is unavailable.
        """
        if self._redis is None:
            return

        stream_key = f"context:{namespace}:changes"
        self._ensure_consumer_group(stream_key)

        self._running = True
        self._thread = threading.Thread(
            target=self._listen,
            args=(stream_key, callback),
            daemon=True,
            name=f"change-stream-{namespace}",
        )
        self._thread.start()

    def _ensure_consumer_group(self, stream_key: str) -> None:
        """Create consumer group idempotently; ignore BUSYGROUP error."""
        try:
            self._redis.xgroup_create(  # type: ignore[union-attr]
                stream_key, self._group, id="$", mkstream=True
            )
        except Exception as exc:
            # BUSYGROUP means the group already exists — that's fine
            if "BUSYGROUP" not in str(exc):
                logger.warning("xgroup_create failed for %s: %s", stream_key, exc)

    def _listen(self, stream_key: str, callback: Callable[[ChangeEvent], None]) -> None:
        """Daemon thread: read messages, dispatch callback, acknowledge."""
        while self._running:
            try:
                results = self._redis.xreadgroup(  # type: ignore[union-attr]
                    self._group,
                    self._consumer_name,
                    {stream_key: ">"},
                    count=100,
                    block=1000,
                )
                if not results:
                    continue
                for _stream, messages in results:
                    for msg_id, fields in messages:
                        try:
                            evt = ChangeEvent.from_dict(fields)
                            callback(evt)
                        except Exception as cb_exc:
                            logger.warning("Change stream callback error: %s", cb_exc)
                        finally:
                            try:
                                self._redis.xack(stream_key, self._group, msg_id)  # type: ignore[union-attr]
                            except Exception as ack_exc:
                                logger.warning("xack failed: %s", ack_exc)
            except Exception as exc:
                if self._running:
                    logger.warning("ChangeStreamSubscriber._listen error: %s — retrying", exc)
                    time.sleep(1)

    def stop(self) -> None:
        """Signal the listener thread to stop. Safe to call without Redis."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)


# ---------------------------------------------------------------------------
# Global singleton publisher
# ---------------------------------------------------------------------------

_publisher: Optional[ChangeStreamPublisher] = None
_publisher_lock = threading.Lock()


def get_change_publisher() -> ChangeStreamPublisher:
    """Return the process-wide ChangeStreamPublisher (lazy-initialized singleton)."""
    global _publisher
    if _publisher is None:
        with _publisher_lock:
            if _publisher is None:
                _publisher = ChangeStreamPublisher()
    return _publisher
