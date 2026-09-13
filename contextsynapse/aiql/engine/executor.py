"""
AIQL Executor
Main execution engine for AIQL queries and pipelines.
"""

import logging
import uuid
import os
import json
import time
import gc
from pathlib import Path
from typing import Dict, List, Any, Optional
from ...core.graph_structures import GraphEdge, GraphNode

import hashlib
from collections import OrderedDict
from threading import Lock as _Lock

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Query Cache — LRU with TTL and write-invalidation
# ---------------------------------------------------------------------------

class _QueryCache:
    """Per-namespace LRU cache with TTL. Writes invalidate the namespace."""

    def __init__(self, max_size: int = 256, ttl_seconds: float = 30.0):
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._cache: OrderedDict = OrderedDict()  # key → (result, timestamp)
        self._lock = _Lock()

    def _make_key(self, namespace: str, query: str) -> str:
        return f"{namespace}:{hashlib.sha256(query.strip().encode()).hexdigest()[:16]}"

    def get(self, namespace: str, query: str):
        key = self._make_key(namespace, query)
        with self._lock:
            if key in self._cache:
                result, ts = self._cache[key]
                if time.time() - ts < self._ttl:
                    self._cache.move_to_end(key)
                    return result
                else:
                    del self._cache[key]
        return None

    def put(self, namespace: str, query: str, result):
        key = self._make_key(namespace, query)
        with self._lock:
            self._cache[key] = (result, time.time())
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)

    def invalidate(self, namespace: str):
        """Invalidate all cached queries for a namespace (called on writes)."""
        prefix = f"{namespace}:"
        with self._lock:
            keys = [k for k in self._cache if k.startswith(prefix)]
            for k in keys:
                del self._cache[k]

    def clear(self):
        with self._lock:
            self._cache.clear()

    @property
    def size(self):
        return len(self._cache)


# Module-level cache shared across all executor instances
_query_cache = _QueryCache()


# Ray integration
_ray_enabled = None
_ray_config = None

def _get_ray_enabled() -> bool:
    """Check if Ray is enabled and available."""
    global _ray_enabled
    if _ray_enabled is not None:
        return _ray_enabled
    
    # Check environment variable first
    if 'RAY_ENABLED' in os.environ:
        _ray_enabled = os.environ['RAY_ENABLED'].lower() == 'true'
        return _ray_enabled
    
    # Try to load from config
    try:
        from ...ray.ray_config import RayConfigLoader
        config = RayConfigLoader.load_config()
        _ray_enabled = config.enabled
        return _ray_enabled
    except Exception as e:
        logger.debug(f"Could not load Ray config: {e}")
        _ray_enabled = False
        return False

def _get_ray_config():
    """Get Ray configuration."""
    global _ray_config
    if _ray_config is not None:
        return _ray_config
    
    try:
        from ...ray.ray_config import RayConfigLoader
        _ray_config = RayConfigLoader.load_config()
        return _ray_config
    except Exception as e:
        logger.debug(f"Could not load Ray config: {e}")
        return None


def _init_shared_cache():
    """Initialize the module-level query cache (Redis if available, in-process fallback)."""
    global _query_cache
    try:
        from .redis_cache import create_query_cache
        _query_cache = create_query_cache()
    except Exception:
        pass  # keep the default in-process _QueryCache


class AIQLExecutor:
    """Main executor for AIQL queries and pipeline execution."""

    def __init__(self, contextcore=None, graph_registry=None):
        """
        Initialize AIQL executor.

        Args:
            contextcore: AIContextDB graph instance
            graph_registry: Graph registry for managing multiple namespaces
        """
        # Auto-create a graph registry if none provided so CREATE/USE GRAPH always work.
        # Use lightweight file-based registry by default (fast init, no Redis needed).
        # The API server passes its own Redis-backed registry explicitly.
        if graph_registry is None:
            try:
                from ...core.registry import GraphRegistry
                graph_registry = GraphRegistry()
            except Exception:
                pass

        self.contextcore = contextcore
        self.graph_registry = graph_registry
        self.active_namespace = None
        self.schema_parser = None
        self.page_monitor = None

    # ------------------------------------------------------------------
    # Bulk ingestion API — used by pipeline PERSIST stage
    # ------------------------------------------------------------------

    def bulk_ingest(
        self,
        namespace: str,
        nodes: list,
        edges: list,
        *,
        merge_existing: bool = True,
        on_progress=None,
    ) -> dict:
        """Bulk-create nodes and edges through the AIQL layer.

        This is the programmatic entry point that pipelines use to persist
        graph data.  It goes through the same code paths as individual
        ``CREATE NODE`` / ``CREATE EDGE`` AIQL statements but skips parsing
        overhead and uses batch writes for performance.

        Args:
            namespace: Target graph namespace.
            nodes: List of node dicts ``{id, label, properties}``.
            edges: List of edge dicts ``{id, source, target, label, properties}``.
            merge_existing: If True, merge properties into existing nodes instead
                of failing on duplicates.
            on_progress: Optional callback ``(message, progress_float)``.

        Returns:
            ``{nodes_created, nodes_merged, edges_created, errors}``
        """
        import uuid as _uuid
        from datetime import datetime

        graph = self._get_namespace_graph(namespace)
        if not graph:
            if self.graph_registry:
                self.graph_registry.create_graph(namespace)
                graph = self.graph_registry.get_graph(namespace)
            if not graph:
                return {"nodes_created": 0, "edges_created": 0,
                        "errors": [f"Cannot find or create graph: {namespace}"]}

        nodes_created = 0
        nodes_merged = 0
        edges_created = 0
        errors = []
        total = len(nodes) + len(edges)
        done = 0

        # ---- Nodes ----
        for nd in nodes:
            nid = nd.get("id") or str(_uuid.uuid4())
            label = nd.get("label", "Entity")
            props = nd.get("properties", {})
            # Don't store uuid in properties
            props.pop("uuid", None)

            gn = GraphNode(
                id=nid,
                label=label,
                properties=props.copy(),
                name=props.get("name"),
            )

            try:
                existing = graph.get_node(nid)
                if existing and merge_existing:
                    merged = {**existing.properties, **props}
                    mc = (existing.properties.get("mention_count", 0) or 0) + \
                         (props.get("mention_count", 0) or 0)
                    if mc:
                        merged["mention_count"] = mc
                    graph.update_node(nid, merged)
                    nodes_merged += 1
                elif existing:
                    pass  # skip duplicate
                else:
                    graph.add_node(gn)
                    nodes_created += 1

                    # Time-travel version
                    if getattr(graph, "temporal_enabled", False) and getattr(graph, "temporal_storage", None):
                        try:
                            graph.temporal_storage.create_version(
                                entity_id=nid,
                                entity_type="node",
                                properties=props.copy(),
                                operation="CREATE",
                                timestamp=datetime.now(),
                            )
                        except Exception:
                            pass

            except Exception as e:
                errors.append(f"Node {nid}: {e}")

            done += 1
            if on_progress and done % 50 == 0:
                on_progress(f"Created {nodes_created} nodes...", done / total)

        # ---- Edges ----
        for ed in edges:
            eid = ed.get("id") or str(_uuid.uuid4())
            source = ed.get("source", "")
            target = ed.get("target", "")
            label = ed.get("label", "RELATED_TO")
            props = ed.get("properties", {})

            if not source or not target:
                continue

            ge = GraphEdge(
                id=eid,
                source=source,
                target=target,
                label=label,
                properties=props.copy(),
            )

            try:
                graph.add_edge(ge)
                edges_created += 1
            except Exception as e:
                errors.append(f"Edge {eid}: {e}")

            done += 1
            if on_progress and done % 50 == 0:
                on_progress(f"Created {edges_created} edges...", done / total)

        # Flush write buffer
        try:
            graph.flush()
        except Exception:
            pass

        # Save to disk
        if self.graph_registry:
            try:
                self.graph_registry.save_graph(namespace, create_checkpoint=False)
            except Exception as e:
                errors.append(f"Save: {e}")

        # Invalidate query cache for this namespace after bulk writes
        _query_cache.invalidate(namespace)

        return {
            "nodes_created": nodes_created,
            "nodes_merged": nodes_merged,
            "edges_created": edges_created,
            "errors": errors[:20] if errors else [],
        }

    def _wrap_result(self, nodes=None, edges=None, data=None, **kwargs):
        """
        Wrap query results in consistent format for frontend consumption.
        
        Always returns: {"nodes": [...], "edges": [...], "data": {...}, ...}
        """
        result = {
            "nodes": nodes if nodes is not None else [],
            "edges": edges if edges is not None else [],
            "data": data if data is not None else {}
        }
        # Add any additional kwargs to the result
        result.update(kwargs)
        return result
    
    # ── Shortcut handlers for grammar gaps ─────────────────────────

    def _shortcut_delete_node(self, node_id: str, namespace: str):
        """DELETE NODE "uuid" — delete a node by ID."""
        graph = self._get_namespace_graph(namespace)
        if not graph:
            return {"success": False, "error": f"Graph '{namespace}' not found"}
        try:
            node = graph.get_node(node_id)
            if not node:
                return {"success": False, "error": f"Node '{node_id}' not found"}

            # Remove connected edges first
            edges_removed = 0
            for edge in list(graph.get_all_edges()):
                eid = getattr(edge, "id", "")
                src = getattr(edge, "source", "")
                tgt = getattr(edge, "target", "")
                if src == node_id or tgt == node_id:
                    try:
                        graph.remove_edge(eid)
                        edges_removed += 1
                    except Exception:
                        pass

            # Remove node
            graph.remove_node(node_id)

            # Clean up LMDB search index so deleted node doesn't appear in search
            try:
                from ...search.lmdb_index import lmdb_delete_node
                lmdb_delete_node(namespace, node_id)
            except Exception:
                pass

            # Save
            if self.graph_registry:
                try:
                    self.graph_registry.save_graph(namespace, create_checkpoint=False)
                except Exception:
                    pass

            label = getattr(node, "label", "?")
            return {
                "success": True,
                "message": f"Deleted node {label} (id: {node_id}), removed {edges_removed} connected edges",
                "nodes": [], "edges": [],
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _shortcut_drop_graph(self, graph_name: str, namespace: str):
        """DELETE GRAPH name — alias for DROP GRAPH."""
        if not self.graph_registry:
            return {"success": False, "error": "No graph registry"}
        try:
            result = self.graph_registry.delete_graph(graph_name)
            if result:
                return {"success": True, "message": f"Graph '{graph_name}' deleted", "nodes": [], "edges": []}
            return {"success": False, "error": f"Failed to delete graph '{graph_name}'"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _shortcut_create_node(self, label: str, props_str: str, namespace: str):
        """Fast-path CREATE NODE Label {key: "val", ...} — no Earley parser."""
        import uuid as _uuid
        import re

        graph = self._get_namespace_graph(namespace)
        if not graph:
            return {"success": False, "error": f"Graph '{namespace}' not found"}

        properties = {}
        if props_str:
            for m in re.finditer(r'(\w+)\s*:\s*"([^"]*)"', props_str):
                properties[m.group(1)] = m.group(2)
            for m in re.finditer(r'(\w+)\s*:\s*(\d+(?:\.\d+)?)', props_str):
                if m.group(1) not in properties:
                    properties[m.group(1)] = float(m.group(2)) if '.' in m.group(2) else int(m.group(2))

        node_id = str(_uuid.uuid4())
        try:
            from ...core.graph_structures import GraphNode
            node = GraphNode(id=node_id, label=label, properties=properties)
            graph.add_node(node, write_through=True)

            # Invalidate cache
            _query_cache.invalidate(namespace)

            name = properties.get("name", label)
            return self._wrap_result(
                data={"uuid": node_id, "label": label, "name": name},
                success=True, message=f"Created [{label}] {name}",
            )
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _shortcut_create_edge(self, edge_type: str, source_id: str, target_id: str,
                              props_str: str, namespace: str):
        """CREATE EDGE label FROM "uuid" TO "uuid" {props}."""
        import uuid as _uuid
        graph = self._get_namespace_graph(namespace)
        if not graph:
            return {"success": False, "error": f"Graph '{namespace}' not found"}

        # Parse properties
        properties = {}
        if props_str:
            props_str = props_str.strip()
            if props_str.startswith("{") and props_str.endswith("}"):
                inner = props_str[1:-1].strip()
                if inner:
                    # Simple key: "value" parsing
                    import re
                    for m in re.finditer(r'(\w+)\s*:\s*"([^"]*)"', inner):
                        properties[m.group(1)] = m.group(2)
                    for m in re.finditer(r'(\w+)\s*:\s*(\d+(?:\.\d+)?)', inner):
                        if m.group(1) not in properties:
                            properties[m.group(1)] = float(m.group(2)) if '.' in m.group(2) else int(m.group(2))

        # Verify source and target exist
        src_node = graph.get_node(source_id)
        tgt_node = graph.get_node(target_id)
        if not src_node:
            return {"success": False, "error": f"Source node '{source_id}' not found"}
        if not tgt_node:
            return {"success": False, "error": f"Target node '{target_id}' not found"}

        # Create edge
        try:
            from ...core.graph_structures import GraphEdge
            edge_id = str(_uuid.uuid4())
            edge = GraphEdge(
                id=edge_id, source=source_id, target=target_id,
                label=edge_type, properties=properties,
            )
            graph.add_edge(edge)

            # Save
            if self.graph_registry:
                try:
                    self.graph_registry.save_graph(namespace, create_checkpoint=False)
                except Exception:
                    pass

            return {
                "success": True,
                "message": f"Edge {edge_type} created: {source_id[:12]}... -> {target_id[:12]}...",
                "edge_id": edge_id,
                "nodes": [], "edges": [],
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _shortcut_show_edges(self, namespace: str):
        """SHOW EDGES — list all edges in the current graph."""
        graph = self._get_namespace_graph(namespace)
        if not graph:
            return {"success": False, "error": f"Graph '{namespace}' not found"}
        all_edges = graph.get_all_edges()
        seen = set()
        edges = []
        for e in all_edges:
            eid = getattr(e, "id", "")
            if eid in seen:
                continue
            seen.add(eid)
            edges.append({
                "id": eid,
                "source": getattr(e, "source", ""),
                "target": getattr(e, "target", ""),
                "label": getattr(e, "label", ""),
                "properties": getattr(e, "properties", {}),
            })
        return {
            "success": True,
            "message": f"{len(edges)} edges in graph '{namespace}'",
            "nodes": [], "edges": edges,
        }

    def _execute_find_all(self, query_upper: str, namespace: str):
        """Handle FIND NODES / FIND NODES, EDGES shorthand."""
        graph = self._get_namespace_graph(namespace)
        all_nodes = graph.get_all_nodes() if graph else []
        all_edges = graph.get_all_edges() if graph else []
        nodes = [self._format_node_for_display(n) for n in all_nodes]
        edges = [self._format_edge_for_display(e) for e in all_edges] if 'EDGES' in query_upper else []
        return self._wrap_result(nodes=nodes, edges=edges, success=True,
                                 message=f"Found {len(nodes)} nodes, {len(edges)} edges")

    def _execute_find_all_edges(self, namespace: str):
        """Handle FIND EDGES shorthand."""
        graph = self._get_namespace_graph(namespace)
        all_edges = graph.get_all_edges() if graph else []
        edges = [self._format_edge_for_display(e) for e in all_edges]
        return self._wrap_result(nodes=[], edges=edges, success=True,
                                 message=f"Found {len(edges)} edges")

    def _execute_find_where(self, query: str, namespace: str):
        """Handle FIND NODES WHERE ... / FIND EDGES WHERE ... shorthand with filtering."""
        import re
        graph = self._get_namespace_graph(namespace)
        query_upper = query.upper()
        is_edges = query_upper.startswith('FIND EDGES')

        # Handle FIND NODES WITH * (show all)
        if 'WITH *' in query_upper:
            all_nodes = graph.get_all_nodes() if graph else []
            nodes = [self._format_node_for_display(n) for n in all_nodes]
            return self._wrap_result(nodes=nodes, edges=[], success=True,
                                     message=f"Found {len(nodes)} nodes")

        # Extract WHERE clause
        where_match = re.search(r'WHERE\s+(.+?)(?:\s+ORDER\s+BY|\s+LIMIT|\s*$)', query, re.IGNORECASE)
        if not where_match:
            # No valid WHERE clause, return all
            if is_edges:
                return self._execute_find_all_edges(namespace)
            return self._execute_find_all(query_upper, namespace)

        where_clause = where_match.group(1).strip()

        # Get all items
        if is_edges:
            all_items = graph.get_all_edges() if graph else []
        else:
            all_items = graph.get_all_nodes() if graph else []

        # Parse and apply filters
        filtered = []
        for item in all_items:
            if self._item_matches_where(item, where_clause):
                filtered.append(item)

        if is_edges:
            edges = [self._format_edge_for_display(e) for e in filtered]
            return self._wrap_result(nodes=[], edges=edges, success=True,
                                     message=f"Found {len(edges)} edges")
        else:
            nodes = [self._format_node_for_display(n) for n in filtered]
            # Also fetch edges between matched nodes for graph display
            all_edges = graph.get_all_edges() if graph else []
            node_ids = {n.get('id') if isinstance(n, dict) else getattr(n, 'id', None) for n in filtered}
            related_edges = []
            for e in all_edges:
                src = e.get('source') if isinstance(e, dict) else getattr(e, 'source', None)
                tgt = e.get('target') if isinstance(e, dict) else getattr(e, 'target', None)
                if src in node_ids and tgt in node_ids:
                    related_edges.append(self._format_edge_for_display(e))
            return self._wrap_result(nodes=nodes, edges=related_edges, success=True,
                                     message=f"Found {len(nodes)} nodes, {len(related_edges)} edges")

    def _item_matches_where(self, item, where_clause: str) -> bool:
        """Evaluate a WHERE clause against a node or edge."""
        import re

        # Get properties from item
        if isinstance(item, dict):
            props = item.get('properties', {})
            label = item.get('label', '')
            item_id = item.get('id', '')
            name = item.get('name', '')
        else:
            props = getattr(item, 'properties', {}) or {}
            label = getattr(item, 'label', '') or ''
            item_id = getattr(item, 'id', '') or ''
            name = getattr(item, 'name', '') or ''

        # Build lookup dict (label, id, name + all properties)
        lookup = {'label': label, 'id': item_id, 'name': name or props.get('name', '')}
        lookup.update(props)

        # Handle OR conditions
        if ' OR ' in where_clause.upper():
            parts = re.split(r'\s+OR\s+', where_clause, flags=re.IGNORECASE)
            return any(self._item_matches_where(item, p.strip()) for p in parts)

        # Handle AND conditions
        if ' AND ' in where_clause.upper():
            parts = re.split(r'\s+AND\s+', where_clause, flags=re.IGNORECASE)
            return all(self._item_matches_where(item, p.strip()) for p in parts)

        # Handle CONTAINS
        contains_match = re.match(r'(\w+)\s+CONTAINS\s+"([^"]*)"', where_clause, re.IGNORECASE)
        if contains_match:
            field, value = contains_match.group(1), contains_match.group(2)
            field_val = str(lookup.get(field, ''))
            return value.lower() in field_val.lower()

        # Handle != operator
        neq_match = re.match(r'(\w+)\s*!=\s*"([^"]*)"', where_clause)
        if neq_match:
            field, value = neq_match.group(1), neq_match.group(2)
            return str(lookup.get(field, '')) != value

        # Handle = operator (string)
        eq_match = re.match(r'(\w+)\s*=\s*"([^"]*)"', where_clause)
        if eq_match:
            field, value = eq_match.group(1), eq_match.group(2)
            return str(lookup.get(field, '')) == value

        # Handle > operator (numeric)
        gt_match = re.match(r'(\w+)\s*>\s*(\d+(?:\.\d+)?)', where_clause)
        if gt_match:
            field, value = gt_match.group(1), float(gt_match.group(2))
            try:
                return float(lookup.get(field, 0)) > value
            except (ValueError, TypeError):
                return False

        # Handle < operator
        lt_match = re.match(r'(\w+)\s*<\s*(\d+(?:\.\d+)?)', where_clause)
        if lt_match:
            field, value = lt_match.group(1), float(lt_match.group(2))
            try:
                return float(lookup.get(field, 0)) < value
            except (ValueError, TypeError):
                return False

        # Handle >= operator
        gte_match = re.match(r'(\w+)\s*>=\s*(\d+(?:\.\d+)?)', where_clause)
        if gte_match:
            field, value = gte_match.group(1), float(gte_match.group(2))
            try:
                return float(lookup.get(field, 0)) >= value
            except (ValueError, TypeError):
                return False

        # Default: no match
        return False

    def _execute_delete_where(self, query: str, namespace: str):
        """Handle DELETE NODES WHERE ... / DELETE EDGES WHERE ... shorthand."""
        import re
        graph = self._get_namespace_graph(namespace)
        query_upper = query.upper()
        is_edges = query_upper.startswith('DELETE EDGES')

        where_match = re.search(r'WHERE\s+(.+)', query, re.IGNORECASE)
        if not where_match:
            return self._wrap_result(success=False, error="DELETE requires a WHERE clause")

        where_clause = where_match.group(1).strip()

        if is_edges:
            all_items = graph.get_all_edges() if graph else []
            to_delete = [e for e in all_items if self._item_matches_where(e, where_clause)]
            deleted = 0
            for edge in to_delete:
                eid = edge.get('id') if isinstance(edge, dict) else getattr(edge, 'id', None)
                if eid and hasattr(graph, 'remove_edge'):
                    try:
                        graph.remove_edge(eid)
                        deleted += 1
                    except Exception:
                        pass
            return self._wrap_result(success=True, message=f"Deleted {deleted} edges")
        else:
            all_items = graph.get_all_nodes() if graph else []
            to_delete = [n for n in all_items if self._item_matches_where(n, where_clause)]
            deleted = 0
            for node in to_delete:
                nid = node.get('id') if isinstance(node, dict) else getattr(node, 'id', None)
                if nid and hasattr(graph, 'remove_node'):
                    try:
                        graph.remove_node(nid)
                        deleted += 1
                    except Exception:
                        pass
            return self._wrap_result(success=True, message=f"Deleted {deleted} nodes")

    def _execute_update_where(self, query: str, namespace: str):
        """Handle UPDATE NODES SET prop = val WHERE ... shorthand."""
        import re
        graph = self._get_namespace_graph(namespace)

        # Parse: UPDATE NODES SET key = value WHERE condition
        set_match = re.search(r'SET\s+(.+?)\s+WHERE\s+(.+)', query, re.IGNORECASE)
        if not set_match:
            return self._wrap_result(success=False, error="UPDATE requires SET ... WHERE ... clause")

        set_clause = set_match.group(1).strip()
        where_clause = set_match.group(2).strip()

        # Parse SET assignments (key = value pairs)
        updates = {}
        for assignment in re.findall(r'(\w+)\s*=\s*(?:"([^"]*)"|(\d+(?:\.\d+)?))', set_clause):
            key = assignment[0]
            value = assignment[1] if assignment[1] else float(assignment[2]) if '.' in assignment[2] else int(assignment[2])
            updates[key] = value

        if not updates:
            return self._wrap_result(success=False, error="No valid SET assignments found")

        all_nodes = graph.get_all_nodes() if graph else []
        to_update = [n for n in all_nodes if self._item_matches_where(n, where_clause)]
        updated = 0
        for node in to_update:
            nid = node.get('id') if isinstance(node, dict) else getattr(node, 'id', None)
            if nid and hasattr(graph, 'update_node_properties'):
                try:
                    graph.update_node_properties(nid, updates)
                    updated += 1
                except Exception:
                    pass
        return self._wrap_result(success=True, message=f"Updated {updated} nodes")

    def _execute_aggregate_shorthand(self, query: str, namespace: str):
        """Handle AGGREGATE func(field) GROUP BY field shorthand."""
        import re
        graph = self._get_namespace_graph(namespace)

        # Parse: AGGREGATE FUNC(field) GROUP BY field
        agg_match = re.match(
            r'AGGREGATE\s+(COUNT|SUM|AVG|MIN|MAX)\s*\(\s*([^)]+)\s*\)\s+GROUP\s+BY\s+(\w+)',
            query, re.IGNORECASE
        )
        if not agg_match:
            return self._wrap_result(success=False, error="Invalid AGGREGATE syntax. Use: AGGREGATE FUNC(field) GROUP BY field")

        func = agg_match.group(1).upper()
        field = agg_match.group(2).strip()
        group_by = agg_match.group(3).strip()

        all_nodes = graph.get_all_nodes() if graph else []

        # Group nodes
        groups = {}
        for node in all_nodes:
            props = node.get('properties', {}) if isinstance(node, dict) else getattr(node, 'properties', {}) or {}
            label = node.get('label', '') if isinstance(node, dict) else getattr(node, 'label', '')
            lookup = {'label': label}
            lookup.update(props)
            group_val = str(lookup.get(group_by, 'unknown'))
            if group_val not in groups:
                groups[group_val] = []
            groups[group_val].append(lookup)

        # Compute aggregates
        results_data = []
        for group_val, items in groups.items():
            row = {group_by: group_val}
            if func == 'COUNT':
                row[f'count'] = len(items)
            elif field == '*':
                row[f'{func.lower()}'] = len(items)
            else:
                values = []
                for item in items:
                    v = item.get(field)
                    if v is not None:
                        try:
                            values.append(float(v))
                        except (ValueError, TypeError):
                            pass
                if values:
                    if func == 'SUM':
                        row[f'sum_{field}'] = sum(values)
                    elif func == 'AVG':
                        row[f'avg_{field}'] = round(sum(values) / len(values), 2)
                    elif func == 'MIN':
                        row[f'min_{field}'] = min(values)
                    elif func == 'MAX':
                        row[f'max_{field}'] = max(values)
                else:
                    row[f'{func.lower()}_{field}'] = None
            results_data.append(row)

        return self._wrap_result(data={"aggregate": results_data}, success=True,
                                 message=f"Aggregated {len(results_data)} groups")

    def _get_namespace_graph(self, namespace: str):
        """Get graph for namespace."""
        if self.graph_registry:
            g = self.graph_registry.get_graph(namespace, load_if_missing=True)
            if g:
                return g
        return self.contextcore

    def _format_node_for_display(self, node):
        """Convert a GraphNode to a displayable dict format."""
        if isinstance(node, dict):
            return node
        return {
            "id": getattr(node, 'id', None),
            "label": getattr(node, 'label', None),
            "properties": getattr(node, 'properties', {}),
            "name": getattr(node, 'name', None),
            "uuid": getattr(node, 'uuid', None)
        }
    
    def _resolve_node_display_name(self, node_id: str) -> str:
        """Resolve a node UUID to a human-readable display name."""
        if not node_id:
            return node_id
        try:
            graph = self._get_namespace_graph(self.active_namespace or "default")
            if graph and hasattr(graph, 'get_node'):
                node = graph.get_node(node_id)
                if node:
                    # Prefer 'name' property, then label, then fall back to ID
                    name = (node.properties or {}).get('name') or getattr(node, 'name', None)
                    if name:
                        return name
                    if getattr(node, 'label', None):
                        return node.label
        except Exception:
            pass
        return node_id

    def _format_edge_for_display(self, edge):
        """Convert a GraphEdge to a displayable dict format."""
        if isinstance(edge, dict):
            return edge
        source_id = getattr(edge, 'source', None)
        target_id = getattr(edge, 'target', None)
        return {
            "id": getattr(edge, 'id', None),
            "source": self._resolve_node_display_name(source_id),
            "target": self._resolve_node_display_name(target_id),
            "source_id": source_id,
            "target_id": target_id,
            "label": getattr(edge, 'label', None),
            "properties": getattr(edge, 'properties', {}),
            "name": getattr(edge, 'name', None),
            "uuid": getattr(edge, 'uuid', None)
        }
        
    def execute(self, query: str) -> Dict[str, Any]:
        """
        Execute an AIQL query with caching.

        Read queries are cached with a 30s TTL per namespace.
        Write queries (CREATE, UPDATE, DELETE, DROP, LOAD) invalidate
        the cache for the current namespace.
        """
        namespace = self.active_namespace or "default"
        query_upper_stripped = query.strip().upper()

        # Handle EXPLAIN — return query plan without executing
        if query_upper_stripped.startswith("EXPLAIN "):
            return self._handle_explain(query.strip()[8:], namespace)

        is_write = any(query_upper_stripped.startswith(w) for w in (
            'CREATE ', 'INSERT ', 'UPDATE ', 'DELETE ', 'DROP ', 'LOAD ',
        ))

        # Check cache for read queries
        if not is_write:
            cached = _query_cache.get(namespace, query)
            if cached is not None:
                logger.debug("[CACHE HIT] ns=%s query=%s", namespace, query.strip()[:80])
                return cached

        result = self._execute_inner(query)

        # Cache read results, invalidate on writes
        if is_write:
            logger.debug("[CACHE INVALIDATE] ns=%s (write: %s)", namespace, query.strip()[:60])
            _query_cache.invalidate(namespace)
        elif result and result.get("success", True):
            _query_cache.put(namespace, query, result)
            logger.debug("[CACHE STORE] ns=%s query=%s (cache_size=%d)", namespace, query.strip()[:80], _query_cache.size)

        return result

    def _handle_explain(self, query: str, namespace: str) -> Dict[str, Any]:
        """Handle EXPLAIN prefix — return query plan without executing."""
        try:
            from ..compiler.statistics import (
                GraphStatisticsCollector, QueryPlanSelector, ExplainResult,
            )
            graph = self._get_namespace_graph(namespace)
            collector = GraphStatisticsCollector(graph)
            stats = collector.collect()
            selector = QueryPlanSelector()

            # Parse the query to extract label and where
            q_upper = query.strip().upper()
            label = None
            where_props = {}
            if "FROM " in q_upper:
                parts = q_upper.split("FROM ", 1)[1].strip().split()
                if parts:
                    label = parts[0].strip()
            if "WHERE " in q_upper:
                # Basic extraction — won't handle complex WHERE
                where_part = query.split("WHERE")[1].strip() if "WHERE" in query else ""
                if "=" in where_part:
                    for cond in where_part.split("AND"):
                        cond = cond.strip()
                        if "=" in cond:
                            k, v = cond.split("=", 1)
                            where_props[k.strip().strip('"')] = v.strip().strip('"').strip("'")

            plan = selector.select_plan(stats, label=label, where_props=where_props or None)
            explain = ExplainResult(query=query, plan=plan, statistics=stats)
            return {
                "success": True,
                "data": {"type": "EXPLAIN", **explain.to_dict()},
                "nodes": [],
                "edges": [],
            }
        except Exception as e:
            return {"success": False, "error": f"EXPLAIN failed: {e}", "nodes": [], "edges": []}

    def _execute_inner(self, query: str) -> Dict[str, Any]:
        """Core query execution (no caching)."""
        try:
            from ..parser import AIQLParser
            from ..compiler import AIQLPlanner

            # Store query for temporal query fallback
            self._last_query = query

            # ── Shorthand handlers for common syntaxes not in Lark grammar ──
            query_upper = query.strip().upper()
            namespace = self.active_namespace or "default"

            # ── Fast-path SELECT shortcuts (bypass Earley parser) ──
            import re as _re

            # SELECT * — return all nodes
            if query_upper in ('SELECT *', 'SELECT *;', 'SELECT * ;'):
                graph = self._get_namespace_graph(namespace)
                if graph:
                    all_nodes = graph.get_all_nodes()
                    formatted = [self._format_node_for_display(n) for n in all_nodes]
                    return self._wrap_result(nodes=formatted, data={"query_type": "SELECT", "node_type": "*"}, success=True)
                return self._wrap_result(nodes=[], data={"query_type": "SELECT"}, success=True)

            # SELECT * FROM <Label> — return nodes of a specific type
            _m = _re.match(r'SELECT\s+\*\s+FROM\s+(\w+)\s*;?\s*$', query.strip(), _re.IGNORECASE)
            if _m:
                label = _m.group(1)
                graph = self._get_namespace_graph(namespace)
                if graph:
                    all_nodes = graph.get_all_nodes()
                    filtered = [n for n in all_nodes if (getattr(n, 'label', '') or '').lower() == label.lower()]
                    formatted = [self._format_node_for_display(n) for n in filtered]
                    return self._wrap_result(nodes=formatted, data={"query_type": "SELECT", "node_type": label}, success=True)
                return self._wrap_result(nodes=[], data={"query_type": "SELECT", "node_type": label}, success=True)

            # SELECT * WHERE <property> = "<value>" — simple property filter
            _m = _re.match(r'SELECT\s+\*\s+WHERE\s+(\w+)\s*=\s*"([^"]+)"\s*;?\s*$', query.strip(), _re.IGNORECASE)
            if _m:
                prop_name, prop_val = _m.group(1), _m.group(2)
                graph = self._get_namespace_graph(namespace)
                if graph:
                    all_nodes = graph.get_all_nodes()
                    filtered = [n for n in all_nodes if str((getattr(n, 'properties', {}) or {}).get(prop_name, '')).lower() == prop_val.lower()]
                    formatted = [self._format_node_for_display(n) for n in filtered]
                    return self._wrap_result(nodes=formatted, data={"query_type": "SELECT", "filter": f"{prop_name}={prop_val}"}, success=True)
                return self._wrap_result(nodes=[], success=True)

            # SHOW GRAPHS — list available namespaces
            if query_upper in ('SHOW GRAPHS', 'SHOW GRAPHS;'):
                graphs_list = self.graph_registry.list_graphs() if self.graph_registry else []
                return self._wrap_result(data={"graphs": graphs_list}, success=True)

            # DELETE NODE "uuid" — grammar only accepts identifier, not quoted string
            _m = _re.match(
                r'DELETE\s+NODE\s+"([^"]+)"', query.strip(), _re.IGNORECASE,
            )
            if _m:
                return self._shortcut_delete_node(_m.group(1), namespace)

            # DELETE GRAPH name — grammar uses DROP GRAPH, but users type DELETE GRAPH
            _m = _re.match(r'DELETE\s+GRAPH\s+(\S+)', query.strip(), _re.IGNORECASE)
            if _m:
                return self._shortcut_drop_graph(_m.group(1), namespace)

            # SHOW EDGES — not in grammar but commonly used
            if query_upper in ('SHOW EDGES', 'SHOW EDGES;'):
                return self._shortcut_show_edges(namespace)

            # CREATE EDGE label FROM "uuid" TO "uuid" {props}
            _m = _re.match(
                r'CREATE\s+EDGE\s+(\w+)\s+FROM\s+"([^"]+)"\s+TO\s+"([^"]+)"\s*(\{.*\})?\s*$',
                query.strip(), _re.IGNORECASE | _re.DOTALL,
            )
            if _m:
                return self._shortcut_create_edge(
                    _m.group(1), _m.group(2), _m.group(3),
                    _m.group(4), namespace,
                )

            # Handle FIND shorthand queries (not in grammar, used by frontend)
            if query_upper in ('FIND NODES', 'FIND NODES, EDGES', 'FIND EDGES, NODES'):
                return self._execute_find_all(query_upper, self.active_namespace or "default")
            if query_upper == 'FIND EDGES':
                return self._execute_find_all_edges(self.active_namespace or "default")
            # Handle FIND NODES WHERE ... / FIND EDGES WHERE ... / FIND NODES WITH *
            if query_upper.startswith('FIND NODES WHERE ') or query_upper.startswith('FIND EDGES WHERE ') or query_upper.startswith('FIND NODES WITH '):
                return self._execute_find_where(query.strip(), self.active_namespace or "default")
            # Handle DELETE nodes/edges WHERE ... shorthand
            if query_upper.startswith('DELETE NODES WHERE ') or query_upper.startswith('DELETE EDGES WHERE '):
                return self._execute_delete_where(query.strip(), self.active_namespace or "default")
            # Handle UPDATE nodes SET ... WHERE ... shorthand
            if query_upper.startswith('UPDATE NODES SET '):
                return self._execute_update_where(query.strip(), self.active_namespace or "default")
            # Handle AGGREGATE ... GROUP BY ... shorthand
            if query_upper.startswith('AGGREGATE '):
                return self._execute_aggregate_shorthand(query.strip(), self.active_namespace or "default")

            # Handle search shorthands (DENSE/SPARSE/HYBRID/SEMANTIC SEARCH)
            for search_prefix in ('DENSE SEARCH ', 'SPARSE SEARCH ', 'HYBRID SEARCH ', 'SEMANTIC SEARCH ', 'GRAPH SEARCH '):
                if query_upper.startswith(search_prefix):
                    return self._execute_search_shorthand(query.strip(), self.active_namespace or "default")

            # CREATE NODE Label {prop: "val", ...} — fast-path (bypass Earley parser)
            _m = _re.match(
                r'CREATE\s+NODE\s+(\w+)\s*\{(.*)\}\s*$',
                query.strip(), _re.IGNORECASE | _re.DOTALL,
            )
            if _m:
                return self._shortcut_create_node(_m.group(1), _m.group(2), namespace)

            # Parse query (reuse cached parser — init is expensive)
            if not hasattr(self, '_parser') or self._parser is None:
                self._parser = AIQLParser()
            parse_result = self._parser.parse(query)
            
            if not parse_result or not parse_result.get('success'):
                return {"success": False, "error": parse_result.get('error', "Failed to parse query")}
            
            # Get query type and AST from parse result
            query_type = parse_result.get('query_type')
            ast_nodes = parse_result.get('ast', [])
            
            if not ast_nodes:
                return {"success": False, "error": "Empty AST"}
            
            # Handle multiple statements (separated by semicolons)
            # The parser returns a list of AST nodes for multi-statement queries
            if isinstance(ast_nodes, list) and len(ast_nodes) > 1:
                # Process all statements in sequence
                results = {}
                import sys
                for idx, ast_node in enumerate(ast_nodes):
                    # Map node_type (AIQLNodeType enum) to query_type string
                    node_query_type = None
                    if hasattr(ast_node, 'node_type'):
                        # AST nodes have node_type as AIQLNodeType enum
                        from ..parser.aiql_parser import AIQLNodeType
                        node_type_enum = ast_node.node_type
                        # Map enum to query type string
                        node_type_map = {
                            AIQLNodeType.USE_NAMESPACE: "USE_NAMESPACE",
                            AIQLNodeType.CREATE_NAMESPACE: "CREATE_NAMESPACE",
                            AIQLNodeType.RUN_PIPELINE: "RUN_PIPELINE",
                            AIQLNodeType.CREATE_PIPELINE: "CREATE_PIPELINE",
                            AIQLNodeType.SELECT: "SELECT",
                            AIQLNodeType.CREATE_NODE: "CREATE_NODE",
                            AIQLNodeType.UPDATE_NODE: "UPDATE_NODE",
                            AIQLNodeType.DELETE_NODE: "DELETE_NODE",
                            AIQLNodeType.CREATE_EDGE: "CREATE_EDGE",
                            AIQLNodeType.UPDATE_EDGE: "UPDATE_EDGE",
                            AIQLNodeType.DELETE_EDGE: "DELETE_EDGE",
                            AIQLNodeType.TRAVERSE: "TRAVERSE",
                            AIQLNodeType.MATCH: "MATCH",
                            AIQLNodeType.TEMPORAL_QUERY: "TEMPORAL_QUERY",
                            AIQLNodeType.LOAD_CSV: "LOAD_CSV",
                            AIQLNodeType.LOAD_EXCEL: "LOAD_EXCEL",
                            AIQLNodeType.LOAD_JSON: "LOAD_JSON",
                            AIQLNodeType.LOAD_XML: "LOAD_XML",
                            AIQLNodeType.LOAD_DOCUMENT: "LOAD_DOCUMENT",
                            AIQLNodeType.LOAD_FOLDER: "LOAD_FOLDER",
                            AIQLNodeType.CLASSIFY_FILE: "CLASSIFY_FILE",
                        }
                        node_query_type = node_type_map.get(node_type_enum)
                    
                    # Fallback: try other methods
                    if not node_query_type:
                        if hasattr(ast_node, 'query_type'):
                            node_query_type = ast_node.query_type
                        elif hasattr(ast_node, 'data') and isinstance(ast_node.data, dict):
                            node_query_type = ast_node.data.get('query_type')
                        else:
                            # Last resort: use main query_type
                            node_query_type = query_type
                    
                    # Get namespace (may change during execution)
                    namespace = self.active_namespace or "default"
                    if self.graph_registry:
                        namespace_graph = self.graph_registry.get_graph(namespace, load_if_missing=True)
                    else:
                        namespace_graph = self.contextcore
                    
                    # Execute this statement
                    result = self._execute_single_statement(ast_node, node_query_type, namespace, namespace_graph)
                    
                    # Store result with a key based on query type
                    if isinstance(result, dict):
                        # Use step_name for RUN_PIPELINE, or query_type for others
                        result_key = result.get('step_name') or node_query_type.lower().replace('_', '_')
                        results[result_key] = result
                    else:
                        results[node_query_type.lower().replace('_', '_')] = result
                
                return results if results else {"success": True}
            
            # Single statement - use existing logic
            first_node = ast_nodes[0] if isinstance(ast_nodes, list) else ast_nodes
            
            # If query_type is not set or is UNKNOWN, try to get it from the AST node
            if not query_type or query_type == "UNKNOWN":
                if hasattr(first_node, 'node_type'):
                    from ..parser.aiql_parser import AIQLNodeType
                    node_type_enum = first_node.node_type
                    node_type_map = {
                        AIQLNodeType.USE_NAMESPACE: "USE_NAMESPACE",
                        AIQLNodeType.CREATE_NAMESPACE: "CREATE_NAMESPACE",
                        AIQLNodeType.RUN_PIPELINE: "RUN_PIPELINE",
                        AIQLNodeType.CREATE_PIPELINE: "CREATE_PIPELINE",
                        AIQLNodeType.SELECT: "SELECT",
                        AIQLNodeType.CREATE_NODE: "CREATE_NODE",
                        AIQLNodeType.UPDATE_NODE: "UPDATE_NODE",
                        AIQLNodeType.DELETE_NODE: "DELETE_NODE",
                        AIQLNodeType.CREATE_EDGE: "CREATE_EDGE",
                        AIQLNodeType.UPDATE_EDGE: "UPDATE_EDGE",
                        AIQLNodeType.DELETE_EDGE: "DELETE_EDGE",
                        AIQLNodeType.TRAVERSE: "TRAVERSE",
                        AIQLNodeType.MATCH: "MATCH",
                        AIQLNodeType.MATCH_ENTITY: "MATCH",
                        AIQLNodeType.TEMPORAL_QUERY: "TEMPORAL_QUERY",
                        AIQLNodeType.CREATE_GRAPH: "CREATE_GRAPH",
                        AIQLNodeType.USE_GRAPH: "USE_GRAPH",
                        AIQLNodeType.SHOW_NAMESPACES: "SHOW_NAMESPACES",
                        AIQLNodeType.SHOW_GRAPHS: "SHOW_GRAPHS",
                        AIQLNodeType.SHOW_CURRENT_GRAPH: "SHOW_CURRENT_GRAPH",
                        AIQLNodeType.SHOW: "SHOW",
                        AIQLNodeType.DESCRIBE: "DESCRIBE",
                    }
                    query_type = node_type_map.get(node_type_enum, query_type)
            
            # Get namespace
            namespace = self.active_namespace or "default"
            if self.graph_registry:
                namespace_graph = self.graph_registry.get_graph(namespace, load_if_missing=True)
            else:
                namespace_graph = self.contextcore
            
            # Fallback to contextcore if registry returns None
            if namespace_graph is None:
                namespace_graph = self.contextcore
            
            return self._execute_single_statement(first_node, query_type, namespace, namespace_graph)
                
        except Exception as e:
            logger.error(f"Query execution failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {"success": False, "error": str(e)}
    
    def _execute_single_statement(self, ast_node, query_type: str, namespace: str, namespace_graph, original_query: str = None) -> Dict[str, Any]:
        """Execute a single AST statement."""
        import sys
        
        # Handle different query types
        if query_type == "CREATE_PIPELINE":
            # Extract data from AST node - ast_node is an AIQLNode
            # The parser stores data in 'parameters' attribute
            return self._execute_create_pipeline(ast_node, namespace, namespace_graph)
        elif query_type == "RUN_PIPELINE":
            # Extract data from 'parameters' attribute (parser stores it there)
            data = ast_node.parameters if hasattr(ast_node, 'parameters') else (ast_node.data if hasattr(ast_node, 'data') else {})
            result = self._execute_run_pipeline(data, namespace, namespace_graph)
            return result
        elif query_type in ("LOAD_CSV", "LOAD_EXCEL", "LOAD_JSON", "LOAD_XML", "LOAD_DOCUMENT", "LOAD_FOLDER"):
            data = ast_node.parameters if hasattr(ast_node, 'parameters') else (ast_node.data if hasattr(ast_node, 'data') else {})
            return self._execute_load_file(data, namespace, namespace_graph)
        elif query_type == "CLASSIFY_FILE":
            data = ast_node.parameters if hasattr(ast_node, 'parameters') else (ast_node.data if hasattr(ast_node, 'data') else {})
            return self._execute_classify_file(data, namespace)
        elif query_type == "CREATE_NAMESPACE":
            return {"success": True, "message": "Namespace created"}
        elif query_type == "USE_NAMESPACE":
            # Extract namespace from parameters (parser stores it as 'namespace_name')
            namespace_name = None
            if hasattr(ast_node, 'parameters') and isinstance(ast_node.parameters, dict):
                namespace_name = ast_node.parameters.get('namespace_name') or ast_node.parameters.get('namespace')
            elif hasattr(ast_node, 'data') and isinstance(ast_node.data, dict):
                namespace_name = ast_node.data.get('namespace_name') or ast_node.data.get('namespace')
            
            if namespace_name:
                self.active_namespace = namespace_name
                return {"success": True, "message": f"Using namespace: {self.active_namespace}"}
            else:
                import sys
                return {"success": False, "error": "Namespace name not found in USE_NAMESPACE statement"}
        elif query_type == "SELECT":
            # Get parameters from AST node
            node_data = ast_node.parameters if hasattr(ast_node, 'parameters') else (ast_node.data if hasattr(ast_node, 'data') else {})
            return self._execute_select(node_data, namespace, namespace_graph)
        elif query_type == "CREATE_NODE":
            node_data = ast_node.parameters if hasattr(ast_node, 'parameters') else (ast_node.data if hasattr(ast_node, 'data') else {})
            return self._execute_create_node(node_data, namespace, namespace_graph)
        elif query_type == "UPDATE_NODE":
            node_data = ast_node.parameters if hasattr(ast_node, 'parameters') else (ast_node.data if hasattr(ast_node, 'data') else {})
            return self._execute_update_node(node_data, namespace, namespace_graph)
        elif query_type == "DELETE_NODE":
            node_data = ast_node.parameters if hasattr(ast_node, 'parameters') else (ast_node.data if hasattr(ast_node, 'data') else {})
            return self._execute_delete_node(node_data, namespace, namespace_graph)
        elif query_type == "CREATE_EDGE":
            node_data = ast_node.parameters if hasattr(ast_node, 'parameters') else (ast_node.data if hasattr(ast_node, 'data') else {})
            return self._execute_create_edge(node_data, namespace, namespace_graph)
        elif query_type == "UPDATE_EDGE":
            node_data = ast_node.parameters if hasattr(ast_node, 'parameters') else (ast_node.data if hasattr(ast_node, 'data') else {})
            return self._execute_update_edge(node_data, namespace, namespace_graph)
        elif query_type == "DELETE_EDGE":
            node_data = ast_node.parameters if hasattr(ast_node, 'parameters') else (ast_node.data if hasattr(ast_node, 'data') else {})
            return self._execute_delete_edge(node_data, namespace, namespace_graph)
        elif query_type == "TRAVERSE":
            node_data = ast_node.parameters if hasattr(ast_node, 'parameters') else (ast_node.data if hasattr(ast_node, 'data') else {})
            return self._execute_traverse(node_data, namespace, namespace_graph)
        elif query_type == "FIND_BY_UUID":
            node_data = ast_node.parameters if hasattr(ast_node, 'parameters') else (ast_node.data if hasattr(ast_node, 'data') else {})
            return self._execute_find_by_uuid(node_data, namespace, namespace_graph)
        elif query_type == "MATCH" or query_type == "MATCH_ENTITY":
            node_data = ast_node.parameters if hasattr(ast_node, 'parameters') else (ast_node.data if hasattr(ast_node, 'data') else {})
            return self._execute_match(node_data, namespace, namespace_graph)
        elif query_type == "TEMPORAL_QUERY":
            node_data = ast_node.parameters if hasattr(ast_node, 'parameters') else (ast_node.data if hasattr(ast_node, 'data') else {})
            return self._execute_temporal_query(node_data, namespace, namespace_graph)
        elif query_type == "SHOW_NAMESPACES":
            return self._execute_show_namespaces()
        elif query_type == "SHOW_GRAPHS":
            return self._execute_show_graphs()
        elif query_type == "SHOW_COLLECTIONS":
            return self._execute_show_collections(namespace)
        elif query_type == "SHOW_PIPELINES":
            return self._execute_show_pipelines(namespace)
        elif query_type == "SHOW_INDEXES":
            return self._execute_show_indexes(namespace)
        elif query_type == "SHOW_STATS":
            return self._execute_show_stats(namespace, namespace_graph)
        elif query_type == "SHOW_CURRENT_GRAPH":
            return self._execute_show_current_graph(namespace)
        elif query_type == "SHOW":
            # Generic SHOW handler - check what to show
            node_data = ast_node.parameters if hasattr(ast_node, 'parameters') else (ast_node.data if hasattr(ast_node, 'data') else {})
            show_type = node_data.get('show_type') or node_data.get('target')
            if show_type == 'COLLECTIONS':
                return self._execute_show_collections(namespace)
            elif show_type == 'PIPELINES':
                return self._execute_show_pipelines(namespace)
            elif show_type == 'INDEXES':
                return self._execute_show_indexes(namespace)
            elif show_type == 'STATS' or show_type == 'STATISTICS':
                return self._execute_show_stats(namespace, namespace_graph)
            else:
                return {"success": False, "error": f"Unknown SHOW target: {show_type}"}
        elif query_type == "DESCRIBE":
            node_data = ast_node.parameters if hasattr(ast_node, 'parameters') else (ast_node.data if hasattr(ast_node, 'data') else {})
            return self._execute_describe(node_data, namespace, namespace_graph)
        elif query_type == "CREATE_GRAPH":
            node_data = ast_node.parameters if hasattr(ast_node, 'parameters') else (ast_node.data if hasattr(ast_node, 'data') else {})
            return self._execute_create_graph(node_data, namespace)
        elif query_type == "USE_GRAPH":
            node_data = ast_node.parameters if hasattr(ast_node, 'parameters') else (ast_node.data if hasattr(ast_node, 'data') else {})
            return self._execute_use_graph(node_data, namespace)
        elif query_type == "VARIABLE_DECL" or (query_type == "UNKNOWN" and hasattr(ast_node, 'node_type') and str(ast_node.node_type) == "AIQLNodeType.VARIABLE_DECL"):
            # Handle variable declarations (LET statements)
            # Variables are already stored in the parser, just return success
            node_data = ast_node.parameters if hasattr(ast_node, 'parameters') else (ast_node.data if hasattr(ast_node, 'data') else {})
            var_name = node_data.get('name')
            var_value = node_data.get('value')
            return {
                "success": True,
                "message": f"Variable ${var_name} declared with value: {var_value}",
                "variable": var_name,
                "value": var_value
            }
        elif query_type in ("SEARCH_QUERY", "HYBRID_SEARCH", "HYBRID_SEARCH_WITH_WEIGHTS", "HYBRID_SEARCH_WITH_PROFILE"):
            node_data = ast_node.parameters if hasattr(ast_node, 'parameters') else (ast_node.data if hasattr(ast_node, 'data') else {})
            return self._execute_search(node_data, namespace, namespace_graph)
        elif query_type == "UNKNOWN":
            # Handle unknown query types - if it's a variable, return success
            node_data = ast_node.parameters if hasattr(ast_node, 'parameters') else (ast_node.data if hasattr(ast_node, 'data') else {})
            if node_data.get('name'):  # It's a variable declaration
                var_name = node_data.get('name')
                var_value = node_data.get('value')
                return {
                    "success": True,
                    "message": f"Variable ${var_name} declared with value: {var_value}",
                    "variable": var_name,
                    "value": var_value
                }
            logger.warning(f"Unsupported query type: {query_type}")
            return {"success": False, "error": f"Unsupported query type: {query_type}"}
        else:
            logger.warning(f"Unsupported query type: {query_type}")
            return {"success": False, "error": f"Unsupported query type: {query_type}"}
    
    def _execute_create_pipeline(self, ast_node, namespace: str, namespace_graph) -> Dict[str, Any]:
        """Execute CREATE PIPELINE command."""
        try:
            # ast_node is an AIQLNode - extract data from 'parameters' attribute
            # The parser stores pipeline data in the 'parameters' field
            data = ast_node.parameters if hasattr(ast_node, 'parameters') else (ast_node.data if hasattr(ast_node, 'data') else {})
            
            logger.debug(f"CREATE_PIPELINE data type: {type(data)}, keys: {data.keys() if isinstance(data, dict) else 'not a dict'}")
            
            pipeline_name = data.get('pipeline_name') if isinstance(data, dict) else None
            stages = data.get('stages', []) if isinstance(data, dict) else []
            
            # Store pipeline definition
            if not hasattr(self, '_pipelines'):
                self._pipelines = {}
            
            # Extract other pipeline properties
            source_collection = data.get('source_collection', 'raw_docs') if isinstance(data, dict) else 'raw_docs'
            target_collection = data.get('target_collection', 'processed_docs') if isinstance(data, dict) else 'processed_docs'
            
            # Extract checkpoint policy from pipeline data
            checkpoint_policy = data.get('checkpoint_policy', 'disabled') if isinstance(data, dict) else 'disabled'
            
            self._pipelines[pipeline_name] = {
                'name': pipeline_name,
                'namespace': namespace,
                'stages': stages,
                'source_collection': source_collection,
                'target_collection': target_collection,
                'checkpoint_policy': checkpoint_policy,
                'ast': ast_node
            }
            
            logger.info(f"Pipeline created: {pipeline_name} with {len(stages)} stages")
            import sys
            for i, stage in enumerate(stages):
                step_name = stage.get('step_name', 'unknown') if isinstance(stage, dict) else 'unknown'
                stage_type = stage.get('type', stage.get('stage_type', 'unknown')) if isinstance(stage, dict) else 'unknown'
            
            return {"success": True, "pipeline_name": pipeline_name}
            
        except Exception as e:
            logger.error(f"Pipeline creation failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            print(f"[ERROR] Pipeline creation exception: {e}")
            traceback.print_exc()
            return {"success": False, "error": str(e)}
    
    def _execute_load_file(self, data: Dict, namespace: str, namespace_graph) -> Dict[str, Any]:
        """Execute LOAD CSV/EXCEL/JSON/XML/DOCUMENT — ingest a file into the active graph."""
        import os
        load_type = data.get('load_type', 'DOCUMENT')
        file_path = data.get('path', '')
        target_graph = data.get('target', namespace)

        if not file_path:
            return {"success": False, "error": "No file path specified"}
        if not os.path.exists(file_path):
            return {"success": False, "error": f"File not found: {file_path}"}

        try:
            from ..ingestion.stage_executor import StageExecutor
            from ..ingestion.scenario_router import ScenarioRouter
            from ..ingestion.pipeline_context import PipelineContext, ClassificationResult

            # Use the scenario-driven pipeline
            ctx = PipelineContext(
                graph_namespace=target_graph,
                stages=["PARSE_FILE", "CLASSIFY"],
                source_bytes_path=file_path,
                source_filename=os.path.basename(file_path),
                intent="graph_rag",
            )

            executor = StageExecutor(graph_registry=self.graph_registry)
            executor.execute_all(ctx)

            if ctx.status == "failed":
                return {"success": False, "error": f"File parsing/classification failed", "details": ctx.stage_results}

            # Route remaining stages
            classification = ClassificationResult.from_dict(ctx.classification) if ctx.classification else None
            if classification:
                router = ScenarioRouter()
                remaining = router.route(classification, "graph_rag")
            else:
                remaining = ["CHUNK", "PERSIST"]

            ctx.stages = remaining
            ctx.current_stage_index = 0
            ctx.status = "running"
            executor.execute_all(ctx)

            return {
                "success": ctx.status != "failed",
                "load_type": load_type,
                "file": file_path,
                "graph": target_graph,
                "classification": ctx.classification,
                "nodes_created": len(ctx.nodes),
                "edges_created": len(ctx.edges),
                "stages": [sr["stage_name"] for sr in ctx.stage_results],
            }
        except Exception as e:
            logger.error("LOAD %s failed: %s", load_type, e)
            return {"success": False, "error": str(e)}

    def _execute_classify_file(self, data: Dict, namespace: str) -> Dict[str, Any]:
        """Execute CLASSIFY FILE — detect file type and structure without ingesting."""
        import os
        file_path = data.get('file_path', '')

        if not file_path:
            return {"success": False, "error": "No file path specified"}
        if not os.path.exists(file_path):
            return {"success": False, "error": f"File not found: {file_path}"}

        try:
            from ..ingestion.stage_executor import StageExecutor
            from ..ingestion.pipeline_context import PipelineContext
            from ..ingestion.scenario_router import ScenarioRouter

            ctx = PipelineContext(
                graph_namespace=namespace,
                stages=["PARSE_FILE", "CLASSIFY"],
                source_bytes_path=file_path,
                source_filename=os.path.basename(file_path),
            )

            executor = StageExecutor(graph_registry=self.graph_registry)
            executor.execute_all(ctx)

            classification = ctx.classification or {}

            # Also show what pipeline would be selected
            from ..ingestion.pipeline_context import ClassificationResult
            if ctx.classification:
                cr = ClassificationResult.from_dict(ctx.classification)
                router = ScenarioRouter()
                suggested_stages = router.route(cr, "graph_rag")
            else:
                suggested_stages = []

            return {
                "success": ctx.status != "failed",
                "file": file_path,
                "classification": classification,
                "suggested_pipeline_stages": suggested_stages,
                "extraction_summary": {
                    "pages": len((ctx.extraction_result or {}).get("pages", [])),
                    "tables": len((ctx.extraction_result or {}).get("tables", [])),
                },
            }
        except Exception as e:
            logger.error("CLASSIFY FILE failed: %s", e)
            return {"success": False, "error": str(e)}

    def _execute_run_pipeline(self, ast_data, namespace: str, namespace_graph) -> Dict[str, Any]:
        """Execute RUN PIPELINE command."""
        import sys
        try:
            # ast_data is already a dict from the execute() method
            pipeline_name = ast_data.get('pipeline_name') if isinstance(ast_data, dict) else None
            from_step = ast_data.get('from_step') if isinstance(ast_data, dict) else None
            checkpoint_id = ast_data.get('checkpoint') if isinstance(ast_data, dict) else None
            
            # Initialize stage_data if needed (before checkpoint restoration)
            # Use namespace-specific stage_data to persist across separate RUN commands
            if not hasattr(self, '_stage_data'):
                self._stage_data = {}
            
            # Use namespace as key to persist data across separate RUN commands
            if namespace not in self._stage_data:
                self._stage_data[namespace] = {}
            
            # Handle checkpoint restoration if specified
            if checkpoint_id:
                print(f"[CHECKPOINT] Restoring from checkpoint: {checkpoint_id}", flush=True)
                from ...core.checkpoint import CheckpointManager
                checkpoint_manager = CheckpointManager(base_dir="contextcore_data")
                
                # Get graph file path for this namespace
                if self.graph_registry:
                    graph_file = self.graph_registry.get_graph_file_path(namespace)
                elif self.contextcore:
                    # Try to get graph file path from contextsynapse
                    if hasattr(self.contextcore, 'graph_file'):
                        graph_file = self.contextsynapse.graph_file
                    else:
                        # Default path
                        graph_file = f"contextcore_data/namespaces/{namespace}/graph.json"
                else:
                    graph_file = f"contextcore_data/namespaces/{namespace}/graph.json"
                
                # Restore checkpoint (now returns dict with success and stage_data)
                restore_result = checkpoint_manager.restore_checkpoint(
                    namespace=namespace,
                    checkpoint_id=checkpoint_id,
                    target_graph_file=graph_file
                )
                
                if restore_result.get("success"):
                    print(f"[CHECKPOINT] Successfully restored from checkpoint: {checkpoint_id}", flush=True)
                    
                    # Restore stage_data if available
                    if "stage_data" in restore_result:
                        self._stage_data[namespace] = restore_result["stage_data"]
                        print(f"[CHECKPOINT] Restored stage_data with keys: {list(restore_result['stage_data'].keys())}", flush=True)
                    
                    # Reload graph after restoration
                    if self.graph_registry:
                        namespace_graph = self.graph_registry.get_graph(namespace, load_if_missing=True)
                    else:
                        namespace_graph = self.contextcore
                else:
                    error_msg = restore_result.get("error", "Unknown error")
                    print(f"[CHECKPOINT] WARNING: Failed to restore checkpoint: {checkpoint_id}. Error: {error_msg}. Continuing with current state.", flush=True)
            
            # Get pipeline definition
            if not hasattr(self, '_pipelines'):
                self._pipelines = {}
            
            import sys
            
            if pipeline_name not in self._pipelines:
                return {"success": False, "error": f"Pipeline not found: {pipeline_name}. Available: {list(self._pipelines.keys())}"}
            
            pipeline = self._pipelines[pipeline_name]
            stages = pipeline['stages']
            
            # Find the step to run from
            stage_to_run = None
            import sys
            
            # Determine which stages to run
            stages_to_run = []
            if from_step:
                # Run specific stage
                for i, stage in enumerate(stages):
                    step_name = stage.get('step_name') if isinstance(stage, dict) else None
                    if step_name == from_step:
                        stages_to_run = [stage]
                        break
                if not stages_to_run:
                    return {"success": False, "error": f"Stage not found: {from_step}"}
            else:
                # Run all stages in sequence
                stages_to_run = stages
            
            # Get collections from pipeline
            source_collection = pipeline.get('source_collection', 'raw_docs')
            target_collection = pipeline.get('target_collection', 'processed_docs')
            
            # stage_data is already initialized above (before checkpoint restoration)
            stage_data = self._stage_data[namespace]
            import sys
            
            # Execute all stages in sequence
            all_results = {}
            for stage_to_run in stages_to_run:
                stage_type = stage_to_run.get('type') or stage_to_run.get('stage_type') if isinstance(stage_to_run, dict) else None
                step_name = stage_to_run.get('step_name') if isinstance(stage_to_run, dict) else None
                
            
                
                # Execute based on stage type
                if stage_type == 'EXTRACT' or step_name == 'extract':
                    import time
                    stage_start = time.time()
                    print(f"\n{'='*80}")
                    print(f"STAGE: {step_name.upper()} - Starting...")
                    print(f"{'='*80}")
                    result = self._execute_extract_stage(stage_to_run, stage_data, namespace, source_collection, target_collection, namespace_graph)
                    stage_time = time.time() - stage_start
                    print(f"{'='*80}")
                    print(f"STAGE: {step_name.upper()} - Completed in {stage_time:.2f} seconds ({stage_time/60:.2f} minutes)")
                    print(f"{'='*80}\n")
                    all_results[step_name] = result
                elif stage_type == 'CONNECT' or step_name == 'connect':
                    import time
                    stage_start = time.time()
                    print(f"\n{'='*80}")
                    print(f"STAGE: {step_name.upper()} - Starting...")
                    print(f"{'='*80}")
                    result = self._execute_connect_stage(stage_to_run, stage_data, namespace, source_collection, target_collection, namespace_graph)
                    stage_time = time.time() - stage_start
                    print(f"{'='*80}")
                    print(f"STAGE: {step_name.upper()} - Completed in {stage_time:.2f} seconds ({stage_time/60:.2f} minutes)")
                    print(f"{'='*80}\n")
                    all_results[step_name] = result
                elif stage_type == 'CHUNK' or step_name == 'chunk':
                    import time
                    stage_start = time.time()
                    print(f"\n{'='*80}")
                    print(f"STAGE: {step_name.upper()} - Starting...")
                    print(f"{'='*80}")
                    result = self._execute_chunk_stage(stage_to_run, stage_data, namespace, source_collection, target_collection, namespace_graph)
                    stage_time = time.time() - stage_start
                    print(f"{'='*80}")
                    print(f"STAGE: {step_name.upper()} - Completed in {stage_time:.2f} seconds ({stage_time/60:.2f} minutes)")
                    print(f"{'='*80}\n")
                    all_results[step_name] = result
                elif stage_type == 'EMBED' or step_name == 'embed':
                    import time
                    stage_start = time.time()
                    print(f"\n{'='*80}")
                    print(f"STAGE: {step_name.upper()} - Starting...")
                    print(f"{'='*80}")
                    result = self._execute_embed_stage(stage_to_run, stage_data, namespace, source_collection, target_collection, namespace_graph)
                    stage_time = time.time() - stage_start
                    print(f"{'='*80}")
                    print(f"STAGE: {step_name.upper()} - Completed in {stage_time:.2f} seconds ({stage_time/60:.2f} minutes)")
                    print(f"{'='*80}\n")
                    all_results[step_name] = result
                elif stage_type == 'EXTRACT_ENTITIES' or step_name == 'extract_entities':
                    import time
                    stage_start = time.time()
                    print(f"\n{'='*80}")
                    print(f"STAGE: {step_name.upper()} - Starting...")
                    print(f"{'='*80}")
                    result = self._execute_extract_entities_stage(stage_to_run, stage_data, namespace, source_collection, target_collection, namespace_graph)
                    stage_time = time.time() - stage_start
                    print(f"{'='*80}")
                    print(f"STAGE: {step_name.upper()} - Completed in {stage_time:.2f} seconds ({stage_time/60:.2f} minutes)")
                    print(f"{'='*80}\n")
                    all_results[step_name] = result
                else:
                    import sys
                    all_results[step_name] = {"success": False, "error": f"Unsupported stage type: {stage_type}"}
                
                # Create checkpoint after each stage if checkpoint policy is enabled
                checkpoint_policy = pipeline.get('checkpoint_policy', 'disabled')
                if checkpoint_policy == 'enabled':
                    # Check if stage completed successfully
                    stage_succeeded = False
                    if isinstance(result, dict):
                        status = result.get('status', '')
                        # Consider stage successful if status is 'completed' or not 'failed'
                        stage_succeeded = status == 'completed' or (status != 'failed' and status != 'error')
                    elif result is not None:
                        # If result is not a dict but not None, assume success
                        stage_succeeded = True
                    
                    if stage_succeeded:
                        try:
                            from ...core.checkpoint import CheckpointManager
                            checkpoint_manager = CheckpointManager(base_dir="contextcore_data")
                            
                            # Get graph file path for this namespace
                            if self.graph_registry:
                                # Use _get_namespace_path method
                                namespace_path = self.graph_registry._get_namespace_path(namespace)
                                if namespace_path:
                                    graph_file = str(namespace_path)
                                else:
                                    graph_file = f"contextcore_data/namespaces/{namespace}/graph.json"
                            elif self.contextcore:
                                if hasattr(self.contextcore, 'graph_file'):
                                    graph_file = self.contextsynapse.graph_file
                                else:
                                    graph_file = f"contextcore_data/namespaces/{namespace}/graph.json"
                            else:
                                graph_file = f"contextcore_data/namespaces/{namespace}/graph.json"
                            
                            # Create checkpoint after successful stage (with stage_data for resumption)
                            # Save a deep copy of stage_data at this point to capture all intermediate state
                            import copy
                            checkpoint_stage_data = copy.deepcopy(stage_data) if stage_data else {}
                            
                            # Add stage result to checkpoint data for reference
                            checkpoint_stage_data['_last_stage_result'] = {
                                'stage': step_name,
                                'result': result
                            }
                            
                            checkpoint_id = checkpoint_manager.create_checkpoint(
                                namespace=namespace,
                                graph_file=graph_file,
                                message=f"Pipeline checkpoint after {step_name} stage",
                                stage_data=checkpoint_stage_data
                            )
                            print(f"[CHECKPOINT] Created checkpoint: {checkpoint_id} after {step_name} stage", flush=True)
                            
                            # Store checkpoint_id in result if result is a dict
                            if isinstance(result, dict):
                                result['checkpoint_id'] = checkpoint_id
                            all_results[step_name]['checkpoint_id'] = checkpoint_id
                        except Exception as e:
                            logger.warning(f"Failed to create checkpoint after {step_name} stage: {e}")
                            print(f"[CHECKPOINT] WARNING: Failed to create checkpoint: {e}", flush=True)
                            import traceback
                            logger.debug(traceback.format_exc())
            
            # Return all results
            all_results["success"] = all(v.get("status") != "failed" if isinstance(v, dict) else True for v in all_results.values())
            return all_results
                
        except Exception as e:
            logger.error(f"Pipeline execution failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {"success": False, "error": str(e)}
    
    def _execute_extract_stage(self, stage, stage_data, namespace, source_collection, target_collection, namespace_graph) -> Dict[str, Any]:
        """Execute EXTRACT stage."""
        try:
            from .extraction_integration import integrate_extraction_subsystem
            
            result = integrate_extraction_subsystem(
                stage=stage,
                namespace=namespace,
                source_collection=source_collection,
                target_collection=target_collection,
                namespace_graph=namespace_graph
            )
            
            
            if result:
                # Store result in stage_data for next stages (namespace-specific)
                stage_data['extract'] = result
                return result
            else:
                return {"status": "failed", "error": "Extraction returned None"}
                
        except Exception as e:
            logger.error(f"EXTRACT stage failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            print(f"[ERROR] EXTRACT stage exception: {e}")
            traceback.print_exc()
            return {"status": "failed", "error": str(e)}
    
    def _execute_connect_stage(self, stage, stage_data, namespace, source_collection, target_collection, namespace_graph) -> Dict[str, Any]:
        """Execute CONNECT stage - create nodes from normalized JSON."""
        import sys
        import json
        import traceback
        import time
        import gc
        import uuid
        
        
        try:
            from .node_creation_from_normalized import NodeCreatorFromNormalized
            
            if hasattr(self, '_stage_data') and namespace in self._stage_data:
                stage_data = self._stage_data[namespace]

            # Get extraction result from previous stage
            extract_result = stage_data.get('extract', {})
            
            
            if not extract_result:
                return {"status": "failed", "error": "No extraction result found. Run EXTRACT stage first."}
            
            normalized_doc = extract_result.get('normalized_doc')
            document_id = extract_result.get('document_id')
            
            
            if not normalized_doc or not document_id:
                return {"status": "failed", "error": "Missing normalized_doc or document_id in extraction result"}
            
            # Check what's in the normalized doc
            if isinstance(normalized_doc, dict):
                content = normalized_doc.get('content', {})
                tables = content.get('tables', [])
                pages = content.get('pages', [])
                images = content.get('images', [])
            
            # Parse node types from stage
            node_types = stage.get('create_nodes', ['Document', 'Table', 'Image'])
            if isinstance(node_types, str):
                # Parse from string like "(Document, Table, Image)"
                import re
                node_types = re.findall(r'(\w+)', node_types)
            
            # Parse edge types
            edge_types = stage.get('link', {})
            
            # Create node creator
            node_creator = NodeCreatorFromNormalized(namespace=namespace)
            
            # Create all nodes and edges
            result = node_creator.create_all_nodes_and_edges(
                normalized_doc=normalized_doc,
                document_id=document_id,
                node_types=node_types,
                create_per_page=False,
                edge_types=edge_types
            )
            
            
            
            # Import performance monitoring
            from .performance_monitor import PerformanceMonitor, BatchProcessor
            
            # CRITICAL: Ensure graph is registered in registry and has correct name
            # This is required for _save_to_disk() to work properly
            if self.graph_registry and namespace_graph:
                # Ensure graph name is set to namespace
                if not hasattr(namespace_graph, 'name') or namespace_graph.name != namespace:
                    namespace_graph.name = namespace
                
                # Ensure graph is registered in registry (required for save operations)
                if namespace not in self.graph_registry.graphs:
                    self.graph_registry.graphs[namespace] = namespace_graph
                    print(f"[CONNECT] Registered graph '{namespace}' in registry", flush=True)
                elif self.graph_registry.graphs[namespace] is not namespace_graph:
                    # Update registry with current graph instance
                    self.graph_registry.graphs[namespace] = namespace_graph
                    print(f"[CONNECT] Updated graph '{namespace}' in registry", flush=True)
            
            # Initialize performance monitoring
            perf_monitor = PerformanceMonitor(namespace=namespace, enable_detailed_logging=True)
            perf_monitor.start_monitoring("CONNECT")
            
            # Add nodes to graph with optimized batch processing
            nodes_created = 0
            edges_created = 0
            nodes_failed = 0
            edges_failed = 0
            
            # Batch processing configuration
            BATCH_SIZE = 100  # Process nodes in batches
            FLUSH_INTERVAL = 10  # Flush every 10 batches
            
            all_nodes = result.get('nodes', [])
            total_nodes = len(all_nodes)
            print(f"[CONNECT] Processing {total_nodes} nodes in batches of {BATCH_SIZE}...", flush=True)
            
            # Process nodes in batches
            for batch_idx in range(0, total_nodes, BATCH_SIZE):
                batch_end = min(batch_idx + BATCH_SIZE, total_nodes)
                batch_nodes = all_nodes[batch_idx:batch_end]
                batch_start_time = time.time()
                
                for node_dict in batch_nodes:
                    node_id = node_dict.get('node_id')
                    node_type = node_dict.get('node_type')
                    properties = node_dict.get('properties', {})
                    
                    node = GraphNode(
                        id=node_id,
                        label=node_type,
                        properties=properties
                    )
                    
                    try:
                        namespace_graph.add_node(node, write_through=False)
                        nodes_created += 1
                        
                        # Only print debug for first few nodes
                        if nodes_created <= 5:
                            pass
                    except Exception as e:
                        nodes_failed += 1
                        if nodes_failed <= 5:  # Only print first few errors
                            import traceback
                            traceback.print_exc()
                
                # Periodic flush and monitoring
                if (batch_idx // BATCH_SIZE + 1) % FLUSH_INTERVAL == 0:
                    try:
                        flush_start = time.time()
                        if hasattr(namespace_graph, '_save_to_disk'):
                            namespace_graph._save_to_disk()
                        flush_time = time.time() - flush_start
                        
                        perf_monitor.log_checkpoint(
                            f"Node batch {batch_idx // BATCH_SIZE + 1}",
                            items_processed=nodes_created,
                            additional_info={
                                'batch_size': len(batch_nodes),
                                'batch_time': time.time() - batch_start_time,
                                'flush_time': flush_time,
                                'nodes_failed': nodes_failed
                            }
                        )
                        gc.collect()
                    except Exception as flush_err:
                        print(f"[CONNECT] ⚠️  Flush failed: {flush_err}", flush=True)
            
            # Final flush for nodes
            try:
                if hasattr(namespace_graph, '_save_to_disk'):
                    namespace_graph._save_to_disk()
                gc.collect()
            except Exception as final_flush_err:
                print(f"[CONNECT] ⚠️  Final node flush failed: {final_flush_err}", flush=True)
            
            print(f"[CONNECT] Nodes: {nodes_created}/{total_nodes} created, {nodes_failed} failed", flush=True)
            
            # Add edges to graph with batch processing
            all_edges = result.get('edges', [])
            total_edges = len(all_edges)
            print(f"[CONNECT] Processing {total_edges} edges in batches of {BATCH_SIZE}...", flush=True)
            
            for batch_idx in range(0, total_edges, BATCH_SIZE):
                batch_end = min(batch_idx + BATCH_SIZE, total_edges)
                batch_edges = all_edges[batch_idx:batch_end]
                
                for edge_dict in batch_edges:
                    # Use uuid from properties if available, otherwise generate one
                    edge_id = edge_dict.get('uuid') or edge_dict.get('id')
                    if not edge_id or (len(edge_id) != 36 and '_' in edge_id):  # Not a UUID format
                        edge_id = str(uuid.uuid4())
                    
                    source = edge_dict.get('source')
                    target = edge_dict.get('target')
                    edge_type = edge_dict.get('edge_type', 'RELATED_TO')
                    properties = edge_dict.get('properties', {})
                    
                    # Don't store UUID in properties - edge.id is the single source of truth
                    properties.pop('uuid', None)
                    
                    edge = GraphEdge(
                        id=edge_id,
                        source=source,
                        target=target,
                        label=edge_type,
                        properties=properties
                    )
                    
                    try:
                        namespace_graph.add_edge(edge)
                        edges_created += 1
                    except Exception as e:
                        edges_failed += 1
                        if edges_failed <= 5:  # Only print first few errors
                            logger.debug(f"Edge creation failed: {e}")

                # Periodic flush for edges
                if (batch_idx // BATCH_SIZE + 1) % FLUSH_INTERVAL == 0:
                    try:
                        if hasattr(namespace_graph, '_save_to_disk'):
                            namespace_graph._save_to_disk()
                        gc.collect()
                    except Exception as flush_err:
                        print(f"[CONNECT] ⚠️  Edge flush failed: {flush_err}", flush=True)
            
            # Final flush for edges
            try:
                if hasattr(namespace_graph, '_save_to_disk'):
                    namespace_graph._save_to_disk()
                gc.collect()
            except Exception as final_flush_err:
                print(f"[CONNECT] ⚠️  Final edge flush failed: {final_flush_err}", flush=True)
            
            print(f"[CONNECT] Edges: {edges_created}/{total_edges} created, {edges_failed} failed", flush=True)
            
            # End performance monitoring
            perf_summary = perf_monitor.end_monitoring("CONNECT", {
                'nodes_created': nodes_created,
                'edges_created': edges_created,
                'nodes_failed': nodes_failed,
                'edges_failed': edges_failed
            })
            
            # Store result
            stage_data['connect'] = {
                'nodes_created': nodes_created,
                'edges_created': edges_created,
                'document_nodes': result.get('document_nodes', []),
                'table_nodes': result.get('table_nodes', []),
                'image_nodes': result.get('image_nodes', [])
            }
            
            logger.info(f"[CONNECT] Created {nodes_created} nodes and {edges_created} edges")
            
            return {
                'status': 'completed',
                'nodes_created': nodes_created,
                'edges_created': edges_created,
                'document_nodes': result.get('document_nodes', []),
                'table_nodes': result.get('table_nodes', []),
                'image_nodes': result.get('image_nodes', [])
            }
            
        except Exception as e:
            logger.error(f"CONNECT stage failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {"status": "failed", "error": str(e)}
    
    def _execute_chunk_stage(self, stage, stage_data, namespace, source_collection, target_collection, namespace_graph) -> Dict[str, Any]:
        """Execute CHUNK stage - create chunks from normalized document."""
        try:
            from ..engine.chunk_storage import ChunkStorage
            from ...core.graph_structures import GraphNode, GraphEdge
            from .performance_monitor import PerformanceMonitor
            
            # CRITICAL: Ensure graph is registered in registry and has correct name
            # This is required for _save_to_disk() to work properly
            if self.graph_registry and namespace_graph:
                # Ensure graph name is set to namespace
                if not hasattr(namespace_graph, 'name') or namespace_graph.name != namespace:
                    namespace_graph.name = namespace
                
                # Ensure graph is registered in registry (required for save operations)
                if namespace not in self.graph_registry.graphs:
                    self.graph_registry.graphs[namespace] = namespace_graph
                    print(f"[CHUNK] Registered graph '{namespace}' in registry", flush=True)
                elif self.graph_registry.graphs[namespace] is not namespace_graph:
                    # Update registry with current graph instance
                    self.graph_registry.graphs[namespace] = namespace_graph
                    print(f"[CHUNK] Updated graph '{namespace}' in registry", flush=True)
            
            # Initialize performance monitoring
            perf_monitor = PerformanceMonitor(namespace=namespace, enable_detailed_logging=True)
            perf_monitor.start_monitoring("CHUNK")
            
            # Get extraction result from previous stage
            extract_result = stage_data.get('extract', {})
            if not extract_result:
                return {"status": "failed", "error": "No extraction result found. Run EXTRACT stage first."}
            
            document_id = extract_result.get('document_id')
            
            if not document_id:
                return {"status": "failed", "error": "Missing document_id in extraction result"}
            
            # Get normalized version - try to get from extract result, or use default
            normalized_version = extract_result.get('normalized_version', 'v1')
            
            # If not in extract result, try to find the latest version from file path
            if normalized_version == 'v1' and 'normalized_file' in extract_result:
                # Extract version from file path if possible
                import re
                file_path = extract_result.get('normalized_file', '')
                version_match = re.search(r'normalized_(\w+)\.json', file_path)
                if version_match:
                    normalized_version = version_match.group(1)
                    # Remove "normalized_" prefix if present
                    if normalized_version.startswith('normalized_'):
                        normalized_version = normalized_version.replace('normalized_', '')
            
            # Also try to get version from normalized_doc if it's already loaded
            if normalized_version == 'v1' and 'normalized_doc' in extract_result:
                doc_version = extract_result['normalized_doc'].get('version', '')
                if doc_version:
                    # Extract version (handle both "v1" and "normalized_v1" formats)
                    if doc_version.startswith('normalized_'):
                        normalized_version = doc_version.replace('normalized_', '')
                    else:
                        normalized_version = doc_version
            
            # Get chunking parameters from stage
            # Parser sets 'chunk_method', but we also check 'chunk_by' for compatibility
            chunking_strategy = stage.get('chunk_method') or stage.get('chunk_by', 'fixed')
            params = stage.get('parameters', {})
            chunk_size = params.get('chunk_size', 1000)
            overlap = params.get('overlap', 200)
            
            # Memory monitoring helper (define early for use throughout)
            def get_memory_usage():
                try:
                    import psutil
                    import os
                    process = psutil.Process(os.getpid())
                    return process.memory_info().rss / (1024 * 1024)  # MB
                except:
                    return None
            
            # OOM-SAFE: Use streaming for simple strategies, full load only for complex ones
            import json
            from pathlib import Path
            import time
            import gc
            import uuid
            from ..engine.streaming_json import StreamingNormalizedDocumentParser
            
            normalized_doc = None
            normalized_file = extract_result.get('normalized_file')
            
            # Determine if strategy needs full document structure
            strategies_requiring_full_doc = {"hierarchical", "parent_child", "table_aware", "smart", 
                                           "image_aware", "code_aware", "list_aware", "cross_page"}
            use_streaming = chunking_strategy not in strategies_requiring_full_doc
            
            # Get normalized file path (Parquet first, then JSON fallback)
            if normalized_file and Path(normalized_file).exists():
                normalized_file_path = Path(normalized_file)
            else:
                # Fallback: try to construct path
                base_dir = extract_result.get('output_dir', 'contextcore_data')
                normalized_dir = Path(base_dir) / "namespaces" / namespace / "documents" / document_id / "normalized"
                # Try Parquet first
                parquet_path = normalized_dir / f"normalized_{normalized_version}.parquet"
                json_path = normalized_dir / f"normalized_{normalized_version}.json"
                
                if parquet_path.exists():
                    normalized_file_path = parquet_path
                elif json_path.exists():
                    normalized_file_path = json_path
                else:
                    normalized_file_path = parquet_path  # Will fail with clear error
            
            if not normalized_file_path.exists():
                return {"status": "failed", "error": f"Normalized file not found: {normalized_file_path}"}
            
            # Detect file format
            is_parquet = normalized_file_path.suffix == '.parquet'
            
            file_size_mb = normalized_file_path.stat().st_size / (1024 * 1024)
            print(f"[CHUNK] Reading normalized data from: {normalized_file_path}", flush=True)
            print(f"[CHUNK] File format: {'Parquet' if is_parquet else 'JSON'}", flush=True)
            print(f"[CHUNK] File size: {file_size_mb:.2f} MB", flush=True)
            print(f"[CHUNK] Strategy: {chunking_strategy} (streaming: {use_streaming})", flush=True)
            
            # Log memory before processing
            try:
                import psutil
                import os
                process = psutil.Process(os.getpid())
                mem_before = process.memory_info().rss / (1024 * 1024)
                print(f"[CHUNK] Memory before processing: {mem_before:.1f} MB", flush=True)
            except:
                mem_before = None
            
            # For complex strategies, load full doc (but use callback to write immediately)
            normalized_doc = None
            if not use_streaming:
                load_start = time.time()
                if is_parquet:
                    # Load from Parquet by streaming all pages and building doc structure
                    from .streaming_parquet import StreamingParquetReader
                    reader = StreamingParquetReader(normalized_file_path)
                    metadata = reader.get_document_metadata()
                    
                    # Build document structure from streamed pages
                    pages = []
                    for page in reader.stream_pages():
                        pages.append(page)
                    
                    normalized_doc = {
                        'document_id': metadata.get('document_id', document_id),
                        'version': metadata.get('version', normalized_version),
                        'content': {
                            'pages': pages
                        }
                    }
                    document_id = normalized_doc['document_id']
                else:
                    # Load from JSON (backward compatibility)
                    with open(normalized_file_path, 'r', encoding='utf-8') as f:
                        normalized_doc = json.load(f)
                    if normalized_doc and 'document_id' in normalized_doc:
                        document_id = normalized_doc['document_id']
                
                load_time = time.time() - load_start
                
                if mem_before:
                    try:
                        mem_after = process.memory_info().rss / (1024 * 1024)
                        print(f"[CHUNK] Memory after load: {mem_after:.1f} MB (delta: {mem_after - mem_before:.1f} MB)", flush=True)
                    except:
                        pass
                print(f"[CHUNK] Load time: {load_time:.2f}s", flush=True)
                
                if normalized_doc and 'document_id' in normalized_doc:
                    document_id = normalized_doc['document_id']
                    print(f"[CHUNK] Using document_id from normalized document: {document_id}", flush=True)
            
            # Get document node ID
            connect_result = stage_data.get('connect', {})
            if isinstance(connect_result, dict) and connect_result.get('document_nodes'):
                doc_node_id = connect_result['document_nodes'][0] if isinstance(connect_result['document_nodes'][0], str) else connect_result['document_nodes'][0].get('id', '')
            else:
                doc_node_id = f"doc_{document_id}"
            
            # If normalized_doc is already loaded, use its document_id (it's the source of truth)
            if normalized_doc and 'document_id' in normalized_doc:
                document_id = normalized_doc['document_id']
                print(f"[CHUNK] Using document_id from loaded normalized document: {document_id}", flush=True)
            
            # Initialize chunk storage
            chunk_storage = ChunkStorage(namespace=namespace, base_dir="contextcore_data")
            # Pass page monitor to chunk storage if available
            if hasattr(self, 'page_monitor') and self.page_monitor:
                chunk_storage.page_monitor = self.page_monitor
            
            # Track chunk count for reporting
            # chunks_in_memory: Chunks added to graph buffer but not yet flushed to disk
            # chunks_flushed_to_disk: Chunks that have been successfully flushed to disk
            # Flushing happens:
            #   1. Periodically every write_batch_size chunks (default: 50)
            #   2. At the end of chunking (final flush)
            #   3. When _save_to_disk() is called, it saves the graph to disk via registry
            chunk_nodes_created = 0
            chunks_in_memory = 0  # Chunks in buffer (not yet flushed)
            chunks_flushed_to_disk = 0  # Chunks that have been flushed to disk
            write_batch_size = 50  # Flush every 50 chunks
            
            # Callback function to write chunks immediately (OOM-safe)
            def write_chunk_immediately(chunk_props):
                nonlocal chunk_nodes_created, chunks_in_memory, chunks_flushed_to_disk
                chunk_id = chunk_props.get("id") or chunk_props.get("chunk_id")
                if not chunk_id:
                    return
                
                chunk_node = GraphNode(id=chunk_id, label="Chunk", properties=chunk_props)
                namespace_graph.add_node(chunk_node, write_through=False)
                
                edge_id = str(uuid.uuid4())
                edge = GraphEdge(id=edge_id, source=doc_node_id, target=chunk_id, label="HAS_CHUNK", properties={})
                namespace_graph.add_edge(edge)
                
                chunk_nodes_created += 1
                chunks_in_memory += 1  # Chunk added to buffer
                
                # Log when reaching 50 chunks threshold
                if chunks_in_memory == write_batch_size:
                    print(f"[CHUNK] ⚠️  Reached {write_batch_size} chunks in memory - attempting flush to prevent memory growth", flush=True)
                
                # Periodic flush to prevent memory buildup
                if chunk_nodes_created % write_batch_size == 0:
                    # Ensure graph is registered before saving
                    if self.graph_registry and namespace_graph:
                        if namespace not in self.graph_registry.graphs:
                            self.graph_registry.graphs[namespace] = namespace_graph
                        if not hasattr(namespace_graph, 'name') or namespace_graph.name != namespace:
                            namespace_graph.name = namespace
                    
                    # Flush chunks to disk
                    flush_start = time.time()
                    flush_succeeded = False
                    if hasattr(namespace_graph, '_save_to_disk'):
                        try:
                            namespace_graph._save_to_disk()
                            chunks_flushed_to_disk += chunks_in_memory  # All buffered chunks are now flushed
                            flush_time = time.time() - flush_start
                            flush_succeeded = True
                            print(f"[CHUNK] ✅ FLUSH SUCCESS: Flushed {chunks_in_memory} chunks to disk (total written: {chunk_nodes_created}, total flushed: {chunks_flushed_to_disk}, in memory: 0) [{flush_time:.2f}s]", flush=True)
                            chunks_in_memory = 0  # Reset counter after flush
                        except Exception as save_err:
                            import traceback
                            error_details = traceback.format_exc()
                            print(f"[CHUNK] ❌ FLUSH FAILED at {write_batch_size} chunks: Cannot flush {chunks_in_memory} chunks to disk", flush=True)
                            print(f"[CHUNK]    ⚠️  WARNING: {chunks_in_memory} chunks remain in memory and cannot be flushed!", flush=True)
                            print(f"[CHUNK]    Error: {save_err}", flush=True)
                            print(f"[CHUNK]    Details: {error_details[:500]}", flush=True)
                            print(f"[CHUNK]    Attempting registry fallback...", flush=True)
                            # Try direct save via registry as fallback
                            if self.graph_registry and namespace in self.graph_registry.graphs:
                                try:
                                    self.graph_registry.save_graph(namespace)
                                    chunks_flushed_to_disk += chunks_in_memory
                                    flush_succeeded = True
                                    print(f"[CHUNK] ✅ FLUSH SUCCESS via registry fallback: Flushed {chunks_in_memory} chunks (total flushed: {chunks_flushed_to_disk})", flush=True)
                                    chunks_in_memory = 0
                                except Exception as fallback_err:
                                    print(f"[CHUNK] ❌ Registry fallback also failed: {fallback_err}", flush=True)
                                    print(f"[CHUNK]    ⚠️  CRITICAL: {chunks_in_memory} chunks remain in memory - memory may grow!", flush=True)
                            else:
                                print(f"[CHUNK] ❌ Cannot use registry fallback - graph not registered or registry unavailable", flush=True)
                                print(f"[CHUNK]    ⚠️  CRITICAL: {chunks_in_memory} chunks remain in memory - memory may grow!", flush=True)
                    else:
                        # If no _save_to_disk method, try registry save
                        if self.graph_registry and namespace in self.graph_registry.graphs:
                            try:
                                self.graph_registry.save_graph(namespace)
                                chunks_flushed_to_disk += chunks_in_memory
                                flush_succeeded = True
                                print(f"[CHUNK] ✅ FLUSH SUCCESS via registry: Flushed {chunks_in_memory} chunks (total written: {chunk_nodes_created}, total flushed: {chunks_flushed_to_disk}, in memory: 0)", flush=True)
                                chunks_in_memory = 0
                            except Exception as reg_err:
                                import traceback
                                error_details = traceback.format_exc()
                                print(f"[CHUNK] ❌ FLUSH FAILED at {write_batch_size} chunks via registry: Cannot flush {chunks_in_memory} chunks", flush=True)
                                print(f"[CHUNK]    ⚠️  WARNING: {chunks_in_memory} chunks remain in memory and cannot be flushed!", flush=True)
                                print(f"[CHUNK]    Error: {reg_err}", flush=True)
                                print(f"[CHUNK]    Details: {error_details[:500]}", flush=True)
                                print(f"[CHUNK]    {chunks_in_memory} chunks remain in memory - memory may grow!", flush=True)
                    
                    gc.collect()  # Force garbage collection
                    if mem_before:
                        try:
                            mem_current = process.memory_info().rss / (1024 * 1024)
                            print(f"[CHUNK] Memory: {mem_current:.1f} MB (written: {chunk_nodes_created}, flushed: {chunks_flushed_to_disk}, in memory: {chunks_in_memory})", flush=True)
                        except:
                            print(f"[CHUNK] Written {chunk_nodes_created} chunks (flushed: {chunks_flushed_to_disk}, in memory: {chunks_in_memory})...", flush=True)
                else:
                    # Log progress for smaller batches
                    if chunk_nodes_created % 10 == 0:
                        print(f"[CHUNK] Progress: {chunk_nodes_created} written, {chunks_flushed_to_disk} flushed, {chunks_in_memory} in memory", flush=True)
            
            # Prepare chunks from normalized data with immediate write callback
            print(f"[CHUNK] Preparing chunks using {chunking_strategy} strategy...", flush=True)
            print(f"[CHUNK] document_id={document_id}, normalized_version={normalized_version}, namespace={namespace}", flush=True)
            
            if use_streaming:
                # STREAMING MODE: Process pages one at a time (OOM-safe)
                print(f"[CHUNK] Using streaming mode for memory efficiency", flush=True)
                
                if is_parquet:
                    # Stream from Parquet
                    from .streaming_parquet import StreamingParquetReader
                    reader = StreamingParquetReader(normalized_file_path)
                    doc_metadata = reader.get_document_metadata()
                    if doc_metadata.get('document_id'):
                        document_id = doc_metadata['document_id']
                    
                    # Stream pages and chunk immediately
                    for page in reader.stream_pages():
                        page_no = page.get('page_no')
                        page_text = page.get('flat_text', '')
                        
                        if not page_text or not page_text.strip():
                            continue
                        
                        # Chunk this page
                        if chunking_strategy == "semantic":
                            page_chunks = chunk_storage._chunk_semantic(page_text, chunk_size, overlap)
                        elif chunking_strategy == "paragraph":
                            page_chunks = chunk_storage._chunk_paragraph(page_text, overlap)
                        elif chunking_strategy == "recursive":
                            page_chunks = chunk_storage._chunk_recursive(page_text, chunk_size, overlap)
                        elif chunking_strategy == "sliding_window":
                            step_size = params.get('step_size', chunk_size - overlap) if params else (chunk_size - overlap)
                            page_chunks = chunk_storage._chunk_sliding_window(page_text, chunk_size, overlap, step_size=step_size)
                        else:  # fixed
                            page_chunks = chunk_storage._chunk_fixed(page_text, chunk_size, overlap)
                        
                        # Write chunks immediately
                        from contextsynapse.extraction.id_generator import IDGenerator
                        for chunk_idx, (chunk_text, start_char, end_char) in enumerate(page_chunks):
                            chunk_id = IDGenerator.generate_chunk_id(
                                document_id=document_id,
                                page_no=page_no,
                                chunk_index=chunk_idx,
                                chunking_strategy=chunking_strategy,
                                strategy_version="v1",
                                namespace=None
                            )
                            
                            chunk_props = chunk_storage.create_chunk_with_pointer(
                                chunk_id=chunk_id,
                                content=chunk_text,
                                document_id=document_id,
                                normalized_version=normalized_version,
                                page_no=page_no,
                                start_char=start_char,
                                end_char=end_char,
                                chunk_index=chunk_idx,
                                chunking_strategy=chunking_strategy,
                                strategy_version="v1",
                                chunking_params={"chunk_size": chunk_size, "overlap": overlap},
                                document_node_id=doc_node_id,
                                metadata={"chunk_size": len(chunk_text)}
                            )
                            
                            write_chunk_immediately(chunk_props)
                        
                        # Free page from memory
                        del page
                        gc.collect()
                else:
                    # Stream from JSON (backward compatibility)
                    from .streaming_json import StreamingNormalizedDocumentParser
                    with StreamingNormalizedDocumentParser(normalized_file_path, strategy="page") as parser:
                        # Get document metadata
                        doc_metadata = parser.get_document_metadata()
                        if doc_metadata.get('document_id'):
                            document_id = doc_metadata['document_id']
                        
                        # Stream pages and chunk immediately
                        for page in parser.stream_pages():
                            page_no = page.get('page_no')
                            page_text = page.get('flat_text', '')
                            
                            if not page_text or not page_text.strip():
                                continue
                            
                            # Chunk this page
                            if chunking_strategy == "semantic":
                                page_chunks = chunk_storage._chunk_semantic(page_text, chunk_size, overlap)
                            elif chunking_strategy == "paragraph":
                                page_chunks = chunk_storage._chunk_paragraph(page_text, overlap)
                            elif chunking_strategy == "recursive":
                                page_chunks = chunk_storage._chunk_recursive(page_text, chunk_size, overlap)
                            elif chunking_strategy == "sliding_window":
                                step_size = params.get('step_size', chunk_size - overlap) if params else (chunk_size - overlap)
                                page_chunks = chunk_storage._chunk_sliding_window(page_text, chunk_size, overlap, step_size=step_size)
                            else:  # fixed
                                page_chunks = chunk_storage._chunk_fixed(page_text, chunk_size, overlap)
                            
                            # Write chunks immediately
                            from contextsynapse.extraction.id_generator import IDGenerator
                            for chunk_idx, (chunk_text, start_char, end_char) in enumerate(page_chunks):
                                chunk_id = IDGenerator.generate_chunk_id(
                                    document_id=document_id,
                                    page_no=page_no,
                                    chunk_index=chunk_idx,
                                    chunking_strategy=chunking_strategy,
                                    strategy_version="v1",
                                    namespace=None
                                )
                                
                                chunk_props = chunk_storage.create_chunk_with_pointer(
                                    chunk_id=chunk_id,
                                    content=chunk_text,
                                    document_id=document_id,
                                    normalized_version=normalized_version,
                                    page_no=page_no,
                                    start_char=start_char,
                                    end_char=end_char,
                                    chunk_index=chunk_idx,
                                    chunking_strategy=chunking_strategy,
                                    strategy_version="v1",
                                    chunking_params={"chunk_size": chunk_size, "overlap": overlap},
                                    document_node_id=doc_node_id,
                                    metadata={"chunk_size": len(chunk_text)}
                                )
                                
                                write_chunk_immediately(chunk_props)
                            
                            # Free page from memory
                            del page
                            gc.collect()
            else:
                # FULL DOC MODE: Load full doc but use callback for immediate writes
                print(f"[CHUNK] Using full document mode (strategy requires full structure)", flush=True)
                chunks = chunk_storage.create_chunks_from_normalized_json(
                    document_id=document_id,
                    normalized_version=normalized_version,
                    chunking_strategy=chunking_strategy,
                    strategy_version="v1",
                    chunk_size=chunk_size,
                    overlap=overlap,
                    document_node_id=doc_node_id,
                    normalized_doc=normalized_doc,
                    chunk_callback=write_chunk_immediately,  # Write immediately, don't accumulate
                )
                
                # If callback was used, chunks will be empty (already written)
                # If not, write them now (fallback for compatibility)
                if chunks:
                    for chunk_props in chunks:
                        write_chunk_immediately(chunk_props)
            
            # Final flush - ensure all chunks are offloaded to disk
            # Ensure graph is registered before final save
            if self.graph_registry and namespace_graph:
                if namespace not in self.graph_registry.graphs:
                    self.graph_registry.graphs[namespace] = namespace_graph
                if not hasattr(namespace_graph, 'name') or namespace_graph.name != namespace:
                    namespace_graph.name = namespace
            
            # Final flush: flush any remaining chunks in memory
            if chunks_in_memory > 0:
                flush_start = time.time()
                if hasattr(namespace_graph, '_save_to_disk'):
                    try:
                        namespace_graph._save_to_disk()
                        chunks_flushed_to_disk += chunks_in_memory
                        flush_time = time.time() - flush_start
                        print(f"[CHUNK] 💾 Final flush: {chunks_in_memory} chunks flushed to disk ({flush_time:.2f}s)", flush=True)
                        chunks_in_memory = 0
                    except Exception as save_err:
                        import traceback
                        error_details = traceback.format_exc()
                        print(f"[CHUNK] ❌ FINAL FLUSH FAILED: Cannot flush {chunks_in_memory} chunks to disk", flush=True)
                        print(f"[CHUNK]    Error: {save_err}", flush=True)
                        print(f"[CHUNK]    Details: {error_details[:500]}", flush=True)
                        print(f"[CHUNK]    Attempting registry fallback...", flush=True)
                        # Try direct save via registry as fallback
                        if self.graph_registry and namespace in self.graph_registry.graphs:
                            try:
                                self.graph_registry.save_graph(namespace)
                                chunks_flushed_to_disk += chunks_in_memory
                                print(f"[CHUNK] ✅ Final flush via registry fallback: {chunks_in_memory} chunks flushed", flush=True)
                                chunks_in_memory = 0
                            except Exception as fallback_err:
                                import traceback
                                fallback_details = traceback.format_exc()
                                print(f"[CHUNK] ❌ Final flush fallback also failed: {fallback_err}", flush=True)
                                print(f"[CHUNK]    Fallback details: {fallback_details[:500]}", flush=True)
                                print(f"[CHUNK]    ⚠️  WARNING: {chunks_in_memory} chunks remain in memory!", flush=True)
                        else:
                            print(f"[CHUNK] ❌ Cannot use registry fallback - graph not registered", flush=True)
                            print(f"[CHUNK]    ⚠️  WARNING: {chunks_in_memory} chunks remain in memory!", flush=True)
                else:
                    # If no _save_to_disk method, try registry save
                    if self.graph_registry and namespace in self.graph_registry.graphs:
                        try:
                            self.graph_registry.save_graph(namespace)
                            chunks_flushed_to_disk += chunks_in_memory
                            print(f"[CHUNK] 💾 Final flush via registry: {chunks_in_memory} chunks flushed", flush=True)
                            chunks_in_memory = 0
                        except Exception as reg_err:
                            print(f"[CHUNK] ⚠️  Warning: Final flush via registry failed: {reg_err}", flush=True)
            
            gc.collect()
            
            if chunk_nodes_created == 0:
                return {"status": "failed", "error": "No chunks created"}
            
            # Final summary
            print(f"[CHUNK] ✅ Completed: {chunk_nodes_created} chunks written, {chunks_flushed_to_disk} flushed to disk, {chunks_in_memory} remaining in memory", flush=True)
            
            perf_monitor.stop_monitoring()
            
            return {
                "status": "success",
                "chunks_created": chunk_nodes_created,
                "chunks_flushed_to_disk": chunks_flushed_to_disk,
                "chunks_in_memory": chunks_in_memory,
                "chunking_strategy": chunking_strategy,
                "chunk_size": chunk_size,
                "overlap": overlap,
                "document_id": document_id,
                "normalized_version": normalized_version,
                "doc_node_id": doc_node_id
            }
            if normalized_file:
                normalized_file_path = Path(normalized_file)
                if normalized_file_path.exists():
                    print(f"[CHUNK] Loading normalized document from file: {normalized_file}", flush=True)
                    try:
                        # OPTIMIZATION: Load only what's needed for chunking
                        # For most strategies, we only need pages with page_no and flat_text
                        # This can reduce memory usage by 80-90% for large documents
                        needs_full_doc = chunking_strategy in ["hierarchical", "parent_child", "table_aware", "smart", "image_aware", "code_aware", "list_aware"]
                        
                        if needs_full_doc:
                            # Load full document for strategies that need structure
                            with open(normalized_file_path, 'r', encoding='utf-8') as f:
                                normalized_doc = json.load(f)
                            print(f"[CHUNK] Loaded full normalized JSON (needed for {chunking_strategy} strategy)", flush=True)
                        else:
                            # OPTIMIZATION: Load only pages array with minimal fields
                            # Note: For streaming mode, this will be overridden later in the chunking logic
                            with open(normalized_file_path, 'r', encoding='utf-8') as f:
                                full_doc = json.load(f)
                            
                            # Extract only essential fields
                            normalized_doc = {
                                'document_id': full_doc.get('document_id'),
                                'version': full_doc.get('version', 'v1'),
                                'content': {
                                    'pages': [
                                        {
                                            'page_no': p.get('page_no'),
                                            'flat_text': p.get('flat_text', '')
                                        }
                                        for p in full_doc.get('content', {}).get('pages', [])
                                    ]
                                }
                            }
                            
                            # Get file size for reporting
                            file_size_mb = normalized_file_path.stat().st_size / (1024 * 1024)
                            print(f"[CHUNK] Optimized loading: Extracted {len(normalized_doc['content']['pages'])} pages (reduced from {file_size_mb:.1f}MB file)", flush=True)
                        
                        load_time = time.time() - load_start
                        print(f"[CHUNK] Successfully loaded normalized JSON in {load_time:.2f}s", flush=True)
                    except Exception as e:
                        logger.warning(f"Failed to load from {normalized_file}: {e}")
                        normalized_doc = None
            
            # If file path not provided or file doesn't exist, try to construct path
            if normalized_doc is None:
                # Try multiple path formats:
                # 1. Legacy format: contextcore_data/{document_id}/normalized/normalized_{version}.json
                # 2. Namespace-aware: contextcore_data/namespaces/{namespace}/documents/{document_id}/normalized/normalized_{version}.json
                
                base_dir = extract_result.get('output_dir', 'contextcore_data')
                normalized_file_path = None
                
                # First try legacy format (where extract actually stores it)
                # Check for Parquet first, then JSON (backward compatibility)
                legacy_normalized_dir = Path(base_dir) / document_id / "normalized"
                legacy_parquet = legacy_normalized_dir / f"normalized_{normalized_version}.parquet"
                legacy_json = legacy_normalized_dir / f"normalized_{normalized_version}.json"
                
                if legacy_parquet.exists():
                    normalized_file_path = legacy_parquet
                    print(f"[CHUNK] Found normalized document (Parquet) in legacy path: {legacy_parquet}", flush=True)
                elif legacy_json.exists():
                    normalized_file_path = legacy_json
                    print(f"[CHUNK] Found normalized document (JSON) in legacy path: {legacy_json}", flush=True)
                
                # If not found, try namespace-aware path
                if not normalized_file_path:
                    if 'namespaces' not in str(base_dir):
                        normalized_dir = Path(base_dir) / "namespaces" / namespace / "documents" / document_id / "normalized"
                    else:
                        base_path = Path(base_dir)
                        if "documents" not in str(base_path):
                            normalized_dir = base_path / "documents" / document_id / "normalized"
                        else:
                            normalized_dir = base_path / document_id / "normalized"
                    
                    namespace_parquet = normalized_dir / f"normalized_{normalized_version}.parquet"
                    namespace_json = normalized_dir / f"normalized_{normalized_version}.json"
                    
                    if namespace_parquet.exists():
                        normalized_file_path = namespace_parquet
                        print(f"[CHUNK] Found normalized document (Parquet) in namespace path: {namespace_parquet}", flush=True)
                    elif namespace_json.exists():
                        normalized_file_path = namespace_json
                        print(f"[CHUNK] Found normalized document (JSON) in namespace path: {namespace_json}", flush=True)
                
                # Load from found path
                if normalized_file_path:
                    print(f"[CHUNK] Loading normalized document from: {normalized_file_path}", flush=True)
                    try:
                        with open(normalized_file_path, 'r', encoding='utf-8') as f:
                            normalized_doc = json.load(f)
                        print(f"[CHUNK] Successfully loaded normalized JSON from: {normalized_file_path}", flush=True)
                    except Exception as e:
                        logger.warning(f"Failed to load from {normalized_file_path}: {e}")
                        normalized_doc = None
                else:
                    # If no file found yet, try to find any normalized file in legacy path
                    # Check for both Parquet and JSON files
                    if legacy_normalized_dir.exists():
                        parquet_files = list(legacy_normalized_dir.glob("normalized_*.parquet"))
                        json_files = list(legacy_normalized_dir.glob("normalized_*.json"))
                        normalized_files = parquet_files + json_files
                        
                        if normalized_files:
                            normalized_files.sort(reverse=True)
                            latest_file = normalized_files[0]
                            print(f"[CHUNK] Loading normalized document from latest file in legacy path: {latest_file}", flush=True)
                            
                            is_parquet_latest = latest_file.suffix == '.parquet'
                            if is_parquet_latest:
                                # Load from Parquet
                                from .streaming_parquet import StreamingParquetReader
                                reader = StreamingParquetReader(latest_file)
                                metadata = reader.get_document_metadata()
                                pages = []
                                for page in reader.stream_pages():
                                    pages.append(page)
                                normalized_doc = {
                                    'document_id': metadata.get('document_id', document_id),
                                    'version': metadata.get('version', normalized_version),
                                    'content': {'pages': pages}
                                }
                                document_id = normalized_doc['document_id']
                                import re
                                version_match = re.search(r'normalized_(\w+)\.parquet', latest_file.name)
                            else:
                                # Load from JSON
                                try:
                                    with open(latest_file, 'r', encoding='utf-8') as f:
                                        normalized_doc = json.load(f)
                                    import re
                                    version_match = re.search(r'normalized_(\w+)\.json', latest_file.name)
                                except Exception as e:
                                    logger.warning(f"Failed to load from {latest_file}: {e}")
                                    normalized_doc = None
                                    version_match = None
                            
                            if version_match:
                                normalized_version = version_match.group(1)
                                # Remove "normalized_" prefix if present
                                if normalized_version.startswith('normalized_'):
                                    normalized_version = normalized_version.replace('normalized_', '')
                            print(f"[CHUNK] Successfully loaded normalized document from: {latest_file}", flush=True)
                    
                    # If still not found, search more broadly in base_dir for any directory containing normalized files
                    if normalized_doc is None:
                        print(f"[CHUNK] Searching broadly in {base_dir} for normalized files...", flush=True)
                        base_path = Path(base_dir)
                        
                        # First, try to find directories that match the namespace in their name (priority)
                        namespace_patterns = [
                            f"doc_*{namespace}*",
                            f"*{namespace}*"
                        ]
                        
                        # Then fall back to general patterns
                        general_patterns = [
                            f"doc_*{document_id}*",
                            f"*{document_id}*",
                            f"doc_*",
                            "aiql_*"
                        ]
                        
                        all_patterns = namespace_patterns + general_patterns
                        
                        for pattern in all_patterns:
                            matching_dirs = list(base_path.glob(pattern))
                            for dir_path in matching_dirs:
                                if dir_path.is_dir():
                                    normalized_dir = dir_path / "normalized"
                                    if normalized_dir.exists():
                                        # Check for both Parquet and JSON files
                                        parquet_files = list(normalized_dir.glob("normalized_*.parquet"))
                                        json_files = list(normalized_dir.glob("normalized_*.json"))
                                        normalized_files = parquet_files + json_files
                                        
                                        if normalized_files:
                                            # Check if this file contains the correct document_id
                                            for norm_file in sorted(normalized_files, reverse=True):
                                                is_parquet_file = norm_file.suffix == '.parquet'
                                                try:
                                                    if is_parquet_file:
                                                        # Check Parquet metadata
                                                        from .streaming_parquet import StreamingParquetReader
                                                        reader = StreamingParquetReader(norm_file)
                                                        metadata = reader.get_document_metadata()
                                                        test_doc_id = metadata.get('document_id')
                                                    else:
                                                        # Check JSON
                                                        with open(norm_file, 'r', encoding='utf-8') as f:
                                                            test_doc = json.load(f)
                                                        test_doc_id = test_doc.get('document_id')
                                                    
                                                    if test_doc_id == document_id:
                                                        if is_parquet_file:
                                                            # Load full Parquet document
                                                            reader = StreamingParquetReader(norm_file)
                                                            metadata = reader.get_document_metadata()
                                                            pages = []
                                                            for page in reader.stream_pages():
                                                                pages.append(page)
                                                            normalized_doc = {
                                                                'document_id': metadata.get('document_id', document_id),
                                                                'version': metadata.get('version', normalized_version),
                                                                'content': {'pages': pages}
                                                            }
                                                        else:
                                                            normalized_doc = test_doc
                                                        normalized_file_path = norm_file
                                                        # Extract version
                                                        import re
                                                        version_match = re.search(r'normalized_(\w+)\.(parquet|json)', norm_file.name)
                                                        if version_match:
                                                            normalized_version = version_match.group(1)
                                                            if normalized_version.startswith('normalized_'):
                                                                normalized_version = normalized_version.replace('normalized_', '')
                                                        print(f"[CHUNK] Found matching document in: {norm_file}", flush=True)
                                                        break
                                                except Exception as e:
                                                    continue
                                            if normalized_doc:
                                                break
                                if normalized_doc:
                                    break
                            if normalized_doc:
                                break
            
            # Final fallback: try NormalizedDocumentStore (uses namespace-aware paths)
            if normalized_doc is None:
                print(f"[CHUNK] File not found, trying NormalizedDocumentStore...", flush=True)
                try:
                    from contextsynapse.extraction.normalized_store import NormalizedDocumentStore
                    # NormalizedDocumentStore already uses namespace-aware paths: contextcore_data/namespaces/{namespace}/documents
                    store = NormalizedDocumentStore(namespace=namespace, base_dir="contextcore_data")
                    
                    # Try specific version first
                    normalized_doc = store.get(document_id, normalized_version, load_full=True)
                    if normalized_doc:
                        print(f"[CHUNK] Normalized document loaded from NormalizedDocumentStore (version: {normalized_version})", flush=True)
                    else:
                        # Try latest version (None = latest)
                        print(f"[CHUNK] Version {normalized_version} not found, trying latest version...", flush=True)
                        normalized_doc = store.get(document_id, version=None, load_full=True)
                        if normalized_doc:
                            # Update normalized_version to match what was found
                            found_version = normalized_doc.get('version', normalized_version)
                            # Remove "normalized_" prefix if present
                            if found_version.startswith('normalized_'):
                                found_version = found_version.replace('normalized_', '')
                            normalized_version = found_version
                            print(f"[CHUNK] Normalized document loaded from NormalizedDocumentStore (latest version: {normalized_version})", flush=True)
                except Exception as e:
                    logger.warning(f"NormalizedDocumentStore load failed: {e}")
                    print(f"[CHUNK] NormalizedDocumentStore error: {e}", flush=True)
            
            # If still no document found, provide detailed error with diagnostic info
            if not normalized_doc:
                error_msg = f"Normalized document not found: {document_id}/{normalized_version}. "
                error_msg += f"Namespace: {namespace}. "
                error_msg += "Checked: file system paths and NormalizedDocumentStore."
                
                # Try to list available documents for diagnostic purposes
                try:
                    from contextsynapse.extraction.normalized_store import NormalizedDocumentStore
                    import sqlite3
                    store = NormalizedDocumentStore(namespace=namespace, base_dir="contextcore_data")
                    conn = sqlite3.connect(str(store.db_path))
                    available_docs = conn.execute('''
                        SELECT document_id, version, title, page_count
                        FROM normalized_documents
                        ORDER BY created_at DESC
                        LIMIT 10
                    ''').fetchall()
                    conn.close()
                    
                    if available_docs:
                        print(f"[CHUNK] Available documents in namespace '{namespace}':", flush=True)
                        for doc_id, ver, title, page_count in available_docs:
                            print(f"  - {doc_id} (version: {ver}, title: {title or 'N/A'}, pages: {page_count or 'N/A'})", flush=True)
                    else:
                        print(f"[CHUNK] No documents found in NormalizedDocumentStore for namespace '{namespace}'", flush=True)
                except Exception as diag_e:
                    print(f"[CHUNK] Could not retrieve diagnostic info: {diag_e}", flush=True)
                
                print(f"[CHUNK] ERROR: {error_msg}", flush=True)
                return {"status": "failed", "error": error_msg}
            
            # Validate normalized document structure before chunking with detailed diagnostics
            print(f"[CHUNK] Validating normalized document structure...", flush=True)
            validation_errors = []
            validation_warnings = []
            
            # Diagnostic: Check document structure
            print(f"[CHUNK] [DIAG] Document ID: {normalized_doc.get('document_id', 'MISSING')}", flush=True)
            print(f"[CHUNK] [DIAG] Document version: {normalized_doc.get('version', 'MISSING')}", flush=True)
            
            # Check required fields
            if 'content' not in normalized_doc:
                validation_errors.append("Missing 'content' field")
                print(f"[CHUNK] [DIAG] ERROR: 'content' field is missing from normalized document", flush=True)
                print(f"[CHUNK] [DIAG] Available top-level keys: {list(normalized_doc.keys())}", flush=True)
            elif 'pages' not in normalized_doc.get('content', {}):
                validation_errors.append("Missing 'content.pages' array")
                print(f"[CHUNK] [DIAG] ERROR: 'content.pages' array is missing", flush=True)
                content_keys = list(normalized_doc.get('content', {}).keys())
                print(f"[CHUNK] [DIAG] Available content keys: {content_keys}", flush=True)
            elif not isinstance(normalized_doc['content'].get('pages'), list):
                pages_type = type(normalized_doc['content'].get('pages'))
                validation_errors.append(f"'content.pages' must be an array, got {pages_type}")
                print(f"[CHUNK] [DIAG] ERROR: 'content.pages' is not a list, got {pages_type}", flush=True)
            elif len(normalized_doc['content']['pages']) == 0:
                validation_errors.append("'content.pages' is empty - no pages to chunk")
                print(f"[CHUNK] [DIAG] ERROR: 'content.pages' array is empty", flush=True)
            else:
                # Validate pages have required fields
                pages = normalized_doc['content']['pages']
                print(f"[CHUNK] [DIAG] Found {len(pages)} pages in normalized document", flush=True)
                
                pages_without_text = 0
                pages_with_invalid_structure = 0
                pages_with_invalid_types = 0
                total_text_length = 0
                
                for i, page in enumerate(pages):
                    if not isinstance(page, dict):
                        validation_errors.append(f"Page {i} is not a dictionary (got {type(page)})")
                        pages_with_invalid_structure += 1
                        print(f"[CHUNK] [DIAG] ERROR: Page {i} is not a dict, got {type(page)}", flush=True)
                        continue
                    
                    # Check page_no
                    if 'page_no' not in page:
                        validation_warnings.append(f"Page {i} missing 'page_no' (will use index {i+1})")
                    elif not isinstance(page.get('page_no'), (int, float)):
                        pages_with_invalid_types += 1
                        validation_warnings.append(f"Page {i} has invalid 'page_no' type: {type(page.get('page_no'))}")
                    
                    # Check for text content with detailed diagnostics
                    has_text = False
                    text_length = 0
                    text_source = None
                    
                    if 'flat_text' in page:
                        flat_text = page.get('flat_text')
                        if flat_text is not None:
                            if isinstance(flat_text, str):
                                text_length = len(flat_text)
                                total_text_length += text_length
                                if flat_text.strip():
                                    has_text = True
                                    text_source = 'flat_text'
                                else:
                                    print(f"[CHUNK] [DIAG] Page {i}: flat_text exists but is empty/whitespace", flush=True)
                            else:
                                print(f"[CHUNK] [DIAG] Page {i}: flat_text is not a string, got {type(flat_text)}", flush=True)
                    
                    if not has_text and 'normalized_blocks' in page:
                        blocks = page.get('normalized_blocks')
                        if blocks and isinstance(blocks, list) and len(blocks) > 0:
                            has_text = True
                            text_source = 'normalized_blocks'
                            # Estimate text length
                            for block in blocks:
                                if isinstance(block, dict) and 'text' in block:
                                    text_length += len(str(block.get('text', '')))
                    
                    if not has_text and 'raw_blocks' in page:
                        blocks = page.get('raw_blocks')
                        if blocks and isinstance(blocks, list) and len(blocks) > 0:
                            has_text = True
                            text_source = 'raw_blocks'
                    
                    if not has_text:
                        pages_without_text += 1
                        page_no = page.get('page_no', i+1)
                        validation_errors.append(f"Page {i} (page_no={page_no}) has no text content")
                        print(f"[CHUNK] [DIAG] ERROR: Page {i} (page_no={page_no}) has no text - missing flat_text, normalized_blocks, and raw_blocks", flush=True)
                        print(f"[CHUNK] [DIAG]   Available page keys: {list(page.keys())}", flush=True)
                    else:
                        if text_length > 100000:  # Very large page
                            print(f"[CHUNK] [DIAG] WARN: Page {i} (page_no={page.get('page_no', i+1)}) has very large text ({text_length:,} chars) from {text_source}", flush=True)
                
                print(f"[CHUNK] [DIAG] Page validation summary:", flush=True)
                print(f"[CHUNK] [DIAG]   Total pages: {len(pages)}", flush=True)
                print(f"[CHUNK] [DIAG]   Pages without text: {pages_without_text}", flush=True)
                print(f"[CHUNK] [DIAG]   Pages with invalid structure: {pages_with_invalid_structure}", flush=True)
                print(f"[CHUNK] [DIAG]   Pages with invalid types: {pages_with_invalid_types}", flush=True)
                print(f"[CHUNK] [DIAG]   Total text length: {total_text_length:,} characters ({total_text_length/(1024*1024):.2f}MB)", flush=True)
                
                if pages_without_text > 0:
                    validation_warnings.append(f"{pages_without_text} pages have no text content")
                
                if pages_without_text == len(pages):
                    validation_errors.append("ALL pages have no text content - cannot chunk")
                    print(f"[CHUNK] [DIAG] CRITICAL: All {len(pages)} pages have no text content!", flush=True)
            
            if validation_errors:
                error_msg = f"Normalized document validation failed: {'; '.join(validation_errors[:5])}"
                if len(validation_errors) > 5:
                    error_msg += f" (and {len(validation_errors) - 5} more errors)"
                print(f"[CHUNK] [ERROR] {error_msg}", flush=True)
                print(f"[CHUNK] [DIAG] Run 'python diagnose_normalized_json.py <file>' for detailed diagnostics", flush=True)
                return {
                    "status": "failed",
                    "error": error_msg,
                    "validation_errors": validation_errors,
                    "validation_warnings": validation_warnings,
                    "diagnostic_suggestion": "Run diagnose_normalized_json.py for detailed analysis"
                }
            
            if validation_warnings:
                print(f"[CHUNK] [WARN] Validation warnings: {len(validation_warnings)}", flush=True)
                for warning in validation_warnings[:5]:
                    print(f"[CHUNK] [WARN]   - {warning}", flush=True)
            
            print(f"[CHUNK] Validation passed", flush=True)
            
            # Initialize chunk storage (uses namespace-aware paths: contextcore_data/namespaces/{namespace}/documents)
            print(f"[CHUNK] Initializing ChunkStorage for namespace={namespace}, base_dir=contextcore_data...", flush=True)
            chunk_storage = ChunkStorage(namespace=namespace, base_dir="contextcore_data")
            print(f"[CHUNK] ChunkStorage initialized successfully", flush=True)
            
            # Get document node ID from connect stage or extract from document_id
            # This must be done BEFORE logging configuration to avoid UnboundLocalError
            connect_result = stage_data.get('connect', {})
            if isinstance(connect_result, dict):
                document_nodes = connect_result.get('document_nodes', [])
                if document_nodes:
                    doc_node_id = document_nodes[0] if isinstance(document_nodes[0], str) else document_nodes[0].get('id', '')
                else:
                    # Try to find document node in graph
                    print(f"[CHUNK] Finding document node in graph...", flush=True)
                    all_nodes = namespace_graph.get_all_nodes()
                    doc_nodes = [n for n in all_nodes if n.label == 'Document']
                    if doc_nodes:
                        doc_node_id = doc_nodes[0].id
                    else:
                        doc_node_id = f"doc_{document_id}"
            else:
                # Fallback: construct from document_id
                doc_node_id = f"doc_{document_id}"
            
            # Log chunking configuration
            print(f"[CHUNK] ========================================================================", flush=True)
            print(f"[CHUNK] CHUNKING CONFIGURATION", flush=True)
            print(f"[CHUNK] ========================================================================", flush=True)
            print(f"[CHUNK] Strategy: {chunking_strategy}", flush=True)
            print(f"[CHUNK] Chunk Size: {chunk_size} characters", flush=True)
            print(f"[CHUNK] Overlap: {overlap} characters", flush=True)
            print(f"[CHUNK] Document ID: {document_id}", flush=True)
            print(f"[CHUNK] Normalized Version: {normalized_version}", flush=True)
            print(f"[CHUNK] Document Node ID: {doc_node_id}", flush=True)
            print(f"[CHUNK] ========================================================================", flush=True)
            
            # Create chunks directly from normalized_doc
            # Optimize: Extract only what's needed for chunking (page_no and flat_text)
            # This reduces memory usage and speeds up processing
            content = normalized_doc.get('content', {})
            full_pages = content.get('pages', [])
            
            # Create lightweight pages array for chunking (only page_no and flat_text)
            # This is much faster and uses less memory, especially for Ray parallel processing
            print(f"[CHUNK] Preparing pages for chunking...", flush=True)
            pages = []
            total_text_chars = 0
            pages_with_text = 0
            pages_without_text = 0
            
            for page in full_pages:
                page_no = page.get('page_no', len(pages) + 1)
                flat_text = page.get('flat_text', '')
                text_length = len(flat_text) if isinstance(flat_text, str) else 0
                
                if text_length > 0:
                    total_text_chars += text_length
                    pages_with_text += 1
                else:
                    pages_without_text += 1
                
                lightweight_page = {
                    'page_no': page_no,
                    'flat_text': flat_text
                }
                # Only include additional fields if strategy needs them
                if chunking_strategy in ['section_based', 'table_aware', 'smart', 'code_aware', 'image_aware', 'list_aware']:
                    # Include normalized_blocks for strategies that need structure
                    if 'normalized_blocks' in page:
                        lightweight_page['normalized_blocks'] = page.get('normalized_blocks', [])
                    # Include full page object for advanced strategies
                    if chunking_strategy in ['table_aware', 'smart', 'image_aware']:
                        lightweight_page['_full_page'] = page
                
                pages.append(lightweight_page)
            
            print(f"[CHUNK] ========================================================================", flush=True)
            print(f"[CHUNK] PAGES PREPARATION SUMMARY", flush=True)
            print(f"[CHUNK] ========================================================================", flush=True)
            print(f"[CHUNK] Total pages: {len(pages)}", flush=True)
            print(f"[CHUNK] Pages with text: {pages_with_text}", flush=True)
            print(f"[CHUNK] Pages without text: {pages_without_text}", flush=True)
            print(f"[CHUNK] Total text characters: {total_text_chars:,} ({total_text_chars/(1024*1024):.2f}MB)", flush=True)
            if pages_with_text > 0:
                avg_text_per_page = total_text_chars / pages_with_text
                print(f"[CHUNK] Average text per page: {avg_text_per_page:,.0f} characters", flush=True)
            print(f"[CHUNK] Optimized pages structure: {len(pages)} pages with minimal fields for faster processing", flush=True)
            print(f"[CHUNK] ========================================================================", flush=True)
            
            # Initialize chunk counter (used across all code paths)
            chunk_nodes_created = 0
            chunks = []  # For tracking only
            import gc
            
            # Memory monitoring helper
            def get_memory_usage():
                try:
                    import psutil
                    process = psutil.Process()
                    return process.memory_info().rss / (1024 * 1024)  # MB
                except:
                    return None
            
            # Handle document-level chunking strategies (hierarchical, parent_child, section_based, table_aware, smart)
            # Note: section_based, table_aware, and smart can work page-by-page but may benefit from document context
            if chunking_strategy in ["hierarchical", "parent_child"]:
                print(f"\n[CHUNK] {'='*70}", flush=True)
                print(f"[CHUNK] DOCUMENT-LEVEL CHUNKING STRATEGY: {chunking_strategy}", flush=True)
                print(f"[CHUNK] {'='*70}", flush=True)
                print(f"[CHUNK] Processing entire document as a single unit", flush=True)
                print(f"[CHUNK] Total pages: {len(full_pages)}", flush=True)
                print(f"[CHUNK] Chunk size: {chunk_size}, Overlap: {overlap}", flush=True)
                print(f"[CHUNK] {'='*70}\n", flush=True)
                try:
                    print(f"[CHUNK] Calling create_chunks_from_normalized_json()...", flush=True)
                    chunk_creation_start = time.time()
                    
                    chunks = chunk_storage.create_chunks_from_normalized_json(
                        document_id=document_id,
                        normalized_version=normalized_version,
                        chunking_strategy=chunking_strategy,
                        strategy_version="v1",
                        chunk_size=chunk_size,
                        overlap=overlap,
                        document_node_id=doc_node_id,
                    )
                    
                    chunk_creation_time = time.time() - chunk_creation_start
                    
                    # Validate chunks result
                    if chunks is None:
                        logger.error(f"[CHUNK] {chunking_strategy} strategy returned None, falling back to fixed strategy")
                        print(f"[CHUNK] ⚠️  Strategy returned None, falling back to fixed", flush=True)
                        chunks = []
                    elif not isinstance(chunks, list):
                        logger.error(f"[CHUNK] {chunking_strategy} strategy returned invalid type {type(chunks)}, falling back to fixed strategy")
                        print(f"[CHUNK] ⚠️  Strategy returned invalid type {type(chunks)}, falling back to fixed", flush=True)
                        chunks = []
                    
                    print(f"[CHUNK] ✅ Created {len(chunks)} chunks using {chunking_strategy} strategy in {chunk_creation_time:.2f}s", flush=True)
                    if len(chunks) > 0:
                        avg_chunk_size = sum(len(c.get('content', '')) if isinstance(c, dict) else 0 for c in chunks) / len(chunks)
                        print(f"[CHUNK] Average chunk size: {avg_chunk_size:,.0f} characters", flush=True)
                    
                    # Performance checkpoint after chunk creation
                    perf_monitor.log_checkpoint(
                        "Chunk creation complete",
                        items_processed=len(chunks),
                        additional_info={
                            'chunking_strategy': chunking_strategy,
                            'chunk_creation_time': chunk_creation_time,
                            'avg_chunk_size': avg_chunk_size if len(chunks) > 0 else 0
                        }
                    )
                except MemoryError as mem_err:
                    logger.error(f"[CHUNK] Memory error during {chunking_strategy} chunking: {mem_err}")
                    print(f"[CHUNK] Memory error with {chunking_strategy} strategy, falling back to fixed strategy", flush=True)
                    gc.collect()
                    chunks = []
                except Exception as e:
                    logger.error(f"[CHUNK] Error with {chunking_strategy} strategy: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    print(f"[CHUNK] Error with {chunking_strategy} strategy: {e}, falling back to fixed strategy", flush=True)
                    chunks = []
                
                # Write hierarchical chunks incrementally to prevent memory issues
                if chunks:
                    print(f"\n[CHUNK] Writing {len(chunks)} hierarchical chunks to graph incrementally...", flush=True)
                    print(f"[CHUNK] Write batch size: 50 chunks (flush every 50 chunks)", flush=True)
                    chunk_nodes_created = 0
                    write_batch_size = 50
                    stored_chunk_ids = set()
                    chunks_failed = 0
                    write_start_time = time.time()
                    import gc
                    
                    for chunk_props in chunks:
                        try:
                            if not isinstance(chunk_props, dict):
                                logger.warning(f"[CHUNK] Invalid chunk_props type: {type(chunk_props)}, skipping")
                                chunks_failed += 1
                                continue
                            
                            chunk_id = chunk_props.get("id") or chunk_props.get("chunk_id")
                            if not chunk_id or chunk_id in stored_chunk_ids:
                                continue
                            
                            stored_chunk_ids.add(chunk_id)
                            
                            # Validate chunk properties
                            if not chunk_props.get("content"):
                                logger.warning(f"[CHUNK] Chunk {chunk_id} has no content, skipping")
                                chunks_failed += 1
                                continue
                            
                            try:
                                chunk_node = GraphNode(id=chunk_id, label="Chunk", properties=chunk_props)
                                namespace_graph.add_node(chunk_node, write_through=False)
                                
                                edge_id = str(uuid.uuid4())
                                edge = GraphEdge(id=edge_id, source=doc_node_id, target=chunk_id, label="HAS_CHUNK", properties={})
                                namespace_graph.add_edge(edge)
                                
                                chunk_nodes_created += 1
                                
                                # Periodic flush
                                if chunk_nodes_created % write_batch_size == 0:
                                    try:
                                        # Ensure graph is registered before saving
                                        if self.graph_registry and namespace_graph:
                                            if namespace not in self.graph_registry.graphs:
                                                self.graph_registry.graphs[namespace] = namespace_graph
                                            if not hasattr(namespace_graph, 'name') or namespace_graph.name != namespace:
                                                namespace_graph.name = namespace
                                        
                                        flush_start = time.time()
                                        if hasattr(namespace_graph, '_save_to_disk'):
                                            try:
                                                namespace_graph._save_to_disk()
                                                print(f"[CHUNK] Offloaded {chunk_nodes_created} chunks to disk", flush=True)
                                            except Exception as save_err:
                                                import traceback
                                                error_details = traceback.format_exc()
                                                print(f"[CHUNK] ❌ FAILED TO FLUSH: Cannot flush chunks to disk", flush=True)
                                                print(f"[CHUNK]    Error: {save_err}", flush=True)
                                                print(f"[CHUNK]    Details: {error_details[:500]}", flush=True)
                                                raise  # Re-raise to trigger fallback
                                        flush_time = time.time() - flush_start
                                        mem_usage = get_memory_usage()
                                        mem_str = f", Memory: {mem_usage:.1f}MB" if mem_usage else ""
                                        print(f"[CHUNK] 💾 Flushed {chunk_nodes_created} chunks to disk ({flush_time:.2f}s{mem_str})", flush=True)
                                        gc.collect()
                                    except Exception as flush_err:
                                        logger.warning(f"[CHUNK] Flush failed: {flush_err}")
                                        print(f"[CHUNK] ⚠️  Flush failed: {flush_err}", flush=True)
                                        print(f"[CHUNK]    Attempting registry fallback...", flush=True)
                                        # Try direct save via registry as fallback
                                        if self.graph_registry and namespace in self.graph_registry.graphs:
                                            try:
                                                self.graph_registry.save_graph(namespace)
                                                print(f"[CHUNK] ✅ Offloaded chunks via registry fallback", flush=True)
                                            except Exception as fallback_err:
                                                print(f"[CHUNK] ❌ Registry fallback also failed: {fallback_err}", flush=True)
                                                print(f"[CHUNK]    Chunks remain in memory - memory may grow!", flush=True)
                                        else:
                                            print(f"[CHUNK] ❌ Cannot use registry fallback - graph not registered", flush=True)
                                            print(f"[CHUNK]    Chunks remain in memory - memory may grow!", flush=True)
                                        
                            except MemoryError:
                                # Flush and retry
                                try:
                                    if hasattr(namespace_graph, '_save_to_disk'):
                                        namespace_graph._save_to_disk()
                                    gc.collect()
                                    chunk_node = GraphNode(id=chunk_id, label="Chunk", properties=chunk_props)
                                    namespace_graph.add_node(chunk_node, write_through=True)
                                    edge_id = str(uuid.uuid4())
                                    edge = GraphEdge(id=edge_id, source=doc_node_id, target=chunk_id, label="HAS_CHUNK", properties={})
                                    namespace_graph.add_edge(edge)
                                    chunk_nodes_created += 1
                                except Exception as retry_err:
                                    logger.error(f"[CHUNK] Failed to write chunk {chunk_id} after flush: {retry_err}")
                                    chunks_failed += 1
                            except Exception as e:
                                logger.error(f"[CHUNK] Failed to write chunk {chunk_id}: {e}")
                                chunks_failed += 1
                                continue
                        except Exception as chunk_err:
                            logger.error(f"[CHUNK] Unexpected error processing hierarchical chunk: {chunk_err}")
                            chunks_failed += 1
                            continue
                    
                    # Final flush - ensure all chunks are offloaded
                    write_time = time.time() - write_start_time
                    # Ensure graph is registered before final save
                    if self.graph_registry and namespace_graph:
                        if namespace not in self.graph_registry.graphs:
                            self.graph_registry.graphs[namespace] = namespace_graph
                        if not hasattr(namespace_graph, 'name') or namespace_graph.name != namespace:
                            namespace_graph.name = namespace
                    
                    try:
                        flush_start = time.time()
                        if hasattr(namespace_graph, '_save_to_disk'):
                            namespace_graph._save_to_disk()
                            print(f"[CHUNK] Final offload: All {chunk_nodes_created} chunks saved to disk", flush=True)
                        flush_time = time.time() - flush_start
                        print(f"[CHUNK] Final flush: {flush_time:.2f}s", flush=True)
                        gc.collect()
                    except Exception as final_flush_err:
                        import traceback
                        error_details = traceback.format_exc()
                        logger.warning(f"[CHUNK] Final flush failed: {final_flush_err}")
                        print(f"[CHUNK] ❌ FINAL FLUSH FAILED: Cannot flush {chunk_nodes_created} chunks to disk", flush=True)
                        print(f"[CHUNK]    Error: {final_flush_err}", flush=True)
                        print(f"[CHUNK]    Details: {error_details[:500]}", flush=True)
                        print(f"[CHUNK]    Attempting registry fallback...", flush=True)
                        # Try direct save via registry as fallback
                        if self.graph_registry and namespace in self.graph_registry.graphs:
                            try:
                                self.graph_registry.save_graph(namespace)
                                print(f"[CHUNK] ✅ Final offload via registry fallback successful", flush=True)
                            except Exception as fallback_err:
                                import traceback
                                fallback_details = traceback.format_exc()
                                print(f"[CHUNK] ❌ Final offload fallback also failed: {fallback_err}", flush=True)
                                print(f"[CHUNK]    Fallback details: {fallback_details[:500]}", flush=True)
                                print(f"[CHUNK]    ⚠️  WARNING: {chunk_nodes_created} chunks may remain in memory!", flush=True)
                        else:
                            print(f"[CHUNK] ❌ Cannot use registry fallback - graph not registered", flush=True)
                            print(f"[CHUNK]    ⚠️  WARNING: {chunk_nodes_created} chunks may remain in memory!", flush=True)
                    
                    print(f"\n[CHUNK] {'='*70}", flush=True)
                    print(f"[CHUNK] HIERARCHICAL CHUNKING COMPLETE", flush=True)
                    print(f"[CHUNK] {'='*70}", flush=True)
                    print(f"[CHUNK] Chunks written: {chunk_nodes_created}/{len(chunks)}", flush=True)
                    if chunks_failed > 0:
                        print(f"[CHUNK] ⚠️  Chunks failed: {chunks_failed}", flush=True)
                    print(f"[CHUNK] Write time: {write_time:.2f}s ({write_time/60:.1f} minutes)", flush=True)
                    mem_usage = get_memory_usage()
                    if mem_usage:
                        print(f"[CHUNK] Memory usage: {mem_usage:.1f}MB", flush=True)
                    print(f"[CHUNK] {'='*70}\n", flush=True)
                    
                    # Performance checkpoint after writing chunks
                    perf_monitor.log_checkpoint(
                        "Chunks written to graph",
                        items_processed=chunk_nodes_created,
                        additional_info={
                            'chunks_failed': chunks_failed,
                            'write_time': write_time
                        }
                    )
                else:
                    logger.warning(f"[CHUNK] No chunks created with {chunking_strategy} strategy")
                    chunk_nodes_created = 0
            else:
                # Use Ray for parallel chunking if available
                use_ray = _get_ray_enabled()
                print(f"[CHUNK] Ray parallel processing: {'ENABLED' if use_ray else 'DISABLED'}", flush=True)
                
                if use_ray:
                    try:
                        from .ray_stage_executors import chunk_pages_parallel
                        
                        # Create optimized normalized_doc snapshot (only what's needed)
                        # This reduces data transfer to Ray workers significantly
                        optimized_normalized_doc = None
                        needs_full_doc = chunking_strategy in ["table_aware", "smart", "image_aware", "code_aware", "list_aware"]
                        
                        if needs_full_doc:
                            # Only include tables and minimal metadata for strategies that need it
                            # For image_aware strategy, only include image metadata (not image bytes)
                            # This prevents loading large image data into memory
                            images_for_chunking = []
                            if chunking_strategy == "image_aware":
                                raw_images = normalized_doc.get("content", {}).get("images", [])
                                for img in raw_images:
                                    # Only include metadata, not image bytes
                                    image_metadata = {
                                        "id": img.get("id"),
                                        "caption": img.get("caption"),
                                        "description": img.get("description"),
                                        "from_page": img.get("from_page"),
                                        "path": img.get("path"),
                                        "format": img.get("format"),
                                        "width": img.get("width"),
                                        "height": img.get("height"),
                                        "size_bytes": img.get("size_bytes"),
                                        # Explicitly exclude image_bytes_base64 and any binary data
                                    }
                                    images_for_chunking.append(image_metadata)
                                print(f"[CHUNK] Using {len(images_for_chunking)} image placeholders (metadata only, no image bytes)", flush=True)
                            
                            optimized_normalized_doc = {
                                "content": {
                                    "tables": normalized_doc.get("content", {}).get("tables", []),
                                    "images": images_for_chunking
                                },
                                "document_id": document_id
                            }
                            print(f"[CHUNK] Created optimized normalized_doc snapshot for {chunking_strategy} strategy", flush=True)
                        
                        print(f"[CHUNK] Starting parallel chunking with Ray: {len(pages)} pages, strategy={chunking_strategy}", flush=True)
                        
                        try:
                            # OPTIMIZED: Process Ray results incrementally using streaming callback
                            # This prevents accumulating all chunks in memory before writing
                            print(f"[CHUNK] Starting streaming Ray chunking (results will be written incrementally)...", flush=True)
                            
                            # Use streaming chunk processing - chunks written as they arrive
                            chunks = chunk_pages_parallel(
                                pages=pages,
                                document_id=document_id,
                                normalized_version=normalized_version,
                                chunking_strategy=chunking_strategy,
                                chunk_size=chunk_size,
                                overlap=overlap,
                                namespace=namespace,
                                base_dir="contextcore_data",
                                use_ray=True,
                                chunking_params=params,
                                normalized_doc=optimized_normalized_doc,  # Use optimized version
                                max_concurrent=None,  # Auto-detect
                                stream_results=True,  # Enable streaming mode
                                write_callback=lambda chunk_props: self._write_chunk_immediately(
                                    chunk_props, doc_node_id, namespace_graph, stored_chunk_ids=set()
                                ) if hasattr(self, '_write_chunk_immediately') else None
                            )
                            
                            # Validate chunks result
                            if chunks is None:
                                logger.error("[CHUNK] Ray chunking returned None, falling back to sequential")
                                chunks = []
                            elif not isinstance(chunks, list):
                                logger.error(f"[CHUNK] Ray chunking returned invalid type {type(chunks)}, falling back to sequential")
                                chunks = []
                            
                            # Update document_node_id for all chunks with validation
                            # OPTIMIZED: Process and write chunks immediately, don't accumulate
                            valid_chunks = []
                            stored_chunk_ids = set()
                            for chunk_props in chunks:
                                try:
                                    if not isinstance(chunk_props, dict):
                                        logger.warning(f"[CHUNK] Invalid chunk_props type from Ray: {type(chunk_props)}, skipping")
                                        continue
                                    
                                    if 'source' in chunk_props and isinstance(chunk_props['source'], dict):
                                        chunk_props['source']['document_node_id'] = doc_node_id
                                    elif 'document_node_id' in chunk_props:
                                        chunk_props['document_node_id'] = doc_node_id
                                    
                                    # Validate chunk has required fields
                                    if not chunk_props.get("id") and not chunk_props.get("chunk_id"):
                                        logger.warning("[CHUNK] Chunk from Ray missing ID, skipping")
                                        continue
                                    if not chunk_props.get("content"):
                                        logger.warning("[CHUNK] Chunk from Ray missing content, skipping")
                                        continue
                                    
                                    valid_chunks.append(chunk_props)
                                except Exception as chunk_validate_err:
                                    logger.warning(f"[CHUNK] Error validating chunk from Ray: {chunk_validate_err}")
                                    continue
                            
                            chunks = valid_chunks
                            print(f"[CHUNK] Ray parallel processing: Created {len(chunks)} valid chunks", flush=True)
                            
                            # Chunks from Ray need to be written to graph
                            # Write them incrementally to prevent memory issues
                            if chunks:
                                print(f"[CHUNK] Writing {len(chunks)} chunks from Ray to graph incrementally...", flush=True)
                                chunk_nodes_created = 0
                                write_batch_size = 50
                                stored_chunk_ids = set()
                                chunks_failed = 0
                                import gc
                                
                                for chunk_props in chunks:
                                    try:
                                        chunk_id = chunk_props.get("id") or chunk_props.get("chunk_id")
                                        if not chunk_id or chunk_id in stored_chunk_ids:
                                            continue
                                        
                                        stored_chunk_ids.add(chunk_id)
                                        
                                        try:
                                            chunk_node = GraphNode(id=chunk_id, label="Chunk", properties=chunk_props)
                                            namespace_graph.add_node(chunk_node, write_through=False)
                                            
                                            edge_id = str(uuid.uuid4())
                                            edge = GraphEdge(id=edge_id, source=doc_node_id, target=chunk_id, label="HAS_CHUNK", properties={})
                                            namespace_graph.add_edge(edge)
                                            
                                            chunk_nodes_created += 1
                                            
                                            # Periodic flush
                                            if chunk_nodes_created % write_batch_size == 0:
                                                try:
                                                    if hasattr(namespace_graph, '_save_to_disk'):
                                                        namespace_graph._save_to_disk()
                                                    gc.collect()
                                                except Exception as flush_err:
                                                    logger.warning(f"[CHUNK] Flush failed: {flush_err}")
                                                    
                                        except MemoryError:
                                            # Flush and retry
                                            try:
                                                if hasattr(namespace_graph, '_save_to_disk'):
                                                    namespace_graph._save_to_disk()
                                                gc.collect()
                                                chunk_node = GraphNode(id=chunk_id, label="Chunk", properties=chunk_props)
                                                namespace_graph.add_node(chunk_node, write_through=True)
                                                edge_id = str(uuid.uuid4())
                                                edge = GraphEdge(id=edge_id, source=doc_node_id, target=chunk_id, label="HAS_CHUNK", properties={})
                                                namespace_graph.add_edge(edge)
                                                chunk_nodes_created += 1
                                            except Exception as retry_err:
                                                logger.error(f"[CHUNK] Failed to write chunk {chunk_id} after flush: {retry_err}")
                                                chunks_failed += 1
                                        except Exception as e:
                                            logger.error(f"[CHUNK] Failed to write chunk {chunk_id}: {e}")
                                            chunks_failed += 1
                                            continue
                                    except Exception as chunk_err:
                                        logger.error(f"[CHUNK] Unexpected error processing Ray chunk: {chunk_err}")
                                        chunks_failed += 1
                                        continue
                                
                                # Final flush - ensure all chunks are offloaded
                                # Ensure graph is registered before final save
                                if self.graph_registry and namespace_graph:
                                    if namespace not in self.graph_registry.graphs:
                                        self.graph_registry.graphs[namespace] = namespace_graph
                                    if not hasattr(namespace_graph, 'name') or namespace_graph.name != namespace:
                                        namespace_graph.name = namespace
                                
                                try:
                                    if hasattr(namespace_graph, '_save_to_disk'):
                                        namespace_graph._save_to_disk()
                                        print(f"[CHUNK] Final offload: All {chunk_nodes_created} chunks saved to disk", flush=True)
                                    gc.collect()
                                except Exception as final_flush_err:
                                    logger.warning(f"[CHUNK] Final flush failed: {final_flush_err}")
                                    # Try direct save via registry as fallback
                                    if self.graph_registry and namespace in self.graph_registry.graphs:
                                        try:
                                            self.graph_registry.save_graph(namespace)
                                            print(f"[CHUNK] Final offload via registry fallback successful", flush=True)
                                        except Exception as fallback_err:
                                            print(f"[CHUNK] Error: Final offload fallback also failed: {fallback_err}", flush=True)
                                
                                print(f"[CHUNK] Written {chunk_nodes_created} chunks to graph from Ray processing", flush=True)
                                if chunks_failed > 0:
                                    print(f"[CHUNK] Warning: {chunks_failed} chunks failed to write from Ray", flush=True)
                            else:
                                logger.warning("[CHUNK] No valid chunks from Ray processing")
                                chunk_nodes_created = 0
                                
                        except MemoryError as mem_err:
                            mem_usage = get_memory_usage()
                            logger.error(f"[CHUNK] Memory error during Ray chunking: {mem_err}")
                            logger.error(f"[CHUNK] Memory usage: {mem_usage:.1f}MB" if mem_usage else "Memory usage: N/A")
                            logger.error(f"[CHUNK] Chunks created so far: {chunk_nodes_created}")
                            print(f"[CHUNK] ⚠️  MEMORY ERROR with Ray (memory: {mem_usage:.1f}MB), falling back to sequential", flush=True)
                            gc.collect()
                            use_ray = False
                            chunk_nodes_created = 0  # Reset for sequential fallback
                        except Exception as e:
                            mem_usage = get_memory_usage()
                            logger.warning(f"Ray chunking failed, falling back to sequential: {e}")
                            logger.warning(f"Memory usage: {mem_usage:.1f}MB" if mem_usage else "Memory usage: N/A")
                            import traceback
                            error_trace = traceback.format_exc()
                            logger.warning(error_trace)
                            print(f"[CHUNK] ⚠️  Ray chunking failed: {e} (memory: {mem_usage:.1f}MB), falling back to sequential", flush=True)
                            print(f"[CHUNK]   Error trace: {error_trace[:300]}...", flush=True)
                            use_ray = False
                            chunk_nodes_created = 0  # Reset for sequential fallback
                    except Exception as outer_err:
                        mem_usage = get_memory_usage()
                        logger.error(f"[CHUNK] Error setting up Ray chunking: {outer_err}")
                        logger.error(f"Memory usage: {mem_usage:.1f}MB" if mem_usage else "Memory usage: N/A")
                        import traceback
                        error_trace = traceback.format_exc()
                        logger.error(error_trace)
                        print(f"[CHUNK] ⚠️  Ray setup failed: {outer_err} (memory: {mem_usage:.1f}MB), falling back to sequential", flush=True)
                        print(f"[CHUNK]   Error trace: {error_trace[:300]}...", flush=True)
                        use_ray = False
                        chunk_nodes_created = 0  # Reset for sequential fallback
                
                if not use_ray:
                    # Sequential chunking (fallback) - process in batches to avoid memory issues
                    # CRITICAL: Write chunks incrementally to graph to prevent memory exhaustion
                    # chunk_nodes_created already initialized above
                    
                    # STREAMING OPTIMIZATION: For simple strategies, use streaming JSON parser
                    # This prevents loading entire document into memory
                    simple_strategies = ["semantic", "fixed", "paragraph", "recursive", "sliding_window"]
                    use_streaming = chunking_strategy in simple_strategies and normalized_file_path and normalized_file_path.exists()
                    
                    # Log streaming availability
                    print(f"[CHUNK] Streaming check:", flush=True)
                    print(f"[CHUNK]   Strategy '{chunking_strategy}' in simple strategies: {chunking_strategy in simple_strategies}", flush=True)
                    print(f"[CHUNK]   Normalized file exists: {normalized_file_path.exists() if normalized_file_path else False}", flush=True)
                    print(f"[CHUNK]   Streaming available: {use_streaming}", flush=True)
                    print(f"[CHUNK]   Ray available: {use_ray}", flush=True)
                    
                    # Check if we can use streaming + Ray (best of both worlds!)
                    use_streaming_ray = use_streaming and use_ray
                    print(f"[CHUNK]   Streaming + Ray mode: {use_streaming_ray}", flush=True)
                    
                    if use_streaming_ray:
                        print(f"[CHUNK] 🚀⚡ Using STREAMING + RAY mode for {chunking_strategy} strategy", flush=True)
                        print(f"[CHUNK] Benefits: Memory efficient (streaming) + Fast (parallel Ray)", flush=True)
                        
                        try:
                            from .streaming_ray_chunking import chunk_with_streaming_ray
                            
                            # Initialize chunk counter and tracking
                            self._chunk_nodes_created = 0
                            self._chunks_in_memory = 0  # Track chunks in memory for this callback
                            stored_chunk_ids = set()
                            
                            # Write callback to persist chunks immediately
                            def write_chunk_callback(chunk_props):
                                """Write chunk immediately to graph."""
                                try:
                                    chunk_id = chunk_props.get("id")
                                    if not chunk_id or chunk_id in stored_chunk_ids:
                                        if self._chunk_nodes_created < 5:  # Log first few skips
                                            print(f"[CHUNK]   Skipping duplicate chunk: {chunk_id[:50]}...", flush=True)
                                        return
                                    
                                    stored_chunk_ids.add(chunk_id)
                                    
                                    # Log chunk being written (first 5 and every 20th)
                                    chunk_content = chunk_props.get("content", "")
                                    chunk_size = len(chunk_content) if isinstance(chunk_content, str) else 0
                                    if self._chunk_nodes_created < 5 or self._chunk_nodes_created % 20 == 0:
                                        chunk_preview = chunk_content[:100].replace('\n', ' ').strip() if chunk_content else ""
                                        print(f"[CHUNK]   Writing chunk {self._chunk_nodes_created + 1}: id={chunk_id[:50]}..., size={chunk_size:,} chars", flush=True)
                                        if chunk_preview:
                                            print(f"[CHUNK]     Preview: {chunk_preview}...", flush=True)
                                    
                                    # Update source pointer with doc_node_id
                                    if "source_pointer" in chunk_props:
                                        chunk_props["source_pointer"]["document_node_id"] = doc_node_id
                                    
                                    chunk_node = GraphNode(id=chunk_id, label="Chunk", properties=chunk_props)
                                    namespace_graph.add_node(chunk_node, write_through=False)
                                    
                                    edge_id = str(uuid.uuid4())
                                    edge = GraphEdge(id=edge_id, source=doc_node_id, target=chunk_id, label="HAS_CHUNK", properties={})
                                    namespace_graph.add_edge(edge)
                                    
                                    self._chunk_nodes_created += 1
                                    self._chunks_in_memory += 1
                                    
                                    # Log when reaching 50 chunks threshold
                                    if self._chunks_in_memory == 50:
                                        print(f"[CHUNK] ⚠️  Reached 50 chunks in memory (streaming ray) - attempting flush to prevent memory growth", flush=True)
                                    
                                    # Periodic flush with logging
                                    if self._chunk_nodes_created % 50 == 0:
                                        mem_before_flush = get_memory_usage()
                                        flush_start = time.time()
                                        flush_succeeded = False
                                        if hasattr(namespace_graph, '_save_to_disk'):
                                            try:
                                                namespace_graph._save_to_disk()
                                                flush_succeeded = True
                                                flush_time = time.time() - flush_start
                                                print(f"[CHUNK] ✅ FLUSH SUCCESS (streaming ray): Flushed {self._chunks_in_memory} chunks to disk (total: {self._chunk_nodes_created}) [{flush_time:.2f}s]", flush=True)
                                                self._chunks_in_memory = 0
                                            except Exception as save_err:
                                                import traceback
                                                error_details = traceback.format_exc()
                                                print(f"[CHUNK] ❌ FLUSH FAILED at 50 chunks (streaming ray): Cannot flush {self._chunks_in_memory} chunks to disk", flush=True)
                                                print(f"[CHUNK]    ⚠️  WARNING: {self._chunks_in_memory} chunks remain in memory and cannot be flushed!", flush=True)
                                                print(f"[CHUNK]    Error: {save_err}", flush=True)
                                                print(f"[CHUNK]    Details: {error_details[:500]}", flush=True)
                                        else:
                                            # Try registry save as fallback
                                            if self.graph_registry and namespace in self.graph_registry.graphs:
                                                try:
                                                    self.graph_registry.save_graph(namespace)
                                                    flush_succeeded = True
                                                    print(f"[CHUNK] ✅ FLUSH SUCCESS via registry (streaming ray): Flushed {self._chunks_in_memory} chunks", flush=True)
                                                    self._chunks_in_memory = 0
                                                except Exception as reg_err:
                                                    print(f"[CHUNK] ❌ FLUSH FAILED at 50 chunks via registry (streaming ray): Cannot flush {self._chunks_in_memory} chunks", flush=True)
                                                    print(f"[CHUNK]    ⚠️  WARNING: {self._chunks_in_memory} chunks remain in memory and cannot be flushed!", flush=True)
                                        
                                        gc.collect()
                                        mem_after_flush = get_memory_usage()
                                        if mem_before_flush and mem_after_flush:
                                            print(f"[CHUNK]   Memory: {mem_before_flush:.1f}MB -> {mem_after_flush:.1f}MB", flush=True)
                                
                                except MemoryError as mem_err:
                                    mem_usage = get_memory_usage()
                                    logger.error(f"[CHUNK] Memory error writing chunk {chunk_id[:50]}: {mem_err}")
                                    logger.error(f"[CHUNK] Memory usage: {mem_usage:.1f}MB" if mem_usage else "Memory usage: N/A")
                                    logger.error(f"[CHUNK] Chunk size: {chunk_size:,} chars")
                                    print(f"[CHUNK]   ⚠️  MEMORY ERROR writing chunk: {mem_err}", flush=True)
                                    raise  # Re-raise to trigger fallback
                                except Exception as write_err:
                                    logger.error(f"[CHUNK] Error writing chunk {chunk_id[:50] if chunk_id else 'unknown'}: {write_err}")
                                    import traceback
                                    logger.error(traceback.format_exc())
                                    print(f"[CHUNK]   ⚠️  Error writing chunk: {write_err}", flush=True)
                            
                            # Use streaming Ray chunking
                            chunk_start_time = time.time()
                            
                            print(f"[CHUNK] ========================================================================", flush=True)
                            print(f"[CHUNK] STREAMING + RAY CHUNKING MODE", flush=True)
                            print(f"[CHUNK] ========================================================================", flush=True)
                            print(f"[CHUNK] Strategy: {chunking_strategy}", flush=True)
                            print(f"[CHUNK] Chunk size: {chunk_size} characters", flush=True)
                            print(f"[CHUNK] Overlap: {overlap} characters", flush=True)
                            print(f"[CHUNK] Parallel processing: ENABLED (Ray)", flush=True)
                            print(f"[CHUNK] ========================================================================", flush=True)
                            
                            # Stream and process in parallel
                            for chunk_props in chunk_with_streaming_ray(
                                normalized_file_path=normalized_file_path,
                                document_id=document_id,
                                normalized_version=normalized_version,
                                chunking_strategy=chunking_strategy,
                                chunk_size=chunk_size,
                                overlap=overlap,
                                namespace=namespace,
                                base_dir="contextcore_data",
                                chunking_params=params,
                                max_concurrent=None,  # Auto-detect
                                write_callback=write_chunk_callback
                            ):
                                # Chunks are already written by callback, just track
                                pass
                            
                            chunk_nodes_created = self._chunk_nodes_created
                            
                            elapsed_time = time.time() - chunk_start_time
                            print(f"\n[CHUNK] {'='*70}", flush=True)
                            print(f"[CHUNK] STREAMING + RAY COMPLETE", flush=True)
                            print(f"[CHUNK] Total chunks created: {chunk_nodes_created}", flush=True)
                            print(f"[CHUNK] Total time: {elapsed_time:.1f}s ({elapsed_time/60:.1f} minutes)", flush=True)
                            if chunk_nodes_created > 0:
                                print(f"[CHUNK] Speed: {chunk_nodes_created/elapsed_time:.1f} chunks/sec", flush=True)
                            print(f"[CHUNK] {'='*70}", flush=True)
                            
                        except Exception as streaming_ray_err:
                            logger.error(f"[CHUNK] Streaming Ray failed: {streaming_ray_err}")
                            import traceback
                            logger.error(traceback.format_exc())
                            print(f"[CHUNK] Streaming Ray failed: {streaming_ray_err}, falling back to streaming only", flush=True)
                            use_streaming_ray = False
                            use_streaming = True  # Fall back to streaming without Ray
                    
                    if use_streaming and not use_streaming_ray:
                        print(f"[CHUNK] 🚀 Using STREAMING mode for {chunking_strategy} strategy (no full doc load)", flush=True)
                        print(f"[CHUNK]   Reason: Streaming available but Ray not available/disabled", flush=True)
                        # Use streaming parser - pages loaded one at a time
                        from .streaming_json import StreamingNormalizedDocumentParser
                        
                        # Initialize chunk counter
                        self._chunk_nodes_created = 0
                        
                        with StreamingNormalizedDocumentParser(normalized_file_path, strategy="page") as stream_parser:
                            # Get document metadata without loading full doc
                            doc_metadata = stream_parser.get_document_metadata()
                            if not document_id:
                                document_id = doc_metadata.get('document_id', document_id)
                            if not normalized_version:
                                normalized_version = doc_metadata.get('version', normalized_version)
                            
                            # Stream pages and process incrementally
                            total_pages = 0  # Will count as we stream
                            page_stream = stream_parser.stream_pages()
                            
                            # Process pages in batches using streaming
                            batch_size = 10  # Process 10 pages at a time
                            write_batch_size = 50
                            
                            source_pointer_base = {
                                "document_id": document_id,
                                "normalized_version": normalized_version,
                                "normalized_json_path": f"documents/{document_id}/normalized/normalized_{normalized_version}.json",
                                "hdf5_path": f"{document_id}/{normalized_version}",
                                "chunking_strategy": chunking_strategy,
                                "strategy_version": "v1",
                                "chunking_params": {
                                    "chunk_size": chunk_size,
                                    "overlap": overlap
                                },
                                "document_node_id": doc_node_id
                            }
                            
                            stored_chunk_ids = set()
                            batch = []
                            batch_num = 0
                            chunk_start_time = time.time()
                            
                            print(f"[CHUNK] ========================================================================", flush=True)
                            print(f"[CHUNK] STREAMING CHUNKING MODE", flush=True)
                            print(f"[CHUNK] ========================================================================", flush=True)
                            print(f"[CHUNK] Strategy: {chunking_strategy}", flush=True)
                            print(f"[CHUNK] Chunk size: {chunk_size} characters", flush=True)
                            print(f"[CHUNK] Overlap: {overlap} characters", flush=True)
                            print(f"[CHUNK] Batch size: {batch_size} pages per batch", flush=True)
                            print(f"[CHUNK] ========================================================================", flush=True)
                            
                            try:
                                for page in page_stream:
                                    total_pages += 1
                                    batch.append(page)
                                    
                                    # Process batch when full
                                    if len(batch) >= batch_size:
                                        batch_num += 1
                                        self._process_page_batch_streaming(
                                            batch, batch_num, document_id, normalized_version,
                                            chunking_strategy, chunk_size, overlap, params,
                                            doc_node_id, namespace_graph, source_pointer_base,
                                            stored_chunk_ids, write_batch_size, chunk_start_time
                                        )
                                        chunk_nodes_created = self._chunk_nodes_created
                                        batch = []
                                        gc.collect()  # Force cleanup after each batch
                                
                                # Process remaining pages in final batch
                                if batch:
                                    batch_num += 1
                                    self._process_page_batch_streaming(
                                        batch, batch_num, document_id, normalized_version,
                                        chunking_strategy, chunk_size, overlap, params,
                                        doc_node_id, namespace_graph, source_pointer_base,
                                        stored_chunk_ids, write_batch_size, chunk_start_time
                                    )
                                    chunk_nodes_created = self._chunk_nodes_created
                                    batch = []
                                    gc.collect()
                                
                                print(f"\n[CHUNK] {'='*70}", flush=True)
                                print(f"[CHUNK] STREAMING COMPLETE", flush=True)
                                print(f"[CHUNK] Total pages processed: {total_pages}", flush=True)
                                print(f"[CHUNK] Total chunks created: {chunk_nodes_created}", flush=True)
                                print(f"[CHUNK] {'='*70}", flush=True)
                                
                            except Exception as stream_err:
                                logger.error(f"[CHUNK] Streaming error: {stream_err}")
                                import traceback
                                logger.error(traceback.format_exc())
                                print(f"[CHUNK] Streaming failed: {stream_err}, falling back to standard mode", flush=True)
                                use_streaming = False  # Fall through to standard processing
                                chunk_nodes_created = self._chunk_nodes_created if hasattr(self, '_chunk_nodes_created') else 0
                    
                    # Skip standard processing if streaming (with or without Ray) succeeded
                    if use_streaming_ray or (use_streaming and chunk_nodes_created > 0):
                        # Already processed with streaming, skip standard mode
                        print(f"[CHUNK] ✅ Streaming processing completed, skipping standard mode", flush=True)
                        pass
                    elif not use_streaming:
                        print(f"[CHUNK] Using STANDARD mode (streaming not available)", flush=True)
                        print(f"[CHUNK]   Reason: Strategy '{chunking_strategy}' requires full document or file not found", flush=True)
                        # Standard mode: pages already loaded in memory
                        # Adaptive batch size based on total pages (smaller batches to prevent crashes)
                        total_pages = len(pages)
                    if total_pages > 100:
                        batch_size = 10  # Smaller batches for large documents to prevent crashes
                    elif total_pages > 50:
                        batch_size = 8
                    else:
                        batch_size = 5  # Even smaller for safety
                    
                    # Incremental write batch size (write to graph every N chunks)
                    write_batch_size = 50  # Write to graph every 50 chunks to prevent memory buildup
                    
                    import time
                    import gc
                    chunk_start_time = time.time()
                    
                    print(f"[CHUNK] ========================================================================", flush=True)
                    print(f"[CHUNK] STARTING SEQUENTIAL CHUNKING", flush=True)
                    print(f"[CHUNK] ========================================================================", flush=True)
                    print(f"[CHUNK] Total pages: {total_pages}", flush=True)
                    print(f"[CHUNK] Strategy: {chunking_strategy}", flush=True)
                    print(f"[CHUNK] Chunk size: {chunk_size} characters", flush=True)
                    print(f"[CHUNK] Overlap: {overlap} characters", flush=True)
                    print(f"[CHUNK] Batch size: {batch_size} pages per batch", flush=True)
                    print(f"[CHUNK] Estimated batches: {(total_pages + batch_size - 1) // batch_size}", flush=True)
                    print(f"[CHUNK] Write batch size: {write_batch_size} chunks (flush to disk every {write_batch_size} chunks)", flush=True)
                    print(f"[CHUNK] ========================================================================", flush=True)
                    
                    # Track stored chunk IDs to prevent duplicates
                    stored_chunk_ids = set()
                    
                    # Build source pointer base once (reused for all chunks)
                    source_pointer_base = {
                        "document_id": document_id,
                        "normalized_version": normalized_version,
                        "normalized_json_path": f"documents/{document_id}/normalized/normalized_{normalized_version}.json",
                        "hdf5_path": f"{document_id}/{normalized_version}",
                        "chunking_strategy": chunking_strategy,
                        "strategy_version": "v1",
                        "chunking_params": {
                            "chunk_size": chunk_size,
                            "overlap": overlap
                        },
                        "document_node_id": doc_node_id
                    }
                    
                    for batch_start in range(0, total_pages, batch_size):
                        try:
                            batch_end = min(batch_start + batch_size, total_pages)
                            batch_pages = pages[batch_start:batch_end]
                            batch_start_time = time.time()
                            batch_num = (batch_start // batch_size) + 1
                            total_batches = (total_pages + batch_size - 1) // batch_size
                            
                            print(f"\n[CHUNK] {'='*70}", flush=True)
                            print(f"[CHUNK] BATCH {batch_num}/{total_batches}: Pages {batch_start+1}-{batch_end} of {total_pages} ({batch_end-batch_start} pages)", flush=True)
                            print(f"[CHUNK] {'='*70}", flush=True)
                            
                            # Calculate batch text stats
                            batch_text_chars = sum(len(p.get('flat_text', '')) for p in batch_pages if isinstance(p.get('flat_text'), str))
                            print(f"[CHUNK] Batch text: {batch_text_chars:,} characters ({batch_text_chars/(1024*1024):.2f}MB)", flush=True)
                            
                            batch_chunks = 0
                            # REMOVED: batch_chunk_props accumulation - chunks are written immediately to prevent memory buildup
                            
                            for page_idx, page in enumerate(batch_pages):
                                page_no = page.get('page_no', batch_start + page_idx + 1)
                                page_text = page.get('flat_text', '')
                                page_text_length = len(page_text) if isinstance(page_text, str) else 0
                            
                                if not page_text or page_text_length == 0:
                                    print(f"[CHUNK]   Page {page_no}: ⚠️  Skipped (no text content)", flush=True)
                                    continue
                                
                                page_start_time = time.time()
                                print(f"\n[CHUNK]   ┌─ Page {page_no} ({page_idx+1}/{len(batch_pages)} in batch)", flush=True)
                                print(f"[CHUNK]   │  Text length: {page_text_length:,} characters", flush=True)
                                print(f"[CHUNK]   │  Strategy: {chunking_strategy}", flush=True)
                                print(f"[CHUNK]   │  Starting chunking...", flush=True)
                                
                                # Chunk the page text with comprehensive error handling
                                page_chunks = None
                                chunk_failed = False
                                
                                # Check memory before chunking large pages
                                if page_text_length > 100000:  # Very large page (>100KB)
                                    mem_usage = get_memory_usage()
                                    if mem_usage and mem_usage > 8000:  # >8GB used
                                        print(f"[CHUNK]   │  ⚠️  Large page detected ({page_text_length:,} chars)", flush=True)
                                        print(f"[CHUNK]   │  Memory usage: {mem_usage:.1f}MB - flushing before chunking...", flush=True)
                                        if hasattr(namespace_graph, '_save_to_disk'):
                                            namespace_graph._save_to_disk()
                                        gc.collect()
                                        print(f"[CHUNK]   │  ✅ Flush complete", flush=True)
                                
                                try:
                                    chunk_start = time.time()
                                    print(f"[CHUNK]   │  Calling chunking function...", flush=True)
                                    print(f"[CHUNK]   │    Strategy: {chunking_strategy}", flush=True)
                                    print(f"[CHUNK]   │    Chunk size: {chunk_size}, Overlap: {overlap}", flush=True)
                                    
                                    # Validate chunking parameters before calling
                                    if chunk_size <= 0:
                                        logger.warning(f"[CHUNK] Invalid chunk_size={chunk_size}, using default 1000")
                                        chunk_size = 1000
                                    if overlap < 0 or overlap >= chunk_size:
                                        logger.warning(f"[CHUNK] Invalid overlap={overlap}, using default 200")
                                        overlap = min(200, chunk_size - 1)
                                    
                                    # Call chunking strategy with fallback to fixed if complex strategy fails
                                    try:
                                        if chunking_strategy == "semantic":
                                            page_chunks = chunk_storage._chunk_semantic(page_text, chunk_size, overlap)
                                        elif chunking_strategy == "paragraph":
                                            page_chunks = chunk_storage._chunk_paragraph(page_text, overlap)
                                        elif chunking_strategy == "recursive":
                                            page_chunks = chunk_storage._chunk_recursive(page_text, chunk_size, overlap)
                                        elif chunking_strategy == "sliding_window":
                                            step_size = params.get('step_size', chunk_size - overlap)
                                            page_chunks = chunk_storage._chunk_sliding_window(page_text, chunk_size, overlap, step_size=step_size)
                                        elif chunking_strategy == "section_based":
                                            section_markers = params.get('section_markers', ["##", "###", "Chapter", "Section", "Part", "#"])
                                            preserve_hierarchy = params.get('preserve_hierarchy', True)
                                            page_chunks = chunk_storage._chunk_section_based(page, chunk_size, overlap, 
                                                                                            section_markers=section_markers, 
                                                                                            preserve_hierarchy=preserve_hierarchy)
                                        elif chunking_strategy == "table_aware":
                                            extract_tables = params.get('extract_tables', True)
                                            preserve_table_context = params.get('preserve_table_context', True)
                                            table_chunk_size = params.get('table_chunk_size', 300)
                                            context_before = params.get('context_before', 200)
                                            context_after = params.get('context_after', 200)
                                            page_chunks = chunk_storage._chunk_table_aware(page, normalized_doc, chunk_size, overlap,
                                                                                          extract_tables=extract_tables,
                                                                                          preserve_table_context=preserve_table_context,
                                                                                          table_chunk_size=table_chunk_size,
                                                                                          context_before=context_before,
                                                                                          context_after=context_after)
                                        elif chunking_strategy == "smart":
                                            document_type_detection = params.get('document_type_detection', True)
                                            fallback_strategy = params.get('fallback_strategy', 'fixed')
                                            page_chunks = chunk_storage._chunk_smart(page, normalized_doc, chunk_size, overlap,
                                                                                    document_type_detection=document_type_detection,
                                                                                    fallback_strategy=fallback_strategy)
                                        elif chunking_strategy == "token_based":
                                            max_tokens = params.get('max_tokens', 512)
                                            overlap_tokens = params.get('overlap_tokens', 50)
                                            tokenizer = params.get('tokenizer', 'tiktoken')
                                            model = params.get('model', 'gpt-3.5-turbo')
                                            page_chunks = chunk_storage._chunk_token_based(page_text, max_tokens, overlap_tokens, tokenizer, model)
                                        elif chunking_strategy == "semantic_similarity":
                                            similarity_threshold = params.get('similarity_threshold', 0.7)
                                            max_chunk_size = params.get('max_chunk_size', chunk_size)
                                            min_chunk_size = params.get('min_chunk_size', 200)
                                            embedding_model = params.get('embedding_model', 'sentence-transformers')
                                            page_chunks = chunk_storage._chunk_semantic_similarity(page_text, similarity_threshold, max_chunk_size, min_chunk_size, embedding_model)
                                        elif chunking_strategy == "topic_aware":
                                            num_topics = params.get('num_topics', 10)
                                            min_chunk_size = params.get('min_chunk_size', 300)
                                            topic_model = params.get('topic_model', 'simple')
                                            page_chunks = chunk_storage._chunk_topic_aware(page_text, num_topics, min_chunk_size, topic_model)
                                        elif chunking_strategy == "qa_aware":
                                            question_length = params.get('question_length', 50)
                                            context_size = params.get('context_size', 500)
                                            answer_markers = params.get('answer_markers', None)
                                            page_chunks = chunk_storage._chunk_qa_aware(page_text, question_length, context_size, answer_markers)
                                        elif chunking_strategy == "code_aware":
                                            preserve_code_blocks = params.get('preserve_code_blocks', True)
                                            code_context = params.get('code_context', 200)
                                            min_code_block_size = params.get('min_code_block_size', 50)
                                            page_chunks = chunk_storage._chunk_code_aware(page, chunk_size, overlap, preserve_code_blocks, code_context, min_code_block_size)
                                        elif chunking_strategy == "image_aware":
                                            include_captions = params.get('include_captions', True)
                                            caption_context = params.get('caption_context', 300)
                                            image_chunk_size = params.get('image_chunk_size', 200)
                                            page_chunks = chunk_storage._chunk_image_aware(page, normalized_doc, chunk_size, overlap, include_captions, caption_context, image_chunk_size)
                                        elif chunking_strategy == "adaptive":
                                            base_chunk_size = params.get('base_chunk_size', chunk_size)
                                            complexity_threshold = params.get('complexity_threshold', 0.5)
                                            density_factor = params.get('density_factor', 1.2)
                                            page_chunks = chunk_storage._chunk_adaptive(page_text, base_chunk_size, overlap, complexity_threshold, density_factor)
                                        elif chunking_strategy == "entity_aware":
                                            entity_types = params.get('entity_types', None)
                                            page_chunks = chunk_storage._chunk_entity_aware(page_text, chunk_size, overlap, entity_types)
                                        elif chunking_strategy == "multilang":
                                            per_language_chunking = params.get('per_language_chunking', True)
                                            page_chunks = chunk_storage._chunk_multilang(page_text, chunk_size, overlap, per_language_chunking)
                                        elif chunking_strategy == "citation_aware":
                                            citation_patterns = params.get('citation_patterns', None)
                                            citation_context = params.get('citation_context', 300)
                                            page_chunks = chunk_storage._chunk_citation_aware(page_text, chunk_size, overlap, citation_patterns, citation_context)
                                        elif chunking_strategy == "dialogue_aware":
                                            speaker_detection = params.get('speaker_detection', True)
                                            preserve_turns = params.get('preserve_turns', True)
                                            page_chunks = chunk_storage._chunk_dialogue_aware(page_text, chunk_size, overlap, speaker_detection, preserve_turns)
                                        elif chunking_strategy == "formula_aware":
                                            formula_patterns = params.get('formula_patterns', None)
                                            formula_context = params.get('formula_context', 200)
                                            page_chunks = chunk_storage._chunk_formula_aware(page_text, chunk_size, overlap, formula_patterns, formula_context)
                                        elif chunking_strategy == "list_aware":
                                            preserve_lists = params.get('preserve_lists', True)
                                            list_context = params.get('list_context', 200)
                                            page_chunks = chunk_storage._chunk_list_aware(page, chunk_size, overlap, preserve_lists, list_context)
                                        else:  # fixed
                                            page_chunks = chunk_storage._chunk_fixed(page_text, chunk_size, overlap)
                                    except (MemoryError, OSError) as mem_err:
                                        # Memory or system resource error - fallback to fixed strategy
                                        logger.warning(f"[CHUNK] Memory/resource error with {chunking_strategy} on page {page_no}, falling back to fixed strategy: {mem_err}")
                                        print(f"[CHUNK]     Memory error with {chunking_strategy}, falling back to fixed strategy...", flush=True)
                                        gc.collect()
                                        try:
                                            page_chunks = chunk_storage._chunk_fixed(page_text, chunk_size, overlap)
                                        except Exception as fallback_err:
                                            logger.error(f"[CHUNK] Fallback fixed chunking also failed on page {page_no}: {fallback_err}")
                                            chunk_failed = True
                                            page_chunks = None
                                    
                                    # Validate chunk results
                                    if page_chunks is None:
                                        logger.warning(f"[CHUNK] Chunking returned None for page {page_no}, using fallback")
                                        chunk_failed = True
                                        try:
                                            page_chunks = chunk_storage._chunk_fixed(page_text, chunk_size, overlap)
                                        except Exception as fallback_err:
                                            logger.error(f"[CHUNK] Fallback chunking failed: {fallback_err}")
                                            page_chunks = []
                                    elif not isinstance(page_chunks, (list, tuple)):
                                        logger.warning(f"[CHUNK] Chunking returned invalid type {type(page_chunks)} for page {page_no}, using fallback")
                                        chunk_failed = True
                                        try:
                                            page_chunks = chunk_storage._chunk_fixed(page_text, chunk_size, overlap)
                                        except Exception as fallback_err:
                                            logger.error(f"[CHUNK] Fallback chunking failed: {fallback_err}")
                                            page_chunks = []
                                    elif len(page_chunks) == 0 and len(page_text.strip()) > 0:
                                        # Empty chunks but page has text - might be an issue
                                        logger.warning(f"[CHUNK] Chunking returned empty list for page {page_no} with {len(page_text)} chars, using fallback")
                                        chunk_failed = True
                                        try:
                                            page_chunks = chunk_storage._chunk_fixed(page_text, chunk_size, overlap)
                                        except Exception as fallback_err:
                                            logger.error(f"[CHUNK] Fallback chunking failed: {fallback_err}")
                                            page_chunks = []
                                    
                                    # Validate chunk format (should be list of tuples: (text, start, end))
                                    if page_chunks:
                                        validated_chunks = []
                                        for idx, chunk in enumerate(page_chunks):
                                            if not isinstance(chunk, (tuple, list)) or len(chunk) < 2:
                                                logger.warning(f"[CHUNK] Invalid chunk format at index {idx} on page {page_no}, skipping")
                                                continue
                                            chunk_text = chunk[0] if len(chunk) > 0 else ""
                                            start = chunk[1] if len(chunk) > 1 else 0
                                            end = chunk[2] if len(chunk) > 2 else len(chunk_text)
                                            
                                            # Validate chunk boundaries
                                            if not isinstance(chunk_text, str):
                                                logger.warning(f"[CHUNK] Invalid chunk text type on page {page_no}, chunk {idx}, skipping")
                                                continue
                                            if start < 0 or end < 0 or start >= end:
                                                logger.warning(f"[CHUNK] Invalid chunk boundaries on page {page_no}, chunk {idx} (start={start}, end={end}), skipping")
                                                continue
                                            if end > page_text_length:
                                                logger.warning(f"[CHUNK] Chunk end exceeds page length on page {page_no}, chunk {idx}, clamping")
                                                end = page_text_length
                                                chunk_text = chunk_text[:end-start] if len(chunk_text) > (end-start) else chunk_text
                                            
                                            validated_chunks.append((chunk_text, start, end))
                                        
                                        page_chunks = validated_chunks
                                        if len(validated_chunks) < len(page_chunks):
                                            logger.warning(f"[CHUNK] Validated {len(validated_chunks)}/{len(page_chunks)} chunks for page {page_no}")
                                    
                                    chunk_time = time.time() - chunk_start
                                    num_chunks = len(page_chunks) if page_chunks else 0
                                    if chunk_failed:
                                        print(f"[CHUNK]   │  ✅ Chunking complete (with fallback): {num_chunks} chunks in {chunk_time:.2f}s", flush=True)
                                    else:
                                        print(f"[CHUNK]   │  ✅ Chunking complete: {num_chunks} chunks in {chunk_time:.2f}s", flush=True)
                                    
                                    if num_chunks > 0:
                                        # Log detailed chunk information
                                        chunk_sizes = [len(c[0]) if len(c) > 0 else 0 for c in page_chunks]
                                        avg_chunk_size = sum(chunk_sizes) / num_chunks
                                        min_chunk_size = min(chunk_sizes)
                                        max_chunk_size = max(chunk_sizes)
                                        total_chunk_chars = sum(chunk_sizes)
                                        
                                        print(f"[CHUNK]   │  Chunk statistics:", flush=True)
                                        print(f"[CHUNK]   │    Count: {num_chunks}", flush=True)
                                        print(f"[CHUNK]   │    Total chars: {total_chunk_chars:,}", flush=True)
                                        print(f"[CHUNK]   │    Avg size: {avg_chunk_size:,.0f} chars", flush=True)
                                        print(f"[CHUNK]   │    Min size: {min_chunk_size:,} chars", flush=True)
                                        print(f"[CHUNK]   │    Max size: {max_chunk_size:,} chars", flush=True)
                                        
                                        # Log first chunk preview
                                        if page_chunks and len(page_chunks[0]) > 0:
                                            first_chunk_text = page_chunks[0][0]
                                            first_chunk_preview = first_chunk_text[:150].replace('\n', ' ').strip()
                                            print(f"[CHUNK]   │    First chunk preview: {first_chunk_preview}...", flush=True)
                                        
                                        # Log memory after chunking
                                        mem_after_chunk = get_memory_usage()
                                        if mem_after_chunk:
                                            print(f"[CHUNK]   │    Memory after chunking: {mem_after_chunk:.1f} MB", flush=True)
                                        
                                except MemoryError as mem_err:
                                    mem_usage = get_memory_usage()
                                    mem_str = f" (current: {mem_usage:.1f}MB)" if mem_usage else ""
                                    print(f"[CHUNK]   Page {page_no}: ⚠️  MEMORY ERROR during chunking{mem_str}", flush=True)
                                    print(f"[CHUNK]   │  Error: {mem_err}", flush=True)
                                    logger.warning(f"[CHUNK] Memory error on page {page_no}: {mem_err}")
                                    logger.warning(f"[CHUNK] Page text length: {page_text_length:,} chars")
                                    logger.warning(f"[CHUNK] Memory usage: {mem_usage:.1f}MB" if mem_usage else "Memory usage: N/A")
                                    
                                    gc.collect()  # Force garbage collection
                                    # Try to flush graph to free memory
                                    if hasattr(namespace_graph, '_save_to_disk'):
                                        try:
                                            print(f"[CHUNK]   │  Flushing graph to free memory...", flush=True)
                                            namespace_graph._save_to_disk()
                                            mem_after_flush = get_memory_usage()
                                            if mem_after_flush:
                                                print(f"[CHUNK]   │  Memory after flush: {mem_after_flush:.1f}MB", flush=True)
                                        except Exception as flush_err:
                                            print(f"[CHUNK]   │  Flush failed: {flush_err}", flush=True)
                                            logger.error(f"[CHUNK] Flush failed: {flush_err}")
                                    print(f"[CHUNK]   │  Skipping page {page_no} due to memory error", flush=True)
                                    continue
                                except Exception as e:
                                    mem_usage = get_memory_usage()
                                    mem_str = f" (memory: {mem_usage:.1f}MB)" if mem_usage else ""
                                    logger.error(f"[CHUNK] Error chunking page {page_no}: {e}")
                                    logger.error(f"[CHUNK] Page text length: {page_text_length:,} chars{mem_str}")
                                    import traceback
                                    error_trace = traceback.format_exc()
                                    logger.error(error_trace)
                                    print(f"[CHUNK]   Page {page_no}: ⚠️  Error - {e}{mem_str}", flush=True)
                                    print(f"[CHUNK]   │  Attempting fallback...", flush=True)
                                    
                                    # Last resort: try fixed chunking
                                    try:
                                        gc.collect()
                                        page_chunks = chunk_storage._chunk_fixed(page_text, chunk_size, overlap)
                                        print(f"[CHUNK]   Page {page_no}: Fallback fixed chunking succeeded, {len(page_chunks)} chunks", flush=True)
                                    except Exception as fallback_err:
                                        logger.error(f"[CHUNK] Fallback chunking also failed for page {page_no}: {fallback_err}")
                                        print(f"[CHUNK]   Page {page_no}: All chunking attempts failed, skipping page", flush=True)
                                        continue
                                
                                # Validate page_chunks before processing
                                if not page_chunks or len(page_chunks) == 0:
                                    print(f"[CHUNK]   │  ⚠️  No chunks created, skipping page", flush=True)
                                    print(f"[CHUNK]   └─ Page {page_no} complete: 0 chunks", flush=True)
                                    continue
                                
                                num_page_chunks = len(page_chunks)
                                batch_chunks += num_page_chunks
                                
                                print(f"[CHUNK]   │  Processing {num_page_chunks} chunks for graph storage...", flush=True)
                                
                                # Create chunk properties and write incrementally to prevent memory buildup
                                props_start = time.time()
                                source_pointer_base['page_no'] = page_no
                                
                                chunks_written_this_page = 0
                                chunks_failed_this_page = 0
                                chunks_skipped_this_page = 0
                                
                                for chunk_idx, chunk_data in enumerate(page_chunks):
                                    try:
                                        # Validate chunk data format
                                        if not isinstance(chunk_data, (tuple, list)) or len(chunk_data) < 2:
                                            logger.warning(f"[CHUNK] Invalid chunk data format at index {chunk_idx} on page {page_no}, skipping")
                                            chunks_failed_this_page += 1
                                            continue
                                        
                                        chunk_text = chunk_data[0] if len(chunk_data) > 0 else ""
                                        start = chunk_data[1] if len(chunk_data) > 1 else 0
                                        end = chunk_data[2] if len(chunk_data) > 2 else len(chunk_text)
                                        
                                        # Validate chunk text
                                        if not isinstance(chunk_text, str):
                                            logger.warning(f"[CHUNK] Invalid chunk text type at index {chunk_idx} on page {page_no}, skipping")
                                            chunks_failed_this_page += 1
                                            continue
                                        
                                        # Skip empty chunks
                                        if not chunk_text or not chunk_text.strip():
                                            logger.debug(f"[CHUNK] Empty chunk at index {chunk_idx} on page {page_no}, skipping")
                                            continue
                                        
                                        # Validate boundaries
                                        if start < 0:
                                            start = 0
                                        if end > page_text_length:
                                            end = page_text_length
                                        if start >= end:
                                            logger.warning(f"[CHUNK] Invalid boundaries at index {chunk_idx} on page {page_no} (start={start}, end={end}), skipping")
                                            chunks_failed_this_page += 1
                                            continue
                                        
                                        from contextsynapse.extraction.id_generator import IDGenerator
                                        try:
                                            chunk_id = IDGenerator.generate_chunk_id(
                                                document_id=document_id,
                                                page_no=page_no,
                                                chunk_index=chunk_idx,
                                                chunking_strategy=chunking_strategy,
                                                strategy_version="v1",
                                                namespace=None
                                            )
                                        except Exception as id_err:
                                            logger.error(f"[CHUNK] Failed to generate chunk ID for page {page_no}, chunk {chunk_idx}: {id_err}")
                                            chunks_failed_this_page += 1
                                            continue
                                        
                                        if not chunk_id:
                                            logger.warning(f"[CHUNK] Generated empty chunk ID for page {page_no}, chunk {chunk_idx}, skipping")
                                            chunks_failed_this_page += 1
                                            continue
                                        
                                        # Skip if already stored (deduplication)
                                        if chunk_id in stored_chunk_ids:
                                            chunks_skipped_this_page += 1
                                            continue
                                        stored_chunk_ids.add(chunk_id)
                                        
                                        # Create chunk properties with validation
                                        try:
                                            source_pointer = source_pointer_base.copy()
                                            source_pointer["start_char"] = start
                                            source_pointer["end_char"] = end
                                            
                                            chunk_props = {
                                                "id": chunk_id,
                                                "content": chunk_text,
                                                "source_pointer": source_pointer,
                                                "chunk_index": chunk_idx,
                                                "metadata": {
                                                    "chunk_size": len(chunk_text),
                                                    "page_no": page_no
                                                }
                                            }
                                            
                                            # Validate chunk_props before writing
                                            if not chunk_props.get("id") or not chunk_props.get("content"):
                                                logger.warning(f"[CHUNK] Invalid chunk properties for chunk {chunk_id}, skipping")
                                                chunks_failed_this_page += 1
                                                continue
                                            
                                        except Exception as props_err:
                                            logger.error(f"[CHUNK] Failed to create chunk properties for page {page_no}, chunk {chunk_idx}: {props_err}")
                                            chunks_failed_this_page += 1
                                            continue
                                        
                                        # Write immediately to graph (incremental write to prevent memory buildup)
                                        try:
                                            # Log chunk being written
                                            chunk_text_preview = chunk_text[:100].replace('\n', ' ').strip() if chunk_text else ""
                                            if chunk_nodes_created < 3 or chunk_nodes_created % 10 == 0:  # Log first 3 and every 10th
                                                print(f"[CHUNK]   │    Writing chunk {chunk_idx+1}/{num_page_chunks}: id={chunk_id[:50]}..., size={len(chunk_text):,} chars", flush=True)
                                                if chunk_text_preview:
                                                    print(f"[CHUNK]   │      Preview: {chunk_text_preview}...", flush=True)
                                            
                                            chunk_node = GraphNode(id=chunk_id, label="Chunk", properties=chunk_props)
                                            namespace_graph.add_node(chunk_node, write_through=False)  # Batch write
                                            
                                            # Link to document
                                            edge_id = str(uuid.uuid4())
                                            edge = GraphEdge(id=edge_id, source=doc_node_id, target=chunk_id, label="HAS_CHUNK", properties={})
                                            namespace_graph.add_edge(edge)
                                            
                                            chunk_nodes_created += 1
                                            chunks_written_this_page += 1
                                            # REMOVED: chunks.append() - no accumulation, chunks written immediately
                                            
                                            # Periodic flush to disk to prevent memory issues
                                            if chunk_nodes_created % write_batch_size == 0:
                                                # Ensure graph is registered before saving
                                                if self.graph_registry and namespace_graph:
                                                    if namespace not in self.graph_registry.graphs:
                                                        self.graph_registry.graphs[namespace] = namespace_graph
                                                    if not hasattr(namespace_graph, 'name') or namespace_graph.name != namespace:
                                                        namespace_graph.name = namespace
                                                
                                                flush_start = time.time()
                                                try:
                                                    if hasattr(namespace_graph, '_save_to_disk'):
                                                        namespace_graph._save_to_disk()
                                                        print(f"[CHUNK]   │  Offloaded {chunk_nodes_created} chunks to disk", flush=True)
                                                    flush_time = time.time() - flush_start
                                                    mem_usage = get_memory_usage()
                                                    mem_str = f", Memory: {mem_usage:.1f}MB" if mem_usage else ""
                                                    print(f"[CHUNK]   │  💾 Flushed {chunk_nodes_created} chunks to disk ({flush_time:.2f}s{mem_str})", flush=True)
                                                    gc.collect()  # Force garbage collection after flush
                                                except Exception as flush_err:
                                                    logger.warning(f"[CHUNK] Flush failed: {flush_err}")
                                                    print(f"[CHUNK]   │  ⚠️  Flush failed: {flush_err}", flush=True)
                                                    # Try direct save via registry as fallback
                                                    if self.graph_registry and namespace in self.graph_registry.graphs:
                                                        try:
                                                            self.graph_registry.save_graph(namespace)
                                                            print(f"[CHUNK]   │  Offloaded chunks via registry fallback", flush=True)
                                                        except:
                                                            pass
                                                    
                                        except MemoryError:
                                            # If memory error, flush immediately and retry
                                            print(f"[CHUNK]     Memory pressure detected, flushing to disk...", flush=True)
                                            try:
                                                if hasattr(namespace_graph, '_save_to_disk'):
                                                    namespace_graph._save_to_disk()
                                                gc.collect()
                                                
                                                # Retry once with immediate write
                                                chunk_node = GraphNode(id=chunk_id, label="Chunk", properties=chunk_props)
                                                namespace_graph.add_node(chunk_node, write_through=True)  # Immediate write
                                                edge_id = str(uuid.uuid4())
                                                edge = GraphEdge(id=edge_id, source=doc_node_id, target=chunk_id, label="HAS_CHUNK", properties={})
                                                namespace_graph.add_edge(edge)
                                                chunk_nodes_created += 1
                                                chunks_written_this_page += 1
                                                # REMOVED: chunks.append() - no accumulation
                                            except Exception as e2:
                                                logger.error(f"[CHUNK] Failed to write chunk {chunk_id} after flush: {e2}")
                                                chunks_failed_this_page += 1
                                                continue
                                        except Exception as e:
                                            logger.error(f"[CHUNK] Failed to write chunk {chunk_id}: {e}")
                                            chunks_failed_this_page += 1
                                            continue
                                            
                                    except Exception as chunk_err:
                                        logger.error(f"[CHUNK] Unexpected error processing chunk {chunk_idx} on page {page_no}: {chunk_err}")
                                        import traceback
                                        logger.error(traceback.format_exc())
                                        chunks_failed_this_page += 1
                                        continue
                                
                                if chunks_failed_this_page > 0:
                                    logger.warning(f"[CHUNK] Page {page_no}: {chunks_failed_this_page} chunks failed to write out of {num_page_chunks} total")
                                
                                props_time = time.time() - props_start
                                
                                page_time = time.time() - page_start_time
                                # Always show progress for each page
                                print(f"[CHUNK]   │  Chunks written: {chunks_written_this_page}/{num_page_chunks}", flush=True)
                                if chunks_skipped_this_page > 0:
                                    print(f"[CHUNK]   │  Chunks skipped (duplicates): {chunks_skipped_this_page}", flush=True)
                                if chunks_failed_this_page > 0:
                                    print(f"[CHUNK]   │  ⚠️  Chunks failed: {chunks_failed_this_page}", flush=True)
                                
                                print(f"[CHUNK]   │  Timing: chunk={chunk_time:.2f}s, props={props_time:.2f}s, total={page_time:.2f}s", flush=True)
                                print(f"[CHUNK]   └─ ✅ Page {page_no} complete: {chunks_written_this_page} chunks written", flush=True)
                            
                            batch_time = time.time() - batch_start_time
                            elapsed_time = time.time() - chunk_start_time
                            total_chunks = chunk_nodes_created
                            avg_time_per_page = elapsed_time / (batch_end) if batch_end > 0 else 0
                            remaining_pages = total_pages - batch_end
                            estimated_remaining = avg_time_per_page * remaining_pages if avg_time_per_page > 0 else 0
                            
                            # Force flush after each batch
                            flush_start = time.time()
                            if hasattr(namespace_graph, '_save_to_disk'):
                                namespace_graph._save_to_disk()
                            flush_time = time.time() - flush_start
                            gc.collect()  # Force garbage collection
                            
                            # Memory monitoring
                            mem_usage = get_memory_usage()
                            mem_str = f"{mem_usage:.1f}MB" if mem_usage else "N/A"
                            
                            print(f"\n[CHUNK] {'='*70}", flush=True)
                            print(f"[CHUNK] BATCH {batch_num}/{total_batches} COMPLETE", flush=True)
                            print(f"[CHUNK] {'='*70}", flush=True)
                            print(f"[CHUNK] Pages processed: {batch_end-batch_start} pages (pages {batch_start+1}-{batch_end})", flush=True)
                            print(f"[CHUNK] Chunks created: {batch_chunks} chunks from this batch", flush=True)
                            print(f"[CHUNK] Total chunks in graph: {total_chunks:,} chunks", flush=True)
                            print(f"[CHUNK] Batch time: {batch_time:.2f}s (flush: {flush_time:.2f}s)", flush=True)
                            print(f"[CHUNK] Memory usage: {mem_str}", flush=True)
                            print(f"[CHUNK] Progress: {batch_end}/{total_pages} pages ({100*batch_end/total_pages:.1f}%)", flush=True)
                            print(f"[CHUNK] Elapsed time: {elapsed_time:.1f}s ({elapsed_time/60:.1f} minutes)", flush=True)
                            if estimated_remaining > 0:
                                print(f"[CHUNK] Estimated remaining: {estimated_remaining:.1f}s ({estimated_remaining/60:.1f} minutes)", flush=True)
                            print(f"[CHUNK] {'='*70}", flush=True)
                            
                            # Clear batch pages from memory
                            del batch_pages
                            # REMOVED: del batch_chunk_props - no longer accumulating
                            
                            # Create checkpoint after each batch for resumption capability
                            # Checkpoints are now stored in namespace directory: namespaces/{namespace}/checkpoints/
                            try:
                                from ...core.checkpoint import CheckpointManager
                                # Use namespace-centric structure - checkpoints in namespace directory
                                checkpoint_manager = CheckpointManager(base_dir="contextcore_data")
                                
                                # Get graph file path
                                if self.graph_registry:
                                    namespace_path = self.graph_registry._get_namespace_path(namespace)
                                    graph_file = str(namespace_path) if namespace_path else f"contextcore_data/namespaces/{namespace}/graph.json"
                                else:
                                    graph_file = f"contextcore_data/namespaces/{namespace}/graph.json"
                                
                                # Create checkpoint with current progress
                                checkpoint_stage_data = {
                                    'chunk': {
                                        'status': 'in_progress',
                                        'chunks_created': chunk_nodes_created,
                                        'chunking_strategy': chunking_strategy,
                                        'chunk_size': chunk_size,
                                        'overlap': overlap,
                                        'document_id': document_id,
                                        'normalized_version': normalized_version,
                                        'doc_node_id': doc_node_id,
                                        'last_processed_page': batch_end,
                                        'total_pages': total_pages,
                                        'batch_num': batch_num,
                                        'total_batches': total_batches
                                    }
                                }
                                
                                checkpoint_id = checkpoint_manager.create_checkpoint(
                                    namespace=namespace,
                                    graph_file=graph_file,
                                    message=f"Chunking batch {batch_num}/{total_batches} - {chunk_nodes_created} chunks created",
                                    stage_data=checkpoint_stage_data
                                )
                                logger.debug(f"[CHUNK] Created checkpoint {checkpoint_id} after batch {batch_num}")
                            except Exception as checkpoint_err:
                                # Don't fail chunking if checkpointing fails
                                logger.warning(f"[CHUNK] Failed to create checkpoint after batch {batch_num}: {checkpoint_err}")
                            
                        except Exception as batch_err:
                            logger.error(f"[CHUNK] Critical error in batch {batch_num}: {batch_err}")
                            import traceback
                            logger.error(f"[CHUNK] Batch error traceback:\n{traceback.format_exc()}")
                            print(f"\n[CHUNK] {'='*70}", flush=True)
                            print(f"[CHUNK] ⚠️  BATCH {batch_num}/{total_batches} FAILED", flush=True)
                            print(f"[CHUNK] Error: {batch_err}", flush=True)
                            print(f"[CHUNK] Continuing with next batch...", flush=True)
                            print(f"[CHUNK] {'='*70}", flush=True)
                            
                            # Try to flush and free memory before continuing
                            # Ensure graph is registered before saving
                            if self.graph_registry and namespace_graph:
                                if namespace not in self.graph_registry.graphs:
                                    self.graph_registry.graphs[namespace] = namespace_graph
                                if not hasattr(namespace_graph, 'name') or namespace_graph.name != namespace:
                                    namespace_graph.name = namespace
                            
                            try:
                                if hasattr(namespace_graph, '_save_to_disk'):
                                    namespace_graph._save_to_disk()
                                    print(f"[CHUNK] Offloaded chunks to disk before continuing", flush=True)
                                gc.collect()
                            except:
                                pass
                            
                            # Continue to next batch instead of crashing
                            continue
                    
                    # If chunks were written incrementally, skip the final graph writing step
                    if chunk_nodes_created > 0:
                        # Chunks already written incrementally, just finalize
                        print(f"[CHUNK] Chunks written incrementally: {chunk_nodes_created} total chunks in graph")
                        # Final flush - ensure all chunks are offloaded
                        # Ensure graph is registered before final save
                        if self.graph_registry and namespace_graph:
                            if namespace not in self.graph_registry.graphs:
                                self.graph_registry.graphs[namespace] = namespace_graph
                            if not hasattr(namespace_graph, 'name') or namespace_graph.name != namespace:
                                namespace_graph.name = namespace
                        
                        try:
                            if hasattr(namespace_graph, '_save_to_disk'):
                                namespace_graph._save_to_disk()
                                print(f"[CHUNK] Final offload: All {chunk_nodes_created} chunks saved to disk", flush=True)
                            gc.collect()
                        except Exception as final_flush_err:
                            logger.warning(f"[CHUNK] Final flush failed: {final_flush_err}")
                            # Try direct save via registry as fallback
                            if self.graph_registry and namespace in self.graph_registry.graphs:
                                try:
                                    self.graph_registry.save_graph(namespace)
                                    print(f"[CHUNK] Final offload via registry fallback successful", flush=True)
                                except Exception as fallback_err:
                                    print(f"[CHUNK] Error: Final offload fallback also failed: {fallback_err}", flush=True)
                    else:
                        # Fallback: if no chunks written yet (shouldn't happen), use batch mode
                        logger.warning("[CHUNK] No chunks written incrementally, using batch mode fallback")
                        # Final deduplication before storing (safety check)
                        seen_chunk_ids = set()
                        deduplicated_chunks = []
                        duplicate_count_final = 0
                        
                        for chunk_props in chunks:
                            chunk_id = chunk_props.get("id") or chunk_props.get("chunk_id")
                            if not chunk_id:
                                continue
                            
                            if chunk_id in seen_chunk_ids:
                                duplicate_count_final += 1
                                continue
                            
                            seen_chunk_ids.add(chunk_id)
                            deduplicated_chunks.append(chunk_props)
                        
                        if duplicate_count_final > 0:
                            logger.warning(f"Final deduplication: Removed {duplicate_count_final} duplicate chunks")
                        
                        chunks = deduplicated_chunks
                        
                        # Create chunk nodes in graph (batch mode for better performance)
                        print(f"[CHUNK] Creating {len(chunks)} unique chunk nodes in graph...")
                        node_creation_start = time.time()
                        
                        batch_size_nodes = 100
                        flush_interval = 10
                        stored_chunk_ids = set()
                        
                        for batch_idx in range(0, len(chunks), batch_size_nodes):
                            batch_end = min(batch_idx + batch_size_nodes, len(chunks))
                            batch_chunks = chunks[batch_idx:batch_end]
                            
                            for chunk_props in batch_chunks:
                                chunk_id = chunk_props.get("id") or chunk_props.get("chunk_id")
                                if not chunk_id or chunk_id in stored_chunk_ids:
                                    continue
                                
                                stored_chunk_ids.add(chunk_id)
                                chunk_node = GraphNode(id=chunk_id, label="Chunk", properties=chunk_props)
                                namespace_graph.add_node(chunk_node, write_through=False)
                                
                                edge_id = str(uuid.uuid4())
                                edge = GraphEdge(id=edge_id, source=doc_node_id, target=chunk_id, label="HAS_CHUNK", properties={})
                                namespace_graph.add_edge(edge)
                                
                                chunk_nodes_created += 1
                            
                            if (batch_idx // batch_size_nodes + 1) % flush_interval == 0:
                                if hasattr(namespace_graph, '_save_to_disk'):
                                    namespace_graph._save_to_disk()
                        
                        if hasattr(namespace_graph, '_save_to_disk'):
                            namespace_graph._save_to_disk()
                        
                        node_creation_time = time.time() - node_creation_start
                        print(f"[CHUNK] Graph node creation complete: {chunk_nodes_created} nodes in {node_creation_time:.2f}s")
                        
                        # Performance checkpoint after graph node creation
                        perf_monitor.log_checkpoint(
                            "Graph nodes created",
                            items_processed=chunk_nodes_created,
                            additional_info={
                                'node_creation_time': node_creation_time
                            }
                        )
            
            # Final flush and cleanup
            print(f"\n[CHUNK] {'='*70}", flush=True)
            print(f"[CHUNK] FINALIZING CHUNKING STAGE", flush=True)
            print(f"[CHUNK] {'='*70}", flush=True)
            
            try:
                flush_start = time.time()
                if hasattr(namespace_graph, '_save_to_disk'):
                    namespace_graph._save_to_disk()
                flush_time = time.time() - flush_start
                print(f"[CHUNK] Final flush to disk: {flush_time:.2f}s", flush=True)
                gc.collect()
            except Exception as final_flush_err:
                logger.warning(f"[CHUNK] Final flush failed: {final_flush_err}")
                print(f"[CHUNK] ⚠️  Final flush failed: {final_flush_err}", flush=True)
            
            # Memory usage summary
            mem_usage = get_memory_usage()
            if mem_usage:
                print(f"[CHUNK] Final memory usage: {mem_usage:.1f}MB", flush=True)
            
            print(f"[CHUNK] {'='*70}", flush=True)
            print(f"[CHUNK] CHUNKING STAGE COMPLETE", flush=True)
            print(f"[CHUNK] {'='*70}", flush=True)
            print(f"[CHUNK] Total chunks created: {chunk_nodes_created:,} chunks", flush=True)
            print(f"[CHUNK] Strategy used: {chunking_strategy}", flush=True)
            print(f"[CHUNK] Chunk size: {chunk_size} characters", flush=True)
            print(f"[CHUNK] Overlap: {overlap} characters", flush=True)
            print(f"[CHUNK] Document: {document_id}", flush=True)
            print(f"[CHUNK] {'='*70}\n", flush=True)
            
            logger.info(f"[CHUNK] Created {chunk_nodes_created} chunks using {chunking_strategy} strategy")
            
            # Store chunk result in stage_data for next stages and checkpointing
            chunk_result = {
                'status': 'completed',
                'chunks_created': chunk_nodes_created,
                'chunking_strategy': chunking_strategy,
                'chunk_size': chunk_size,
                'overlap': overlap,
                'document_id': document_id,
                'normalized_version': normalized_version,
                'doc_node_id': doc_node_id
            }
            stage_data['chunk'] = chunk_result
            
            # End performance monitoring
            perf_summary = perf_monitor.end_monitoring("CHUNK", {
                'chunks_created': chunk_nodes_created,
                'chunking_strategy': chunking_strategy
            })
            chunk_result['performance_summary'] = perf_summary
            
            return chunk_result
            
        except Exception as e:
            logger.error(f"CHUNK stage failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {"status": "failed", "error": str(e)}
    
    def _process_page_batch_streaming(
        self, batch_pages, batch_num, document_id, normalized_version,
        chunking_strategy, chunk_size, overlap, params,
        doc_node_id, namespace_graph, source_pointer_base,
        stored_chunk_ids, write_batch_size, chunk_start_time
    ) -> int:
        """
        Process a batch of pages from streaming parser.
        
        This method handles chunking and immediate persistence for a batch of pages
        loaded from streaming JSON parser. Chunks are written immediately to prevent
        memory accumulation.
        """
        from ..engine.chunk_storage import ChunkStorage
        from ...core.graph_structures import GraphNode, GraphEdge
        from contextsynapse.extraction.id_generator import IDGenerator
        
        chunk_storage = ChunkStorage(namespace=self.active_namespace or "default", base_dir="contextcore_data")
        # Pass page monitor to chunk storage if available
        if hasattr(self, 'page_monitor') and self.page_monitor:
            chunk_storage.page_monitor = self.page_monitor
        
        # Use instance variable instead of global
        if not hasattr(self, '_chunk_nodes_created'):
            self._chunk_nodes_created = 0
        batch_start_time = time.time()
        batch_chunks = 0
        
        print(f"\n[CHUNK] {'='*70}", flush=True)
        print(f"[CHUNK] STREAMING BATCH {batch_num}: {len(batch_pages)} pages", flush=True)
        print(f"[CHUNK] {'='*70}", flush=True)
        
        for page_idx, page in enumerate(batch_pages):
            page_no = page.get('page_no', (batch_num - 1) * len(batch_pages) + page_idx + 1)
            page_text = page.get('flat_text', '')
            page_text_length = len(page_text) if isinstance(page_text, str) else 0
            
            if not page_text or page_text_length == 0:
                print(f"[CHUNK]   Page {page_no}: ⚠️  Skipped (no text content)", flush=True)
                continue
            
            page_start_time = time.time()
            print(f"\n[CHUNK]   ┌─ Page {page_no} ({page_idx+1}/{len(batch_pages)} in batch)", flush=True)
            print(f"[CHUNK]   │  Text length: {page_text_length:,} characters", flush=True)
            
            # Page monitoring hook - before processing
            page_start_metrics = None
            if hasattr(self, 'page_monitor') and self.page_monitor:
                try:
                    page_start_metrics = self.page_monitor.monitor_page_start(page_no)
                except Exception as monitor_err:
                    logger.debug(f"Page monitor error (start): {monitor_err}")
            
            try:
                # Chunk the page
                chunk_start = time.time()
                if chunking_strategy == "semantic":
                    page_chunks = chunk_storage._chunk_semantic(page_text, chunk_size, overlap)
                elif chunking_strategy == "paragraph":
                    page_chunks = chunk_storage._chunk_paragraph(page_text, overlap)
                elif chunking_strategy == "recursive":
                    page_chunks = chunk_storage._chunk_recursive(page_text, chunk_size, overlap)
                elif chunking_strategy == "sliding_window":
                    step_size = params.get('step_size', chunk_size - overlap) if params else (chunk_size - overlap)
                    page_chunks = chunk_storage._chunk_sliding_window(page_text, chunk_size, overlap, step_size=step_size)
                else:
                    # Fallback to fixed
                    page_chunks = chunk_storage._chunk_fixed(page_text, chunk_size, overlap)
                
                chunk_time = time.time() - chunk_start
                num_chunks = len(page_chunks) if page_chunks else 0
                print(f"[CHUNK]   │  ✅ Chunking complete: {num_chunks} chunks in {chunk_time:.2f}s", flush=True)
                
                if not page_chunks:
                    print(f"[CHUNK]   └─ Page {page_no} complete: 0 chunks", flush=True)
                    continue
                
                # Write chunks immediately to graph
                props_start = time.time()
                source_pointer_base['page_no'] = page_no
                
                for chunk_idx, chunk_data in enumerate(page_chunks):
                    try:
                        if not isinstance(chunk_data, (tuple, list)) or len(chunk_data) < 2:
                            continue
                        
                        chunk_text = chunk_data[0] if len(chunk_data) > 0 else ""
                        start = chunk_data[1] if len(chunk_data) > 1 else 0
                        end = chunk_data[2] if len(chunk_data) > 2 else len(chunk_text)
                        
                        if not chunk_text or not chunk_text.strip():
                            continue
                        
                        # Generate chunk ID
                        chunk_id = IDGenerator.generate_chunk_id(
                            document_id=document_id,
                            page_no=page_no,
                            chunk_index=chunk_idx,
                            chunking_strategy=chunking_strategy,
                            strategy_version="v1",
                            namespace=None
                        )
                        
                        if not chunk_id or chunk_id in stored_chunk_ids:
                            continue
                        
                        stored_chunk_ids.add(chunk_id)
                        
                        # Create chunk properties
                        source_pointer = source_pointer_base.copy()
                        source_pointer["start_char"] = start
                        source_pointer["end_char"] = end
                        
                        chunk_props = {
                            "id": chunk_id,
                            "content": chunk_text,
                            "source_pointer": source_pointer,
                            "chunk_index": chunk_idx,
                            "metadata": {
                                "chunk_size": len(chunk_text),
                                "page_no": page_no
                            }
                        }
                        
                        # Write immediately to graph
                        chunk_node = GraphNode(id=chunk_id, label="Chunk", properties=chunk_props)
                        namespace_graph.add_node(chunk_node, write_through=False)
                        
                        edge_id = str(uuid.uuid4())
                        edge = GraphEdge(id=edge_id, source=doc_node_id, target=chunk_id, label="HAS_CHUNK", properties={})
                        namespace_graph.add_edge(edge)
                        
                        self._chunk_nodes_created += 1
                        batch_chunks += 1
                        
                        # Periodic flush
                        if self._chunk_nodes_created % write_batch_size == 0:
                            if hasattr(namespace_graph, '_save_to_disk'):
                                namespace_graph._save_to_disk()
                            gc.collect()
                    
                    except Exception as chunk_err:
                        logger.error(f"[CHUNK] Error processing chunk {chunk_idx} on page {page_no}: {chunk_err}")
                        continue
                
                props_time = time.time() - props_start
                page_time = time.time() - page_start_time
                print(f"[CHUNK]   │  Timing: chunk={chunk_time:.2f}s, props={props_time:.2f}s, total={page_time:.2f}s", flush=True)
                print(f"[CHUNK]   └─ ✅ Page {page_no} complete: {num_chunks} chunks written", flush=True)
                
                # Page monitoring hook - after processing
                if hasattr(self, 'page_monitor') and self.page_monitor and page_start_metrics:
                    try:
                        self.page_monitor.monitor_page_end(page_no, page_start_metrics, chunks_created=num_chunks, processing_time=page_time)
                    except Exception as monitor_err:
                        logger.debug(f"Page monitor error (end): {monitor_err}")
            
            except Exception as page_err:
                logger.error(f"[CHUNK] Error processing page {page_no}: {page_err}")
                print(f"[CHUNK]   └─ ⚠️  Page {page_no} failed: {page_err}", flush=True)
                continue
        
        batch_time = time.time() - batch_start_time
        elapsed_time = time.time() - chunk_start_time
        
        # Flush after batch
        if hasattr(namespace_graph, '_save_to_disk'):
            namespace_graph._save_to_disk()
        gc.collect()
        
        print(f"\n[CHUNK] {'='*70}", flush=True)
        print(f"[CHUNK] STREAMING BATCH {batch_num} COMPLETE", flush=True)
        print(f"[CHUNK] Chunks created: {batch_chunks} chunks from this batch", flush=True)
        print(f"[CHUNK] Total chunks: {self._chunk_nodes_created:,} chunks", flush=True)
        
        return batch_chunks
        print(f"[CHUNK] Batch time: {batch_time:.2f}s", flush=True)
        print(f"[CHUNK] Elapsed time: {elapsed_time:.1f}s ({elapsed_time/60:.1f} minutes)", flush=True)
        print(f"[CHUNK] {'='*70}", flush=True)
    
    def _execute_embed_stage(self, stage, stage_data, namespace, source_collection, target_collection, namespace_graph) -> Dict[str, Any]:
        """Execute EMBED stage - generate embeddings for chunks."""
        try:
            from ...llm.openai_embedding_service import OpenAIEmbeddingService
            
            # Get parameters
            params = stage.get('parameters', {})
            model = params.get('model', 'text-embedding-3-large')
            dimensions = params.get('dimensions', 3072)
            embedding_field = stage.get('store_embedding_in', 'Chunk.embedding')
            
            # Get all Chunk nodes
            all_nodes = namespace_graph.get_all_nodes()
            chunk_nodes = [n for n in all_nodes if n.label == 'Chunk']
            
            if not chunk_nodes:
                logger.warning("[EMBED] No Chunk nodes found. Run CHUNK stage first.")
                return {"status": "completed", "chunks_embedded": 0, "message": "No chunks to embed"}
            
            # Prepare chunk data for parallel processing
            chunk_data = [
                {"id": n.id, "content": n.properties.get('content', '')}
                for n in chunk_nodes
                if n.properties.get('content', '')
            ]
            
            # Use Ray for parallel embedding if available
            use_ray = _get_ray_enabled()
            embeddings = {}
            if use_ray:
                try:
                    from .ray_stage_executors import embed_chunks_parallel
                    embeddings = embed_chunks_parallel(
                        chunks=chunk_data,
                        model=model,
                        dimensions=dimensions,
                        embedding_field=embedding_field,
                        use_ray=True
                    )
                except Exception as e:
                    logger.warning(f"Ray embedding failed, falling back to sequential: {e}")
                    use_ray = False
            
            if not use_ray:
                # Sequential embedding (fallback)
                embedding_service = OpenAIEmbeddingService()
                for chunk in chunk_data:
                    try:
                        embedding = embedding_service.generate_embedding(chunk['content'])
                        embeddings[chunk['id']] = embedding
                    except Exception as e:
                        logger.warning(f"[EMBED] Failed to embed chunk {chunk['id']}: {e}")
            
            # Update all chunk nodes with embeddings
            chunks_embedded = 0
            for chunk_node in chunk_nodes:
                chunk_id = chunk_node.id
                if chunk_id in embeddings:
                    embedding = embeddings[chunk_id]
                    
                    # Store embedding via API (LMDB-safe — no reference mutation)
                    if embedding_field == 'Chunk.embedding':
                        field_name = 'embedding'
                    else:
                        # Parse field path like "Chunk.embedding"
                        field_name = embedding_field.split('.')[-1]
                    embed_update = {field_name: embedding}
                    if hasattr(namespace_graph, 'csr_adapter') and namespace_graph.csr_adapter:
                        namespace_graph.csr_adapter.update_node_properties(chunk_node.id, embed_update)
                    # Also update via high-level API if available
                    if hasattr(namespace_graph, 'update_node'):
                        namespace_graph.update_node(chunk_node.id, embed_update, write_through=True)
                    chunks_embedded += 1
            
            logger.info(f"[EMBED] Generated embeddings for {chunks_embedded} chunks")
            
            # Store embed result in stage_data for checkpointing
            embed_result = {
                'status': 'completed',
                'chunks_embedded': chunks_embedded,
                'model': model,
                'dimensions': dimensions
            }
            stage_data['embed'] = embed_result
            
            return embed_result
            
        except Exception as e:
            logger.error(f"EMBED stage failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {"status": "failed", "error": str(e)}
    
    def _execute_extract_entities_stage(self, stage, stage_data, namespace, source_collection, target_collection, namespace_graph) -> Dict[str, Any]:
        """Execute EXTRACT_ENTITIES stage - extract entities from chunks using LLM."""
        try:
            from ...llm.universal_llm import AIContextDBUniversalLLM
            from ...core.graph_structures import GraphNode, GraphEdge
            import json
            
            # Get parameters
            llm_model = stage.get('using_llm', 'gpt-4')
            prompt = stage.get('prompt', 'Extract all named entities from the text.')
            
            # Get all Chunk nodes
            all_nodes = namespace_graph.get_all_nodes()
            chunk_nodes = [n for n in all_nodes if n.label == 'Chunk']
            
            if not chunk_nodes:
                logger.warning("[EXTRACT_ENTITIES] No Chunk nodes found. Run CHUNK stage first.")
                return {"status": "completed", "entities_extracted": 0, "message": "No chunks to process"}
            
            # Prepare chunk data for parallel processing
            chunk_data = [
                {"id": n.id, "content": n.properties.get('content', '')}
                for n in chunk_nodes
                if n.properties.get('content', '')
            ]
            
            # Use Ray for parallel entity extraction if available
            use_ray = _get_ray_enabled()
            all_entities_by_chunk = {}
            if use_ray:
                try:
                    from .ray_stage_executors import extract_entities_parallel
                    all_entities_by_chunk = extract_entities_parallel(
                        chunks=chunk_data,
                        llm_model=llm_model,
                        prompt=prompt,
                        use_ray=True
                    )
                except Exception as e:
                    logger.warning(f"Ray entity extraction failed, falling back to sequential: {e}")
                    use_ray = False
            
            if not use_ray:
                # Sequential entity extraction (fallback)
                llm_manager = AIContextDBUniversalLLM()
                for chunk in chunk_data:
                    try:
                        full_prompt = f"{prompt}\n\nText:\n{chunk['content'][:2000]}"
                        result = llm_manager.generate(
                            model_name=llm_model,
                            prompt=full_prompt,
                            temperature=0.3,
                            max_tokens=2000
                        )
                        
                        if result.get('success'):
                            response_text = result.get('response', '')
                            try:
                                entities_data = json.loads(response_text)
                                if not isinstance(entities_data, list):
                                    entities_data = [entities_data]
                            except:
                                entities_data = self._parse_entities_from_text(response_text)
                            all_entities_by_chunk[chunk['id']] = entities_data
                    except Exception as e:
                        logger.warning(f"[EXTRACT_ENTITIES] Failed to extract entities from chunk {chunk['id']}: {e}")
            
            # Create entity nodes and links
            entities_created = 0
            for chunk_node in chunk_nodes:
                chunk_id = chunk_node.id
                if chunk_id not in all_entities_by_chunk:
                    continue
                
                entities_data = all_entities_by_chunk[chunk_id]
                
                # Create entity nodes
                try:
                    for entity_data in entities_data:
                        if not isinstance(entity_data, dict):
                            continue
                        
                        entity_name = entity_data.get('name', entity_data.get('entity', ''))
                        entity_type = entity_data.get('type', entity_data.get('category', 'Entity'))
                        
                        if not entity_name:
                            continue
                        
                        entity_id = f"entity_{entity_name.lower().replace(' ', '_')}"
                        entity_node = GraphNode(
                            id=entity_id,
                            label="Entity",
                            properties={
                                'name': entity_name,
                                'type': entity_type,
                                'description': entity_data.get('description', ''),
                                'context': entity_data.get('context', '')
                            }
                        )
                        
                        # Check if entity already exists
                        existing = namespace_graph.get_node(entity_id)
                        if not existing:
                            namespace_graph.add_node(entity_node, write_through=True)
                            entities_created += 1
                        
                        # Link entity to chunk
                        edge_id = str(uuid.uuid4())
                        edge = GraphEdge(
                            id=edge_id,
                            source=chunk_node.id,
                            target=entity_id,
                            label="MENTIONS",
                            properties={}
                        )
                        namespace_graph.add_edge(edge)
                except Exception as e:
                    logger.warning(f"[EXTRACT_ENTITIES] Failed to process entities for chunk {chunk_node.id}: {e}")
                    continue
            
            logger.info(f"[EXTRACT_ENTITIES] Extracted {entities_created} entities")
            
            return {
                'status': 'completed',
                'entities_extracted': entities_created,
                'model': llm_model
            }
            
        except Exception as e:
            logger.error(f"EXTRACT_ENTITIES stage failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {"status": "failed", "error": str(e)}
    
    def _parse_entities_from_text(self, text: str) -> List[Dict[str, Any]]:
        """Parse entities from text response (fallback method)."""
        entities = []
        # Simple parsing - look for patterns like "Entity: Name (Type)"
        import re
        patterns = [
            r'(\w+):\s*([^(]+)\s*\(([^)]+)\)',
            r'Name:\s*([^,]+),\s*Type:\s*([^,]+)',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                if len(match) >= 2:
                    entities.append({
                        'name': match[1] if len(match) > 1 else match[0],
                        'type': match[2] if len(match) > 2 else 'Entity'
                    })
        return entities
    
    def _execute_select(self, ast, namespace: str, namespace_graph) -> Dict[str, Any]:
        """Execute SELECT query (simplified)."""
        try:
            # Extract parameters from AST node (AIQLNode object has 'parameters' attribute)
            if hasattr(ast, 'parameters'):
                ast_params = ast.parameters
            elif isinstance(ast, dict):
                ast_params = ast
            else:
                ast_params = {}
            
            # Get node type from FROM clause or node_type parameter
            node_type = None
            if isinstance(ast_params, dict):
                # First check if node_type is directly in params
                node_type = ast_params.get('node_type')
                if not node_type:
                    # Check from clause (can be string or dict)
                    from_clause = ast_params.get('from')
                    if isinstance(from_clause, str):
                        node_type = from_clause
                    elif isinstance(from_clause, dict):
                        node_type = from_clause.get('node_type')
            else:
                # Fallback: try to get from AST data structure if it's not a dict
                if hasattr(ast, 'data') and isinstance(ast.data, dict):
                    node_type = ast.data.get('node_type')
                    if not node_type:
                        from_clause = ast.data.get('from')
                        if isinstance(from_clause, str):
                            node_type = from_clause
                        elif isinstance(from_clause, dict):
                            node_type = from_clause.get('node_type')
            
            if not node_type:
                # SELECT * without FROM — return ALL nodes in namespace
                all_nodes = namespace_graph.get_all_nodes()
                formatted = [self._format_node_for_display(n) for n in all_nodes]
                return self._wrap_result(
                    nodes=formatted,
                    data={"query_type": "SELECT", "node_type": "*"},
                    success=True,
                )

            # Check for temporal query (AT TIMESTAMP or AS OF)
            temporal_timestamp = None
            if isinstance(ast_params, dict):
                # Check for as_of or for_system_time (temporal clauses)
                as_of = ast_params.get('as_of')
                for_system_time = ast_params.get('for_system_time')
                at_timestamp = ast_params.get('at_timestamp')  # Support AT TIMESTAMP syntax
                
                if as_of:
                    # Extract timestamp from as_of clause
                    if isinstance(as_of, dict):
                        if as_of.get('type') == 'timestamp':
                            temporal_timestamp = as_of.get('value')
                        else:
                            temporal_timestamp = as_of.get('timestamp') or as_of.get('value')
                    else:
                        temporal_timestamp = as_of
                elif for_system_time:
                    temporal_timestamp = for_system_time.get('timestamp') if isinstance(for_system_time, dict) else for_system_time
                elif at_timestamp:
                    # Support AT TIMESTAMP syntax
                    temporal_timestamp = at_timestamp.get('value') if isinstance(at_timestamp, dict) else at_timestamp
            
            # Fallback: Check if this is a TEMPORAL_QUERY that was mis-parsed as SELECT
            # If the query contains "AT TIMESTAMP" but temporal_timestamp is None, try to extract it
            if temporal_timestamp is None and hasattr(self, '_last_query'):
                import re
                # Try to extract AT TIMESTAMP from query string
                match = re.search(r'AT\s+TIMESTAMP\s+["\']([^"\']+)["\']', self._last_query, re.IGNORECASE)
                if match:
                    temporal_timestamp = match.group(1)
                    logger.debug(f"Extracted temporal timestamp from query string: {temporal_timestamp}")
            
            # If temporal query, get nodes from temporal storage
            if temporal_timestamp and namespace_graph.temporal_enabled and namespace_graph.temporal_storage:
                from datetime import datetime
                # Parse timestamp string to datetime
                if isinstance(temporal_timestamp, str):
                    try:
                        # Try ISO format first
                        temporal_timestamp = datetime.fromisoformat(temporal_timestamp.replace('Z', '+00:00'))
                    except:
                        try:
                            # Try common formats
                            from dateutil import parser
                            temporal_timestamp = parser.parse(temporal_timestamp)
                        except:
                            logger.warning(f"Could not parse timestamp: {temporal_timestamp}")
                            temporal_timestamp = None
                
                if temporal_timestamp:
                    # Query temporal storage for entities at this timestamp
                    # Note: get_entities_at filters by entity_type='node', so we need to filter by node type in properties or label
                    versions = namespace_graph.temporal_storage.get_entities_at(temporal_timestamp, entity_type='node')
                    # Convert versions to GraphNode objects and filter by node type
                    from ...core.graph_structures import GraphNode
                    filtered_nodes = []
                    for version in versions:
                        # Check if this version's node matches the requested node type
                        # We need to check the actual node's label if it exists, or infer from properties
                        node_label = node_type  # Default to requested type
                        if version.entity_id in namespace_graph.node_index:
                            actual_node = namespace_graph.node_index[version.entity_id]
                            node_label = actual_node.label
                            # Only include if node type matches
                            if actual_node.label != node_type:
                                continue
                        
                        # Create node from version
                        node = GraphNode(
                            id=version.entity_id,
                            label=node_label,
                            properties=version.properties
                        )
                        filtered_nodes.append(node)
                else:
                    # Fallback to current state — use label index
                    filtered_nodes = list(namespace_graph.get_all_nodes(label=node_type))
            else:
                # Regular query — use optimizer to choose scan strategy
                try:
                    from ..compiler.statistics import GraphStatisticsCollector, QueryPlanSelector
                    _stats = GraphStatisticsCollector(namespace_graph).collect()
                    _where = ast_params.get('where') if isinstance(ast_params, dict) else None
                    _plan = QueryPlanSelector().select_plan(_stats, label=node_type, where_props=_where if isinstance(_where, dict) else None)
                    logger.debug("[OPTIMIZER] %s: %s (cost=%.3f, rows=%d)",
                                 node_type, _plan.scan_type, _plan.cost.estimated_cost, _plan.cost.estimated_rows)
                except Exception:
                    pass
                # Execute: use label index for O(k) instead of O(n)
                filtered_nodes = list(namespace_graph.get_all_nodes(label=node_type))
            
            # Apply WHERE clause if present
            where_clause = ast_params.get('where') if isinstance(ast_params, dict) else None
            if where_clause:
                filtered_nodes = self._apply_where_clause(filtered_nodes, where_clause)
            
            # Apply OFFSET and LIMIT if present
            offset_val = ast_params.get('offset') if isinstance(ast_params, dict) else None
            if offset_val is not None:
                try:
                    offset_val = int(offset_val)
                except (ValueError, TypeError):
                    offset_val = 0
                if offset_val > 0:
                    filtered_nodes = filtered_nodes[offset_val:]

            limit = ast_params.get('limit') if isinstance(ast_params, dict) else None
            if limit is not None:
                # Handle both int and string representations
                if isinstance(limit, str) and limit.isdigit():
                    limit = int(limit)
                elif isinstance(limit, (int, float)):
                    limit = int(limit)
                else:
                    limit = None
                if limit is not None and limit > 0:
                    filtered_nodes = filtered_nodes[:limit]
            
            
            # Check for GROUP BY clause
            group_by_fields = ast_params.get('group_by', []) if isinstance(ast_params, dict) else []
            
            # Handle aggregation functions
            # Parser puts aggregation in select_items: [{'type': 'aggregation', 'function': 'COUNT', 'argument': '*'}]
            select_items = ast_params.get('select_items', []) if isinstance(ast_params, dict) else []
            projections = ast_params.get('projections', []) if isinstance(ast_params, dict) else []
            
            # Extract aggregation functions and fields from select_items or projections
            agg_functions = []  # List of (function, field, alias) tuples
            select_fields = []  # Fields to select (for GROUP BY)
            
            # Parse select_items or projections
            items_to_parse = select_items if select_items else projections
            for item in items_to_parse:
                if isinstance(item, dict):
                    if item.get('type') == 'aggregation':
                        func = item.get('function', '').upper()
                        arg = item.get('argument') or item.get('field')
                        if arg == '*':
                            arg = None
                        alias = item.get('alias')
                        agg_functions.append((func, arg, alias))
                    elif item.get('type') == 'field':
                        field = item.get('field')
                        alias = item.get('alias')
                        if field:
                            select_fields.append((field, alias))
                    # Also handle direct field names (not wrapped in type dict)
                    elif 'field' in item:
                        field = item.get('field')
                        alias = item.get('alias')
                        if field:
                            select_fields.append((field, alias))
                elif isinstance(item, str):
                    # Direct field name as string
                    select_fields.append((item, None))
            
            # Fallback: check select clause for single aggregation
            if not agg_functions and not select_fields:
                select_clause = ast_params.get('select', {}) if isinstance(ast_params, dict) else (ast.data.get('select', {}) if hasattr(ast, 'data') else {})
                if isinstance(select_clause, dict):
                    func = select_clause.get('function')
                    field = select_clause.get('field')
                    if func:
                        agg_functions.append((func.upper(), field, None))
                elif isinstance(ast_params, dict):
                    # Check for COUNT(*), SUM(field), AVG(field), etc.
                    if 'COUNT' in str(ast_params) or ast_params.get('count'):
                        agg_functions.append(('COUNT', None, None))
                    elif 'SUM' in str(ast_params) or ast_params.get('sum'):
                        agg_functions.append(('SUM', ast_params.get('sum_field'), None))
                    elif 'AVG' in str(ast_params) or ast_params.get('avg'):
                        agg_functions.append(('AVG', ast_params.get('avg_field'), None))
                    elif 'MAX' in str(ast_params) or ast_params.get('max'):
                        agg_functions.append(('MAX', ast_params.get('max_field'), None))
                    elif 'MIN' in str(ast_params) or ast_params.get('min'):
                        agg_functions.append(('MIN', ast_params.get('min_field'), None))
            
            # If GROUP BY is present, group nodes and apply aggregations per group
            if group_by_fields:
                result = self._execute_group_by(
                    filtered_nodes, group_by_fields, agg_functions, select_fields
                )
            elif agg_functions:
                # Global aggregation (no GROUP BY)
                result = self._execute_aggregation(filtered_nodes, agg_functions)
            else:
                # Regular SELECT - return node data
                if select_fields:
                    # Select specific fields
                    result = []
                    for n in filtered_nodes:
                        row = {}
                        for field, alias in select_fields:
                            key = alias or field
                            row[key] = n.properties.get(field, None)
                        result.append(row)
                else:
                    # Select all - use uuid from properties, not internal id
                    result = [self._format_node_result(n) for n in filtered_nodes]
            
            # Return in consistent format (nodes array inside wrapped result)
            return self._wrap_result(
                nodes=result,
                data={"query_type": "SELECT", "node_type": node_type},
                success=True
            )
                
        except Exception as e:
            logger.error(f"SELECT query failed: {e}")
            return self._wrap_result(data={"success": False, "error": str(e)}, success=False)
        """Execute GROUP BY aggregation."""
        from collections import defaultdict
        
        # Group nodes by the specified fields
        groups = defaultdict(list)
        for node in nodes:
            # Create group key from group_by_fields
            group_key = tuple(
                node.properties.get(field, None) for field in group_by_fields
            )
            groups[group_key].append(node)
        
        # Apply aggregations per group
        result = []
        for group_key, group_nodes in groups.items():
            row = {}
            
            # Add group_by field values
            for i, field in enumerate(group_by_fields):
                row[field] = group_key[i]
            
            # Add select_fields (non-aggregated fields)
            for field, alias in select_fields:
                if field not in group_by_fields:
                    # For non-grouped fields, take first value (or could use ANY/MAX/MIN)
                    if group_nodes:
                        key = alias or field
                        row[key] = group_nodes[0].properties.get(field, None)
            
            # Apply aggregation functions
            for func, field, alias in agg_functions:
                agg_result = self._apply_aggregation_function(group_nodes, func, field)
                key = alias or (f"{func}({field or '*'})")
                row[key] = agg_result
            
            result.append(row)
        
        return result
    
    def _execute_aggregation(self, nodes: List[GraphNode], agg_functions: List[tuple]) -> List[Dict[str, Any]]:
        """Execute global aggregation (no GROUP BY)."""
        result = {}
        
        for func, field, alias in agg_functions:
            agg_result = self._apply_aggregation_function(nodes, func, field)
            key = alias or (f"{func}({field or '*'})")
            result[key] = agg_result
        
        return [result]
    
    def _apply_aggregation_function(self, nodes: List[GraphNode], func: str, field: str = None) -> Any:
        """Apply a single aggregation function to a list of nodes."""
        if func == 'COUNT':
            # Deduplicate nodes by ID before counting
            seen = set()
            unique_nodes = []
            for n in nodes:
                if n.id not in seen:
                    seen.add(n.id)
                    unique_nodes.append(n)
            return len(unique_nodes)
        elif func == 'SUM' and field:
            values = [n.properties.get(field, 0) for n in nodes 
                     if isinstance(n.properties.get(field, None), (int, float))]
            return sum(values) if values else 0
        elif func == 'AVG' and field:
            values = [n.properties.get(field, 0) for n in nodes 
                     if isinstance(n.properties.get(field, None), (int, float))]
            return sum(values) / len(values) if values else 0
        elif func == 'MAX' and field:
            values = [n.properties.get(field) for n in nodes if field in n.properties]
            return max(values) if values else None
        elif func == 'MIN' and field:
            values = [n.properties.get(field) for n in nodes if field in n.properties]
            return min(values) if values else None
        else:
            return None
    
    def _format_node_result(self, node: GraphNode) -> Dict[str, Any]:
        """Format a node for query results - uses node.id (UUID) as the identifier."""
        result = {"id": node.id, "label": node.label, "uuid": node.id}
        for key, value in node.properties.items():
            if key != 'uuid':
                result[key] = value
        return result
    
    def _format_edge_result(self, edge: GraphEdge) -> Dict[str, Any]:
        """Format an edge for query results - uses edge.id (UUID) as the identifier."""
        result = {
            "id": edge.id,
            "label": edge.label,
            "uuid": edge.id,
            "source": self._resolve_node_display_name(edge.source),
            "target": self._resolve_node_display_name(edge.target),
            "source_id": edge.source,
            "target_id": edge.target
        }
        for key, value in edge.properties.items():
            if key != 'uuid':
                result[key] = value
        return result
    
    def _apply_where_clause(self, nodes: List[GraphNode], where_clause: Any) -> List[GraphNode]:
        """Apply WHERE clause conditions to filter nodes.

        Uses property indexes for O(1) equality lookups when available,
        falling back to O(n) scan otherwise.
        """
        if not where_clause:
            return nodes

        # Fast path: simple equality condition with a property index
        if isinstance(where_clause, dict) and not where_clause.get('logical_op'):
            field = where_clause.get('field') or where_clause.get('property') or where_clause.get('left') or where_clause.get('lhs')
            operator = where_clause.get('operator', '=') or where_clause.get('op', '=')
            value = where_clause.get('value') or where_clause.get('right') or where_clause.get('rhs')

            if field and operator == '=' and value is not None:
                graph = self.contextcore
                if graph and hasattr(graph, 'filter_by_property'):
                    # Normalize value
                    if isinstance(value, str):
                        value = value.strip("'\"")
                    indexed_ids = graph.filter_by_property(field, value)
                    if indexed_ids:
                        id_set = set(indexed_ids)
                        return [n for n in nodes if n.id in id_set]

        # Fallback: full scan
        filtered = []
        for node in nodes:
            if self._evaluate_condition(node, where_clause):
                filtered.append(node)
        return filtered
    
    def _apply_domain_filter(self, nodes: List[GraphNode], domain: str) -> List[GraphNode]:
        """Filter nodes by domain."""
        if not domain:
            return nodes
        
        filtered = []
        for node in nodes:
            node_domain = node.properties.get('domain')
            if node_domain == domain:
                filtered.append(node)
        return filtered
    
    def _evaluate_condition(self, node: GraphNode, condition: Any) -> bool:
        """Evaluate a condition against a node."""
        if isinstance(condition, dict):
            # Handle condition dict: {field: value, operator: '=', ...}
            # Also check for different formats from parser
            field = condition.get('field') or condition.get('property') or condition.get('left') or condition.get('lhs')
            operator = condition.get('operator', '=') or condition.get('op', '=')
            value = condition.get('value') or condition.get('right') or condition.get('rhs')
            
            if field:
                # Support domain-based filtering: WHERE domain = 'finance'
                if field == 'domain' or field == 'schema' or field == 'namespace':
                    node_value = node.properties.get(field) or node.properties.get('_domain') or node.properties.get('_schema')
                    if node_value is None:
                        # Check if node has domain in label or properties
                        node_value = getattr(node, 'domain', None)
                else:
                    node_value = node.properties.get(field)
                    # Fallback: check node attributes (e.g. node.name) if not in properties
                    if node_value is None and hasattr(node, field):
                        node_value = getattr(node, field)

                # Handle string comparisons
                # Normalize string values (remove quotes if present)
                if isinstance(value, str):
                    # Remove surrounding quotes if present
                    value = value.strip("'\"")
                if isinstance(node_value, str):
                    node_value = str(node_value).strip("'\"")
                
                if operator == '=' or operator == '==' or operator == 'EQUALS':
                    return str(node_value) == str(value)
                elif operator == '!=' or operator == '<>' or operator == 'NOT_EQUALS':
                    return node_value != value
                elif operator == '>' or operator == 'GT':
                    if isinstance(node_value, (int, float)) and isinstance(value, (int, float)):
                        return node_value > value
                    return False
                elif operator == '>=' or operator == 'GTE':
                    if isinstance(node_value, (int, float)) and isinstance(value, (int, float)):
                        return node_value >= value
                    return False
                elif operator == '<' or operator == 'LT':
                    if isinstance(node_value, (int, float)) and isinstance(value, (int, float)):
                        return node_value < value
                    return False
                elif operator == '<=' or operator == 'LTE':
                    if isinstance(node_value, (int, float)) and isinstance(value, (int, float)):
                        return node_value <= value
                    return False
        elif isinstance(condition, list):
            # Handle AND/OR conditions
            # For now, treat as AND
            return all(self._evaluate_condition(node, c) for c in condition)
        elif hasattr(condition, '__dict__'):
            # Handle object with attributes
            return self._evaluate_condition(node, condition.__dict__)
        
        return True
    
    def _execute_create_node(self, ast: Dict[str, Any], namespace: str, namespace_graph) -> Dict[str, Any]:
        """Execute CREATE NODE query with domain support."""
        """Execute CREATE NODE query."""
        try:
            import uuid
            
            node_type = ast.get('node_type')
            properties = ast.get('properties', {})
            alias = ast.get('alias')
            
            if not node_type:
                return {"success": False, "error": "Node type not specified"}
            
            # Extract name from properties or AST and clean quotes
            name = ast.get('name') or (properties.get('name') if isinstance(properties, dict) else None)
            if isinstance(name, str):
                name = name.strip('"').strip("'")
            
            # Clean string values in properties (remove quotes)
            if isinstance(properties, dict):
                cleaned_properties = {}
                for key, value in properties.items():
                    if isinstance(value, str):
                        # Strip quotes from string values
                        cleaned_value = value.strip('"').strip("'")
                        cleaned_properties[key] = cleaned_value
                    else:
                        cleaned_properties[key] = value
                properties = cleaned_properties
                # Update name from cleaned properties if not already set
                if not name and 'name' in properties:
                    name = properties['name']
            
            # Extract domain from properties or AST (for domain-based filtering)
            domain = ast.get('domain') or (properties.get('domain') if isinstance(properties, dict) else None)
            if domain and isinstance(properties, dict):
                properties['domain'] = domain
                properties['_domain'] = domain  # Also store as _domain for filtering
            
            # Check for duplicate: same type + name should not be created twice
            if name and namespace_graph:
                existing_nodes = namespace_graph.get_all_nodes(label=node_type)
                for existing in existing_nodes:
                    existing_name = existing.name or (existing.properties.get('name') if existing.properties else None)
                    if existing_name and existing_name == name:
                        return self._wrap_result(
                            data={"node_type": node_type, "name": name},
                            success=False,
                            message=f"Node {node_type} with name '{name}' already exists (id: {existing.id})"
                        )

            # Generate UUID - this will be the single identifier (node.id = uuid)
            node_uuid = str(uuid.uuid4())
            
            # Don't store UUID in properties - node.id is the single source of truth
            # Remove uuid from properties if user provided it (to avoid duplication)
            if isinstance(properties, dict):
                properties.pop('uuid', None)
            
            # Create GraphNode - use UUID as the id (single identifier)
            node = GraphNode(
                id=node_uuid,  # Use UUID as the id - single identifier
                label=node_type,
                properties=properties.copy() if properties else {},
                name=name,
                uuid=node_uuid,  # Keep for backward compatibility during transition
                alias=alias
            )
            
            # Add to graph
            namespace_graph.add_node(node, alias=alias, write_through=True)
            
            # Create version for time travel (if enabled)
            if namespace_graph.temporal_enabled and namespace_graph.temporal_storage:
                try:
                    from datetime import datetime
                    namespace_graph.temporal_storage.create_version(
                        entity_id=node.id,
                        entity_type='node',
                        properties=node.properties.copy(),
                        operation='CREATE',
                        timestamp=datetime.now()
                    )
                except Exception as e:
                    logger.warning(f"[TIME_TRAVEL] Failed to create version for new node {node.id}: {e}")
            
            # Auto-persist to disk
            if self.graph_registry:
                try:
                    self.graph_registry.save_graph(namespace, create_checkpoint=False)
                except Exception as save_err:
                    logger.debug(f"Auto-save after CREATE NODE: {save_err}")

            # Return in standard format with the created node
            return self._wrap_result(
                nodes=[self._format_node_for_display(node)],
                data={
                    "uuid": node_uuid,
                    "node_type": node_type,
                    "name": name,
                    "alias": alias,
                    "properties": properties
                },
                success=True,
                message=f"Node {node_type} created successfully"
            )
        except Exception as e:
            logger.error(f"CREATE NODE failed: {e}")
            return self._wrap_result(data={"success": False, "error": str(e)}, success=False)

    def _execute_update_node(self, ast: Dict[str, Any], namespace: str, namespace_graph) -> Dict[str, Any]:
        """Execute UPDATE NODE query with version creation for time travel."""
        try:
            node_type = ast.get('node_type')
            update_props = ast.get('set', {}) or ast.get('properties', {})
            where_clause = ast.get('where')
            
            if not node_type:
                return {"success": False, "error": "Node type not specified"}
            
            # Get all nodes of this type
            all_nodes = namespace_graph.get_all_nodes()
            matching_nodes = [n for n in all_nodes if n.label == node_type]
            
            # Apply WHERE clause
            if where_clause:
                matching_nodes = self._apply_where_clause(matching_nodes, where_clause)
            
            # Update nodes
            updated_count = 0
            for node in matching_nodes:
                # Build update dict, then apply via API (LMDB-safe — no reference mutation)
                previous_props = node.properties.copy()
                final_props = dict(node.properties)
                final_props.update(update_props)
                from datetime import datetime as _dt, timezone as _tz
                from contextsynapse.core.write_context import get_write_context
                final_props['_updated_at'] = _dt.now(_tz.utc).isoformat()
                _wctx = get_write_context()
                if _wctx and _wctx.agent_id:
                    final_props['_updated_by'] = _wctx.agent_id
                # Write through adapter API (works for both CSR and LMDB)
                if hasattr(namespace_graph, 'csr_adapter') and namespace_graph.csr_adapter:
                    namespace_graph.csr_adapter.update_node_properties(node.id, final_props)
                # Also update compatibility dicts if they exist
                if hasattr(namespace_graph, 'node_properties'):
                    namespace_graph.node_properties[node.id] = final_props
                if hasattr(namespace_graph, 'node_index') and node.id in namespace_graph.node_index:
                    namespace_graph.node_index[node.id].properties = final_props
                
                # Create version after update (for time travel) - only one version per update
                if namespace_graph.temporal_enabled and namespace_graph.temporal_storage:
                    try:
                        from datetime import datetime
                        namespace_graph.temporal_storage.create_version(
                            entity_id=node.id,
                            entity_type='node',
                            properties=final_props.copy(),
                            operation='UPDATE',
                            timestamp=datetime.now()
                        )
                    except Exception as e:
                        logger.warning(f"[TIME_TRAVEL] Failed to create version after update for node {node.id}: {e}")

                updated_count += 1

                # Re-index into LMDB so updated content is immediately searchable
                try:
                    from ...search.lmdb_index import lmdb_reindex_node
                    lmdb_reindex_node(namespace, node.id, node_type, final_props)
                except Exception:
                    pass

            # Auto-persist to disk
            if updated_count > 0 and self.graph_registry:
                try:
                    self.graph_registry.save_graph(namespace, create_checkpoint=False)
                except Exception:
                    pass

            return {
                "success": True,
                "updated_count": updated_count,
                "node_type": node_type
            }
        except Exception as e:
            logger.error(f"UPDATE NODE failed: {e}")
            return {"success": False, "error": str(e)}

    def _execute_delete_node(self, ast: Dict[str, Any], namespace: str, namespace_graph) -> Dict[str, Any]:
        """Execute DELETE NODE query."""
        try:
            node_type = ast.get('node_type')
            where_clause = ast.get('where')
            
            if not node_type:
                return {"success": False, "error": "Node type not specified"}
            
            # Get all nodes of this type
            all_nodes = namespace_graph.get_all_nodes()
            matching_nodes = [n for n in all_nodes if n.label == node_type]
            
            # Apply WHERE clause
            if where_clause:
                matching_nodes = self._apply_where_clause(matching_nodes, where_clause)
            
            # Delete nodes
            deleted_count = 0
            for node in matching_nodes:
                try:
                    # Remove from graph via the top-level remove_node (handles all storage backends)
                    if hasattr(namespace_graph, 'remove_node'):
                        namespace_graph.remove_node(node.id)
                    else:
                        if hasattr(namespace_graph, 'node_index'):
                            namespace_graph.node_index.pop(node.id, None)
                        if hasattr(namespace_graph, 'node_properties'):
                            namespace_graph.node_properties.pop(node.id, None)
                    deleted_count += 1

                    # Clean up LMDB search index
                    try:
                        from ...search.lmdb_index import lmdb_delete_node
                        lmdb_delete_node(namespace, node.id)
                    except Exception:
                        pass
                except Exception as e:
                    logger.warning(f"Failed to delete node {node.id}: {e}")

            # Auto-persist to disk
            if deleted_count > 0 and self.graph_registry:
                try:
                    self.graph_registry.save_graph(namespace, create_checkpoint=False)
                except Exception:
                    pass

            return {
                "success": True,
                "deleted_count": deleted_count,
                "node_type": node_type
            }
        except Exception as e:
            logger.error(f"DELETE NODE failed: {e}")
            return {"success": False, "error": str(e)}

    def _execute_create_edge(self, ast: Dict[str, Any], namespace: str, namespace_graph) -> Dict[str, Any]:
        """Execute CREATE EDGE query."""
        try:
            import uuid
            
            logger.debug(f"CREATE EDGE ast keys: {list(ast.keys()) if isinstance(ast, dict) else 'not a dict'}")
            logger.debug(f"CREATE EDGE ast: {ast}")
            
            edge_type = ast.get('edge_type') or ast.get('label') or 'EDGE'
            properties = ast.get('properties', {})
            
            # Check for different parameter names from parser
            source_node = ast.get('source_node') or ast.get('from') or ast.get('from_node_type')
            target_node = ast.get('target_node') or ast.get('to') or ast.get('to_node_type')
            from_node_id = ast.get('from_node_id') or ast.get('source_node_id')
            to_node_id = ast.get('to_node_id') or ast.get('target_node_id')

            # Also check for SRC/DEST syntax and source_type/target_type from parser
            if not source_node:
                source_node = ast.get('src_node') or ast.get('source_type')
            if not target_node:
                target_node = ast.get('dest_node') or ast.get('target_type')

            # Extract WHERE conditions for SRC/DEST (from parser's node_selector)
            source_where = ast.get('source_where')
            target_where = ast.get('target_where')

            # source_node and target_node from parser are node type identifiers (e.g., "Person", "Company")
            # BUT they can also be UUID strings when FROM "uuid" TO "uuid" syntax is used
            # Detect UUID-like strings and treat them as node IDs instead of node types
            def _looks_like_uuid(val):
                """Check if a value looks like a UUID (contains hyphens or is 32+ hex chars)."""
                if not val or not isinstance(val, str):
                    return False
                # Standard UUID format: 8-4-4-4-12
                if len(val) == 36 and val.count('-') == 4:
                    return True
                # Hex string without hyphens
                if len(val) >= 32:
                    try:
                        int(val.replace('-', ''), 16)
                        return True
                    except ValueError:
                        pass
                return False

            # If source_node/target_node look like UUIDs, promote them to node IDs
            if _looks_like_uuid(source_node) and not from_node_id:
                from_node_id = source_node
                source_node = None
            if _looks_like_uuid(target_node) and not to_node_id:
                to_node_id = target_node
                target_node = None

            from_node_type = source_node
            to_node_type = target_node

            # If node IDs are provided, use them directly
            if from_node_id and to_node_id:
                from_node = namespace_graph.get_node(from_node_id)
                to_node = namespace_graph.get_node(to_node_id)
                if not from_node or not to_node:
                    return {"success": False, "error": f"Source or target node not found"}
                
                # Generate UUID - this will be the single identifier (edge.id = uuid)
                edge_uuid = str(uuid.uuid4())
                
                # Extract name and alias from AST or properties and clean quotes
                edge_name = ast.get('name') or (properties.get('name') if isinstance(properties, dict) else None)
                if isinstance(edge_name, str):
                    edge_name = edge_name.strip('"').strip("'")
                edge_alias = ast.get('alias')
                
                # Clean string values in properties (remove quotes)
                if isinstance(properties, dict):
                    cleaned_properties = {}
                    for key, value in properties.items():
                        if isinstance(value, str):
                            # Strip quotes from string values
                            cleaned_value = value.strip('"').strip("'")
                            cleaned_properties[key] = cleaned_value
                        else:
                            cleaned_properties[key] = value
                    properties = cleaned_properties
                    # Update name from cleaned properties if not already set
                    if not edge_name and 'name' in properties:
                        edge_name = properties['name']
                
                # Don't store UUID in properties - edge.id is the single source of truth
                # Remove uuid from properties if user provided it (to avoid duplication)
                if isinstance(properties, dict):
                    properties.pop('uuid', None)
                
                edge = GraphEdge(
                    id=edge_uuid,  # Use UUID as the id - single identifier
                    source=from_node.id,  # from_node.id is now the UUID
                    target=to_node.id,    # to_node.id is now the UUID
                    label=edge_type or "EDGE",
                    properties=properties.copy() if properties else {},
                    name=edge_name,
                    uuid=edge_uuid,  # Keep for backward compatibility during transition
                    alias=edge_alias
                )
                
                # Add edge to graph (will use direct addition if buffer disabled)
                namespace_graph.add_edge(edge)
                return self._wrap_result(
                    edges=[self._format_edge_for_display(edge)],
                    data={
                        "uuid": edge_uuid,
                        "edge_type": edge_type or "EDGE",
                        "name": edge_name,
                        "alias": edge_alias,
                        "properties": properties
                    },
                    success=True,
                    message=f"Edge {edge_type or 'EDGE'} created successfully"
                )
            
            # Otherwise, use node types to find nodes
            if not from_node_type or not to_node_type:
                return self._wrap_result(
                    success=False,
                    message="Edge source and target must be specified (use SRC/DEST or FROM/TO)"
                )

            # Get nodes of source and target types
            all_nodes = namespace_graph.get_all_nodes()
            from_nodes = [n for n in all_nodes if n.label == from_node_type]
            to_nodes = [n for n in all_nodes if n.label == to_node_type]
            # Apply WHERE conditions to narrow down source/target nodes
            if source_where and from_nodes:
                logger.debug(f"CREATE EDGE: Filtering {len(from_nodes)} {from_node_type} nodes with WHERE: {source_where}")
                from_nodes = self._apply_where_clause(from_nodes, source_where)
                logger.debug(f"CREATE EDGE: {len(from_nodes)} source nodes after WHERE filter")
            if target_where and to_nodes:
                logger.debug(f"CREATE EDGE: Filtering {len(to_nodes)} {to_node_type} nodes with WHERE: {target_where}")
                to_nodes = self._apply_where_clause(to_nodes, target_where)
                logger.debug(f"CREATE EDGE: {len(to_nodes)} target nodes after WHERE filter")

            if not from_nodes:
                all_of_type = [n for n in namespace_graph.get_all_nodes() if n.label == from_node_type]
                available_names = [n.name or n.properties.get('name', n.id) for n in all_of_type]
                where_desc = f" matching WHERE {source_where}" if source_where else ""
                if not all_of_type:
                    return self._wrap_result(
                        success=False,
                        message=f"No {from_node_type} nodes exist in the graph. Create them first with CREATE NODE {from_node_type} {{name: \"...\"}}"
                    )
                return self._wrap_result(
                    success=False,
                    message=f"No {from_node_type} node found{where_desc}. Available {from_node_type} nodes: {', '.join(str(n) for n in available_names[:10])}"
                )
            if not to_nodes:
                all_of_type = [n for n in namespace_graph.get_all_nodes() if n.label == to_node_type]
                available_names = [n.name or n.properties.get('name', n.id) for n in all_of_type]
                where_desc = f" matching WHERE {target_where}" if target_where else ""
                if not all_of_type:
                    return self._wrap_result(
                        success=False,
                        message=f"No {to_node_type} nodes exist in the graph. Create them first with CREATE NODE {to_node_type} {{name: \"...\"}}"
                    )
                return self._wrap_result(
                    success=False,
                    message=f"No {to_node_type} node found{where_desc}. Available {to_node_type} nodes: {', '.join(str(n) for n in available_names[:10])}"
                )

            from_node = from_nodes[0]
            to_node = to_nodes[0]
            
            # Generate UUID - this will be the single identifier (edge.id = uuid)
            edge_uuid = str(uuid.uuid4())
            
            # Extract name and alias from AST or properties and clean quotes
            edge_name = ast.get('name') or (properties.get('name') if isinstance(properties, dict) else None)
            if isinstance(edge_name, str):
                edge_name = edge_name.strip('"').strip("'")
            edge_alias = ast.get('alias')
            
            # Clean string values in properties (remove quotes)
            if isinstance(properties, dict):
                cleaned_properties = {}
                for key, value in properties.items():
                    if isinstance(value, str):
                        # Strip quotes from string values
                        cleaned_value = value.strip('"').strip("'")
                        cleaned_properties[key] = cleaned_value
                    else:
                        cleaned_properties[key] = value
                properties = cleaned_properties
                # Update name from cleaned properties if not already set
                if not edge_name and 'name' in properties:
                    edge_name = properties['name']
            
            # Don't store UUID in properties - edge.id is the single source of truth
            # Remove uuid from properties if user provided it (to avoid duplication)
            if isinstance(properties, dict):
                properties.pop('uuid', None)
            
            edge = GraphEdge(
                id=edge_uuid,  # Use UUID as the id - single identifier
                source=from_node.id,  # from_node.id is now the UUID
                target=to_node.id,    # to_node.id is now the UUID
                label=edge_type or "EDGE",
                properties=properties.copy() if properties else {},
                name=edge_name,
                uuid=edge_uuid,  # Keep for backward compatibility during transition
                alias=edge_alias
            )
            
            # Add edge to graph
            namespace_graph.add_edge(edge)
            
            return self._wrap_result(
                edges=[self._format_edge_for_display(edge)],
                data={
                    "uuid": edge_uuid,
                    "edge_type": edge_type or "EDGE",
                    "name": edge_name,
                    "alias": edge_alias,
                    "properties": properties
                },
                success=True,
                message=f"Edge {edge_type or 'EDGE'} created successfully"
            )
        except Exception as e:
            logger.error(f"CREATE EDGE failed: {e}")
            import traceback
            traceback.print_exc()
            return {"success": False, "error": str(e)}
        finally:
            # Auto-persist to disk
            if self.graph_registry:
                try:
                    self.graph_registry.save_graph(namespace, create_checkpoint=False)
                except Exception:
                    pass

    def _execute_update_edge(self, ast: Dict[str, Any], namespace: str, namespace_graph) -> Dict[str, Any]:
        """Execute UPDATE EDGE query."""
        try:
            # ast is already the parameters dict from _execute_single_statement
            # Parser may use 'node_type' instead of 'edge_type' for edges
            edge_type = ast.get('edge_type') or ast.get('node_type') or ast.get('label')
            update_props = ast.get('set', {}) or ast.get('properties', {})
            where_clause = ast.get('where')
            
            if not edge_type:
                logger.debug(f"UPDATE EDGE: edge_type not found in ast keys: {list(ast.keys())}")
                return {"success": False, "error": "Edge type not specified"}
            
            # Get all edges of this type
            all_edges = namespace_graph.get_all_edges(label=edge_type)
            
            # Apply WHERE clause if present
            if where_clause:
                all_edges = self._apply_where_clause_to_edges(all_edges, where_clause)
            
            # Update edges
            updated_count = 0
            for edge in all_edges:
                # Build update dict, then apply (LMDB-safe — no reference mutation)
                final_props = dict(edge.properties)
                final_props.update(update_props)
                from datetime import datetime as _dt, timezone as _tz
                from contextsynapse.core.write_context import get_write_context
                final_props['_updated_at'] = _dt.now(_tz.utc).isoformat()
                _wctx = get_write_context()
                if _wctx and _wctx.agent_id:
                    final_props['_updated_by'] = _wctx.agent_id

                # Update in edge_properties storage
                # Use edge.id (UUID) as the key, not source_target
                if hasattr(namespace_graph, 'edge_properties'):
                    namespace_graph.edge_properties[edge.id] = final_props

                # Also update in CSR storage if accessible
                if hasattr(namespace_graph, 'csr_storage') and namespace_graph.csr_storage:
                    # Find edge in CSR storage and update properties
                    for csr_edge in namespace_graph.csr_storage.edge_data:
                        if (csr_edge.source_id == edge.source and
                            csr_edge.target_id == edge.target and
                            csr_edge.edge_type == edge_type):
                            csr_edge.properties = final_props
                            break

                updated_count += 1
            
            # Auto-persist to disk
            if updated_count > 0 and self.graph_registry:
                try:
                    self.graph_registry.save_graph(namespace, create_checkpoint=False)
                except Exception:
                    pass

            return {
                "success": True,
                "updated_count": updated_count,
                "edge_type": edge_type
            }
        except Exception as e:
            logger.error(f"UPDATE EDGE failed: {e}")
            import traceback
            traceback.print_exc()
            return {"success": False, "error": str(e)}

    def _execute_delete_edge(self, ast: Dict[str, Any], namespace: str, namespace_graph) -> Dict[str, Any]:
        """Execute DELETE EDGE query."""
        try:
            # ast is already the parameters dict from _execute_single_statement
            edge_type = ast.get('edge_type') or ast.get('label')
            where_clause = ast.get('where')
            source_node_id = ast.get('source_node_id') or ast.get('src_node_id')
            target_node_id = ast.get('target_node_id') or ast.get('dest_node_id')
            
            if not edge_type:
                return {"success": False, "error": "Edge type not specified"}
            
            # Get all edges of this type
            all_edges = namespace_graph.get_all_edges(label=edge_type)
            
            # Filter by source/target if specified
            if source_node_id:
                all_edges = [e for e in all_edges if e.source == source_node_id]
            if target_node_id:
                all_edges = [e for e in all_edges if e.target == target_node_id]
            
            # Apply WHERE clause if present
            if where_clause:
                all_edges = self._apply_where_clause_to_edges(all_edges, where_clause)
            
            # Delete edges
            deleted_count = 0
            for edge in all_edges:
                try:
                    # Use adapter remove_edge if available (LMDB-safe path)
                    adapter = getattr(namespace_graph, 'csr_adapter', None)
                    if adapter and hasattr(adapter, 'remove_edge'):
                        adapter.remove_edge(edge.source, edge.target, edge_type)
                    elif hasattr(namespace_graph, 'csr_storage') and namespace_graph.csr_storage:
                        # CSR fallback: iterate edge_data list and remove matching entry
                        csr_storage = namespace_graph.csr_storage
                        if hasattr(csr_storage, 'edge_data'):
                            for idx, csr_edge in enumerate(csr_storage.edge_data):
                                if (csr_edge.source_id == edge.source and
                                    csr_edge.target_id == edge.target and
                                    csr_edge.edge_type == edge_type):
                                    csr_storage.edge_data.pop(idx)
                                    if edge_type in csr_storage.edge_types:
                                        csr_storage.edge_types[edge_type].discard(idx)
                                    break

                    # Remove from edge_properties
                    # Use edge.id (UUID) as the key, not source_target
                    if hasattr(namespace_graph, 'edge_properties'):
                        namespace_graph.edge_properties.pop(edge.id, None)

                    deleted_count += 1
                except Exception as e:
                    logger.warning(f"Failed to delete edge {edge.id}: {e}")
            
            # Auto-persist to disk
            if deleted_count > 0 and self.graph_registry:
                try:
                    self.graph_registry.save_graph(namespace, create_checkpoint=False)
                except Exception:
                    pass

            return {
                "success": True,
                "deleted_count": deleted_count,
                "edge_type": edge_type
            }
        except Exception as e:
            logger.error(f"DELETE EDGE failed: {e}")
            import traceback
            traceback.print_exc()
            return {"success": False, "error": str(e)}

    def _execute_traverse(self, ast: Dict[str, Any], namespace: str, namespace_graph) -> Dict[str, Any]:
        """Execute TRAVERSE query with support for FROM...VIA...TO, MAX DEPTH, WHERE."""
        try:
            # Support multiple syntaxes:
            # 1. TRAVERSE FROM Person TO Company VIA WORKS_AT
            # 2. TRAVERSE FROM Person VIA (WORKS_AT) TO Company
            # 3. TRAVERSE FROM Person MAX DEPTH 3
            # 4. TRAVERSE FROM Person WHERE name = "Alice" MAX DEPTH 2
            
            from_node_type = ast.get('from_node_type') or ast.get('from') or ast.get('from_type')
            to_node_type = ast.get('to_node_type') or ast.get('to') or ast.get('to_type')
            via_edges = ast.get('via') or ast.get('via_edges') or ast.get('edge_types')
            max_depth = ast.get('max_depth') or ast.get('depth') or ast.get('MAX_DEPTH', 1)
            where_clause = ast.get('where') or ast.get('where_clause')
            
            # If parameters are empty, return empty result (parser didn't extract parameters)
            if not from_node_type and not ast:
                return {
                    "success": True,
                    "results": [],
                    "count": 0,
                    "message": "TRAVERSE query executed (parser did not extract parameters, empty result returned)"
                }
            
            if not from_node_type:
                return {
                    "success": True,
                    "results": [],
                    "count": 0,
                    "message": "From node type not specified in TRAVERSE query"
                }
            
            # Get starting nodes
            all_nodes = namespace_graph.get_all_nodes()
            start_nodes = [n for n in all_nodes if n.label == from_node_type]
            
            # Apply WHERE clause to starting nodes
            if where_clause:
                start_nodes = self._apply_where_clause(start_nodes, where_clause)
            
            # Enhanced traversal with depth support
            result_nodes = []
            visited = set()
            
            # Convert via_edges to list if it's a single value or tuple
            if via_edges:
                if isinstance(via_edges, str):
                    via_edges = [via_edges]
                elif isinstance(via_edges, (list, tuple)):
                    via_edges = list(via_edges)
                else:
                    via_edges = [str(via_edges)]
            
            for start_node in start_nodes:
                if start_node.id in visited:
                    continue
                
                # Use graph's traverse_graph method for multi-depth traversal
                if via_edges:
                    # Traverse with specific edge types
                    traversed_ids = []
                    for edge_type in via_edges:
                        ids = namespace_graph.traverse_graph(
                            start_node.id,
                            edge_label=edge_type,
                            max_depth=max_depth
                        )
                        traversed_ids.extend(ids)
                    traversed_ids = list(set(traversed_ids))  # Remove duplicates
                else:
                    # Traverse with any edge type
                    traversed_ids = namespace_graph.traverse_graph(
                        start_node.id,
                        edge_label=None,
                        max_depth=max_depth
                    )
                
                for node_id in traversed_ids:
                    if node_id not in visited:
                        neighbor = namespace_graph.get_node(node_id)
                        if neighbor:
                            # Filter by to_node_type if specified
                            if not to_node_type or neighbor.label == to_node_type:
                                result_nodes.append(neighbor)
                                visited.add(node_id)
            
            return {
                "success": True,
                "data": {
                    "nodes": [self._format_node_result(n) for n in result_nodes]
                },
                "nodes": [self._format_node_result(n) for n in result_nodes],
                "count": len(result_nodes)
            }
        except Exception as e:
            logger.error(f"TRAVERSE failed: {e}")
            import traceback
            traceback.print_exc()
            return {"success": False, "error": str(e)}
    
    def _execute_temporal_query(self, ast: Dict[str, Any], namespace: str, namespace_graph) -> Dict[str, Any]:
        """Execute temporal query (AT TIMESTAMP)."""
        try:
            from datetime import datetime
            
            # Check if temporal storage is enabled
            if not hasattr(namespace_graph, 'temporal_enabled') or not namespace_graph.temporal_enabled:
                return {"success": False, "error": "Temporal storage is not enabled. Set config['temporal']['enabled'] = True"}
            
            if not hasattr(namespace_graph, 'temporal_storage') or not namespace_graph.temporal_storage:
                return {"success": False, "error": "Temporal storage not initialized"}
            
            # Extract time value and inner query
            time_value = ast.get('time_value', '')
            inner_stage = ast.get('inner_stage')
            
            if not time_value:
                return {"success": False, "error": "No timestamp specified in temporal query"}
            
            # Parse timestamp
            try:
                # Try ISO format first
                timestamp = datetime.fromisoformat(time_value.replace('Z', '+00:00'))
            except:
                try:
                    # Try common formats
                    from dateutil import parser
                    timestamp = parser.parse(time_value)
                except:
                    return {"success": False, "error": f"Invalid timestamp format: {time_value}"}
            
            # Execute inner query at the specified timestamp
            if inner_stage and hasattr(inner_stage, 'node_type'):
                # Get entity type and WHERE clause from inner query
                entity_type = None
                where_clause = None
                if hasattr(inner_stage, 'parameters'):
                    params = inner_stage.parameters
                    entity_type = params.get('from') or params.get('node_type')
                    where_clause = params.get('where')
                
                # Query temporal storage
                try:
                    from ...temporal.time_travel import TimeTravelQuery
                except ImportError:
                    return {"success": False, "error": "Temporal module not available"}
                time_travel = TimeTravelQuery(namespace_graph.temporal_storage)

                if entity_type:
                    versions = time_travel.query_at_timestamp(timestamp, entity_type=entity_type)
                else:
                    versions = time_travel.query_at_timestamp(timestamp)
                
                # Convert versions to result format
                # versions is a list of dictionaries (from version.to_dict())
                result = []
                for version_dict in versions:
                    if isinstance(version_dict, dict):
                        # Extract properties separately
                        properties = version_dict.get('properties', {})
                        node_data = {
                            "label": properties.get('label') or version_dict.get('entity_type', ''),
                            "timestamp": version_dict.get('timestamp'),
                            "operation": version_dict.get('operation'),
                            "version_id": version_dict.get('version_id'),
                            **properties  # Merge properties into result (includes uuid)
                        }
                        # Remove entity_id if it exists (we use uuid instead)
                        node_data.pop('entity_id', None)
                        
                        # Apply WHERE clause filter if present
                        if where_clause:
                            # Create a temporary node-like object for condition evaluation
                            from ...core.graph_structures import GraphNode
                            temp_node = GraphNode(
                                id=node_data.get('entity_id', ''),
                                label=entity_type or '',
                                properties=properties
                            )
                            if self._evaluate_condition(temp_node, where_clause):
                                result.append(node_data)
                        else:
                            result.append(node_data)
                
                return {
                    "success": True,
                    "data": result,
                    "count": len(result),
                    "timestamp": timestamp.isoformat()
                }
            else:
                # Fallback: query all entities at timestamp
                try:
                    from ...temporal.time_travel import TimeTravelQuery
                except ImportError:
                    return {"success": False, "error": "Temporal module not available"}
                time_travel = TimeTravelQuery(namespace_graph.temporal_storage)
                versions = time_travel.query_at_timestamp(timestamp)
                
                # Convert versions to result format
                result = []
                for version_dict in versions:
                    if isinstance(version_dict, dict):
                        # Extract properties separately
                        properties = version_dict.get('properties', {})
                        node_data = {
                            "label": properties.get('label') or version_dict.get('entity_type', ''),
                            "timestamp": version_dict.get('timestamp'),
                            "operation": version_dict.get('operation'),
                            "version_id": version_dict.get('version_id'),
                            **properties  # Merge properties into result (includes uuid)
                        }
                        # Remove entity_id if it exists (we use uuid instead)
                        node_data.pop('entity_id', None)
                        result.append(node_data)
                
                return {
                    "success": True,
                    "data": result,
                    "count": len(result),
                    "timestamp": timestamp.isoformat()
                }
                
        except Exception as e:
            logger.error(f"Temporal query failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {"success": False, "error": str(e)}
    
    def _execute_find_by_uuid(self, ast: Dict[str, Any], namespace: str, namespace_graph) -> Dict[str, Any]:
        """Execute FIND_BY_UUID query to find a node or edge by its UUID."""
        try:
            uuid_value = ast.get('uuid')
            
            if not uuid_value:
                return {
                    "success": False,
                    "error": "UUID not specified"
                }
            
            # Remove quotes if present
            if isinstance(uuid_value, str):
                uuid_value = uuid_value.strip('"\'')
            
            # Try to find in nodes first
            all_nodes = namespace_graph.get_all_nodes()
            for node in all_nodes:
                if node.id == uuid_value:
                    return {
                        "success": True,
                        "results": [self._format_node_result(node)],
                        "node_type": node.label,
                        "count": 1
                    }
            
            # Try to find in edges
            all_edges = namespace_graph.get_all_edges()
            for edge in all_edges:
                if edge.id == uuid_value:
                    return {
                        "success": True,
                        "results": [{
                            "id": edge.id,
                            "source": edge.source,
                            "target": edge.target,
                            "label": edge.label,
                            "properties": edge.properties or {}
                        }],
                        "edge_type": edge.label,
                        "count": 1
                    }
            
            # UUID not found
            return {
                "success": True,
                "results": [],
                "count": 0,
                "message": f"No node or edge found with UUID: {uuid_value}"
            }
        except Exception as e:
            logger.error(f"FIND_BY_UUID failed: {e}")
            import traceback
            traceback.print_exc()
            return {"success": False, "error": str(e)}
    
    def _execute_match(self, ast: Dict[str, Any], namespace: str, namespace_graph) -> Dict[str, Any]:
        """Execute MATCH query (pattern matching) with support for RETURN and WHERE."""
        try:
            # MATCH supports:
            # 1. MATCH NODE Person WHERE ...
            # 2. MATCH (Person)-[WORKS_AT]->(Company) RETURN Person.name, Company.name
            # 3. MATCH (Person)-[WORKS_AT]->(Company) WHERE Person.name = "Alice"
            
            # Check for "MATCH NODE" syntax (parser puts node_type in parameters)
            node_type = ast.get('node_type')
            where_clause = ast.get('where') or ast.get('where_clause')
            # Handle where_clause as list (from parser)
            if isinstance(where_clause, list) and len(where_clause) > 0:
                where_clause = where_clause[0] if len(where_clause) == 1 else where_clause
            return_clause = ast.get('return') or ast.get('return_clause')
            operation = ast.get('operation', '')
            
            # If it's MATCH NODE syntax
            if operation == 'MATCH_NODE' or node_type:
                # Get all nodes of this type
                all_nodes = namespace_graph.get_all_nodes()
                matching_nodes = [n for n in all_nodes if n.label == node_type]
                
                # Apply WHERE clause
                if where_clause:
                    matching_nodes = self._apply_where_clause(matching_nodes, where_clause)
                
                # Apply RETURN clause if specified
                if return_clause:
                    results = self._apply_return_clause(matching_nodes, return_clause)
                else:
                    results = [self._format_node_result(n) for n in matching_nodes]
                
                return {
                    "success": True,
                    "data": {"nodes": results},
                    "nodes": results
                }
            
            # Check if it's a pattern match (Cypher-style): MATCH (Person)-[WORKS_AT]->(Company)
            match_pattern = ast.get('match_pattern') or ast.get('pattern')
            if not match_pattern and 'pattern' in ast:
                # Pattern is stored as {'nodes': [...], 'edges': [...]}
                pattern_data = ast.get('pattern')
                if isinstance(pattern_data, dict) and 'nodes' in pattern_data:
                    match_pattern = pattern_data
            
            if match_pattern:
                # Parse pattern to extract node types and edge types
                # Handle pattern stored as {'nodes': [...], 'edges': [...]}
                if isinstance(match_pattern, dict) and 'nodes' in match_pattern:
                    # Pattern from parser: {'nodes': [{'type': 'Person', ...}], 'edges': [{'type': 'WORKS_AT', ...}]}
                    nodes = match_pattern.get('nodes', [])
                    edges = match_pattern.get('edges', [])
                    
                    if len(nodes) >= 2 and len(edges) >= 1:
                        from_type = nodes[0].get('type') or nodes[0].get('node_type')
                        edge_type = edges[0].get('type') or edges[0].get('edge_type')
                        to_type = nodes[1].get('type') or nodes[1].get('node_type')
                    elif len(nodes) >= 1:
                        # Single node pattern
                        from_type = nodes[0].get('type') or nodes[0].get('node_type')
                        edge_type = None
                        to_type = None
                    else:
                        from_type = None
                        edge_type = None
                        to_type = None
                elif isinstance(match_pattern, dict):
                    # Pattern with nodes and edges
                    from_type = match_pattern.get('from_type') or match_pattern.get('from')
                    edge_type = match_pattern.get('edge_type') or match_pattern.get('edge')
                    to_type = match_pattern.get('to_type') or match_pattern.get('to')
                    
                    if from_type and edge_type and to_type:
                        # Match pattern: (from_type)-[edge_type]->(to_type)
                        all_nodes = namespace_graph.get_all_nodes()
                        from_nodes = [n for n in all_nodes if n.label == from_type]
                        
                        # Apply WHERE clause to from nodes
                        if where_clause:
                            from_nodes = self._apply_where_clause(from_nodes, where_clause)
                        
                        # Find connected nodes via the edge type
                        result_nodes = []
                        for from_node in from_nodes:
                            # Get neighbors via this edge type
                            if hasattr(namespace_graph, 'get_neighbors'):
                                neighbors = namespace_graph.get_neighbors(from_node.id, edge_label=edge_type)
                                for neighbor_id in neighbors:
                                    neighbor = namespace_graph.get_node(neighbor_id)
                                    if neighbor and neighbor.label == to_type:
                                        result_nodes.append({
                                            "from": self._format_node_result(from_node),
                                            "edge": {"type": edge_type},
                                            "to": self._format_node_result(neighbor)
                                        })
                            else:
                                # Fallback: use traverse_graph
                                traversed_ids = namespace_graph.traverse_graph(
                                    from_node.id,
                                    edge_label=edge_type,
                                    max_depth=1
                                )
                                for node_id in traversed_ids:
                                    neighbor = namespace_graph.get_node(node_id)
                                    if neighbor and neighbor.label == to_type:
                                        result_nodes.append({
                                            "from": self._format_node_result(from_node),
                                            "edge": {"type": edge_type},
                                            "to": self._format_node_result(neighbor)
                                        })
                        
                        # Apply RETURN clause if specified
                        if return_clause:
                            results = self._apply_return_clause_to_pattern(result_nodes, return_clause)
                        else:
                            results = result_nodes
                        
                        return {
                            "success": True,
                            "data": {"matches": results},
                            "matches": results
                        }
                elif isinstance(match_pattern, str):
                    # Simple pattern: "Person" or "(Person)"
                    node_type = match_pattern.strip('()')
                    all_nodes = namespace_graph.get_all_nodes()
                    matching_nodes = [n for n in all_nodes if n.label == node_type]
                    
                    if where_clause:
                        matching_nodes = self._apply_where_clause(matching_nodes, where_clause)
                    
                    if return_clause:
                        results = self._apply_return_clause(matching_nodes, return_clause)
                    else:
                        results = [{"id": n.id, "label": n.label, **n.properties} for n in matching_nodes]
                    
                    return {
                        "success": True,
                        "data": {"nodes": results},
                        "nodes": results
                    }
            
            # If no pattern, try to match by properties or delegate to SELECT
            node_type = ast.get('from')
            if node_type:
                # Delegate to SELECT for now
                return self._execute_select(ast, namespace, namespace_graph)
            
            # Return success with empty result if pattern not found (parser may not have extracted it)
            return {
                "success": True,
                "results": [],
                "count": 0,
                "message": "MATCH pattern not specified, returning empty result"
            }
        except Exception as e:
            logger.error(f"MATCH failed: {e}")
            import traceback
            traceback.print_exc()
            return {"success": False, "error": str(e)}
    
    def _apply_return_clause(self, nodes: List[GraphNode], return_clause: Any) -> List[Dict[str, Any]]:
        """Apply RETURN clause to nodes."""
        if not return_clause:
            return [self._format_node_result(n) for n in nodes]
        
        results = []
        # Parse return expressions
        if isinstance(return_clause, dict):
            return_expressions = return_clause.get('expressions', [])
        elif isinstance(return_clause, list):
            return_expressions = return_clause
        else:
            return_expressions = [return_clause]
        
        for node in nodes:
            result = {}
            for expr in return_expressions:
                if isinstance(expr, dict):
                    # Qualified identifier like Person.name
                    field = expr.get('field') or expr.get('property')
                    alias = expr.get('alias')
                    if field:
                        # Handle qualified identifiers like "Person.name"
                        if '.' in str(field):
                            parts = str(field).split('.')
                            if len(parts) == 2:
                                # node_type.property
                                prop_name = parts[1]
                                value = node.properties.get(prop_name)
                                key = alias or prop_name
                                result[key] = value
                        else:
                            # Simple property
                            value = node.properties.get(field)
                            key = alias or field
                            result[key] = value
                elif expr == '*':
                    # Return all properties
                    result = self._format_node_result(node)
                    break
            if result:
                results.append(result)
        
        return results if results else [self._format_node_result(n) for n in nodes]
    
    def _apply_return_clause_to_pattern(self, matches: List[Dict[str, Any]], return_clause: Any) -> List[Dict[str, Any]]:
        """Apply RETURN clause to pattern matches."""
        if not return_clause:
            return matches
        
        results = []
        # Parse return expressions
        if isinstance(return_clause, dict):
            return_expressions = return_clause.get('expressions', [])
        elif isinstance(return_clause, list):
            return_expressions = return_clause
        else:
            return_expressions = [return_clause]
        
        for match in matches:
            result = {}
            for expr in return_expressions:
                if isinstance(expr, dict):
                    # Qualified identifier like Person.name
                    field = expr.get('field') or expr.get('property')
                    alias = expr.get('alias')
                    if field and '.' in str(field):
                        parts = str(field).split('.')
                        if len(parts) == 2:
                            node_alias = parts[0]  # e.g., "Person"
                            prop_name = parts[1]   # e.g., "name"
                            
                            # Get value from from or to node
                            if 'from' in match and match['from'].get('label') == node_alias:
                                value = match['from'].get(prop_name) or match['from'].get('properties', {}).get(prop_name)
                            elif 'to' in match and match['to'].get('label') == node_alias:
                                value = match['to'].get(prop_name) or match['to'].get('properties', {}).get(prop_name)
                            else:
                                value = None
                            
                            key = alias or field
                            result[key] = value
                elif expr == '*':
                    # Return all match data
                    result = match
                    break
            if result:
                results.append(result)
        
        return results if results else matches
    
    def _apply_where_clause_to_edges(self, edges: List[GraphEdge], where_clause: Any) -> List[GraphEdge]:
        """Apply WHERE clause conditions to filter edges."""
        if not where_clause:
            return edges
        
        filtered = []
        for edge in edges:
            if self._evaluate_condition_for_edge(edge, where_clause):
                filtered.append(edge)
        return filtered
    
    def _evaluate_condition_for_edge(self, edge: GraphEdge, condition: Any) -> bool:
        """Evaluate a condition against an edge."""
        if isinstance(condition, dict):
            # Handle condition dict: {field: value, operator: '=', ...}
            field = condition.get('field') or condition.get('property')
            operator = condition.get('operator', '=') or condition.get('op', '=')
            value = condition.get('value') or condition.get('right') or condition.get('rhs')
            
            if field:
                # Check edge properties
                edge_value = edge.properties.get(field)
                
                # Also check source/target if field is 'source' or 'target'
                if field == 'source' or field == 'source_id':
                    edge_value = edge.source
                elif field == 'target' or field == 'target_id':
                    edge_value = edge.target
                elif field == 'label' or field == 'edge_type':
                    edge_value = edge.label
                
                if operator == '=' or operator == '==' or operator == 'EQUALS':
                    return edge_value == value
                elif operator == '!=' or operator == '<>' or operator == 'NOT_EQUALS':
                    return edge_value != value
                elif operator == '>' or operator == 'GT':
                    if isinstance(edge_value, (int, float)) and isinstance(value, (int, float)):
                        return edge_value > value
                    return False
                elif operator == '>=' or operator == 'GTE':
                    if isinstance(edge_value, (int, float)) and isinstance(value, (int, float)):
                        return edge_value >= value
                    return False
                elif operator == '<' or operator == 'LT':
                    if isinstance(edge_value, (int, float)) and isinstance(value, (int, float)):
                        return edge_value < value
                    return False
                elif operator == '<=' or operator == 'LTE':
                    if isinstance(edge_value, (int, float)) and isinstance(value, (int, float)):
                        return edge_value <= value
                    return False
        elif isinstance(condition, list):
            # Handle AND/OR conditions
            return all(self._evaluate_condition_for_edge(edge, c) for c in condition)
        elif hasattr(condition, '__dict__'):
            return self._evaluate_condition_for_edge(edge, condition.__dict__)
        
        return True
    
    def _store_edge_in_collection(self, edge: GraphEdge, namespace: str, collection: str):
        """Store edge in collection (placeholder)."""
        pass
    
    def _execute_entity_extract_stage(self, stage, stage_data, namespace, source_collection, target_collection) -> Dict:
        """Execute entity extraction stage (placeholder)."""
        return {'status': 'skipped', 'reason': 'Not implemented'}
    
    def _execute_relationship_extract_stage(self, stage, stage_data, namespace, source_collection, target_collection) -> Dict:
        """Execute relationship extraction stage (placeholder)."""
        return {'status': 'skipped', 'reason': 'Not implemented'}
    
    def _execute_extract_entities_and_relationships_stage(self, stage, stage_data, namespace, source_collection, target_collection) -> Dict:
        """
        Execute EXTRACT_ENTITIES_AND_RELATIONSHIPS stage - combined entity and relationship extraction.
        
        This stage:
        1. Extracts entities from chunks
        2. Extracts relationships between entities
        3. Creates Entity nodes and Relationship edges
        """
        logger.info("[EXTRACT_ENTITIES_AND_RELATIONSHIPS] Extracting entities and relationships with LLM...")
        
        # First extract entities (reuse entity extraction logic)
        entity_result = self._execute_entity_extract_stage(stage, stage_data, namespace, source_collection, target_collection)
        
        if entity_result.get('status') != 'completed':
            return entity_result
        
        # Update stage_data with entity results for relationship extraction
        stage_data['extract_entities'] = entity_result
        
        # Then extract relationships (reuse relationship extraction logic)
        relationship_result = self._execute_relationship_extract_stage(stage, stage_data, namespace, source_collection, target_collection)
        
        # Combine results
        combined_result = {
            'status': 'completed',
            'entities_created': entity_result.get('entities_extracted', 0),
            'relationships_created': relationship_result.get('relationships_extracted', 0),
            'llm': stage.get('using_llm', 'gpt-4'),
            'namespace': namespace,
            'collection': target_collection
        }
        
        # Save to stage_data
        stage_data['extract_entities_and_relationships'] = combined_result
        
        logger.info(f"[OK] Combined extraction completed: {combined_result['entities_created']} entities, {combined_result['relationships_created']} relationships")
        return combined_result
    
    def _execute_enhance_graph_stage(self, stage, stage_data, namespace, source_collection, target_collection) -> Dict:
        """
        Execute ENHANCE_GRAPH stage - connects entities to chunks, documents, and other entities.
        
        This stage:
        1. Gets entities from extract_entities_and_relationships stage
        2. Gets chunks, documents from previous stages
        3. Connects entities to chunks (MENTIONS)
        4. Connects entities to documents (APPEARS_IN)
        5. Uses schema to create relationship edges between entities
        """
        logger.info("[ENHANCE_GRAPH] Enhancing graph with entity connections...")
        
        # Get namespace graph
        namespace_graph = self.graph_registry.get_graph(namespace, load_if_missing=True) if self.graph_registry else self.contextcore
        if not namespace_graph:
            return {'status': 'failed', 'reason': f'Namespace graph not found: {namespace}'}
        
        try:
            from .graph_builder import GraphBuilder
            
            # Initialize graph builder
            graph_builder = GraphBuilder(namespace_graph, namespace)
            
            # Get entities from extract_entities_and_relationships stage
            extract_result = stage_data.get('extract_entities_and_relationships', {})
            if extract_result.get('status') != 'completed':
                # Fallback to extract_entities stage
                extract_result = stage_data.get('extract_entities', {})
                if extract_result.get('status') != 'completed':
                    return {'status': 'skipped', 'reason': 'No entities from extraction stage'}
            
            # Get all entity nodes
            all_nodes = namespace_graph.get_all_nodes()
            entity_nodes = [n for n in all_nodes if n.label == 'Entity' and n.properties.get('namespace') == namespace]
            
            logger.info(f"[ENHANCE_GRAPH] Found {len(entity_nodes)} entity nodes")
            
            if not entity_nodes:
                return {'status': 'skipped', 'reason': 'No entity nodes found'}
            
            # Get chunks from chunk stage
            chunk_result = stage_data.get('chunk', {})
            chunk_ids = chunk_result.get('chunk_ids', [])
            chunk_nodes = []
            for chunk_id in chunk_ids:
                chunk_node = namespace_graph.get_node(chunk_id)
                if chunk_node:
                    chunk_nodes.append(chunk_node)
            
            # Get documents from connect stage
            connect_result = stage_data.get('connect', {})
            document_node_ids = connect_result.get('document_nodes', [])
            document_nodes = []
            for doc_id in document_node_ids:
                doc_node = namespace_graph.get_node(doc_id)
                if doc_node:
                    document_nodes.append(doc_node)
            
            edges_created = 0
            
            # Connect entities to chunks (MENTIONS)
            chunk_entity_edges = graph_builder.connect_entities_to_chunks(entity_nodes, chunk_nodes)
            for edge_dict in chunk_entity_edges:
                edge = GraphEdge(
                    id=edge_dict['id'],
                    source=edge_dict['source'],
                    target=edge_dict['target'],
                    label=edge_dict['type'],
                    properties=edge_dict['properties']
                )
                namespace_graph.add_edge(edge)
                self._store_edge_in_collection(edge, namespace, target_collection)
                edges_created += 1
            
            logger.info(f"[ENHANCE_GRAPH] Connected {len(chunk_entity_edges)} entities to chunks")
            
            # Connect entities to documents (APPEARS_IN)
            doc_entity_edges = graph_builder.connect_entities_to_documents(entity_nodes, document_nodes)
            for edge_dict in doc_entity_edges:
                edge = GraphEdge(
                    id=edge_dict['id'],
                    source=edge_dict['source'],
                    target=edge_dict['target'],
                    label=edge_dict['type'],
                    properties=edge_dict['properties']
                )
                namespace_graph.add_edge(edge)
                self._store_edge_in_collection(edge, namespace, target_collection)
                edges_created += 1
            
            logger.info(f"[ENHANCE_GRAPH] Connected {len(doc_entity_edges)} entities to documents")
            
            # Use schema to create relationship edges between entities
            schema_entity_edges = 0
            if self.schema_parser and self.schema_parser.schema:
                try:
                    logger.info("[ENHANCE_GRAPH] Applying schema-based entity relationship edges...")
                    
                    # Get relationship data from extract_entities_and_relationships stage
                    relationship_result = stage_data.get('extract_relationships', {})
                    relationship_data = relationship_result.get('relationship_data', [])
                    
                    if relationship_data:
                        # Build relationship edges using schema
                        entity_relationship_edges = graph_builder.build_relationship_edges(
                            relationship_data, entity_nodes, edge_type='RELATED_TO'
                        )
                        
                        for edge_dict in entity_relationship_edges:
                            edge = GraphEdge(
                                id=edge_dict['id'],
                                source=edge_dict['source'],
                                target=edge_dict['target'],
                                label=edge_dict['type'],
                                properties=edge_dict['properties']
                            )
                            namespace_graph.add_edge(edge)
                            self._store_edge_in_collection(edge, namespace, target_collection)
                            edges_created += 1
                            schema_entity_edges += 1
                        
                        logger.info(f"[ENHANCE_GRAPH] Created {schema_entity_edges} schema-based entity relationship edges")
                    
                    # Also wire schema edges for entities (MENTIONS, RELATED_TO, etc.)
                    entity_ids = [e.id for e in entity_nodes]
                    if entity_ids:
                        # Get schema edge definitions for entities
                        schema_edge_types = ['MENTIONS', 'RELATED_TO', 'HAS_RELATIONSHIP']
                        for edge_type_name in schema_edge_types:
                            edge_def = self.schema_parser.schema.edge_types.get(edge_type_name)
                            if edge_def:
                                # Create edges based on schema
                                schema_edges = self._create_entity_schema_edges(
                                    edge_def, entity_nodes, namespace, target_collection, namespace_graph
                                )
                                edges_created += len(schema_edges)
                                schema_entity_edges += len(schema_edges)
                                if schema_edges:
                                    logger.info(f"[ENHANCE_GRAPH] Created {len(schema_edges)} {edge_type_name} edges from schema")
                    
                except Exception as e:
                    logger.warning(f"[WARN] Schema-based entity edge wiring failed: {e}")
                    import traceback
                    logger.debug(traceback.format_exc())
            
            logger.info(f"[ENHANCE_GRAPH] Successfully created {edges_created} graph enhancement edges")
            
            return {
                'status': 'completed',
                'edges_created': edges_created,
                'entity_to_chunk_edges': len(chunk_entity_edges),
                'entity_to_document_edges': len(doc_entity_edges),
                'schema_entity_edges': schema_entity_edges
            }
            
        except Exception as e:
            logger.error(f"[ERROR] ENHANCE_GRAPH stage failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {
                'status': 'failed',
                'reason': str(e)
            }
    
    def _create_entity_schema_edges(self, edge_def, entity_nodes: List[Any], namespace: str, 
                                    collection: str, namespace_graph) -> List[str]:
        """
        Create schema-based edges for entities.
        
        Args:
            edge_def: EdgeType definition from schema
            entity_nodes: List of entity nodes
            namespace: Namespace name
            collection: Collection name
            namespace_graph: Namespace graph instance
            
        Returns:
            List of created edge IDs
        """
        from datetime import datetime
        edge_ids = []
        
        if edge_def.name == 'MENTIONS':
            # MENTIONS: from [Chunk, Document] to [Entity]
            # This is already handled in connect_entities_to_chunks
            pass
        elif edge_def.name in ['RELATED_TO', 'HAS_RELATIONSHIP']:
            # RELATED_TO/HAS_RELATIONSHIP: from [Entity] to [Entity]
            # Create edges based on entity relationships
            # This would be enhanced with semantic similarity or explicit relationships
            pass
        
        return edge_ids
    
    # ========================================================================
    # SHOW Operations
    # ========================================================================
    
    def _execute_show_namespaces(self) -> Dict[str, Any]:
        """Execute SHOW NAMESPACES query."""
        try:
            namespaces = []
            if self.graph_registry:
                # Get all namespaces from registry
                if hasattr(self.graph_registry, 'list_namespaces'):
                    namespaces = self.graph_registry.list_namespaces()
                elif hasattr(self.graph_registry, 'graphs'):
                    namespaces = list(self.graph_registry.graphs.keys())
                else:
                    # Try to get from registry metadata
                    try:
                        from ...core.registry_metadata import registry_metadata
                        namespaces = registry_metadata.list_namespaces()
                    except:
                        pass
            else:
                # If no registry, return current namespace
                if self.active_namespace:
                    namespaces = [self.active_namespace]
            
            return {
                "success": True,
                "data": {"namespaces": namespaces},
                "namespaces": namespaces
            }
        except Exception as e:
            logger.error(f"SHOW NAMESPACES failed: {e}")
            return {"success": False, "error": str(e)}
    
    def _execute_show_graphs(self) -> Dict[str, Any]:
        """Execute SHOW GRAPHS query."""
        try:
            graphs = []
            if self.graph_registry:
                if hasattr(self.graph_registry, 'list_graphs'):
                    graphs = self.graph_registry.list_graphs()
                elif hasattr(self.graph_registry, 'graphs'):
                    graphs = [{"name": name} for name in self.graph_registry.graphs.keys()]
            
            return {
                "success": True,
                "data": {"graphs": graphs},
                "graphs": graphs
            }
        except Exception as e:
            logger.error(f"SHOW GRAPHS failed: {e}")
            return {"success": False, "error": str(e)}
    
    def _execute_show_collections(self, namespace: str) -> Dict[str, Any]:
        """Execute SHOW COLLECTIONS query."""
        try:
            collections = []
            # Try to get collections from namespace graph
            if self.graph_registry:
                graph = self.graph_registry.get_graph(namespace, load_if_missing=False)
                if graph and hasattr(graph, 'collections'):
                    collections = list(graph.collections.keys()) if isinstance(graph.collections, dict) else []
            
            return {
                "success": True,
                "data": {"collections": collections},
                "collections": collections
            }
        except Exception as e:
            logger.error(f"SHOW COLLECTIONS failed: {e}")
            return {"success": False, "error": str(e)}
    
    def _execute_show_pipelines(self, namespace: str) -> Dict[str, Any]:
        """Execute SHOW PIPELINES query."""
        try:
            pipelines = []
            if hasattr(self, '_pipelines'):
                # Filter pipelines by namespace if provided
                for name, pipeline_info in self._pipelines.items():
                    if not namespace or pipeline_info.get('namespace') == namespace:
                        pipelines.append({
                            "name": name,
                            "namespace": pipeline_info.get('namespace'),
                            "stages": len(pipeline_info.get('stages', []))
                        })
            
            return {
                "success": True,
                "data": {"pipelines": pipelines},
                "pipelines": pipelines
            }
        except Exception as e:
            logger.error(f"SHOW PIPELINES failed: {e}")
            return {"success": False, "error": str(e)}
    
    def _execute_show_indexes(self, namespace: str) -> Dict[str, Any]:
        """Execute SHOW INDEXES query."""
        try:
            indexes = []
            # Try to get indexes from namespace graph
            if self.graph_registry:
                graph = self.graph_registry.get_graph(namespace, load_if_missing=False)
                if graph and hasattr(graph, 'indexes'):
                    indexes = list(graph.indexes.keys()) if isinstance(graph.indexes, dict) else []
            
            return {
                "success": True,
                "data": {"indexes": indexes},
                "indexes": indexes
            }
        except Exception as e:
            logger.error(f"SHOW INDEXES failed: {e}")
            return {"success": False, "error": str(e)}
    
    def _execute_show_stats(self, namespace: str, namespace_graph) -> Dict[str, Any]:
        """Execute SHOW STATS query."""
        try:
            stats = {}
            
            if namespace_graph:
                # Get node count
                all_nodes = namespace_graph.get_all_nodes()
                stats['node_count'] = len(all_nodes)
                
                # Get edge count
                if hasattr(namespace_graph, 'get_all_edges'):
                    all_edges = namespace_graph.get_all_edges()
                    stats['edge_count'] = len(all_edges)
                else:
                    stats['edge_count'] = 0
                
                # Get node types
                node_types = {}
                for node in all_nodes:
                    node_type = node.label
                    node_types[node_type] = node_types.get(node_type, 0) + 1
                stats['node_types'] = node_types
                
                # Get edge types
                if hasattr(namespace_graph, 'get_all_edges'):
                    edge_types = {}
                    for edge in namespace_graph.get_all_edges():
                        edge_type = edge.label
                        edge_types[edge_type] = edge_types.get(edge_type, 0) + 1
                    stats['edge_types'] = edge_types
                else:
                    stats['edge_types'] = {}
            
            return {
                "success": True,
                "data": stats,
                "stats": stats
            }
        except Exception as e:
            logger.error(f"SHOW STATS failed: {e}")
            return {"success": False, "error": str(e)}
    
    def _execute_show_current_graph(self, namespace: str) -> Dict[str, Any]:
        """Execute SHOW CURRENT GRAPH query."""
        try:
            graph_info = {
                "name": namespace or "default",
                "active_namespace": self.active_namespace or namespace
            }
            
            if self.graph_registry:
                graph = self.graph_registry.get_graph(namespace, load_if_missing=False)
                if graph:
                    graph_info["exists"] = True
                    if hasattr(graph, 'get_all_nodes'):
                        graph_info["node_count"] = len(graph.get_all_nodes())
                else:
                    graph_info["exists"] = False
            
            return {
                "success": True,
                "data": graph_info,
                "graph": graph_info
            }
        except Exception as e:
            logger.error(f"SHOW CURRENT GRAPH failed: {e}")
            return {"success": False, "error": str(e)}
    
    # ========================================================================
    # DESCRIBE Operations
    # ========================================================================
    
    def _execute_describe(self, ast: Dict[str, Any], namespace: str, namespace_graph) -> Dict[str, Any]:
        """Execute DESCRIBE query."""
        try:
            # Parser provides: target_type ('NODE', 'EDGE', 'GRAPH') and identifier
            target_type = ast.get('target_type') or ast.get('type')
            identifier = ast.get('identifier') or ast.get('name')
            
            if not target_type:
                return {"success": False, "error": "DESCRIBE target type not specified"}
            
            # Handle DESCRIBE NODE
            if target_type == 'NODE' or 'node' in str(target_type).lower():
                node_type = identifier
                if not node_type:
                    return {"success": False, "error": "Node type not specified"}
                
                # Get sample nodes of this type
                all_nodes = namespace_graph.get_all_nodes()
                sample_nodes = [n for n in all_nodes if n.label == node_type][:5]
                
                # Extract properties from sample nodes
                properties = set()
                for node in sample_nodes:
                    properties.update(node.properties.keys())
                
                return {
                    "success": True,
                    "data": {
                        "node_type": node_type,
                        "properties": sorted(list(properties)),
                        "sample_count": len(sample_nodes),
                        "total_count": len([n for n in all_nodes if n.label == node_type])
                    }
                }
            
            # Handle DESCRIBE EDGE
            elif target_type == 'EDGE' or 'edge' in str(target_type).lower():
                edge_type = identifier
                if not edge_type:
                    return {"success": False, "error": "Edge type not specified"}
                
                # Get sample edges of this type
                if hasattr(namespace_graph, 'get_all_edges'):
                    all_edges = namespace_graph.get_all_edges()
                    sample_edges = [e for e in all_edges if e.label == edge_type][:5]
                    
                    # Extract properties from sample edges
                    properties = set()
                    for edge in sample_edges:
                        properties.update(edge.properties.keys())
                    
                    return {
                        "success": True,
                        "data": {
                            "edge_type": edge_type,
                            "properties": sorted(list(properties)),
                            "sample_count": len(sample_edges),
                            "total_count": len([e for e in all_edges if e.label == edge_type])
                        }
                    }
                else:
                    return {"success": False, "error": "Edge querying not supported"}
            
            # Handle DESCRIBE GRAPH
            elif target_type == 'GRAPH' or 'graph' in str(target_type).lower():
                graph_name = identifier or namespace
                return {
                    "success": True,
                    "data": {
                        "graph_name": graph_name,
                        "namespace": namespace
                    }
                }
            
            return {"success": False, "error": f"Unknown DESCRIBE target type: {target_type}"}
        except Exception as e:
            logger.error(f"DESCRIBE failed: {e}")
            return {"success": False, "error": str(e)}
    
    # ========================================================================
    # CREATE/USE GRAPH Operations
    # ========================================================================
    
    def _execute_create_graph(self, ast: Dict[str, Any], namespace: str) -> Dict[str, Any]:
        """Execute CREATE GRAPH query."""
        try:
            graph_name = ast.get('graph_name') or ast.get('name')
            if not graph_name:
                return {"success": False, "error": "Graph name not specified"}
            
            # Create graph in registry
            if self.graph_registry:
                graph = self.graph_registry.create_graph(graph_name)
                return {
                    "success": True,
                    "message": f"Graph '{graph_name}' created",
                    "graph_name": graph_name
                }
            else:
                return {"success": False, "error": "Graph registry not available"}
        except Exception as e:
            logger.error(f"CREATE GRAPH failed: {e}")
            return {"success": False, "error": str(e)}
    
    def _execute_use_graph(self, ast: Dict[str, Any], namespace: str) -> Dict[str, Any]:
        """Execute USE GRAPH query."""
        try:
            graph_name = ast.get('graph_name') or ast.get('name')
            if not graph_name:
                return {"success": False, "error": "Graph name not specified"}

            if self.graph_registry:
                # Auto-create the graph if it doesn't exist yet
                graph = self.graph_registry.get_graph(graph_name, load_if_missing=True)
                if graph is None:
                    graph = self.graph_registry.create_graph(graph_name)
                self.contextcore = graph
                self.active_namespace = graph_name
                return {
                    "success": True,
                    "message": f"Using graph: {graph_name}",
                    "graph_name": graph_name
                }
            else:
                return {"success": False, "error": "Graph registry not available"}
        except Exception as e:
            logger.error(f"USE GRAPH failed: {e}")
            return {"success": False, "error": str(e)}

    # ========================================================================
    # ContextHub integration
    # ========================================================================

    @property
    def context_hub(self):
        """Return (or lazily create) the executor's ContextHub instance."""
        if not hasattr(self, '_context_hub') or self._context_hub is None:
            try:
                from ....context import ContextHub
            except ImportError:
                from ...context import ContextHub
            self._context_hub = ContextHub()
        return self._context_hub

    def build_context(
        self,
        query: Optional[str] = None,
        node_types: Optional[List[str]] = None,
        system_prompt: Optional[str] = None,
        max_tokens: int = 8000,
    ) -> Any:
        """
        Build a ContextHub from the current graph state.

        Args:
            query: Optional AIQL query whose results populate the hub.
            node_types: Optional list of node label strings to include.
            system_prompt: Optional system instruction prepended to the context.
            max_tokens: Token budget for the returned hub.

        Returns:
            ContextHub ready for .to_messages() / .to_prompt() / etc.
        """
        try:
            from ...context import ContextHub
        except ImportError:
            return {"success": False, "error": "ContextHub not available"}

        hub = ContextHub(system_prompt=system_prompt, max_tokens=max_tokens)
        namespace = self.active_namespace or "default"
        namespace_graph = (
            self.graph_registry.get_graph(namespace, load_if_missing=True)
            if self.graph_registry else self.contextcore
        )

        if namespace_graph:
            hub.add_graph_summary(namespace_graph, namespace=namespace)

            if node_types:
                for nt in node_types:
                    nodes = namespace_graph.get_all_nodes(label=nt)
                    if nodes:
                        hub.add_nodes(nodes, label=f"Nodes: {nt}", source=namespace)
            elif not query:
                # Default: include all nodes
                all_nodes = namespace_graph.get_all_nodes()
                if all_nodes:
                    hub.add_nodes(all_nodes, source=namespace)

        if query:
            result = self.execute(query)
            hub.add_query_result(result, query=query)

        return hub

    # ------------------------------------------------------------------
    # Search execution (DENSE, SPARSE, HYBRID, SEMANTIC, GRAPH SEARCH)
    # ------------------------------------------------------------------

    def _get_search_engines(self, namespace_graph):
        """Lazy-initialize and return (sparse_engine, embedding_service, vector_store)."""
        # Sparse (BM25) engine
        if not hasattr(self, '_sparse_engine'):
            self._sparse_engine = None
        if self._sparse_engine is None:
            try:
                from ...search.whoosh_search import WhooshSearchEngine
                self._sparse_engine = WhooshSearchEngine()
            except Exception:
                self._sparse_engine = None

        # Index current graph nodes into sparse engine (if not already done)
        if self._sparse_engine and namespace_graph:
            if not hasattr(self, '_sparse_indexed_ns') or self._sparse_indexed_ns != id(namespace_graph):
                self._sparse_engine.index_nodes_from_graph(namespace_graph)
                self._sparse_indexed_ns = id(namespace_graph)

        # Embedding service
        if not hasattr(self, '_embedding_service'):
            self._embedding_service = None
        if self._embedding_service is None:
            try:
                from ...models.embedding_service import get_embedding_service
                self._embedding_service = get_embedding_service()
            except Exception:
                self._embedding_service = None

        # Vector store
        if not hasattr(self, '_vector_store'):
            self._vector_store = None
        if self._vector_store is None:
            try:
                from ...vector.custom_vector_store import CustomVectorStore
                self._vector_store = CustomVectorStore(dimension=1536, metric="cosine")
            except Exception:
                self._vector_store = None

        return self._sparse_engine, self._embedding_service, self._vector_store

    def _execute_search_shorthand(self, query: str, namespace: str) -> Dict[str, Any]:
        """Handle search shorthands like: DENSE SEARCH "query" IN Person LIMIT 5"""
        import re

        # Parse: <TYPE> SEARCH "<query>" [IN <collection>] [LIMIT <n>]
        upper = query.upper()
        search_type = None
        for st in ('DENSE', 'SPARSE', 'HYBRID', 'SEMANTIC', 'GRAPH'):
            if upper.startswith(st + ' SEARCH'):
                search_type = st
                break

        if not search_type:
            return {"success": False, "error": "Unknown search type"}

        # Extract quoted query text
        rest = query[len(search_type) + len(' SEARCH'):].strip()
        match = re.match(r"""['"](.+?)['"](.*)""", rest, re.DOTALL)
        if not match:
            return {"success": False, "error": "Search query must be a quoted string"}

        search_text = match.group(1)
        remainder = match.group(2).strip()

        # Parse IN <collection> and LIMIT <n>
        collection = None
        limit = 10
        in_match = re.search(r'\bIN\s+(\w+)', remainder, re.IGNORECASE)
        if in_match:
            collection = in_match.group(1)
        limit_match = re.search(r'\bLIMIT\s+(\d+)', remainder, re.IGNORECASE)
        if limit_match:
            limit = int(limit_match.group(1))

        params = {
            'search_type': search_type,
            'query': search_text,
            'collection': collection,
            'limit': limit,
        }
        namespace_graph = self._get_namespace_graph(namespace)
        return self._execute_search(params, namespace, namespace_graph)

    def _execute_search(self, params: Dict[str, Any], namespace: str, namespace_graph) -> Dict[str, Any]:
        """
        Execute a DENSE/SPARSE/HYBRID/SEMANTIC/GRAPH SEARCH query.

        Args:
            params: Dict with keys: search_type, query, collection (label filter),
                    limit, weights (for hybrid), etc.
            namespace: Active namespace
            namespace_graph: AIContextDB graph instance
        """
        import time as _time
        start = _time.time()

        search_type = (params.get('search_type') or 'HYBRID').upper()
        query_text = params.get('query') or ''
        label_filter = params.get('collection') or params.get('node_type') or params.get('namespace')
        limit = int(params.get('limit') or 10)
        weights = params.get('weights') or {'dense': 0.5, 'sparse': 0.5}

        if not query_text:
            return self._wrap_result(success=False, message="Search query text is required")

        sparse_engine, embedding_svc, vector_store = self._get_search_engines(namespace_graph)

        try:
            if search_type == 'SPARSE':
                return self._do_sparse_search(query_text, limit, label_filter, sparse_engine, start)
            elif search_type in ('DENSE', 'SEMANTIC'):
                return self._do_dense_search(query_text, limit, label_filter,
                                              embedding_svc, vector_store, namespace_graph, start)
            elif search_type in ('HYBRID', 'GRAPH'):
                return self._do_hybrid_search(query_text, limit, label_filter, weights,
                                               sparse_engine, embedding_svc, vector_store,
                                               namespace_graph, start)
            else:
                return self._wrap_result(success=False,
                                          message=f"Unknown search type: {search_type}")
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return self._wrap_result(success=False, message=f"Search error: {e}")

    def _do_sparse_search(self, query_text, limit, label_filter, sparse_engine, start):
        """Execute sparse (BM25) search."""
        import time as _time
        if not sparse_engine:
            return self._wrap_result(
                success=False,
                message="Sparse search engine not available (install whoosh or scikit-learn)")

        result = sparse_engine.search(query_text, limit=limit, label_filter=label_filter)
        search_results = result.get('results', [])

        # Convert to node-like dicts for consistent output
        nodes = []
        for r in search_results:
            nodes.append({
                'id': r['id'],
                'label': r.get('label', 'Node'),
                'properties': {
                    **(r.get('properties', {})),
                    '_search_score': r['score'],
                    '_search_source': r.get('source', 'sparse'),
                },
            })

        return self._wrap_result(
            nodes=nodes,
            data={
                'query_type': 'SPARSE_SEARCH',
                'query': query_text,
                'backend': result.get('backend', 'unknown'),
                'result_count': len(nodes),
            },
            success=True,
            message=f"Sparse search returned {len(nodes)} result(s) (backend: {result.get('backend', '?')})",
        )

    def _do_dense_search(self, query_text, limit, label_filter,
                          embedding_svc, vector_store, namespace_graph, start):
        """Execute dense (embedding-based) search."""
        import time as _time

        if not embedding_svc:
            # Fallback: use sparse search instead
            logger.warning("Embedding service not available, falling back to sparse search")
            sparse_engine, _, _ = self._get_search_engines(namespace_graph)
            return self._do_sparse_search(query_text, limit, label_filter, sparse_engine, start)

        # Embed query — handle nested dict from embedding service
        embed_result = embedding_svc.embed_text(query_text)
        if not embed_result.get('success'):
            logger.warning(f"Embedding failed: {embed_result.get('error')}, falling back to sparse")
            sparse_engine, _, _ = self._get_search_engines(namespace_graph)
            return self._do_sparse_search(query_text, limit, label_filter, sparse_engine, start)

        query_embedding = embed_result.get('embedding')
        # Unwrap nested dict: embedding service may return {'embedding': {'success':..., 'embedding': [...]}}
        while isinstance(query_embedding, dict):
            query_embedding = query_embedding.get('embedding')

        if not query_embedding or not isinstance(query_embedding, (list, tuple)):
            logger.warning("Embedding result is not a vector, falling back to sparse")
            sparse_engine, _, _ = self._get_search_engines(namespace_graph)
            return self._do_sparse_search(query_text, limit, label_filter, sparse_engine, start)

        # If vector store has vectors, search it
        if vector_store and vector_store.vector_count > 0:
            vs_results = vector_store.search(query_embedding, k=limit)
            nodes = []
            for r in vs_results:
                nid = r.get('node_id', r.get('id', ''))
                node = namespace_graph.get_node(nid) if namespace_graph else None
                label = node.label if node else 'Node'
                props = node.properties.copy() if node else {}
                if label_filter and label != label_filter:
                    continue
                props['_search_score'] = r.get('score', 0.0)
                props['_search_source'] = 'dense_vector'
                nodes.append({'id': nid, 'label': label, 'properties': props})
            return self._wrap_result(
                nodes=nodes[:limit],
                data={'query_type': 'DENSE_SEARCH', 'query': query_text,
                      'result_count': len(nodes[:limit])},
                success=True,
                message=f"Dense search returned {len(nodes[:limit])} result(s)",
            )

        # No vectors in store — compute similarity against all node properties
        if namespace_graph:
            import numpy as np
            all_nodes = namespace_graph.get_all_nodes()
            if label_filter:
                all_nodes = [n for n in all_nodes if n.label == label_filter]

            if all_nodes:
                # Build text for each node and batch-embed
                node_texts = []
                for n in all_nodes:
                    props = n.properties if hasattr(n, 'properties') else {}
                    text = ' '.join(f"{k}: {v}" for k, v in props.items()
                                   if k not in ('domain', 'content_hash', 'hash_algorithm', 'uuid', 'id'))
                    node_texts.append(text or n.label)

                batch_result = embedding_svc.embed_batch(node_texts)
                # Unwrap nested embeddings
                raw_embeddings = batch_result.get('embeddings')
                if isinstance(raw_embeddings, dict):
                    raw_embeddings = raw_embeddings.get('embeddings', raw_embeddings)
                if raw_embeddings and isinstance(raw_embeddings, list):
                    # Each element could also be wrapped
                    unwrapped = []
                    for e in raw_embeddings:
                        while isinstance(e, dict):
                            e = e.get('embedding', e.get('embeddings'))
                            if e is None:
                                break
                        if isinstance(e, (list, tuple)):
                            unwrapped.append(e)
                    batch_result = {**batch_result, 'embeddings': unwrapped, 'success': True}
                if batch_result.get('success') and batch_result.get('embeddings'):
                    node_embeddings = np.array(batch_result['embeddings'], dtype=np.float32)
                    query_vec = np.array(query_embedding, dtype=np.float32).reshape(1, -1)

                    # Cosine similarity
                    norms = np.linalg.norm(node_embeddings, axis=1, keepdims=True)
                    norms[norms == 0] = 1
                    node_embeddings_norm = node_embeddings / norms
                    query_norm = query_vec / max(np.linalg.norm(query_vec), 1e-10)
                    scores = (node_embeddings_norm @ query_norm.T).flatten()

                    # Also store in vector store for future searches
                    node_ids = [n.id for n in all_nodes]
                    vector_store.add_vectors(
                        node_ids=node_ids,
                        vectors=node_embeddings.tolist(),
                    )

                    ranked = sorted(zip(all_nodes, scores), key=lambda x: x[1], reverse=True)
                    nodes = []
                    for n, score in ranked[:limit]:
                        props = n.properties.copy() if hasattr(n, 'properties') else {}
                        props['_search_score'] = float(score)
                        props['_search_source'] = 'dense_computed'
                        nodes.append({'id': n.id, 'label': n.label, 'properties': props})

                    return self._wrap_result(
                        nodes=nodes,
                        data={'query_type': 'DENSE_SEARCH', 'query': query_text,
                              'result_count': len(nodes)},
                        success=True,
                        message=f"Dense search returned {len(nodes)} result(s)",
                    )

        return self._wrap_result(
            nodes=[],
            data={'query_type': 'DENSE_SEARCH', 'query': query_text, 'result_count': 0},
            success=True,
            message="Dense search: no results (no embeddings available)",
        )

    def _do_hybrid_search(self, query_text, limit, label_filter, weights,
                           sparse_engine, embedding_svc, vector_store,
                           namespace_graph, start):
        """Execute hybrid search combining sparse + dense results."""
        import time as _time

        dense_weight = float(weights.get('dense', 0.5))
        sparse_weight = float(weights.get('sparse', 0.5))
        graph_weight = float(weights.get('graph', 0.0))
        total = dense_weight + sparse_weight + graph_weight
        if total > 0:
            dense_weight /= total
            sparse_weight /= total
            graph_weight /= total

        # Collect results from both engines
        sparse_results = {}
        dense_results = {}

        # Sparse
        if sparse_engine and sparse_weight > 0:
            sr = sparse_engine.search(query_text, limit=limit * 2, label_filter=label_filter)
            for r in sr.get('results', []):
                sparse_results[r['id']] = r

        # Dense
        if embedding_svc and dense_weight > 0:
            dr = self._do_dense_search(query_text, limit * 2, label_filter,
                                        embedding_svc, vector_store, namespace_graph, start)
            for n in dr.get('nodes', []):
                dense_results[n['id']] = n

        # Merge with Reciprocal Rank Fusion
        all_ids = set(sparse_results.keys()) | set(dense_results.keys())
        fused_scores = {}
        k = 60  # RRF constant

        # Rank sparse
        sparse_ranked = sorted(sparse_results.values(), key=lambda x: x.get('score', x.get('properties', {}).get('_search_score', 0)), reverse=True)
        for rank, r in enumerate(sparse_ranked):
            fused_scores[r['id']] = fused_scores.get(r['id'], 0) + sparse_weight / (k + rank + 1)

        # Rank dense
        dense_ranked = sorted(dense_results.values(), key=lambda x: x.get('properties', {}).get('_search_score', 0), reverse=True)
        for rank, r in enumerate(dense_ranked):
            fused_scores[r['id']] = fused_scores.get(r['id'], 0) + dense_weight / (k + rank + 1)

        # Build final results sorted by fused score
        final_ranked = sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)
        nodes = []
        for nid, score in final_ranked[:limit]:
            # Get best available metadata
            if nid in dense_results:
                entry = dense_results[nid]
                props = entry.get('properties', {}).copy()
                label = entry.get('label', 'Node')
            elif nid in sparse_results:
                entry = sparse_results[nid]
                props = entry.get('properties', {}).copy()
                label = entry.get('label', 'Node')
            else:
                continue
            props['_search_score'] = float(score)
            props['_search_source'] = 'hybrid_rrf'
            props['_sparse_score'] = float(sparse_results[nid].get('score', 0)) if nid in sparse_results else 0.0
            props['_dense_score'] = float(dense_results[nid].get('properties', {}).get('_search_score', 0)) if nid in dense_results else 0.0
            nodes.append({'id': nid, 'label': label, 'properties': props})

        return self._wrap_result(
            nodes=nodes,
            data={
                'query_type': 'HYBRID_SEARCH',
                'query': query_text,
                'fusion_method': 'reciprocal_rank_fusion',
                'weights': {'dense': dense_weight, 'sparse': sparse_weight, 'graph': graph_weight},
                'sparse_count': len(sparse_results),
                'dense_count': len(dense_results),
                'result_count': len(nodes),
            },
            success=True,
            message=f"Hybrid search returned {len(nodes)} result(s) (sparse={len(sparse_results)}, dense={len(dense_results)})",
        )
