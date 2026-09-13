"""StoreDocumentsOperator -- move heavy text to keep graph lean."""
from __future__ import annotations

import logging
import uuid
from typing import List, TYPE_CHECKING

from .base import StageOperator

if TYPE_CHECKING:
    from ..ingest_content import Chunk
    from ..stage_executor import GraphContext

logger = logging.getLogger(__name__)

_MAX_INLINE_LENGTH = 200
_CONTENT_FIELDS = ("content", "statement", "description")


class StoreDocumentsOperator(StageOperator):
    name = "store_documents"

    def process(self, chunks, graph_ctx):
        # Try to get document store
        doc_store = None
        try:
            from contextsynapse.storage.namespace_store import NamespaceStore
            ns = NamespaceStore(graph_ctx.namespace or "default")
            doc_store = ns
        except Exception:
            pass

        count = 0
        for node_id in list(graph_ctx.node_ids):
            try:
                node = graph_ctx.get_node(node_id)
                if not node:
                    continue
                props = getattr(node, "properties", {}) or {}
                updates = {}
                has_long = False

                for field in _CONTENT_FIELDS:
                    val = props.get(field, "")
                    if isinstance(val, str) and len(val) > _MAX_INLINE_LENGTH:
                        has_long = True
                        if doc_store:
                            doc_id = f"doc_{uuid.uuid4().hex[:12]}"
                            try:
                                doc_store.store_document(doc_id, {
                                    "node_id": node_id, "field": field,
                                    "text": val, "label": getattr(node, "label", ""),
                                })
                                updates["_doc_ref"] = doc_id
                            except Exception:
                                pass
                        updates[field] = val[:_MAX_INLINE_LENGTH] + "..."

                if has_long and updates:
                    try:
                        adapter = getattr(graph_ctx.db, "csr_adapter", graph_ctx.db)
                        if hasattr(adapter, "update_node_properties"):
                            adapter.update_node_properties(node_id, updates)
                        count += 1
                    except Exception:
                        pass
            except Exception:
                continue

        if count:
            logger.info("store_documents: truncated %d nodes", count)
        return chunks
