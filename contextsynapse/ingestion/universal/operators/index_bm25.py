"""IndexBM25Operator -- BM25 index ingested nodes via LMDB."""
from __future__ import annotations

import logging
from typing import List, TYPE_CHECKING

from .base import StageOperator

if TYPE_CHECKING:
    from ..ingest_content import Chunk
    from ..stage_executor import GraphContext

logger = logging.getLogger(__name__)


class IndexBM25Operator(StageOperator):
    """Index graph nodes into the LMDB-backed BM25 index.

    Wraps ``contextsynapse.search.lmdb_index.get_lmdb_index``.
    """

    name = "index_bm25"

    def process(self, chunks: List["Chunk"], graph_ctx: "GraphContext") -> List["Chunk"]:
        try:
            from contextsynapse.search.lmdb_index import get_lmdb_index
        except ImportError:
            logger.debug("index_bm25: lmdb_index not available, skipping")
            return chunks

        try:
            graph_name = graph_ctx.namespace or "default"
            idx = get_lmdb_index(graph_name)
            count = 0

            for node_id in graph_ctx.node_ids:
                node_obj = graph_ctx.get_node(node_id)
                if node_obj is None:
                    continue

                label = getattr(node_obj, "label", "")
                props = dict(getattr(node_obj, "properties", {}))
                idx.index_node(node_id, label, props)
                count += 1

            if count:
                logger.info("index_bm25: indexed %d nodes in '%s'", count, graph_name)

        except Exception as exc:
            logger.debug("index_bm25 error: %s", exc)

        return chunks
