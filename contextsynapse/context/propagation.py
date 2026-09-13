"""Signal Propagation Engine — traverses the context graph to find affected entities.

When a signal arrives (e.g., "crude oil +$5"), this engine:
  1. Identifies the source entity (crude_oil)
  2. Walks all outgoing edges (IMPACTS, DEPENDS_ON, etc.)
  3. Applies propagation_weight and decay_per_hop
  4. Returns all affected entities with propagated signal strength

This is how cross-domain effects are discovered automatically.
No LLM needed — just graph traversal with weighted edges.

Example:
  Signal: "RBI raises repo rate by 25bps"
  Source: rbi_policy (macro layer)

  Hop 1: rbi_policy --REGULATED_BY--> banking_sector (weight=0.9)
  Hop 2: banking_sector --CONTAINS--> HDFC Bank (weight=0.8)
         banking_sector --CONTAINS--> ICICI Bank (weight=0.8)
  Hop 3: banking_sector --IMPACTS--> real_estate (weight=0.6)
         banking_sector --IMPACTS--> auto (weight=0.4, via EMI rates)

  Result:
    HDFC Bank:  signal_strength = 0.9 * 0.8 = 0.72
    ICICI Bank: signal_strength = 0.9 * 0.8 = 0.72
    Real estate: signal_strength = 0.9 * 0.6 = 0.54
    Auto:       signal_strength = 0.9 * 0.4 = 0.36
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Propagation edge types we traverse
# ---------------------------------------------------------------------------

# These are the edge labels the engine scans when building the dependency
# graph.  Any edge with one of these labels is treated as a propagation
# channel.  This list is domain-agnostic — layers create these edges.
PROPAGATION_EDGE_LABELS: Set[str] = {
    "DEPENDS_ON",
    "IMPACTS",
    "REGULATED_BY",
    "SUPPLIES_TO",
    "COMPETES_WITH",
    "CONTAINS",
    "PART_OF",
    "AFFECTED_BY",
    "DRIVES",
    "CORRELATED_WITH",
}

# Node labels we index as entities in the dependency graph
ENTITY_NODE_LABELS: Set[str] = {
    "Entity",
    "CompanyIdentity",
    "Sector",
    "SupplyChainInput",
    "Regulator",
    "Competitor",
    "MacroDependency",
    "SharedEntity",
    "Brand",
    "Person",
}


# ---------------------------------------------------------------------------
# Result data class
# ---------------------------------------------------------------------------


@dataclass
class PropagatedSignal:
    """An entity affected by signal propagation through the graph."""

    entity: str               # affected entity name or namespace
    signal_strength: float    # 0-1 after propagation
    hops: int                 # how many edges traversed
    path: List[str]           # traversal path ["source", "hop1", "hop2"]
    edge_types: List[str]     # edge types used at each hop
    source_signal: str        # original signal description
    reasoning: str            # human-readable explanation

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity": self.entity,
            "signal_strength": round(self.signal_strength, 4),
            "hops": self.hops,
            "path": list(self.path),
            "edge_types": list(self.edge_types),
            "source_signal": self.source_signal,
            "reasoning": self.reasoning,
        }


# ---------------------------------------------------------------------------
# Signal Propagation Engine
# ---------------------------------------------------------------------------


class SignalPropagationEngine:
    """Propagates signals through the context graph via weighted edges.

    The engine builds an in-memory adjacency list from the graph registry
    and performs BFS traversal with signal decay to discover affected
    entities.  No LLM calls — pure graph traversal.

    Usage::

        engine = SignalPropagationEngine(registry=my_registry)
        engine.build_dependency_graph()

        affected = engine.propagate(
            source_entity="crude_oil",
            signal_value=0.8,
            signal_description="crude oil rises 10%",
        )
        for sig in affected:
            print(f"{sig.entity}: {sig.signal_strength:.2f} ({sig.reasoning})")

    Performance:
        Graph build: O(nodes + edges), typically < 200ms for 10k nodes.
        Propagation: O(V + E) BFS, typically < 5ms per signal.
    """

    def __init__(self, registry):
        """
        Args:
            registry: GraphRegistry (or RedisGraphRegistry) for graph access.
        """
        self._registry = registry

        # Adjacency list: entity_key -> [(target_key, edge_type, weight, decay)]
        self._adjacency: Dict[str, List[Tuple[str, str, float, float]]] = defaultdict(list)
        # Reverse adjacency for "depended_by" queries
        self._reverse: Dict[str, List[Tuple[str, str, float, float]]] = defaultdict(list)
        # Entity metadata: entity_key -> {name, label, namespace, ...}
        self._entity_meta: Dict[str, Dict[str, Any]] = {}
        # Build timestamp
        self._built_at: Optional[str] = None
        self._build_time_ms: float = 0.0

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    def build_dependency_graph(self) -> None:
        """Build in-memory adjacency list from all dependency edges.

        Scans every graph namespace for entity-like nodes and propagation
        edges.  Builds both forward and reverse adjacency lists.

        This is built ONCE and cached.  Call again to refresh.
        """
        from datetime import datetime, timezone

        t0 = time.monotonic()

        adjacency: Dict[str, List[Tuple[str, str, float, float]]] = defaultdict(list)
        reverse: Dict[str, List[Tuple[str, str, float, float]]] = defaultdict(list)
        entity_meta: Dict[str, Dict[str, Any]] = {}

        graphs = self._registry.list_graphs()
        for graph_info in graphs:
            namespace = graph_info.get("name", "")
            if not namespace or namespace.startswith("_"):
                continue

            graph = self._registry.get_graph(namespace, load_if_missing=False)
            if graph is None:
                continue

            self._index_graph(graph, namespace, adjacency, reverse, entity_meta)

        self._adjacency = dict(adjacency)
        self._reverse = dict(reverse)
        self._entity_meta = entity_meta
        self._built_at = datetime.now(timezone.utc).isoformat()
        self._build_time_ms = (time.monotonic() - t0) * 1000

        logger.info(
            "Dependency graph built: %d entities, %d forward edges, %d reverse edges in %.1fms",
            len(self._entity_meta),
            sum(len(v) for v in self._adjacency.values()),
            sum(len(v) for v in self._reverse.values()),
            self._build_time_ms,
        )

    def _index_graph(
        self,
        graph,
        namespace: str,
        adjacency: Dict[str, List[Tuple[str, str, float, float]]],
        reverse: Dict[str, List[Tuple[str, str, float, float]]],
        entity_meta: Dict[str, Dict[str, Any]],
    ) -> None:
        """Index nodes and edges from a single graph namespace."""
        # Index entity nodes
        node_id_to_key: Dict[str, str] = {}
        for label in ENTITY_NODE_LABELS:
            try:
                nodes = graph.get_all_nodes(label=label)
            except Exception:
                continue
            for node in nodes:
                props = node.properties or {}
                name = props.get("name", "")
                if not name:
                    continue
                key = _entity_key(name)
                node_id_to_key[node.id] = key
                if key not in entity_meta:
                    entity_meta[key] = {
                        "name": name,
                        "label": label,
                        "namespace": namespace,
                        "node_id": node.id,
                    }

        # Index edges that connect known entity nodes
        try:
            all_edges = graph.get_all_edges() if hasattr(graph, "get_all_edges") else []
        except Exception:
            all_edges = []

        for edge in all_edges:
            edge_label = getattr(edge, "label", "") or ""
            if edge_label not in PROPAGATION_EDGE_LABELS:
                continue

            source_key = node_id_to_key.get(edge.source)
            target_key = node_id_to_key.get(edge.target)
            if not source_key or not target_key or source_key == target_key:
                continue

            # Extract weight/decay from edge properties or use defaults
            eprops = edge.properties or {}
            weight = float(eprops.get("propagation_weight", eprops.get("weight", 0.8)))
            decay = float(eprops.get("decay_per_hop", 0.3))

            adjacency[source_key].append((target_key, edge_label, weight, decay))
            reverse[target_key].append((source_key, edge_label, weight, decay))

    # ------------------------------------------------------------------
    # Signal propagation
    # ------------------------------------------------------------------

    def propagate(
        self,
        source_entity: str,
        signal_value: float,
        signal_description: str = "",
        max_hops: int = 3,
        min_strength: float = 0.1,
    ) -> List[PropagatedSignal]:
        """Propagate a signal from source through dependency edges.

        Uses BFS with signal decay.  At each hop the signal strength is
        multiplied by the edge's propagation_weight and reduced by
        (1 - decay_per_hop).

        Args:
            source_entity: Entity name where the signal originates.
            signal_value: Signal strength (-1 to +1).
            signal_description: Human-readable description of the signal.
            max_hops: Maximum traversal depth (default 3).
            min_strength: Stop propagating below this threshold (default 0.1).

        Returns:
            List of PropagatedSignal sorted by signal_strength descending.
        """
        source_key = _entity_key(source_entity)
        if source_key not in self._adjacency and source_key not in self._entity_meta:
            # Try partial match
            source_key = self._resolve_entity(source_entity)
            if not source_key:
                return []

        abs_value = abs(signal_value)
        results: List[PropagatedSignal] = []
        visited: Set[str] = {source_key}

        # BFS queue: (entity_key, current_strength, hop_count, path, edge_types)
        queue: deque = deque()

        # Seed the queue with direct neighbors
        for target_key, edge_type, weight, decay in self._adjacency.get(source_key, []):
            new_strength = abs_value * weight * (1.0 - decay)
            if new_strength >= min_strength:
                queue.append((
                    target_key,
                    new_strength,
                    1,
                    [source_key, target_key],
                    [edge_type],
                ))

        while queue:
            current_key, strength, hops, path, edge_types = queue.popleft()

            if current_key in visited:
                continue
            visited.add(current_key)

            # Record this affected entity
            meta = self._entity_meta.get(current_key, {})
            entity_name = meta.get("name", current_key)
            reasoning = self._build_reasoning(
                path, edge_types, strength, signal_description
            )

            results.append(PropagatedSignal(
                entity=entity_name,
                signal_strength=round(strength, 4),
                hops=hops,
                path=[self._entity_meta.get(k, {}).get("name", k) for k in path],
                edge_types=list(edge_types),
                source_signal=signal_description,
                reasoning=reasoning,
            ))

            # Continue propagation if within hop limit
            if hops < max_hops:
                for next_key, edge_type, weight, decay in self._adjacency.get(current_key, []):
                    if next_key in visited:
                        continue
                    new_strength = strength * weight * (1.0 - decay)
                    if new_strength >= min_strength:
                        queue.append((
                            next_key,
                            new_strength,
                            hops + 1,
                            path + [next_key],
                            edge_types + [edge_type],
                        ))

        # Sort by signal strength descending
        results.sort(key=lambda s: s.signal_strength, reverse=True)
        return results

    # ------------------------------------------------------------------
    # Dependency map
    # ------------------------------------------------------------------

    def get_dependency_map(self, entity: str) -> Dict[str, List[Dict[str, Any]]]:
        """Return all dependencies for an entity.

        Queries both forward and reverse adjacency lists to build a
        complete picture of what the entity depends on, what depends on
        it, its regulators, competitors, and macro factors.

        Returns::

            {
                "depends_on": [{"entity": "crude_oil", "edge": "DEPENDS_ON", "weight": 0.9}],
                "depended_by": [{"entity": "petrol_pump", "edge": "SUPPLIES_TO", "weight": 0.5}],
                "regulators": [{"entity": "sebi", "edge": "REGULATED_BY", "weight": 0.8}],
                "competitors": [{"entity": "ongc", "edge": "COMPETES_WITH", "weight": 0.7}],
                "macro": [{"entity": "india_gdp", "edge": "IMPACTS", "weight": 0.6}],
            }
        """
        entity_key = _entity_key(entity)
        if entity_key not in self._entity_meta:
            entity_key = self._resolve_entity(entity) or entity_key

        dep_map: Dict[str, List[Dict[str, Any]]] = {
            "depends_on": [],
            "depended_by": [],
            "regulators": [],
            "competitors": [],
            "macro": [],
        }

        # Categorize edge types
        edge_category_map = {
            "DEPENDS_ON": "depends_on",
            "SUPPLIES_TO": "depends_on",
            "PART_OF": "depends_on",
            "REGULATED_BY": "regulators",
            "COMPETES_WITH": "competitors",
            "IMPACTS": "macro",
            "AFFECTED_BY": "macro",
            "DRIVES": "macro",
            "CORRELATED_WITH": "macro",
            "CONTAINS": "depends_on",
        }

        # Forward edges: what this entity connects to
        for target_key, edge_type, weight, _decay in self._adjacency.get(entity_key, []):
            target_name = self._entity_meta.get(target_key, {}).get("name", target_key)
            category = edge_category_map.get(edge_type, "depends_on")
            dep_map[category].append({
                "entity": target_name,
                "edge": edge_type,
                "weight": round(weight, 2),
            })

        # Reverse edges: what connects to this entity
        for source_key, edge_type, weight, _decay in self._reverse.get(entity_key, []):
            source_name = self._entity_meta.get(source_key, {}).get("name", source_key)
            dep_map["depended_by"].append({
                "entity": source_name,
                "edge": edge_type,
                "weight": round(weight, 2),
            })

        return dep_map

    # ------------------------------------------------------------------
    # What-if analysis
    # ------------------------------------------------------------------

    def what_if(
        self,
        signal_description: str,
        signal_value: float,
        max_hops: int = 3,
        min_strength: float = 0.1,
    ) -> List[PropagatedSignal]:
        """Given a hypothetical signal, show what would be affected.

        Resolves the signal description to a source entity via text
        matching against entity names in the dependency graph, then
        propagates the signal.

        Args:
            signal_description: Natural language description (e.g.
                "crude oil rises 10%").
            signal_value: Signal strength (-1 to +1).
            max_hops: Maximum traversal depth.
            min_strength: Minimum signal threshold.

        Returns:
            List of PropagatedSignal sorted by strength descending.
            Empty list if no source entity could be resolved.
        """
        source_key = self._resolve_from_text(signal_description)
        if not source_key:
            logger.warning(
                "what_if: could not resolve source entity from '%s'",
                signal_description[:100],
            )
            return []

        source_name = self._entity_meta.get(source_key, {}).get("name", source_key)
        return self.propagate(
            source_entity=source_name,
            signal_value=signal_value,
            signal_description=signal_description,
            max_hops=max_hops,
            min_strength=min_strength,
        )

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        """Return graph statistics."""
        total_entities = len(self._entity_meta)
        total_forward = sum(len(v) for v in self._adjacency.values())
        total_reverse = sum(len(v) for v in self._reverse.values())
        connected = sum(1 for k in self._entity_meta if k in self._adjacency or k in self._reverse)
        avg_connectivity = (
            (total_forward + total_reverse) / total_entities
            if total_entities > 0
            else 0.0
        )

        return {
            "total_entities": total_entities,
            "total_forward_edges": total_forward,
            "total_reverse_edges": total_reverse,
            "connected_entities": connected,
            "isolated_entities": total_entities - connected,
            "avg_connectivity": round(avg_connectivity, 2),
            "built_at": self._built_at,
            "build_time_ms": round(self._build_time_ms, 1),
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_entity(self, entity_name: str) -> Optional[str]:
        """Resolve entity name to a key via partial matching."""
        lower = entity_name.lower().strip()
        # Exact match
        key = _entity_key(entity_name)
        if key in self._entity_meta:
            return key
        # Substring match
        for k, meta in self._entity_meta.items():
            name = meta.get("name", "").lower()
            if lower in name or name in lower:
                return k
        return None

    def _resolve_from_text(self, text: str) -> Optional[str]:
        """Find the best matching entity from a text description.

        Scores each entity by how many of its name words appear in the
        text.  Returns the highest-scoring entity key.
        """
        lower_text = text.lower()
        best_key: Optional[str] = None
        best_score = 0

        for key, meta in self._entity_meta.items():
            name = meta.get("name", "").lower()
            # Check if full name appears
            if name in lower_text:
                score = len(name) * 2
                if score > best_score:
                    best_score = score
                    best_key = key
                continue
            # Check individual words
            words = [w for w in name.split("_") if len(w) > 2]
            if not words:
                continue
            hits = sum(1 for w in words if w in lower_text)
            if hits > 0:
                score = hits
                if score > best_score:
                    best_score = score
                    best_key = key

        return best_key

    def _build_reasoning(
        self,
        path: List[str],
        edge_types: List[str],
        strength: float,
        signal_description: str,
    ) -> str:
        """Build a human-readable reasoning string for a propagated signal."""
        if not path or not edge_types:
            return ""

        parts = []
        source_name = self._entity_meta.get(path[0], {}).get("name", path[0])

        if signal_description:
            parts.append(f"Signal: {signal_description}")

        # Describe the traversal
        for i, edge_type in enumerate(edge_types):
            from_name = self._entity_meta.get(path[i], {}).get("name", path[i])
            to_name = self._entity_meta.get(path[i + 1], {}).get("name", path[i + 1])
            parts.append(f"{from_name} --{edge_type}--> {to_name}")

        parts.append(f"Propagated strength: {strength:.2f}")

        return " | ".join(parts)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entity_key(name: str) -> str:
    """Normalize entity name to a stable lookup key."""
    import re
    key = name.lower().strip()
    key = re.sub(r"[^a-z0-9]+", "_", key)
    return key.strip("_") or "unknown"
