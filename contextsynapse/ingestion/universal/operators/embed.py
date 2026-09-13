"""EmbedOperator -- vector-embed ingested nodes."""
from __future__ import annotations

import logging
from typing import List, TYPE_CHECKING

from .base import StageOperator

if TYPE_CHECKING:
    from ..ingest_content import Chunk
    from ..stage_executor import GraphContext

logger = logging.getLogger(__name__)


class EmbedOperator(StageOperator):
    """Generate vector embeddings for graph nodes.

    Wraps ``contextsynapse.context.vector_integration.get_session_vector_store``
    and calls ``svs.add_text`` for each node with sufficient text.
    """

    name = "embed"

    def process(self, chunks: List["Chunk"], graph_ctx: "GraphContext") -> List["Chunk"]:
        try:
            from contextsynapse.context.vector_integration import get_session_vector_store
        except ImportError:
            logger.debug("embed: vector_integration not available, skipping")
            return chunks

        try:
            svs = get_session_vector_store()
            if not svs.available:
                logger.debug("embed: vector store not available, skipping")
                return chunks

            namespace = graph_ctx.namespace or "default"
            count = 0

            for node_id in graph_ctx.node_ids:
                node_obj = graph_ctx.get_node(node_id)
                if node_obj is None:
                    continue

                props = dict(getattr(node_obj, "properties", {}))
                label = getattr(node_obj, "label", "")

                # Skip structural nodes — embed knowledge, not scaffolding
                if label in ("Turn", "Session", "ContextMeta", "ContextIntelligence"):
                    continue

                name = props.get("name", "")
                detail = props.get("statement") or props.get("description") or ""
                text = f"{name} {detail}".strip() if name else detail

                if len(text) < 10:
                    continue

                metadata = {
                    "label": label,
                    "node_id": node_id,
                    "name": name,
                    "content_preview": detail[:200] if detail else name,
                }
                svs.add_text(
                    session_id=namespace,
                    text=text,
                    node_id=node_id,
                    metadata=metadata,
                )
                count += 1

            if count:
                logger.info("embed: embedded %d nodes into namespace '%s'", count, namespace)

        except Exception as exc:
            logger.debug("embed error: %s", exc)

        return chunks
