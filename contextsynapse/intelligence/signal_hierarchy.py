"""Signal Hierarchy — upstream/downstream context linking with event propagation.

Defines how contexts relate to each other in a hierarchy:
  World → Geopolitical → Country → Market/Sector → Company

When an upstream context ingests a significant event, it propagates
downstream to all linked contexts. Downstream contexts can query
upstream for macro context.

Usage:
    from contextsynapse.intelligence.signal_hierarchy import SignalHierarchy, ContextLink

    hierarchy = SignalHierarchy()

    # Define the hierarchy
    hierarchy.add_link(ContextLink(upstream="Global Macro", downstream="India Economy", relationship="country_in"))
    hierarchy.add_link(ContextLink(upstream="India Economy", downstream="IT Services Sector", relationship="sector_in"))
    hierarchy.add_link(ContextLink(upstream="IT Services Sector", downstream="TCS Intelligence", relationship="company_in"))
    hierarchy.add_link(ContextLink(upstream="IT Services Sector", downstream="Infosys Intelligence", relationship="company_in"))

    # When Global Macro gets a signal, find all downstream contexts
    affected = hierarchy.get_downstream("Global Macro")
    # → ["India Economy", "IT Services Sector", "TCS Intelligence", "Infosys Intelligence"]

    # When TCS needs macro context, find all upstream
    context_chain = hierarchy.get_upstream("TCS Intelligence")
    # → ["IT Services Sector", "India Economy", "Global Macro"]
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# Standard hierarchy levels — defines the natural ordering
HIERARCHY_LEVELS = {
    "world": 0,
    "geopolitical": 1,
    "country": 2,
    "sector": 3,
    "market": 3,
    "supply_chain": 4,
    "company": 5,
    "product": 6,
}


@dataclass
class ContextLink:
    """A directional link between two contexts in the signal hierarchy."""
    upstream: str               # context that produces signals
    downstream: str             # context that receives signals
    relationship: str = "feeds" # how they relate: country_in, sector_in, company_in, supplies_to, competes_with
    propagate_signals: List[str] = field(default_factory=lambda: ["sentiment_reversal", "threshold_breach"])
    weight: float = 1.0         # propagation strength (0.0-1.0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "upstream": self.upstream,
            "downstream": self.downstream,
            "relationship": self.relationship,
            "propagate_signals": self.propagate_signals,
            "weight": self.weight,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ContextLink":
        return cls(
            upstream=data.get("upstream", ""),
            downstream=data.get("downstream", ""),
            relationship=data.get("relationship", "feeds"),
            propagate_signals=data.get("propagate_signals", ["sentiment_reversal", "threshold_breach"]),
            weight=data.get("weight", 1.0),
        )


@dataclass
class PropagatedSignal:
    """A signal propagated from upstream to downstream context."""
    original_signal_type: str
    source_context: str
    target_context: str
    entity_name: str
    relationship: str
    hop_count: int              # how many levels it traveled
    weight: float               # decayed weight (original * link_weight per hop)
    details: Dict[str, Any] = field(default_factory=dict)
    propagated_at: str = ""

    def __post_init__(self):
        if not self.propagated_at:
            self.propagated_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "original_signal_type": self.original_signal_type,
            "source_context": self.source_context,
            "target_context": self.target_context,
            "entity_name": self.entity_name,
            "relationship": self.relationship,
            "hop_count": self.hop_count,
            "weight": round(self.weight, 3),
            "details": self.details,
            "propagated_at": self.propagated_at,
        }


class SignalHierarchy:
    """Manages context hierarchy and signal propagation.

    Contexts are linked in a directed graph. Signals flow downstream
    (from macro to micro) with decaying weight. Upstream queries flow
    upward (from company to macro) for context building.
    """

    def __init__(self):
        self._links: List[ContextLink] = []
        self._downstream_map: Dict[str, List[ContextLink]] = {}  # upstream -> [links]
        self._upstream_map: Dict[str, List[ContextLink]] = {}    # downstream -> [links]
        self._lateral_map: Dict[str, List[ContextLink]] = {}     # peer -> [links] (bidirectional)
        self._context_levels: Dict[str, str] = {}                # context -> level name
        self._propagation_log: List[PropagatedSignal] = []

    def add_link(self, link: ContextLink):
        """Add a hierarchical link between two contexts (upstream → downstream)."""
        self._links.append(link)
        self._downstream_map.setdefault(link.upstream, []).append(link)
        self._upstream_map.setdefault(link.downstream, []).append(link)
        logger.info("[HIERARCHY] Link: %s -[%s]-> %s",
                     link.upstream, link.relationship, link.downstream)

    def add_lateral_link(self, link: ContextLink):
        """Add a lateral link between peer contexts (same level).

        Lateral links represent: competitors, supply chain partners,
        clients, sector peers — entities at the same hierarchy level
        that influence each other sideways.
        """
        self._links.append(link)
        self._lateral_map.setdefault(link.upstream, []).append(link)
        self._lateral_map.setdefault(link.downstream, []).append(link)
        logger.info("[HIERARCHY] Lateral: %s <-[%s]-> %s",
                     link.upstream, link.relationship, link.downstream)

    def set_level(self, context_name: str, level: str):
        """Set the hierarchy level for a context."""
        self._context_levels[context_name] = level

    def get_level(self, context_name: str) -> str:
        """Get the hierarchy level for a context."""
        return self._context_levels.get(context_name, "unknown")

    def get_downstream(self, context_name: str, max_depth: int = 10) -> List[str]:
        """Get all downstream contexts (recursive), ordered by proximity."""
        result = []
        visited: Set[str] = {context_name}
        queue = [context_name]
        depth = 0

        while queue and depth < max_depth:
            next_queue = []
            for ctx in queue:
                for link in self._downstream_map.get(ctx, []):
                    if link.downstream not in visited:
                        visited.add(link.downstream)
                        result.append(link.downstream)
                        next_queue.append(link.downstream)
            queue = next_queue
            depth += 1

        return result

    def get_upstream(self, context_name: str, max_depth: int = 10) -> List[str]:
        """Get all upstream contexts (recursive), ordered by proximity."""
        result = []
        visited: Set[str] = {context_name}
        queue = [context_name]
        depth = 0

        while queue and depth < max_depth:
            next_queue = []
            for ctx in queue:
                for link in self._upstream_map.get(ctx, []):
                    if link.upstream not in visited:
                        visited.add(link.upstream)
                        result.append(link.upstream)
                        next_queue.append(link.upstream)
            queue = next_queue
            depth += 1

        return result

    def get_full_chain(self, context_name: str) -> Dict[str, List[str]]:
        """Get both upstream and downstream chains for a context."""
        return {
            "upstream": self.get_upstream(context_name),
            "downstream": self.get_downstream(context_name),
        }

    def propagate_signal(
        self,
        signal,
        source_context: str,
        max_depth: int = 5,
        min_weight: float = 0.1,
        direction: str = "both",
        on_propagate: Optional[Callable] = None,
    ) -> List[PropagatedSignal]:
        """Propagate a signal through the hierarchy.

        Signals can start at ANY level — a country event propagates both
        down to companies AND up to world/geopolitical awareness.

        Downstream (impact): full weight decay per hop.
        Upstream (awareness): half weight — upstream contexts need to know,
        but the impact is indirect.

        Args:
            signal: Signal dataclass from signals.py
            source_context: Which context the signal originated in
            max_depth: Maximum hops to propagate
            min_weight: Stop propagating when weight falls below this
            direction: "both" | "downstream" | "upstream"

        Returns list of PropagatedSignal for each affected context.
        """
        propagated: List[PropagatedSignal] = []
        visited: Set[str] = {source_context}

        # Direction weight multipliers:
        #   downstream (impact): full weight — direct effect
        #   upstream (awareness): 0.5x — indirect, for awareness
        #   lateral (peer/supply chain): 0.7x — related but not hierarchical
        dir_weights = {"downstream": 1.0, "upstream": 0.5, "lateral": 0.7}

        # BFS: walk ALL connections from source — the signal is a wave
        # Each queue item: (context, weight, hop_count)
        queue = [(source_context, 1.0, 0)]

        while queue:
            current_ctx, current_weight, hop = queue.pop(0)
            if hop >= max_depth:
                continue

            neighbors = []

            # Downstream neighbors
            if direction in ("both", "downstream", "all"):
                for link in self._downstream_map.get(current_ctx, []):
                    neighbors.append((link.downstream, link, "downstream"))

            # Upstream neighbors
            if direction in ("both", "upstream", "all"):
                for link in self._upstream_map.get(current_ctx, []):
                    neighbors.append((link.upstream, link, "upstream"))

            # Lateral neighbors (same level — peers, supply chain, clients)
            if direction in ("both", "lateral", "all"):
                for link in self._lateral_map.get(current_ctx, []):
                    target = link.downstream if link.upstream == current_ctx else link.upstream
                    neighbors.append((target, link, "lateral"))

            for target_ctx, link, flow_dir in neighbors:
                if target_ctx in visited:
                    continue
                if signal.type not in link.propagate_signals:
                    continue

                dir_mult = dir_weights.get(flow_dir, 0.5)
                next_weight = current_weight * link.weight * dir_mult

                if next_weight < min_weight:
                    continue

                visited.add(target_ctx)

                prop = PropagatedSignal(
                    original_signal_type=signal.type,
                    source_context=source_context,
                    target_context=target_ctx,
                    entity_name=signal.entity_name,
                    relationship=link.relationship,
                    hop_count=hop + 1,
                    weight=next_weight,
                    details={
                        "direction": flow_dir,
                        "original_severity": signal.severity,
                        "signal_details": signal.details,
                    },
                )
                propagated.append(prop)
                self._propagation_log.append(prop)
                queue.append((target_ctx, next_weight, hop + 1))

        if propagated:
            by_dir = {}
            for p in propagated:
                d = p.details.get("direction", "unknown")
                by_dir[d] = by_dir.get(d, 0) + 1
            logger.info("[HIERARCHY] Signal '%s' from '%s': %s",
                         signal.type, source_context, by_dir)

        return propagated

    def get_propagation_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent propagated signals."""
        return [p.to_dict() for p in self._propagation_log[-limit:]]

    def build_upstream_context(self, context_name: str) -> List[Dict[str, Any]]:
        """Build a context briefing from all upstream contexts.

        Returns a list of upstream signal summaries that downstream
        agents/analysts should be aware of.
        """
        upstream = self.get_upstream(context_name)
        briefing = []

        for up_ctx in upstream:
            level = self._context_levels.get(up_ctx, "unknown")
            # Get recent propagated signals to this context
            recent = [
                p for p in self._propagation_log
                if p.target_context == context_name and p.source_context == up_ctx
            ]

            briefing.append({
                "context": up_ctx,
                "level": level,
                "relationship": self._get_relationship_to(up_ctx, context_name),
                "recent_signals": [p.to_dict() for p in recent[-5:]],
            })

        return briefing

    def _get_relationship_to(self, upstream: str, downstream: str) -> str:
        """Get the relationship type between two contexts."""
        for link in self._downstream_map.get(upstream, []):
            if link.downstream == downstream:
                return link.relationship
        return "indirect"

    def get_lateral(self, context_name: str) -> List[str]:
        """Get all lateral (peer) contexts."""
        peers = set()
        for link in self._lateral_map.get(context_name, []):
            peer = link.downstream if link.upstream == context_name else link.upstream
            peers.add(peer)
        return list(peers)

    def get_all_connected(self, context_name: str) -> Dict[str, List[str]]:
        """Get all connected contexts in every direction."""
        return {
            "upstream": self.get_upstream(context_name),
            "downstream": self.get_downstream(context_name),
            "lateral": self.get_lateral(context_name),
        }

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the hierarchy."""
        hierarchical = [l for l in self._links if l.upstream in self._downstream_map
                        and any(dl.downstream == l.downstream for dl in self._downstream_map.get(l.upstream, []))]
        lateral_links = []
        seen = set()
        for links in self._lateral_map.values():
            for l in links:
                key = tuple(sorted([l.upstream, l.downstream]))
                if key not in seen:
                    seen.add(key)
                    lateral_links.append(l)

        return {
            "links": [l.to_dict() for l in self._links if l not in lateral_links],
            "lateral_links": [l.to_dict() for l in lateral_links],
            "levels": dict(self._context_levels),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SignalHierarchy":
        """Deserialize a hierarchy."""
        h = cls()
        for link_data in data.get("links", []):
            h.add_link(ContextLink.from_dict(link_data))
        for link_data in data.get("lateral_links", []):
            h.add_lateral_link(ContextLink.from_dict(link_data))
        for ctx, level in data.get("levels", {}).items():
            h.set_level(ctx, level)
        return h


def build_default_hierarchy() -> SignalHierarchy:
    """Build a sensible default hierarchy for financial analysis.

    World → Geopolitical regions → Countries → Sectors → Companies
    """
    h = SignalHierarchy()

    # Define levels
    shared_contexts = {
        "Global Macro": "world",
        "Geopolitical Risk": "geopolitical",
        "Commodities": "world",
        "US Economy": "country",
        "EU Economy": "country",
        "China Economy": "country",
        "India Economy": "country",
        "Japan Economy": "country",
    }
    for ctx, level in shared_contexts.items():
        h.set_level(ctx, level)

    # World → Country links
    for country in ["US Economy", "EU Economy", "China Economy", "India Economy", "Japan Economy"]:
        h.add_link(ContextLink(
            upstream="Global Macro",
            downstream=country,
            relationship="macro_affects",
            weight=0.8,
        ))
        h.add_link(ContextLink(
            upstream="Geopolitical Risk",
            downstream=country,
            relationship="risk_affects",
            propagate_signals=["sentiment_reversal", "threshold_breach"],
            weight=0.7,
        ))
        h.add_link(ContextLink(
            upstream="Commodities",
            downstream=country,
            relationship="commodity_affects",
            propagate_signals=["threshold_breach"],
            weight=0.6,
        ))

    return h
