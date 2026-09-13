"""Context Radar — proactively push relevant context to agents."""
from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from .config import IntelligenceConfig

logger = logging.getLogger(__name__)


@dataclass
class Suggestion:
    """A proactive context suggestion for an agent."""
    agent_id: str
    trigger: str       # source_changed | conflict_found | high_usefulness | related_decision
    message: str
    node_ids: List[str]
    suggestion_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = "pending"  # pending | delivered | accepted | dismissed
    created_at: float = field(default_factory=time.time)


class ContextRadar:
    """Tracks agent activity and suggests relevant context."""

    def __init__(self, config: IntelligenceConfig, event_bus):
        self._config = config
        self._bus = event_bus
        # agent_id → {session_id, topics: set, last_active, node_ids: set}
        self._agent_contexts: Dict[str, Dict[str, Any]] = {}
        # agent_id → list of pending suggestions
        self._suggestions: Dict[str, List[Suggestion]] = defaultdict(list)

    def track_activity(self, agent_id: str, session_id: str, topics: List[str],
                       node_ids: Optional[List[str]] = None):
        """Record what an agent is working on."""
        if agent_id not in self._agent_contexts:
            self._agent_contexts[agent_id] = {
                "session_id": session_id,
                "topics": set(),
                "node_ids": set(),
                "last_active": time.time(),
            }
        ctx = self._agent_contexts[agent_id]
        ctx["session_id"] = session_id
        ctx["topics"].update(topics)
        ctx["last_active"] = time.time()
        if node_ids:
            ctx["node_ids"].update(node_ids)

    def get_agent_context(self, agent_id: str) -> Dict[str, Any]:
        """Get an agent's current working context."""
        ctx = self._agent_contexts.get(agent_id, {})
        return {
            "session_id": ctx.get("session_id"),
            "topics": list(ctx.get("topics", set())),
            "node_ids": list(ctx.get("node_ids", set())),
            "last_active": ctx.get("last_active"),
        }

    def _is_relevant_to_agent(self, agent_id: str, event_topics: List[str]) -> bool:
        """Check if event topics overlap with agent's working context."""
        ctx = self._agent_contexts.get(agent_id)
        if not ctx:
            return False
        agent_topics = ctx.get("topics", set())
        event_set = set(t.lower() for t in event_topics)
        agent_set = set(t.lower() for t in agent_topics)
        # Simple keyword overlap — future: embedding similarity
        return bool(agent_set & event_set)

    async def on_source_changed(self, event: Dict[str, Any]):
        """Handle source_changed — suggest to agents working with that source."""
        node_id = event.get("node_id", "")
        source_uri = event.get("source_uri", "")
        for agent_id, ctx in self._agent_contexts.items():
            if node_id in ctx.get("node_ids", set()):
                suggestion = Suggestion(
                    agent_id=agent_id,
                    trigger="source_changed",
                    message=f"Source updated: {source_uri[:80]}",
                    node_ids=[node_id],
                )
                self._suggestions[agent_id].append(suggestion)

    async def on_feedback_received(self, event: Dict[str, Any]):
        """Handle feedback_received — adjust relevance weights for affected agents."""
        node_id = event.get("node_id", "")
        signal = event.get("signal", "")
        if signal == "critical":
            # Critical feedback means this node is important — suggest to other agents on same topics
            for agent_id, ctx in self._agent_contexts.items():
                if node_id not in ctx.get("node_ids", set()):
                    # Check topic overlap (agent doesn't have this node but works on related topics)
                    pass  # Phase 2: cross-agent suggestions based on feedback signals

    async def on_conflict_found(self, event: Dict[str, Any]):
        """Handle conflict_found — alert agents using conflicting nodes."""
        node_a = event.get("node_a_id", "")
        node_b = event.get("node_b_id", "")
        for agent_id, ctx in self._agent_contexts.items():
            node_ids = ctx.get("node_ids", set())
            if node_a in node_ids or node_b in node_ids:
                suggestion = Suggestion(
                    agent_id=agent_id,
                    trigger="conflict_found",
                    message=f"Conflict detected between nodes you're using",
                    node_ids=[node_a, node_b],
                )
                self._suggestions[agent_id].append(suggestion)

    def get_suggestions(self, agent_id: str, limit: Optional[int] = None) -> List[Suggestion]:
        """Get and mark suggestions as delivered."""
        max_s = limit or self._config.radar_max_suggestions_per_agent
        pending = [s for s in self._suggestions.get(agent_id, []) if s.status == "pending"]
        results = pending[:max_s]
        for s in results:
            s.status = "delivered"
        return results

    def get_stats(self) -> Dict[str, Any]:
        """Return radar stats for dashboard."""
        all_suggestions = [s for slist in self._suggestions.values() for s in slist]
        return {
            "active_agents": len(self._agent_contexts),
            "total_suggestions": len(all_suggestions),
            "pending": sum(1 for s in all_suggestions if s.status == "pending"),
            "delivered": sum(1 for s in all_suggestions if s.status == "delivered"),
            "accepted": sum(1 for s in all_suggestions if s.status == "accepted"),
        }
