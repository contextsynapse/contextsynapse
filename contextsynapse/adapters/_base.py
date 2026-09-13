"""
AIContextDB Connection — shared base for all framework adapters.

Wraps AIQLExecutor + GraphRegistry + ContextHub into a single interface
that adapters delegate to, avoiding duplication of graph logic.
"""

import json
import uuid
import logging
from typing import Any, Dict, List, Optional

from ..core.graph_structures import GraphNode, GraphEdge
from ..context.hub import ContextHub

logger = logging.getLogger(__name__)


class AIContextDBConnection:
    """
    Unified connection wrapper used by all framework adapters.

    Usage:
        conn = AIContextDBConnection(namespace="my_graph")
        conn.query("CREATE NODE Person {name: 'Alice'}")
        nodes = conn.get_nodes(label="Person")
        hub = conn.build_context(system_prompt="You are a graph analyst.")
    """

    def __init__(
        self,
        namespace: str = "default",
        graph_registry=None,
        contextcore=None,
        daemon_url: Optional[str] = None,
    ):
        """
        Args:
            namespace: Graph namespace to operate on.
            graph_registry: Existing GraphRegistry instance. Auto-created if None.
            contextcore: Existing AIContextDB instance. If None, fetched from registry.
            daemon_url: Optional URL of a running ContextDaemon for live context.
        """
        # Lazy imports to avoid circular deps
        from ..core.registry import GraphRegistry
        from ..aiql.engine.executor import AIQLExecutor

        self.namespace = namespace
        self._namespace = namespace  # alias for tools that use underscore prefix
        self.graph_registry = graph_registry or GraphRegistry()
        self._graph_registry = self.graph_registry  # alias
        self.daemon_url = daemon_url

        # Get or create the graph for this namespace
        if contextcore is not None:
            self.contextcore = contextcore
        else:
            existing = self.graph_registry.get_graph(namespace)
            if existing:
                self.contextcore = existing
            else:
                self.contextcore = self.graph_registry.create_graph(namespace)

        self.executor = AIQLExecutor(
            contextcore=self.contextcore,
            graph_registry=self.graph_registry,
        )
        # Point executor at our namespace
        self.executor.active_namespace = namespace

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query(self, aiql: str) -> Dict[str, Any]:
        """Execute an AIQL query string and return the result dict."""
        return self.executor.execute(aiql)

    # ------------------------------------------------------------------
    # Node operations
    # ------------------------------------------------------------------

    def add_node(self, label: str, properties: Optional[Dict[str, Any]] = None) -> GraphNode:
        """Add a node and return it."""
        node_id = str(uuid.uuid4())
        node = GraphNode(id=node_id, label=label, properties=properties or {})
        self.contextsynapse.add_node(node)
        return node

    def get_nodes(
        self,
        label: Optional[str] = None,
        where: Optional[Dict[str, Any]] = None,
    ) -> List[GraphNode]:
        """
        Get nodes, optionally filtered by label and/or property conditions.
        """
        all_nodes = self.contextsynapse.get_all_nodes()
        results = []
        for n in all_nodes:
            node = n if isinstance(n, GraphNode) else self._to_graph_node(n)
            if label and node.label != label:
                continue
            if where:
                props = node.properties or {}
                if not all(props.get(k) == v for k, v in where.items()):
                    continue
            results.append(node)
        return results

    def get_node(self, node_id: str) -> Optional[GraphNode]:
        """Get a single node by ID."""
        try:
            n = self.contextsynapse.get_node(node_id)
            if n is None:
                return None
            return n if isinstance(n, GraphNode) else self._to_graph_node(n)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Edge operations
    # ------------------------------------------------------------------

    def add_edge(
        self,
        source: str,
        target: str,
        label: str,
        properties: Optional[Dict[str, Any]] = None,
    ) -> GraphEdge:
        """Add an edge and auto-persist."""
        edge_id = str(uuid.uuid4())
        edge = GraphEdge(
            id=edge_id, source=source, target=target,
            label=label, properties=properties or {},
        )
        self.contextsynapse.add_edge(edge)
        # Auto-save to disk
        if self._graph_registry:
            try:
                self._graph_registry.save_graph(self._namespace, create_checkpoint=False)
            except Exception:
                pass
        return edge

    def get_edges(
        self,
        label: Optional[str] = None,
    ) -> List[GraphEdge]:
        """Get edges, optionally filtered by label."""
        all_edges = self.contextsynapse.get_all_edges()
        results = []
        for e in all_edges:
            edge = e if isinstance(e, GraphEdge) else self._to_graph_edge(e)
            if label and edge.label != label:
                continue
            results.append(edge)
        return results

    # ------------------------------------------------------------------
    # Context
    # ------------------------------------------------------------------

    def build_context(
        self,
        system_prompt: Optional[str] = None,
        node_types: Optional[List[str]] = None,
        max_tokens: int = 8000,
        include_daemon: bool = False,
    ) -> ContextHub:
        """
        Build a ContextHub populated with graph data.

        Args:
            system_prompt: System prompt for the LLM.
            node_types: Only include nodes of these labels.
            max_tokens: Token budget.
            include_daemon: If True and daemon_url is set, merge daemon context.
        """
        hub = ContextHub(system_prompt=system_prompt, max_tokens=max_tokens)

        # Add graph summary
        hub.add_graph_summary(self.contextcore, namespace=self.namespace)

        # Add nodes
        nodes = self.contextsynapse.get_all_nodes()
        if node_types:
            nodes = [
                n for n in nodes
                if (getattr(n, "label", None) or (n.get("label") if isinstance(n, dict) else ""))
                in node_types
            ]
        if nodes:
            hub.add_nodes(nodes, label=f"Nodes from {self.namespace}")

        # Add edges — so agents see relationships between nodes
        try:
            edges = self.contextsynapse.get_all_edges()
            if edges:
                # Build a lightweight node-name lookup for readable edge rendering
                node_names = {}
                for n in nodes:
                    if isinstance(n, dict):
                        nid = n.get("uuid", n.get("id", ""))
                        name = n.get("name") or n.get("title") or n.get("label", "")
                    else:
                        nid = str(getattr(n, "id", ""))
                        props = getattr(n, "properties", {}) or {}
                        name = props.get("name") or props.get("title") or getattr(n, "label", "")
                    if nid:
                        node_names[nid] = name

                hub.add_edges(
                    edges, label=f"Relationships from {self.namespace}",
                    node_names=node_names,
                )
        except Exception:
            pass  # edges not available on all backends

        # Merge daemon context if available
        if include_daemon and self.daemon_url:
            self._merge_daemon_context(hub)

        return hub

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> bool:
        """Persist the current graph to disk."""
        return self.graph_registry.save_graph(self.namespace)

    def load(self) -> bool:
        """Reload the graph from disk."""
        graph = self.graph_registry.get_graph(self.namespace, load_if_missing=True)
        if graph:
            self.contextcore = graph
            return True
        return False

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _to_graph_node(data) -> GraphNode:
        """Convert a dict or unknown object to GraphNode."""
        if isinstance(data, dict):
            return GraphNode(
                id=data.get("id", data.get("uuid", str(uuid.uuid4()))),
                label=data.get("label", "Unknown"),
                properties={k: v for k, v in data.items() if k not in ("id", "uuid", "label")},
            )
        # Duck-type: assume it has id, label, properties
        return GraphNode(
            id=str(getattr(data, "id", uuid.uuid4())),
            label=getattr(data, "label", "Unknown"),
            properties=getattr(data, "properties", {}),
        )

    @staticmethod
    def _to_graph_edge(data) -> GraphEdge:
        """Convert a dict or unknown object to GraphEdge."""
        if isinstance(data, dict):
            return GraphEdge(
                id=data.get("id", data.get("uuid", str(uuid.uuid4()))),
                source=data.get("source", ""),
                target=data.get("target", ""),
                label=data.get("label", "EDGE"),
                properties={k: v for k, v in data.items()
                            if k not in ("id", "uuid", "source", "target", "label")},
            )
        return GraphEdge(
            id=str(getattr(data, "id", uuid.uuid4())),
            source=str(getattr(data, "source", "")),
            target=str(getattr(data, "target", "")),
            label=getattr(data, "label", "EDGE"),
            properties=getattr(data, "properties", {}),
        )

    def _merge_daemon_context(self, hub: ContextHub) -> None:
        """Pull rolling context from a running daemon and merge into hub."""
        try:
            import urllib.request
            url = f"{self.daemon_url.rstrip('/')}/context/messages"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode())
            for msg in data.get("items", []):
                hub.add_text(
                    msg.get("content", ""),
                    role=msg.get("role", "user"),
                    label=msg.get("label"),
                    source="daemon",
                )
        except Exception as exc:
            logger.debug(f"Could not fetch daemon context: {exc}")
