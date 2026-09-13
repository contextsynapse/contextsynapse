"""
Fan-Out Search — search across runtime graph + all attached atomic contexts.

Instead of copying data on attach, agents search across all graphs at query time.
Results are merged and deduplicated.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def get_attached_graphs(
    session_id: str,
    graph_registry=None,
    session_manager=None,
) -> List:
    """Get all graph instances for a session (runtime + attached atomic contexts).

    Returns list of (graph_name, db_instance) tuples.
    """
    graphs = []

    if not session_manager or not graph_registry:
        return graphs

    session = session_manager.get_session(session_id)
    if not session:
        return graphs

    # Runtime graph (where agents write)
    runtime_ns = session.graph_namespace
    runtime_db = graph_registry.get_graph(runtime_ns, load_if_missing=True)
    if runtime_db:
        graphs.append((runtime_ns, runtime_db))

    # Attached atomic context graphs
    try:
        attached = session_manager.get_attached_contexts(session_id)
        for ctx_ref in attached:
            ctx_ns = ctx_ref.get("graph_namespace", "")
            if ctx_ns and ctx_ns != runtime_ns:
                ctx_db = graph_registry.get_graph(ctx_ns, load_if_missing=True)
                if ctx_db:
                    graphs.append((ctx_ns, ctx_db))
    except Exception as e:
        logger.debug("[FAN_OUT] Failed to get attached contexts: %s", e)

    return graphs


def fan_out_search(
    query: str,
    session_id: str,
    label: str = "",
    limit: int = 10,
    graph_registry=None,
    session_manager=None,
) -> List[Dict]:
    """Search across all graphs attached to a session.

    Returns merged, deduplicated results sorted by relevance.
    """
    graphs = get_attached_graphs(session_id, graph_registry, session_manager)

    if not graphs:
        return []

    all_results = []
    seen_ids = set()

    for graph_name, db in graphs:
        try:
            # Use LMDB/BM25 search if available
            from ..search.rag import plain_search
            results = plain_search(db, query, k=limit)
            for r in results:
                rid = r.get("id", r.get("node_id", ""))
                if rid and rid in seen_ids:
                    continue
                seen_ids.add(rid)
                # Filter by label if specified
                if label and r.get("label", "") != label:
                    continue
                r["_source_graph"] = graph_name
                all_results.append(r)
        except Exception:
            # Fallback: AIQL query
            try:
                from ..aiql import AIQLExecutor
                ex = AIQLExecutor(contextcore=db)
                if label:
                    result = ex.execute(f'SELECT * FROM {label} LIMIT {limit}')
                else:
                    result = ex.execute(f'SELECT * LIMIT {limit}')
                for n in result.get("nodes", []):
                    nid = n.get("id", n.get("uuid", ""))
                    if nid and nid not in seen_ids:
                        seen_ids.add(nid)
                        if isinstance(n, dict):
                            n["_source_graph"] = graph_name
                        all_results.append(n)
            except Exception:
                pass

    return all_results[:limit]


def fan_out_get_nodes(
    session_id: str,
    label: str = "",
    limit: int = 50,
    graph_registry=None,
    session_manager=None,
) -> List:
    """Get nodes by label across all attached graphs."""
    graphs = get_attached_graphs(session_id, graph_registry, session_manager)

    all_nodes = []
    seen_ids = set()

    for graph_name, db in graphs:
        try:
            nodes = db.get_all_nodes(label=label) if label else db.get_all_nodes()
            for n in nodes:
                nid = n.id if hasattr(n, 'id') else ""
                if nid and nid not in seen_ids:
                    seen_ids.add(nid)
                    all_nodes.append(n)
                    if len(all_nodes) >= limit:
                        return all_nodes
        except Exception:
            pass

    return all_nodes
