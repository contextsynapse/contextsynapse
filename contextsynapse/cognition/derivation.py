"""DerivationTracker — tracks what context produced what outputs (lineage)."""

from __future__ import annotations

from typing import List

from .events import CognitionEvent, CognitionEventStream


class DerivationTracker:
    """Records and queries derivation relationships between nodes.

    A derivation records that an output node was produced from one or more
    source nodes. This enables forward lineage (what did this source produce?)
    and backward lineage (what sources produced this output?).
    """

    def __init__(self, stream: CognitionEventStream):
        self._stream = stream

    def record_derivation(
        self,
        agent_id: str,
        session_id: str,
        output_node_id: str,
        source_node_ids: List[str],
    ) -> CognitionEvent:
        """Emit a 'derive' event linking output to its sources."""
        event = CognitionEvent(
            event_type="derive",
            agent_id=agent_id,
            session_id=session_id,
            node_ids=[output_node_id],
            derived_from=list(source_node_ids),
        )
        self._stream.append(event)
        return event

    def get_lineage(self, node_id: str, limit: int = 50) -> List[str]:
        """Return source node IDs that the given node was derived from."""
        events = self._stream.get_events(event_type="derive", limit=limit)
        sources: List[str] = []
        for ev in events:
            if node_id in ev.node_ids:
                sources.extend(ev.derived_from)
        return sources

    def get_derived_from(self, source_node_id: str, limit: int = 100) -> List[str]:
        """Return output node IDs that were derived from the given source."""
        events = self._stream.get_events(event_type="derive", limit=limit)
        outputs: List[str] = []
        for ev in events:
            if source_node_id in ev.derived_from:
                outputs.extend(ev.node_ids)
        return outputs
