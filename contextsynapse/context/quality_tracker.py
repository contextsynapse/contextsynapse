"""
Context Quality Tracker
========================
Measures whether context delivered to agents was actually useful.

Tracks: what was delivered → what the agent did next → was context referenced?

Metrics:
- relevance_score: Did the agent's next actions relate to delivered content?
- freshness_score: How old were the delivered nodes?
- coverage_score: What percentage of the graph was represented?
- waste_ratio: Tokens delivered but never referenced by agent actions.

Usage::

    tracker = ContextQualityTracker()

    # When context is delivered
    tracker.record_delivery("agent-1", ["node-a", "node-b"], token_count=2000)

    # After each agent tool call
    tracker.record_agent_action("agent-1", "search_nodes", {"query": "AQI"})

    # Compute quality
    report = tracker.compute_quality("agent-1")
    print(report.relevance_score, report.waste_ratio)
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ContextDelivery:
    """Record of context delivered to an agent."""
    delivery_id: str
    agent_id: str
    timestamp: float
    node_ids: List[str]
    node_labels: List[str]
    token_count: int
    total_graph_nodes: int = 0


@dataclass
class AgentAction:
    """Record of what an agent did after receiving context."""
    agent_id: str
    timestamp: float
    tool_name: str
    params: Dict[str, Any]
    keywords: List[str] = field(default_factory=list)


@dataclass
class ContextUsageReport:
    """Quality metrics for context delivery."""
    relevance_score: float = 0.0    # 0-1: did agent's actions relate to delivered content?
    freshness_score: float = 0.0    # 0-1: how fresh were delivered nodes?
    coverage_score: float = 0.0     # 0-1: what % of graph was represented?
    waste_ratio: float = 0.0        # 0-1: tokens not referenced (higher = more waste)
    deliveries_count: int = 0
    actions_count: int = 0
    total_tokens_delivered: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "relevance_score": round(self.relevance_score, 3),
            "freshness_score": round(self.freshness_score, 3),
            "coverage_score": round(self.coverage_score, 3),
            "waste_ratio": round(self.waste_ratio, 3),
            "deliveries_count": self.deliveries_count,
            "actions_count": self.actions_count,
            "total_tokens_delivered": self.total_tokens_delivered,
        }


class ContextQualityTracker:
    """Tracks context deliveries and agent actions to compute quality metrics."""

    def __init__(self, window_seconds: int = 600):
        self._window = window_seconds
        # agent_id → list of deliveries
        self._deliveries: Dict[str, List[ContextDelivery]] = defaultdict(list)
        # agent_id → list of actions
        self._actions: Dict[str, List[AgentAction]] = defaultdict(list)
        self._delivery_counter = 0

    def record_delivery(
        self,
        agent_id: str,
        node_ids: List[str],
        token_count: int,
        node_labels: Optional[List[str]] = None,
        total_graph_nodes: int = 0,
    ) -> str:
        """Record a context delivery to an agent.

        Called when ContextHub exports context (to_prompt, to_messages).
        """
        self._delivery_counter += 1
        did = f"del_{self._delivery_counter}"
        delivery = ContextDelivery(
            delivery_id=did,
            agent_id=agent_id,
            timestamp=time.time(),
            node_ids=node_ids or [],
            node_labels=node_labels or [],
            token_count=token_count,
            total_graph_nodes=total_graph_nodes,
        )
        self._deliveries[agent_id].append(delivery)
        self._prune(agent_id)
        return did

    def record_agent_action(
        self,
        agent_id: str,
        tool_name: str,
        params: Dict[str, Any],
    ):
        """Record an agent tool call after context delivery.

        Called after ToolRegistry.dispatch() to track what agent did with context.
        """
        # Extract keywords from tool params for relevance matching
        keywords = []
        for v in params.values():
            if isinstance(v, str) and len(v) > 2:
                keywords.extend(w.lower() for w in v.split() if len(w) > 2)

        action = AgentAction(
            agent_id=agent_id,
            timestamp=time.time(),
            tool_name=tool_name,
            params=params,
            keywords=keywords,
        )
        self._actions[agent_id].append(action)
        self._prune(agent_id)

    def compute_quality(
        self,
        agent_id: str,
        window_seconds: Optional[int] = None,
    ) -> ContextUsageReport:
        """Compute quality metrics for an agent's recent context deliveries.

        Metrics:
        - relevance: keyword overlap between delivered node content and agent actions
        - freshness: average age of delivered nodes (inverse of staleness)
        - coverage: ratio of delivered nodes to total graph nodes
        - waste: ratio of tokens in nodes never referenced by agent actions
        """
        window = window_seconds or self._window
        cutoff = time.time() - window

        deliveries = [d for d in self._deliveries.get(agent_id, []) if d.timestamp > cutoff]
        actions = [a for a in self._actions.get(agent_id, []) if a.timestamp > cutoff]

        if not deliveries:
            return ContextUsageReport()

        # ── Relevance: do agent actions reference delivered content? ──
        delivered_node_ids = set()
        delivered_labels = set()
        for d in deliveries:
            delivered_node_ids.update(d.node_ids)
            delivered_labels.update(d.node_labels)

        # Check if agent searched for labels/content that was delivered
        action_keywords = set()
        action_labels = set()
        for a in actions:
            action_keywords.update(a.keywords)
            label = a.params.get("label", "")
            if label:
                action_labels.add(label.lower())

        # Relevance = overlap between delivered labels and action labels/keywords
        delivered_labels_lower = {l.lower() for l in delivered_labels}
        if delivered_labels_lower:
            label_overlap = len(delivered_labels_lower & action_labels) / len(delivered_labels_lower)
        else:
            label_overlap = 0.0

        # Also check keyword overlap between delivered node IDs mentioned in params
        node_refs = sum(1 for a in actions for v in a.params.values()
                        if isinstance(v, str) and any(nid[:8] in v for nid in delivered_node_ids))
        node_ref_ratio = min(1.0, node_refs / max(len(delivered_node_ids), 1))

        relevance = (label_overlap * 0.6 + node_ref_ratio * 0.4)

        # ── Freshness: how recent were deliveries? ──
        now = time.time()
        ages = [now - d.timestamp for d in deliveries]
        avg_age = sum(ages) / len(ages) if ages else 0
        # Normalize: 0 age = 1.0 freshness, window age = 0.0
        freshness = max(0.0, 1.0 - (avg_age / window))

        # ── Coverage: what % of graph was represented? ──
        total_graph = max(d.total_graph_nodes for d in deliveries) if deliveries else 0
        if total_graph > 0:
            coverage = min(1.0, len(delivered_node_ids) / total_graph)
        else:
            coverage = 0.0

        # ── Waste: tokens delivered but agent didn't use ──
        total_tokens = sum(d.token_count for d in deliveries)
        # If agent took no actions after delivery, waste = 1.0
        # If agent referenced all content, waste = 0.0
        if not actions:
            waste = 1.0
        else:
            # Estimate: if agent only searched for 1 label out of 5 delivered, waste = 0.8
            used_fraction = relevance  # reuse relevance as proxy for usage
            waste = max(0.0, 1.0 - used_fraction)

        return ContextUsageReport(
            relevance_score=round(relevance, 3),
            freshness_score=round(freshness, 3),
            coverage_score=round(coverage, 3),
            waste_ratio=round(waste, 3),
            deliveries_count=len(deliveries),
            actions_count=len(actions),
            total_tokens_delivered=total_tokens,
        )

    def get_quality_timeline(
        self,
        experiment_id: str = "",
        agent_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Get quality scores for dashboard visualization."""
        results = []
        target_agents = agent_ids or list(self._deliveries.keys())
        for aid in target_agents:
            report = self.compute_quality(aid)
            if report.deliveries_count > 0:
                results.append({
                    "agent_id": aid,
                    "experiment_id": experiment_id,
                    **report.to_dict(),
                })
        return results

    def _prune(self, agent_id: str):
        """Remove entries older than the window."""
        cutoff = time.time() - self._window
        self._deliveries[agent_id] = [d for d in self._deliveries[agent_id] if d.timestamp > cutoff]
        self._actions[agent_id] = [a for a in self._actions[agent_id] if a.timestamp > cutoff]


# Global singleton
_tracker: Optional[ContextQualityTracker] = None


def get_quality_tracker() -> ContextQualityTracker:
    """Get or create the global quality tracker."""
    global _tracker
    if _tracker is None:
        _tracker = ContextQualityTracker()
    return _tracker
