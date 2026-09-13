"""
Real-Time Collaboration Hub — WebSocket-based live graph editing.

Manages connected clients (humans + agents), broadcasts graph changes,
handles conflict resolution.

Usage:
    from contextsynapse.realtime.collaboration import CollaborationHub
    hub = CollaborationHub()
    await hub.connect(websocket, session_id, user_id)
    await hub.broadcast_change(session_id, change_event)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class CollaborationHub:
    """Manages real-time collaboration sessions."""

    def __init__(self):
        # session_id → set of (websocket, user_id) tuples
        self._connections: Dict[str, Set] = defaultdict(set)
        # session_id → list of recent changes (ring buffer)
        self._recent_changes: Dict[str, List[Dict]] = defaultdict(list)
        # user_id → {cursor_position, active_node, last_seen}
        self._cursors: Dict[str, Dict[str, Any]] = {}
        self._max_history = 50

    async def connect(self, websocket, session_id: str, user_id: str, user_name: str = ""):
        """Register a WebSocket client for a session."""
        self._connections[session_id].add((websocket, user_id))
        self._cursors[user_id] = {
            "session_id": session_id,
            "user_name": user_name or user_id[:12],
            "active_node": None,
            "last_seen": time.time(),
        }

        # Notify others
        await self._broadcast(session_id, {
            "type": "user_joined",
            "user_id": user_id,
            "user_name": user_name or user_id[:12],
            "users_online": self._get_online_users(session_id),
        }, exclude=user_id)

        # Send recent history to the new joiner
        try:
            history = self._recent_changes.get(session_id, [])[-20:]
            await websocket.send_json({
                "type": "sync",
                "changes": history,
                "users_online": self._get_online_users(session_id),
            })
        except Exception:
            pass

        logger.info("[COLLAB] %s joined session %s (%d online)",
                     user_name or user_id[:12], session_id[:12],
                     len(self._connections[session_id]))

    async def disconnect(self, websocket, session_id: str, user_id: str):
        """Remove a WebSocket client."""
        self._connections[session_id].discard((websocket, user_id))
        self._cursors.pop(user_id, None)

        await self._broadcast(session_id, {
            "type": "user_left",
            "user_id": user_id,
            "users_online": self._get_online_users(session_id),
        })

    async def broadcast_change(self, session_id: str, change: Dict[str, Any]):
        """Broadcast a graph change to all connected clients."""
        change["timestamp"] = time.time()
        self._recent_changes[session_id].append(change)
        if len(self._recent_changes[session_id]) > self._max_history:
            self._recent_changes[session_id] = self._recent_changes[session_id][-self._max_history:]

        await self._broadcast(session_id, change)

    async def update_cursor(self, session_id: str, user_id: str, active_node: str = None):
        """Update a user's cursor position (which node they're looking at)."""
        if user_id in self._cursors:
            self._cursors[user_id]["active_node"] = active_node
            self._cursors[user_id]["last_seen"] = time.time()

        await self._broadcast(session_id, {
            "type": "cursor_update",
            "user_id": user_id,
            "active_node": active_node,
        }, exclude=user_id)

    def _get_online_users(self, session_id: str) -> List[Dict[str, Any]]:
        """Get list of online users in a session."""
        users = []
        for _, uid in self._connections.get(session_id, set()):
            cursor = self._cursors.get(uid, {})
            users.append({
                "user_id": uid,
                "user_name": cursor.get("user_name", uid[:12]),
                "active_node": cursor.get("active_node"),
            })
        return users

    async def _broadcast(self, session_id: str, message: Dict[str, Any], exclude: str = None):
        """Send message to all connected clients in a session."""
        dead = set()
        for ws, uid in self._connections.get(session_id, set()):
            if exclude and uid == exclude:
                continue
            try:
                await ws.send_json(message)
            except Exception:
                dead.add((ws, uid))

        # Clean dead connections
        for d in dead:
            self._connections[session_id].discard(d)

    def get_session_info(self, session_id: str) -> Dict[str, Any]:
        """Get info about a collaboration session."""
        return {
            "session_id": session_id,
            "users_online": len(self._connections.get(session_id, set())),
            "recent_changes": len(self._recent_changes.get(session_id, [])),
            "users": self._get_online_users(session_id),
        }


# Singleton
_hub = None

def get_collaboration_hub() -> CollaborationHub:
    global _hub
    if _hub is None:
        _hub = CollaborationHub()
    return _hub
