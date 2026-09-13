"""
Redis Graph Adapter — wraps RedisGraphStorage to match the CSRGraphStorageAdapter interface.

This allows AIContextDB to use Redis as its storage backend without
changing any calling code. All 51 files that call add_node, get_node, etc.
continue to work unchanged.

Usage:
    from contextsynapse.storage.redis_graph_adapter import RedisGraphAdapter

    adapter = RedisGraphAdapter("my_graph")
    adapter.add_node(node_id, label, properties)
    node = adapter.get_node(node_id)
    neighbors = adapter.get_neighbors(node_id)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from ..core.graph_structures import GraphNode, GraphEdge

logger = logging.getLogger(__name__)


class _CompatEdge:
    """Edge wrapper with both GraphEdge attrs (source/target) and CSREdge attrs (source_id/target_id)."""
    def __init__(self, source: str, target: str, label: str, properties: dict, edge_id: str = ""):
        self.id = edge_id
        self.source = source
        self.target = target
        self.label = label
        self.properties = properties
        # CSREdge compatibility
        self.source_id = source
        self.target_id = target
        self.edge_type = label
        self.weight = 1.0


class RedisGraphAdapter:
    """Adapter that makes RedisGraphStorage compatible with AIContextDB's CSR interface."""

    def __init__(self, graph_name: str, redis_url: str = None):
        from .redis_graph_storage import RedisGraphStorage
        self._store = RedisGraphStorage(graph_name, redis_url)
        self._graph_name = graph_name

    # ------------------------------------------------------------------
    # Node operations (match CSRGraphStorageAdapter interface)
    # ------------------------------------------------------------------

    def add_node(self, node_id: str, label: str, properties: Dict[str, Any] = None, alias: str = None) -> bool:
        return self._store.add_node(node_id, label, properties or {})

    def get_node(self, node_id: str):
        rn = self._store.get_node(node_id)
        if not rn:
            return None
        node = GraphNode(id=rn.id, label=rn.node_type, properties=rn.properties)
        node.node_type = rn.node_type  # CSRNode compatibility
        return node

    def get_all_nodes(self, node_type: str = None) -> List[GraphNode]:
        nodes = []
        for n in self._store.get_all_nodes(node_type=node_type):
            gn = GraphNode(id=n.id, label=n.node_type, properties=n.properties)
            gn.node_type = n.node_type  # CSRNode compatibility
            nodes.append(gn)
        return nodes

    def remove_node(self, node_id: str) -> bool:
        return self._store.remove_node(node_id)

    def update_node_properties(self, node_id: str, properties: Dict[str, Any]) -> bool:
        return self._store.update_node_properties(node_id, properties)

    def update_node(self, node_id: str, properties: Dict[str, Any]) -> bool:
        """Alias for update_node_properties — used by PortfolioManager.update_prices."""
        return self._store.update_node_properties(node_id, properties)

    def get_node_count(self) -> int:
        return self._store.get_node_count()

    def get_nodes_by_type(self, node_type: str) -> List[str]:
        return self._store.get_nodes_by_type(node_type)

    # ------------------------------------------------------------------
    # Edge operations
    # ------------------------------------------------------------------

    def add_edge(self, source_or_edge, target: str = None, label: str = None,
                 properties: Dict[str, Any] = None) -> bool:
        """Accept both GraphEdge object or positional args (source, target, label, props)."""
        if isinstance(source_or_edge, GraphEdge):
            edge = source_or_edge
            return self._store.add_edge(
                source_id=edge.source, target_id=edge.target,
                edge_type=edge.label, properties=edge.properties, edge_id=edge.id,
            )
        # Positional: add_edge(source, target, label, properties)
        return self._store.add_edge(
            source_id=source_or_edge, target_id=target or "",
            edge_type=label or "", properties=properties or {},
        )

    def get_all_edges(self) -> list:
        return [
            _CompatEdge(e.source_id, e.target_id, e.edge_type, e.properties)
            for e in self._store.get_all_edges()
        ]

    def get_edge_count(self) -> int:
        return self._store.get_edge_count()

    # ------------------------------------------------------------------
    # Adjacency
    # ------------------------------------------------------------------

    def get_neighbors(self, node_id: str, edge_type: str = None) -> List[Tuple[str, Any]]:
        """Returns [(neighbor_id, edge)] matching CSR interface."""
        results = self._store.get_neighbors(node_id, edge_type)
        return [
            (nid, _CompatEdge(node_id, nid, e.edge_type, e.properties))
            for nid, e in results
        ]

    def get_incoming_neighbors(self, node_id: str, edge_type: str = None) -> List[Tuple[str, Any]]:
        results = self._store.get_incoming_neighbors(node_id, edge_type)
        return [
            (src_id, _CompatEdge(src_id, node_id, e.edge_type, e.properties))
            for src_id, e in results
        ]

    # ------------------------------------------------------------------
    # Traversal + property lookup
    # ------------------------------------------------------------------

    def traverse(self, start_node_id: str, edge_label: str = None,
                 max_depth: int = 3, direction: str = "OUTGOING") -> List[Tuple[str, int]]:
        """BFS traversal from start node. Returns [(node_id, depth)]."""
        visited = set()
        result = []
        queue = [(start_node_id, 0)]
        while queue:
            nid, depth = queue.pop(0)
            if nid in visited or depth > max_depth:
                continue
            visited.add(nid)
            result.append((nid, depth))
            if depth < max_depth:
                if direction == "OUTGOING":
                    neighbors = self.get_neighbors(nid, edge_label)
                else:
                    neighbors = self.get_incoming_neighbors(nid, edge_label)
                for neighbor_id, _ in neighbors:
                    if neighbor_id not in visited:
                        queue.append((neighbor_id, depth + 1))
        return result

    def get_nodes_by_property(self, property_name: str, property_value: Any) -> List[str]:
        """Find nodes by property value. Scans all nodes (no index)."""
        result = []
        for node in self._store.get_all_nodes():
            if node.properties.get(property_name) == property_value:
                result.append(node.id)
        return result

    # ------------------------------------------------------------------
    # Graph management
    # ------------------------------------------------------------------

    def delete_graph(self) -> int:
        return self._store.delete_graph()

    def export_json(self) -> Dict[str, Any]:
        return self._store.export_json()

    @property
    def node_count(self) -> int:
        return self._store.node_count

    @property
    def edge_count(self) -> int:
        return self._store.edge_count
