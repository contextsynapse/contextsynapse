"""AIContextDB WebSocket subscription."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, AsyncIterator

from .models import Event

if TYPE_CHECKING:
    from .client import AIContextDB


async def subscribe_session(client: AIContextDB, session_id: str) -> AsyncIterator[Event]:
    """Subscribe to real-time events for a session.

    Yields Event objects as they arrive.
    """
    import websockets

    base = client.base_url.replace("http://", "ws://").replace("https://", "wss://")
    url = f"{base}/context/sessions/{session_id}/ws"

    async with websockets.connect(url) as ws:
        # Handshake
        await ws.send(json.dumps({
            "agent_id": client._agent_id or "sdk-subscriber",
            "type": "subscribe",
        }))

        async for raw in ws:
            try:
                data = json.loads(raw)
                yield Event(
                    event_type=data.get("event_type", "unknown"),
                    agent_id=data.get("agent_id"),
                    content_type=data.get("content_type"),
                    data=data,
                )
            except json.JSONDecodeError:
                continue
