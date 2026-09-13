"""
Event Bus
=========
Simple in-process event bus for broadcasting events to connected WebSocket clients.
Events are stored in a bounded deque so clients can fetch recent history.

Usage::

    from contextsynapse.api.events import event_bus
    event_bus.emit("graph_created", {"name": "mydb"})
"""

import asyncio
import json
import logging
import time
from collections import deque
from typing import Any, Dict, List, Set

logger = logging.getLogger(__name__)


class EventBus:
    """Broadcast events to WebSocket subscribers, sync listeners, and keep recent history."""

    def __init__(self, max_history: int = 100):
        self._subscribers: Set[asyncio.Queue] = set()
        self._listeners: Dict[str, List] = {}  # event_type → [callback, ...]
        self._history: deque = deque(maxlen=max_history)

    def on(self, event_type: str, callback):
        """Register a sync callback for an event type.

        Usage:
            event_bus.on("graph_deleted", lambda data: registry.cleanup(data["name"]))
        """
        if event_type not in self._listeners:
            self._listeners[event_type] = []
        self._listeners[event_type].append(callback)

    def emit(self, event_type: str, data: Dict[str, Any] | None = None):
        """Emit an event to all subscribers and listeners."""
        event = {
            "type": event_type,
            "data": data or {},
            "timestamp": time.time(),
        }
        self._history.append(event)

        # Sync listeners (metadata handler, cleanup, etc.)
        for cb in self._listeners.get(event_type, []):
            try:
                cb(event["data"])
            except Exception as e:
                logger.debug("Event listener failed for %s: %s", event_type, e)

        # Wildcard listeners
        for cb in self._listeners.get("*", []):
            try:
                cb(event)
            except Exception:
                pass

        # Async WebSocket subscribers
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def subscribe(self) -> asyncio.Queue:
        """Create a new subscription queue."""
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        """Remove a subscription queue."""
        self._subscribers.discard(q)

    def recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return recent events."""
        items = list(self._history)
        return items[-limit:]


# Global singleton
event_bus = EventBus()
