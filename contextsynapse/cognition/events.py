"""CognitionEvent and CognitionEventStream — append-only event log for cognitive reliability."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List


@dataclass
class CognitionEvent:
    """A single cognitive event recording an agent's interaction with graph data."""

    event_type: str
    agent_id: str
    session_id: str
    node_ids: List[str]
    derived_from: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CognitionEvent:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class CognitionEventStream:
    """Append-only event stream backed by Redis Streams or in-memory list."""

    REDIS_MAXLEN = 100_000

    def __init__(self, namespace: str = "default", redis_client=None):
        self.namespace = namespace
        self.redis = redis_client
        self._key = f"cognition:events:{namespace}"
        self._memory: List[CognitionEvent] = []

    # -- write --

    def append(self, event: CognitionEvent) -> None:
        if self.redis:
            payload = {
                k: json.dumps(v) if isinstance(v, (list, dict)) else str(v)
                for k, v in event.to_dict().items()
            }
            self.redis.xadd(self._key, payload, maxlen=self.REDIS_MAXLEN)
        else:
            self._memory.append(event)

    # -- read --

    def get_events(
        self,
        agent_id: str = "",
        event_type: str = "",
        node_id: str = "",
        limit: int = 100,
    ) -> List[CognitionEvent]:
        if self.redis:
            return self._query_redis(agent_id, event_type, node_id, limit)
        return self._query_memory(agent_id, event_type, node_id, limit)

    def get_recent_reads(self, agent_id: str, limit: int = 20) -> List[str]:
        """Convenience: get unique node_ids recently read by agent."""
        events = self.get_events(agent_id=agent_id, event_type="node_read", limit=limit)
        node_ids: List[str] = []
        for e in events:
            for nid in e.node_ids:
                if nid not in node_ids:
                    node_ids.append(nid)
        return node_ids

    # -- private: in-memory --

    def _query_memory(
        self, agent_id: str, event_type: str, node_id: str, limit: int
    ) -> List[CognitionEvent]:
        results: List[CognitionEvent] = []
        for ev in self._memory:
            if agent_id and ev.agent_id != agent_id:
                continue
            if event_type and ev.event_type != event_type:
                continue
            if node_id and node_id not in ev.node_ids:
                continue
            results.append(ev)
            if len(results) >= limit:
                break
        return results

    # -- private: Redis --

    def _query_redis(
        self, agent_id: str, event_type: str, node_id: str, limit: int
    ) -> List[CognitionEvent]:
        # Read a larger window then filter client-side (Redis Streams lack field filters)
        raw = self.redis.xrevrange(self._key, count=limit * 5)
        results: List[CognitionEvent] = []
        for _msg_id, fields in raw:
            decoded = {}
            for k, v in fields.items():
                key = k.decode() if isinstance(k, bytes) else k
                val = v.decode() if isinstance(v, bytes) else v
                decoded[key] = val

            for list_field in ("node_ids", "derived_from"):
                if list_field in decoded:
                    try:
                        decoded[list_field] = json.loads(decoded[list_field])
                    except (json.JSONDecodeError, TypeError):
                        decoded[list_field] = []
            if "metadata" in decoded:
                try:
                    decoded["metadata"] = json.loads(decoded["metadata"])
                except (json.JSONDecodeError, TypeError):
                    decoded["metadata"] = {}

            if agent_id and decoded.get("agent_id") != agent_id:
                continue
            if event_type and decoded.get("event_type") != event_type:
                continue
            if node_id and node_id not in decoded.get("node_ids", []):
                continue

            results.append(CognitionEvent.from_dict(decoded))
            if len(results) >= limit:
                break
        results.reverse()
        return results
