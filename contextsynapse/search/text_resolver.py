"""
Text Resolver — resolves full text for graph nodes.

When chunks are stored with vector_ref (pointer-only), the graph node
doesn't have the full text. This resolver fetches it from the vector
store's metadata using the node_id.

Usage:
    from contextsynapse.search.text_resolver import resolve_text, resolve_texts

    text = resolve_text(node_id, props, graph_name)
    texts = resolve_texts(node_ids_and_props, graph_name)  # batch
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Cache vector store instance
_svs_cache = None


def _get_vector_store():
    global _svs_cache
    if _svs_cache is None:
        try:
            from contextsynapse.context.vector_integration import SessionVectorStore
            _svs_cache = SessionVectorStore()
        except Exception:
            pass
    return _svs_cache


def resolve_text(node_id: str, props: Dict[str, Any], graph_name: str = "") -> str:
    """Resolve full text for a node.

    Priority:
    1. props["content"] / props["text"] — full text on node
    2. props["statement"] — fact text
    3. props["description"] — entity/doc description
    4. DuckDB ContentResolver via _ref pointer (thin graph nodes)
    5. Vector store metadata["text"] — fetched via node_id
    6. props["name"] — fallback
    """
    # Fast path: text is already on the node
    text = props.get("content") or props.get("text") or props.get("statement") or props.get("description")
    if text:
        return text

    # DuckDB path: thin graph node with _ref pointer
    ref = props.get("_ref", "")
    if ref and ref.startswith("duckdb:"):
        try:
            from contextsynapse.storage.router import get_content_resolver
            resolver = get_content_resolver()
            resolved_text = resolver.get_text(node_id)
            if resolved_text:
                return resolved_text
        except Exception:
            pass

    # Vector store path: fetch by node_id from Qdrant/vector store
    ref = props.get("vector_ref")
    if ref and graph_name:
        try:
            svs = _get_vector_store()
            if svs:
                store = svs._get_store(graph_name)
                if store:
                    # Try metadata dict (in-memory stores)
                    if hasattr(store, "metadata"):
                        meta = store.metadata.get(node_id, {})
                        vtext = meta.get("text", "")
                        if vtext:
                            return vtext
                    # Try Qdrant point fetch
                    if hasattr(store, "client"):
                        try:
                            from contextsynapse.vector.qdrant_vector_store import _stable_int_id
                            point_id = _stable_int_id(node_id)
                            points = store.client.retrieve(
                                collection_name=store.collection_name,
                                ids=[point_id],
                                with_payload=True,
                            )
                            if points:
                                vtext = points[0].payload.get("text", "")
                                if vtext:
                                    return vtext
                        except Exception:
                            pass
        except Exception:
            pass

    return props.get("name", "")


def resolve_texts(
    items: List[Tuple[str, Dict[str, Any]]],
    graph_name: str = "",
) -> Dict[str, str]:
    """Batch resolve text for multiple nodes.

    Args:
        items: list of (node_id, props) tuples
        graph_name: graph namespace for vector store lookup

    Returns:
        {node_id: text} dict
    """
    results = {}
    needs_vector = []

    needs_duckdb = []

    for node_id, props in items:
        text = props.get("content") or props.get("text") or props.get("statement") or props.get("description")
        if text:
            results[node_id] = text
        elif props.get("_ref", "").startswith("duckdb:"):
            needs_duckdb.append(node_id)
            results[node_id] = props.get("name", "")  # placeholder
        elif props.get("vector_ref"):
            needs_vector.append(node_id)
            results[node_id] = props.get("name", "")  # placeholder
        else:
            results[node_id] = props.get("name", "")

    # Batch fetch from DuckDB content store
    if needs_duckdb:
        try:
            from contextsynapse.storage.router import get_content_resolver
            resolver = get_content_resolver()
            db_texts = resolver.get_texts_batch(needs_duckdb)
            for nid, txt in db_texts.items():
                if txt:
                    results[nid] = txt
        except Exception:
            pass

    # Batch fetch from vector store
    if needs_vector and graph_name:
        try:
            svs = _get_vector_store()
            if svs:
                store = svs._get_store(graph_name)
                if store and hasattr(store, "metadata"):
                    for nid in needs_vector:
                        meta = store.metadata.get(nid, {})
                        vtext = meta.get("text", "")
                        if vtext:
                            results[nid] = vtext
        except Exception:
            pass

    return results
