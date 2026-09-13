"""
PlaygroundConn — Shared connection wrapper for playground simulation endpoints.

Bridges the gap between the ToolContext interface (used by MCP tools) and the
graph registry. Resolves tenant-scoped namespaces to bare graph names, loads
graphs from disk, and proxies query/build_context/get_edges/get_node calls.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class PlaygroundConn:
    """Connection wrapper that MCP tools can use via ToolContext.conn."""

    def __init__(self, namespace: str, registry):
        self.namespace = namespace
        self._namespace = namespace  # context tools use _namespace
        self.graph_registry = registry
        self._graph_registry = registry  # context tools use _graph_registry

        # Resolve graph: try scoped name, then bare name after ":"
        self.db = None
        self._resolved_ns = namespace
        if namespace and registry:
            self.db = registry.get_graph(namespace, load_if_missing=True)
            if self.db is None and ":" in namespace:
                bare = namespace.split(":", 1)[1]
                self.db = registry.get_graph(bare, load_if_missing=True)
                if self.db:
                    self._resolved_ns = bare
            if self.db is None:
                self.db = registry.get_graph_for_request(namespace)

        self.executor = None
        try:
            from ..aiql import AIQLExecutor
            self.executor = AIQLExecutor(contextcore=self.db, graph_registry=registry)
            if self._resolved_ns:
                self.executor.active_namespace = self._resolved_ns
        except Exception:
            pass

    def get_graph(self):
        return self.db

    def query(self, q: str):
        if self.executor:
            return self.executor.execute(q)
        raise RuntimeError("No executor available — select a graph first")

    def build_context(self, **kwargs):
        if self.executor:
            return self.executor.build_context(**kwargs)
        raise RuntimeError("No executor available")

    def get_edges(self, label: Optional[str] = None):
        if self.db and hasattr(self.db, "get_all_edges"):
            edges = self.db.get_all_edges()
            if label:
                edges = [e for e in edges if getattr(e, "label", "") == label]
            return edges
        return []

    def get_node(self, node_id: str):
        if self.db and hasattr(self.db, "get_node"):
            return self.db.get_node(node_id)
        return None

    def get_nodes(self, label: str = None, where: dict = None):
        """Return nodes from the graph, optionally filtered by label and properties.

        Required by ProjectGraph.__init__ to find/create the Project node.
        """
        if not self.db or not hasattr(self.db, "get_all_nodes"):
            return []
        nodes = self.db.get_all_nodes()
        if label:
            nodes = [n for n in nodes if getattr(n, "label", "") == label]
        if where:
            filtered = []
            for n in nodes:
                props = getattr(n, "properties", {}) or {}
                if all(props.get(k) == v for k, v in where.items()):
                    filtered.append(n)
            nodes = filtered
        return nodes

    def add_edge(self, source_id: str, target_id: str, label: str, properties: dict = None):
        """Add an edge between two nodes. Required by ProjectGraph."""
        if self.executor:
            if properties:
                props_str = ", ".join(f'{k}: "{v}"' for k, v in properties.items())
                self.executor.execute(
                    f'CREATE EDGE {label} FROM "{source_id}" TO "{target_id}" {{{props_str}}}'
                )
            else:
                self.executor.execute(
                    f'CREATE EDGE {label} FROM "{source_id}" TO "{target_id}"'
                )


def create_tool_context(conn: PlaygroundConn, agent_id: str, agent_name: str):
    """Create a ToolContext with all required attributes for playground tools."""
    from ..tools.registry import ToolContext
    ctx = ToolContext(
        conn=conn,
        agent_id=agent_id,
        agent_name=agent_name,
    )
    ctx.db = conn.db  # intelligence tools use ctx.db
    return ctx
