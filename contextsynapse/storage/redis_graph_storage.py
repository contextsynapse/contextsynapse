"""
Redis Graph Storage — drop-in replacement for CSRGraphStorage.

Stores nodes, edges, and adjacency in Redis hashes and sets.
Workers never load the full graph into memory. All operations
are O(1) or O(degree) via Redis.

Data model:
  node:{graph}:{id}       → HASH (label, properties JSON)
  edge:{graph}:{id}       → HASH (source, target, label, properties JSON)
  adj:{graph}:{id}:out    → SET of edge IDs (outgoing)
  adj:{graph}:{id}:in     → SET of edge IDs (incoming)
  nodes:{graph}           → SET of all node IDs
  edges:{graph}           → SET of all edge IDs
  ntype:{graph}:{label}   → SET of node IDs with this label
  etype:{graph}:{label}   → SET of edge IDs with this label

Disk backup: periodic full export to graph.json (same format as before).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass
class RedisNode:
    id: str
    node_type: str
    properties: Dict[str, Any]


@dataclass
class RedisEdge:
    source_id: str
    target_id: str
    edge_type: str
    properties: Dict[str, Any]
    weight: float = 1.0


class RedisGraphStorage:
    """Graph storage backed by Redis — no in-memory loading required."""

    def __init__(self, graph_name: str, redis_url: str = None):
        import redis
        self._url = redis_url or os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL", "redis://localhost:6379/0")
        # On Windows, "localhost" resolves to IPv6 (::1) first, adding ~2s latency on first connect.
        # Force IPv4 by rewriting localhost → 127.0.0.1.
        _connect_url = self._url.replace("://localhost:", "://127.0.0.1:")
        self._r = redis.from_url(_connect_url, decode_responses=True)
        self._graph = graph_name
        self._prefix = f"g:{graph_name}:"

    # Key helpers
    def _nk(self, node_id: str) -> str:
        return f"{self._prefix}n:{node_id}"

    def _ek(self, edge_id: str) -> str:
        return f"{self._prefix}e:{edge_id}"

    def _adj_out(self, node_id: str) -> str:
        return f"{self._prefix}adj:{node_id}:out"

    def _adj_in(self, node_id: str) -> str:
        return f"{self._prefix}adj:{node_id}:in"

    # ------------------------------------------------------------------
    # Node operations
    # ------------------------------------------------------------------

    def add_node(self, node_id: str, node_type: str, properties: Dict[str, Any] = None) -> bool:
        from datetime import datetime, timezone
        props = properties or {}
        if "_created_at" not in props:
            props["_created_at"] = datetime.now(timezone.utc).isoformat()
        pipe = self._r.pipeline()
        pipe.hset(self._nk(node_id), mapping={
            "id": node_id,
            "label": node_type,
            "props": json.dumps(props, default=str),
        })
        pipe.sadd(f"{self._prefix}nodes", node_id)
        pipe.sadd(f"{self._prefix}ntype:{node_type}", node_id)
        pipe.execute()
        return True

    def get_node(self, node_id: str) -> Optional[RedisNode]:
        data = self._r.hgetall(self._nk(node_id))
        if not data:
            return None
        try:
            props = json.loads(data.get("props", "{}"))
        except Exception:
            props = {}
        return RedisNode(
            id=data.get("id", node_id),
            node_type=data.get("label", ""),
            properties=props,
        )

    def get_node_count(self) -> int:
        return self._r.scard(f"{self._prefix}nodes")

    def get_all_node_ids(self) -> Set[str]:
        return self._r.smembers(f"{self._prefix}nodes")

    def get_nodes_by_type(self, node_type: str) -> List[str]:
        return list(self._r.smembers(f"{self._prefix}ntype:{node_type}"))

    def update_node_properties(self, node_id: str, properties: Dict[str, Any]) -> bool:
        from datetime import datetime, timezone
        existing = self.get_node(node_id)
        if not existing:
            return False
        properties["_updated_at"] = datetime.now(timezone.utc).isoformat()
        merged = {**existing.properties, **properties}
        self._r.hset(self._nk(node_id), "props", json.dumps(merged, default=str))
        return True

    def remove_node(self, node_id: str) -> bool:
        data = self._r.hgetall(self._nk(node_id))
        if not data:
            return False
        label = data.get("label", "")

        # Remove edges connected to this node
        for edge_id in self._r.smembers(self._adj_out(node_id)):
            self._remove_edge_internal(edge_id)
        for edge_id in self._r.smembers(self._adj_in(node_id)):
            self._remove_edge_internal(edge_id)

        pipe = self._r.pipeline()
        pipe.delete(self._nk(node_id))
        pipe.delete(self._adj_out(node_id))
        pipe.delete(self._adj_in(node_id))
        pipe.srem(f"{self._prefix}nodes", node_id)
        if label:
            pipe.srem(f"{self._prefix}ntype:{label}", node_id)
        pipe.execute()
        return True

    # ------------------------------------------------------------------
    # Edge operations
    # ------------------------------------------------------------------

    def add_edge(self, source_id: str, target_id: str, edge_type: str,
                 properties: Dict[str, Any] = None, weight: float = 1.0,
                 edge_id: str = None) -> bool:
        import uuid
        from datetime import datetime, timezone
        eid = edge_id or str(uuid.uuid4())
        props = properties or {}
        if "_created_at" not in props:
            props["_created_at"] = datetime.now(timezone.utc).isoformat()

        pipe = self._r.pipeline()
        pipe.hset(self._ek(eid), mapping={
            "id": eid,
            "source": source_id,
            "target": target_id,
            "label": edge_type,
            "weight": str(weight),
            "props": json.dumps(props, default=str),
        })
        pipe.sadd(f"{self._prefix}edges", eid)
        pipe.sadd(f"{self._prefix}etype:{edge_type}", eid)
        pipe.sadd(self._adj_out(source_id), eid)
        pipe.sadd(self._adj_in(target_id), eid)
        pipe.execute()
        return True

    def get_edge(self, edge_id: str) -> Optional[RedisEdge]:
        data = self._r.hgetall(self._ek(edge_id))
        if not data:
            return None
        try:
            props = json.loads(data.get("props", "{}"))
        except Exception:
            props = {}
        return RedisEdge(
            source_id=data.get("source", ""),
            target_id=data.get("target", ""),
            edge_type=data.get("label", ""),
            properties=props,
            weight=float(data.get("weight", 1.0)),
        )

    def get_edge_count(self) -> int:
        return self._r.scard(f"{self._prefix}edges")

    def _remove_edge_internal(self, edge_id: str):
        data = self._r.hgetall(self._ek(edge_id))
        if not data:
            return
        source = data.get("source", "")
        target = data.get("target", "")
        label = data.get("label", "")

        pipe = self._r.pipeline()
        pipe.delete(self._ek(edge_id))
        pipe.srem(f"{self._prefix}edges", edge_id)
        if label:
            pipe.srem(f"{self._prefix}etype:{label}", edge_id)
        if source:
            pipe.srem(self._adj_out(source), edge_id)
        if target:
            pipe.srem(self._adj_in(target), edge_id)
        pipe.execute()

    # ------------------------------------------------------------------
    # Adjacency / neighbor queries
    # ------------------------------------------------------------------

    def get_neighbors(self, node_id: str, edge_type: str = None) -> List[Tuple[str, RedisEdge]]:
        """Get outgoing neighbors: [(neighbor_id, edge)]"""
        edge_ids = self._r.smembers(self._adj_out(node_id))
        result = []
        for eid in edge_ids:
            edge = self.get_edge(eid)
            if edge and (edge_type is None or edge.edge_type == edge_type):
                result.append((edge.target_id, edge))
        return result

    def get_incoming_neighbors(self, node_id: str, edge_type: str = None) -> List[Tuple[str, RedisEdge]]:
        """Get incoming neighbors: [(source_id, edge)]"""
        edge_ids = self._r.smembers(self._adj_in(node_id))
        result = []
        for eid in edge_ids:
            edge = self.get_edge(eid)
            if edge and (edge_type is None or edge.edge_type == edge_type):
                result.append((edge.source_id, edge))
        return result

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    def get_all_nodes(self, node_type: str = None) -> List[RedisNode]:
        """Get all nodes (or filtered by type). Use sparingly — O(n)."""
        if node_type:
            ids = self._r.smembers(f"{self._prefix}ntype:{node_type}")
        else:
            ids = self._r.smembers(f"{self._prefix}nodes")
        if not ids:
            return []
        # Batch fetch with pipeline
        pipe = self._r.pipeline()
        for nid in ids:
            pipe.hgetall(self._nk(nid))
        results = pipe.execute()
        nodes = []
        for data in results:
            if data:
                try:
                    props = json.loads(data.get("props", "{}"))
                except Exception:
                    props = {}
                nodes.append(RedisNode(
                    id=data.get("id", ""),
                    node_type=data.get("label", ""),
                    properties=props,
                ))
        return nodes

    def get_all_edges(self, edge_type: str = None) -> List[RedisEdge]:
        """Get all edges (or filtered by type). Use sparingly — O(m)."""
        if edge_type:
            ids = self._r.smembers(f"{self._prefix}etype:{edge_type}")
        else:
            ids = self._r.smembers(f"{self._prefix}edges")
        if not ids:
            return []
        pipe = self._r.pipeline()
        for eid in ids:
            pipe.hgetall(self._ek(eid))
        results = pipe.execute()
        edges = []
        for data in results:
            if data:
                try:
                    props = json.loads(data.get("props", "{}"))
                except Exception:
                    props = {}
                edges.append(RedisEdge(
                    source_id=data.get("source", ""),
                    target_id=data.get("target", ""),
                    edge_type=data.get("label", ""),
                    properties=props,
                    weight=float(data.get("weight", 1.0)),
                ))
        return edges

    # ------------------------------------------------------------------
    # Graph deletion
    # ------------------------------------------------------------------

    def delete_graph(self) -> int:
        """Delete all keys for this graph. Returns count of keys deleted."""
        pattern = f"{self._prefix}*"
        count = 0
        cursor = 0
        while True:
            cursor, keys = self._r.scan(cursor, match=pattern, count=200)
            if keys:
                self._r.delete(*keys)
                count += len(keys)
            if cursor == 0:
                break
        logger.info("[REDIS-GRAPH] Deleted graph '%s': %d keys", self._graph, count)
        return count

    # ------------------------------------------------------------------
    # Export (for disk backup)
    # ------------------------------------------------------------------

    def export_json(self) -> Dict[str, Any]:
        """Export entire graph as JSON dict (for disk backup)."""
        nodes = []
        for n in self.get_all_nodes():
            nodes.append({"id": n.id, "label": n.node_type, "properties": n.properties})
        edges = []
        for e in self.get_all_edges():
            edges.append({
                "source": e.source_id, "target": e.target_id,
                "label": e.edge_type, "properties": e.properties,
            })
        return {
            "nodes": nodes,
            "edges": edges,
            "metadata": {
                "name": self._graph,
                "node_count": len(nodes),
                "edge_count": len(edges),
                "storage_backend": "redis",
            },
        }

    @property
    def nodes(self) -> Dict[str, RedisNode]:
        """Compatibility: returns dict-like {node_id: node}. Lazy — fetches from Redis."""
        result = {}
        for n in self.get_all_nodes():
            result[n.id] = n
        return result

    @property
    def node_count(self) -> int:
        return self.get_node_count()

    @property
    def edge_count(self) -> int:
        return self.get_edge_count()

    @property
    def node_types(self) -> Dict[str, Set[str]]:
        """Compatibility: returns {label: {node_ids}}."""
        result: Dict[str, Set[str]] = {}
        # Scan ntype keys
        pattern = f"{self._prefix}ntype:*"
        cursor = 0
        while True:
            cursor, keys = self._r.scan(cursor, match=pattern, count=100)
            for key in keys:
                label = key.replace(f"{self._prefix}ntype:", "")
                result[label] = self._r.smembers(key)
            if cursor == 0:
                break
        return result
