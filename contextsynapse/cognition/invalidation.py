"""InvalidationEngine — cascade invalidation through derivation chain."""
from __future__ import annotations

import logging
from typing import List, Set

from .events import CognitionEvent, CognitionEventStream
from .derivation import DerivationTracker

logger = logging.getLogger(__name__)


class InvalidationEngine:
    """Cascade context invalidation through the derivation graph."""

    def __init__(self, stream: CognitionEventStream, derivation: DerivationTracker, max_depth: int = 3):
        self._stream = stream
        self._derivation = derivation
        self._max_depth = max_depth

    def invalidate(self, node_id: str, reason: str = "", agent_id: str = "system") -> List[str]:
        """Invalidate a node and cascade to all derived nodes.

        Returns list of all invalidated node IDs (including the original).
        """
        invalidated: List[str] = []
        visited: Set[str] = set()
        self._cascade(node_id, 0, invalidated, visited)

        # Emit invalidation event
        self._stream.append(CognitionEvent(
            event_type="invalidate",
            agent_id=agent_id,
            session_id="",
            node_ids=invalidated,
            metadata={"root_cause": node_id, "reason": reason, "cascade_depth": len(invalidated)},
        ))

        logger.info("Invalidated %d nodes (root: %s, reason: %s)", len(invalidated), node_id[:12], reason)

        # AgentShield: penalize agents whose content was invalidated
        try:
            from contextsynapse.shield import get_agent_shield
            shield = get_agent_shield()
            derive_events = self._stream.get_events(event_type="derive", limit=200)
            penalized = set()
            for ev in derive_events:
                for nid in ev.node_ids:
                    if nid in invalidated and ev.agent_id not in penalized:
                        shield.on_invalidation_for_agent(ev.agent_id)
                        penalized.add(ev.agent_id)
        except Exception:
            pass

        return invalidated

    def _cascade(self, node_id: str, depth: int, result: List[str], visited: Set[str]):
        """BFS cascade through derivation graph."""
        if node_id in visited:
            return
        if depth > self._max_depth:
            return

        visited.add(node_id)
        result.append(node_id)

        # Find what was derived from this node
        derived = self._derivation.get_derived_from(node_id)
        for child_id in derived:
            self._cascade(child_id, depth + 1, result, visited)
