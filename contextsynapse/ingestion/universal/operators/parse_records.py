"""ParseRecordsOperator -- create graph nodes from record chunks."""
from __future__ import annotations

import logging
from typing import List, TYPE_CHECKING

from .base import StageOperator

if TYPE_CHECKING:
    from ..ingest_content import Chunk
    from ..stage_executor import GraphContext

logger = logging.getLogger(__name__)

# Fields to check (in priority order) when determining a node's name.
_NAME_FIELDS = ("name", "title", "label", "id", "key", "code")


class ParseRecordsOperator(StageOperator):
    """Convert record-typed chunks into graph nodes.

    For each chunk whose ``chunk_type`` is ``"record"``, the operator reads
    ``fields`` and ``record_type`` from the chunk metadata, creates a graph
    node via *graph_ctx*, and stores the resulting ``node_id`` back into
    ``chunk.metadata["record_node_id"]``.
    """

    name = "parse_records"

    def process(self, chunks: List["Chunk"], graph_ctx: "GraphContext") -> List["Chunk"]:
        count = 0

        for chunk in chunks:
            if chunk.chunk_type != "record":
                continue

            fields: dict = chunk.metadata.get("fields", {})
            record_type: str = chunk.metadata.get("record_type", "Record")

            # Determine node name
            node_name = ""
            for key in _NAME_FIELDS:
                val = fields.get(key)
                if val is not None and str(val).strip():
                    node_name = str(val).strip()
                    break

            # Fallback: first non-empty field value, truncated
            if not node_name:
                for val in fields.values():
                    s = str(val).strip() if val is not None else ""
                    if s:
                        node_name = s[:80]
                        break

            props = dict(fields)
            props["name"] = node_name

            node_id = graph_ctx.add_node(record_type, props)
            chunk.metadata["record_node_id"] = node_id
            count += 1

        if count:
            logger.info("parse_records: created %d nodes", count)

        return chunks
