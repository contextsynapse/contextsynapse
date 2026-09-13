"""
SessionMemory — Tier 2: Per-Session Cross-Agent Context
=======================================================
Consolidated view of all agent activity within a session.
Aggregates events from TaskEventBus + CognitionEventStream
into a ring buffer + per-agent signal state.

Key layout (Redis):
    contextcore:sm:{namespace}:{session_id}:events   → List (ring buffer, max 100)
    contextcore:sm:{namespace}:{session_id}:signals   → Hash (agent_id → JSON signal)
    contextcore:sm:{namespace}:{session_id}:tasks     → Hash (task_id → JSON state)

Falls back to in-memory dicts when Redis is unavailable.
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PREFIX = "contextcore:sm"
_MAX_EVENTS = 100
_TTL_S = 300  # 5 minutes


@dataclass
class AgentSignal:
    """Latest signal from an agent — a discovery, decision, or action."""
    agent_id: str
    signal_type: str  # "finding", "decision", "action", "question"
    content: str
    confidence: float = 0.5
    timestamp: float = 0.0
    node_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "signal_type": self.signal_type,
            "content": self.content,
            "confidence": self.confidence,
            "timestamp": self.timestamp,
            "node_ids": self.node_ids,
        }


@dataclass
class TaskState:
    """Current state of a task in the session."""
    task_id: str
    status: str  # "open", "claimed", "in_progress", "completed", "failed"
    assigned_to: str = ""
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "assigned_to": self.assigned_to,
            "updated_at": self.updated_at,
        }


class SessionMemory:
    """Tier 2 memory — per-session consolidated state for cross-agent signals."""

    def __init__(self, redis_client=None, ttl_s: int = _TTL_S):
        self._redis = redis_client
        self._ttl_s = ttl_s
        # In-memory fallback
        self._events: Dict[str, deque] = {}  # key → deque of events
        self._signals: Dict[str, Dict[str, dict]] = {}  # key → {agent_id: signal}
        self._tasks: Dict[str, Dict[str, dict]] = {}  # key → {task_id: state}

    def _key(self, namespace: str, session_id: str, suffix: str) -> str:
        return f"{_PREFIX}:{namespace}:{session_id}:{suffix}"

    # ── Record Events ──────────────────────────────────────────────

    def record_event(self, namespace: str, session_id: str,
                     event_type: str, agent_id: str, data: Dict[str, Any] = None):
        """Record an event in the session ring buffer."""
        event = {
            "type": event_type,
            "agent_id": agent_id,
            "data": data or {},
            "ts": time.time(),
        }

        if self._redis:
            try:
                key = self._key(namespace, session_id, "events")
                self._redis.lpush(key, json.dumps(event))
                self._redis.ltrim(key, 0, _MAX_EVENTS - 1)
                self._redis.expire(key, self._ttl_s)
                return
            except Exception:
                pass

        # In-memory fallback
        fk = f"{namespace}:{session_id}"
        if fk not in self._events:
            self._events[fk] = deque(maxlen=_MAX_EVENTS)
        self._events[fk].appendleft(event)

    def record_signal(self, namespace: str, session_id: str, signal: AgentSignal):
        """Record or update the latest signal from an agent."""
        signal.timestamp = signal.timestamp or time.time()

        if self._redis:
            try:
                key = self._key(namespace, session_id, "signals")
                self._redis.hset(key, signal.agent_id, json.dumps(signal.to_dict()))
                self._redis.expire(key, self._ttl_s)
                return
            except Exception:
                pass

        fk = f"{namespace}:{session_id}"
        self._signals.setdefault(fk, {})[signal.agent_id] = signal.to_dict()

    def record_task_state(self, namespace: str, session_id: str, state: TaskState):
        """Update task state in session memory."""
        state.updated_at = state.updated_at or time.time()

        if self._redis:
            try:
                key = self._key(namespace, session_id, "tasks")
                self._redis.hset(key, state.task_id, json.dumps(state.to_dict()))
                self._redis.expire(key, self._ttl_s)
                return
            except Exception:
                pass

        fk = f"{namespace}:{session_id}"
        self._tasks.setdefault(fk, {})[state.task_id] = state.to_dict()

    # ── Read ───────────────────────────────────────────────────────

    def get_recent(self, namespace: str, session_id: str, limit: int = 20) -> List[Dict]:
        """Get recent events from the ring buffer."""
        if self._redis:
            try:
                key = self._key(namespace, session_id, "events")
                raw = self._redis.lrange(key, 0, limit - 1)
                return [json.loads(r) for r in (raw or [])]
            except Exception:
                pass

        fk = f"{namespace}:{session_id}"
        events = list(self._events.get(fk, []))
        return events[:limit]

    def get_agent_signals(self, namespace: str, session_id: str,
                          agent_id: str = None) -> List[Dict]:
        """Get latest signals — all agents or a specific one."""
        if self._redis:
            try:
                key = self._key(namespace, session_id, "signals")
                if agent_id:
                    raw = self._redis.hget(key, agent_id)
                    return [json.loads(raw)] if raw else []
                all_raw = self._redis.hgetall(key)
                return [json.loads(v) for v in (all_raw or {}).values()]
            except Exception:
                pass

        fk = f"{namespace}:{session_id}"
        signals = self._signals.get(fk, {})
        if agent_id:
            s = signals.get(agent_id)
            return [s] if s else []
        return list(signals.values())

    def get_task_states(self, namespace: str, session_id: str) -> Dict[str, Dict]:
        """Get current task states."""
        if self._redis:
            try:
                key = self._key(namespace, session_id, "tasks")
                raw = self._redis.hgetall(key)
                return {k: json.loads(v) for k, v in (raw or {}).items()}
            except Exception:
                pass

        fk = f"{namespace}:{session_id}"
        return dict(self._tasks.get(fk, {}))

    def get_context_for_agent(self, namespace: str, session_id: str,
                              agent_id: str) -> Dict[str, Any]:
        """Build a consolidated context view for an agent — used by orient() fallthrough.

        Returns recent events, cross-agent signals, and task states.
        """
        recent = self.get_recent(namespace, session_id, limit=10)
        signals = self.get_agent_signals(namespace, session_id)
        # Filter out this agent's own signals — they want cross-agent intel
        cross_signals = [s for s in signals if s.get("agent_id") != agent_id]
        tasks = self.get_task_states(namespace, session_id)

        return {
            "recent_events": recent,
            "cross_agent_signals": cross_signals,
            "task_states": tasks,
            "session_id": session_id,
        }


class AccessTracker:
    """Track node access frequency for auto-promotion to working memory.

    Redis hash: contextcore:access:{namespace} with HINCRBY per node_id.
    TTL resets on each increment (sliding window).
    """

    def __init__(self, redis_client=None, ttl_s: int = 300):
        self._redis = redis_client
        self._ttl_s = ttl_s
        self._local: Dict[str, Dict[str, int]] = {}  # namespace → {node_id: count}

    def _key(self, namespace: str) -> str:
        return f"contextcore:access:{namespace}"

    def increment(self, namespace: str, node_ids: List[str]):
        """Record access to one or more nodes."""
        if not node_ids:
            return

        if self._redis:
            try:
                key = self._key(namespace)
                pipe = self._redis.pipeline()
                for nid in node_ids:
                    pipe.hincrby(key, nid, 1)
                pipe.expire(key, self._ttl_s)
                pipe.execute()
                return
            except Exception:
                pass

        counts = self._local.setdefault(namespace, {})
        for nid in node_ids:
            counts[nid] = counts.get(nid, 0) + 1

    def get_hot_nodes(self, namespace: str, threshold: int = 3) -> List[str]:
        """Return node IDs accessed >= threshold times."""
        if self._redis:
            try:
                key = self._key(namespace)
                raw = self._redis.hgetall(key)
                return [
                    nid for nid, count in (raw or {}).items()
                    if int(count) >= threshold
                ]
            except Exception:
                pass

        counts = self._local.get(namespace, {})
        return [nid for nid, c in counts.items() if c >= threshold]

    def reset(self, namespace: str):
        """Clear access counts for a namespace."""
        if self._redis:
            try:
                self._redis.delete(self._key(namespace))
            except Exception:
                pass
        self._local.pop(namespace, None)


# ── Singletons ─────────────────────────────────────────────────────

_session_memory: Optional[SessionMemory] = None
_access_tracker: Optional[AccessTracker] = None


def get_session_memory() -> SessionMemory:
    global _session_memory
    if _session_memory is None:
        redis_client = None
        try:
            import os, redis
            url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
            if url:
                redis_client = redis.from_url(url, decode_responses=True)
        except Exception:
            pass
        _session_memory = SessionMemory(redis_client=redis_client)
    return _session_memory


def get_access_tracker() -> AccessTracker:
    global _access_tracker
    if _access_tracker is None:
        redis_client = None
        try:
            import os, redis
            url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
            if url:
                redis_client = redis.from_url(url, decode_responses=True)
        except Exception:
            pass
        _access_tracker = AccessTracker(redis_client=redis_client)
    return _access_tracker
