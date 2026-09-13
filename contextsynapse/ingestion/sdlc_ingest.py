"""sdlc_ingest — write typed SDLC artifacts through the stage pipeline.

Replaces raw db.add_node() / db.add_edge() calls with GraphContext-mediated
writes so that post-ingest operators (BM25 index, vector embed) run
automatically after nodes are committed.

Usage::

    from contextsynapse.ingestion.sdlc_ingest import ingest_sdlc_nodes
    ctx = ingest_sdlc_nodes(db, namespace="my-project", nodes=node_defs, edges=edge_defs)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, TYPE_CHECKING

if TYPE_CHECKING:
    from ..core.hybrid_graph_storage import AIContextDB

from .universal.stage_executor import GraphContext
from .universal.operators.index_bm25 import IndexBM25Operator
from .universal.operators.embed import EmbedOperator

logger = logging.getLogger(__name__)

_BM25 = IndexBM25Operator()
_EMBED = EmbedOperator()


def ingest_sdlc_nodes(
    db: "AIContextDB",
    namespace: str,
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
) -> GraphContext:
    """Write SDLC nodes and edges via GraphContext, then BM25-index and embed them.

    Args:
        db:        AIContextDB instance to write into.
        namespace: Graph namespace name — used by BM25 and vector operators
                   to scope their indexes.
        nodes:     List of node dicts: ``{"id": str, "label": str, "properties": dict}``.
        edges:     List of edge dicts: ``{"label": str, "source": str, "target": str}``.

    Returns:
        The GraphContext accumulator. Inspect ``.node_ids``, ``.edge_count``,
        and ``.errors`` for diagnostics.
    """
    graph_ctx = GraphContext(db=db, namespace=namespace)

    # 1. Write nodes — preserve explicit IDs (req:login, arch:jwt, etc.)
    for node_def in nodes:
        graph_ctx.add_node(
            label=node_def["label"],
            properties=dict(node_def.get("properties", {})),
            node_id=node_def["id"],
        )

    # 2. Write edges
    for edge_def in edges:
        graph_ctx.add_edge(
            source_id=edge_def["source"],
            target_id=edge_def["target"],
            label=edge_def["label"],
        )

    if graph_ctx.errors:
        logger.warning("ingest_sdlc_nodes: %d write errors: %s",
                       len(graph_ctx.errors), graph_ctx.errors)

    # 3. Post-ingest: BM25 index — graceful skip if LMDB unavailable
    try:
        _BM25.process([], graph_ctx)
    except Exception as exc:  # pragma: no cover
        logger.debug("ingest_sdlc_nodes: BM25 indexing skipped: %s", exc)

    # 4. Post-ingest: vector embed — graceful skip if Qdrant unavailable
    try:
        _EMBED.process([], graph_ctx)
    except Exception as exc:  # pragma: no cover
        logger.debug("ingest_sdlc_nodes: embed skipped: %s", exc)

    logger.info(
        "ingest_sdlc_nodes: %d nodes, %d edges written to '%s'",
        len(graph_ctx.node_ids), graph_ctx.edge_count, namespace,
    )
    return graph_ctx
