"""ReadTracker — convenience layer for tracking node reads across agents."""

from __future__ import annotations

from typing import List

from .events import CognitionEvent, CognitionEventStream


class ReadTracker:
    """Records and queries node-read events on a CognitionEventStream."""

    def __init__(self, stream: CognitionEventStream):
        self._stream = stream

    def record_read(
        self, agent_id: str, session_id: str, node_ids: List[str]
    ) -> CognitionEvent:
        """Emit a 'read' event for the given node_ids."""
        event = CognitionEvent(
            event_type="read",
            agent_id=agent_id,
            session_id=session_id,
            node_ids=node_ids,
        )
        self._stream.append(event)
        return event

    def get_consumers(self, node_id: str) -> List[str]:
        """Return unique agent_ids that have read the given node."""
        events = self._stream.get_events(event_type="read", node_id=node_id)
        seen: set[str] = set()
        result: List[str] = []
        for ev in events:
            if ev.agent_id not in seen:
                seen.add(ev.agent_id)
                result.append(ev.agent_id)
        return result

    def get_consumed_by(self, agent_id: str) -> List[str]:
        """Return unique node_ids read by the given agent."""
        events = self._stream.get_events(agent_id=agent_id, event_type="read")
        seen: set[str] = set()
        result: List[str] = []
        for ev in events:
            for nid in ev.node_ids:
                if nid not in seen:
                    seen.add(nid)
                    result.append(nid)
        return result
