"""Content Resolver -- fetches full content from DuckDB for thin graph nodes.

When a graph node has a _ref property (e.g. "duckdb:passages:p_42"),
the resolver fetches the full content from DuckDB.

Usage:
    resolver = get_content_resolver()

    # Single node
    node = graph.get_node("p_42")
    full = resolver.resolve(node)  # -> dict with text, metadata, etc.

    # Batch resolve (efficient -- single DuckDB query)
    nodes = [graph.get_node(pid) for pid in passage_ids]
    resolved = resolver.resolve_batch(nodes)  # -> list of dicts with full content

    # For ContextHub integration
    text = resolver.get_text("p_42")  # -> "Full passage text..."
    texts = resolver.get_texts_batch(["p_42", "p_43"])  # -> {"p_42": "...", "p_43": "..."}
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

_global_resolver: Optional["ContentResolver"] = None
_resolver_lock = threading.Lock()


class ContentResolver:
    """Resolves thin graph nodes to full content via _ref pointers."""

    def __init__(self):
        self._content_store = None

    @property
    def content(self):
        """The content store. Lazy-initialized from config."""
        if self._content_store is None:
            from contextsynapse.storage.router.factory import get_content_store
            self._content_store = get_content_store()
        return self._content_store

    # Alias for backward compat
    @property
    def duckdb(self):
        return self.content

    def resolve(self, node) -> Dict[str, Any]:
        """Resolve a single graph node to its full content.

        If the node has a _ref, fetches the full record from DuckDB
        and merges it with the graph node properties.

        Args:
            node: GraphNode or dict with properties.

        Returns:
            Dict with merged graph + content store properties.
        """
        if node is None:
            return {}

        # Extract properties
        if hasattr(node, "properties"):
            props = dict(node.properties)
            result = {"id": node.id, "type": getattr(node, "label", getattr(node, "node_type", "Unknown"))}
        elif isinstance(node, dict):
            props = dict(node.get("properties", node))
            result = {"id": node.get("id", ""), "type": node.get("type", node.get("label", "Unknown"))}
        else:
            return {}

        result.update(props)

        # Check for _ref pointer
        ref = props.get("_ref", "")
        if not ref or not ref.startswith("duckdb:"):
            return result  # no external content, return as-is

        # Parse ref: "duckdb:table:id"
        parts = ref.split(":", 2)
        if len(parts) != 3:
            return result

        _, table, ref_id = parts

        # Fetch from DuckDB
        content = None
        if table == "passages":
            content = self.duckdb.get_passage(ref_id)
        elif table == "documents":
            content = self.duckdb.get_document(ref_id)
        elif table == "facts":
            content = self.duckdb.get_fact(ref_id)
        elif table == "entity_details":
            content = self.duckdb.get_entity_details(ref_id)

        if content:
            # Merge content into result (content takes precedence for text fields)
            result.update(content)

        return result

    def resolve_batch(self, nodes: List) -> List[Dict[str, Any]]:
        """Resolve multiple nodes efficiently.

        Groups _refs by table and does batch lookups where possible.
        """
        if not nodes:
            return []

        # Separate nodes by ref type
        passage_refs = {}  # idx -> passage_id
        other_nodes = {}   # idx -> node

        for i, node in enumerate(nodes):
            if node is None:
                continue
            props = node.properties if hasattr(node, "properties") else node.get("properties", {})
            ref = props.get("_ref", "")
            if ref.startswith("duckdb:passages:"):
                passage_refs[i] = ref.split(":", 2)[2]
            else:
                other_nodes[i] = node

        # Batch fetch passages (single DuckDB query)
        passage_texts = {}
        if passage_refs:
            passage_ids = list(passage_refs.values())
            passage_texts = self.duckdb.get_passage_texts_batch(passage_ids)

        # Build results
        results = [None] * len(nodes)

        # Fast path for passages
        for i, pid in passage_refs.items():
            node = nodes[i]
            props = node.properties if hasattr(node, "properties") else node.get("properties", {})
            result = {
                "id": node.id if hasattr(node, "id") else node.get("id", ""),
                "type": getattr(node, "label", getattr(node, "node_type", node.get("type", "Passage"))),
            }
            result.update(props)
            result["text"] = passage_texts.get(pid, "")
            results[i] = result

        # Resolve non-passage nodes individually
        for i, node in other_nodes.items():
            results[i] = self.resolve(node)

        return [r for r in results if r is not None]

    def get_text(self, node_or_id: Union[str, Any]) -> str:
        """Get the text content for a node.

        Works with:
            - A passage ID string
            - A GraphNode with _ref
            - A dict with _ref
        """
        if isinstance(node_or_id, str):
            # Try direct passage lookup
            text = self.duckdb.get_passage_text(node_or_id)
            if text:
                return text
            # Try fact lookup
            fact = self.duckdb.get_fact(node_or_id)
            if fact:
                return fact.get("statement", "")
            return ""

        # It's a node object
        resolved = self.resolve(node_or_id)
        return resolved.get("text", resolved.get("statement", resolved.get("content", "")))

    def get_texts_batch(self, ids: List[str]) -> Dict[str, str]:
        """Batch text lookup -- returns {id: text} dict."""
        return self.duckdb.get_passage_texts_batch(ids)


def get_content_resolver() -> ContentResolver:
    """Get or create the global content resolver."""
    global _global_resolver
    if _global_resolver is None:
        with _resolver_lock:
            if _global_resolver is None:
                _global_resolver = ContentResolver()
    return _global_resolver
