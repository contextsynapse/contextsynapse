"""
PubSub Hub
==========
In-memory publish/subscribe for real-time context sharing between agents
via WebSocket connections.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class ContextEvent:
    """An event broadcast to subscribers of a session."""
    event_type: str       # context_added, context_updated, context_deleted, agent_joined, agent_left
    session_id: str
    agent_id: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self))


class PubSubHub:
    """
    In-memory pub/sub hub scoped by session_id.

    Subscribers are async callables (typically WebSocket.send_text).
    Each subscriber is identified by a connection_id.
    """

    def __init__(self):
        # session_id -> {connection_id -> send_callable}
        self._subscribers: Dict[str, Dict[str, Callable]] = defaultdict(dict)
        # connection_id -> (session_id, agent_id)
        self._connections: Dict[str, tuple] = {}
        # Optional Redis Streams forwarder
        self._redis_sync = None

    def set_redis_sync(self, redis_sync) -> None:
        """
        Attach a ``RedisStreamSync`` instance for cross-process event forwarding.

        When set, every ``publish()`` call also pushes the event to Redis Streams,
        so subscribers in other processes receive it.
        """
        self._redis_sync = redis_sync

    async def subscribe(
        self,
        session_id: str,
        connection_id: str,
        agent_id: str,
        send_fn: Callable,
    ):
        """Register a subscriber for a session."""
        self._subscribers[session_id][connection_id] = send_fn
        self._connections[connection_id] = (session_id, agent_id)

        # Broadcast agent_joined
        await self.publish(ContextEvent(
            event_type="agent_joined",
            session_id=session_id,
            agent_id=agent_id,
            payload={"connection_id": connection_id},
        ))

    async def unsubscribe(self, connection_id: str):
        """Remove a subscriber."""
        info = self._connections.pop(connection_id, None)
        if info is None:
            return
        session_id, agent_id = info
        self._subscribers[session_id].pop(connection_id, None)
        if not self._subscribers[session_id]:
            del self._subscribers[session_id]

        await self.publish(ContextEvent(
            event_type="agent_left",
            session_id=session_id,
            agent_id=agent_id,
            payload={"connection_id": connection_id},
        ))

    async def publish(self, event: ContextEvent):
        """Broadcast an event to all in-process subscribers and optionally to Redis."""
        subs = self._subscribers.get(event.session_id, {})

        if subs:
            message = event.to_json()
            dead: List[str] = []

            for conn_id, send_fn in subs.items():
                try:
                    await send_fn(message)
                except Exception:
                    dead.append(conn_id)

            # Clean up dead connections
            for conn_id in dead:
                self._subscribers[event.session_id].pop(conn_id, None)
                self._connections.pop(conn_id, None)

        # Forward to Redis Streams for cross-process subscribers
        if self._redis_sync is not None:
            try:
                await self._redis_sync.publish_change(event.session_id, event)
            except Exception as e:
                logger.warning(f"Redis forward failed: {e}")

    def get_subscribers(self, session_id: str) -> List[Dict[str, str]]:
        """Return list of {connection_id, agent_id} for a session."""
        result = []
        for conn_id in self._subscribers.get(session_id, {}):
            info = self._connections.get(conn_id)
            if info:
                result.append({"connection_id": conn_id, "agent_id": info[1]})
        return result

    def get_session_count(self, session_id: str) -> int:
        return len(self._subscribers.get(session_id, {}))


# Global singleton
pubsub_hub = PubSubHub()
