"""
A2A SSE Streaming Transport
=============================
Server-Sent Events transport for real-time A2A task updates.
Integrates with PubSubHub to stream task state changes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator, Optional

logger = logging.getLogger(__name__)


class SSETransport:
    """SSE subscriber that yields formatted Server-Sent Events."""

    def __init__(self, pubsub=None):
        self._pubsub = pubsub
        self._queues: dict = {}  # connection_id → asyncio.Queue

    async def subscribe(
        self, task_id: str, timeout: float = 300.0
    ) -> AsyncGenerator[str, None]:
        """Subscribe to SSE events for a specific task.

        Yields SSE-formatted strings: ``event: <type>\\ndata: <json>\\n\\n``
        """
        queue: asyncio.Queue = asyncio.Queue()
        conn_id = f"sse_{task_id}_{id(queue)}"
        self._queues[conn_id] = queue

        # Register with PubSub if available
        if self._pubsub:
            async def _send(data: str):
                try:
                    event = json.loads(data)
                    # Only forward events for this task
                    if event.get("data", {}).get("task_id") == task_id:
                        await queue.put(data)
                except Exception:
                    await queue.put(data)

            # Use session_id="" to subscribe to all events (filter in _send)
            await self._pubsub.subscribe("", conn_id, "", _send)

        try:
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=timeout)
                    # Parse event type from data
                    try:
                        parsed = json.loads(data)
                        event_type = parsed.get("event_type", "TaskStatusUpdateEvent")
                        event_data = parsed.get("data", parsed)
                    except (json.JSONDecodeError, TypeError):
                        event_type = "message"
                        event_data = {"text": str(data)}

                    yield format_sse(event_type, event_data)

                except asyncio.TimeoutError:
                    # Send keepalive
                    yield ": keepalive\n\n"

        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            self._queues.pop(conn_id, None)
            if self._pubsub:
                try:
                    await self._pubsub.unsubscribe("", conn_id)
                except Exception:
                    pass

    async def push_event(self, task_id: str, event_type: str, data: dict):
        """Push an event to all SSE subscribers watching this task."""
        payload = json.dumps({"event_type": event_type, "data": {**data, "task_id": task_id}})
        for conn_id, queue in list(self._queues.items()):
            if task_id in conn_id:
                try:
                    await queue.put(payload)
                except Exception:
                    pass


def format_sse(event_type: str, data: dict) -> str:
    """Format an SSE event string."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
