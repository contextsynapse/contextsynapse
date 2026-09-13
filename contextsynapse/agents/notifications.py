"""
Agent Notifications — notify agents when relevant events happen.

Uses Redis pub/sub for real-time notifications.
Agents can subscribe to events for their namespace/session.

Events:
- task_assigned: New task assigned to this agent
- finding_added: Another agent wrote a Finding
- task_completed: A task was completed
- goal_dispatched: A new goal was decomposed into tasks
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_subscribers: Dict[str, List[Callable]] = {}  # channel → [callbacks]


def notify_agents(
    event_type: str,
    data: Dict[str, Any],
    namespace: str = "",
    target_agent: str = "",
):
    """Send a notification to agents.

    Args:
        event_type: "task_assigned", "finding_added", etc.
        data: Event payload
        namespace: Graph namespace
        target_agent: Specific agent to notify (empty = all)
    """
    message = {
        "event": event_type,
        "data": data,
        "namespace": namespace,
        "target_agent": target_agent,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Redis pub/sub
    try:
        import redis
        url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
        if url:
            r = redis.from_url(url)
            channel = f"agent:notifications:{namespace}" if namespace else "agent:notifications:global"
            r.publish(channel, json.dumps(message, default=str))
            logger.debug("[NOTIFY] Published %s to %s", event_type, channel)
    except Exception:
        pass

    # In-memory subscribers
    for key in [f"agent:notifications:{namespace}", "agent:notifications:global"]:
        for callback in _subscribers.get(key, []):
            try:
                callback(message)
            except Exception:
                pass


def subscribe(namespace: str, callback: Callable):
    """Subscribe to notifications for a namespace."""
    key = f"agent:notifications:{namespace}"
    if key not in _subscribers:
        _subscribers[key] = []
    _subscribers[key].append(callback)


def get_pending_notifications(
    agent_name: str,
    namespace: str = "",
    limit: int = 10,
) -> List[Dict]:
    """Get pending notifications for an agent from Redis list."""
    try:
        import redis
        url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
        if not url:
            return []
        r = redis.from_url(url, decode_responses=True)
        key = f"agent:inbox:{agent_name}:{namespace}" if namespace else f"agent:inbox:{agent_name}"
        items = r.lrange(key, 0, limit - 1)
        # Clear read notifications
        r.ltrim(key, limit, -1)
        return [json.loads(item) for item in items]
    except Exception:
        return []


def queue_notification(agent_name: str, message: Dict, namespace: str = ""):
    """Queue a notification for an agent (persisted in Redis list)."""
    try:
        import redis
        url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
        if not url:
            return
        r = redis.from_url(url, decode_responses=True)
        key = f"agent:inbox:{agent_name}:{namespace}" if namespace else f"agent:inbox:{agent_name}"
        r.rpush(key, json.dumps(message, default=str))
        r.expire(key, 86400)  # 24h TTL
    except Exception:
        pass
