"""ValidateGateOperator -- schema validation for ingested nodes."""
from __future__ import annotations

import logging
from typing import List, TYPE_CHECKING

from .base import StageOperator

if TYPE_CHECKING:
    from ..ingest_content import Chunk
    from ..stage_executor import GraphContext

logger = logging.getLogger(__name__)


class ValidateGateOperator(StageOperator):
    """Validate graph nodes against the compiled schema.

    Wraps ``contextsynapse.schema.validation_gate.ValidationGate``.
    If no compiled_schema is present on the GraphContext the operator
    is a no-op and returns chunks unchanged.
    """

    name = "validate_gate"

    def process(self, chunks: List["Chunk"], graph_ctx: "GraphContext") -> List["Chunk"]:
        if graph_ctx.compiled_schema is None:
            return chunks

        try:
            from contextsynapse.schema.validation_gate import ValidationGate
        except ImportError:
            logger.debug("validate_gate: ValidationGate not available, skipping")
            return chunks

        try:
            gate = ValidationGate(graph_ctx.compiled_schema)
            rejected = 0
            quarantined = 0

            for node_id in list(graph_ctx.node_ids):
                node_obj = graph_ctx.get_node(node_id)
                if node_obj is None:
                    continue

                node_dict = {
                    "label": getattr(node_obj, "label", ""),
                    "properties": dict(getattr(node_obj, "properties", {})),
                }
                verdict = gate.validate_node(node_dict)

                if verdict.verdict == "reject":
                    rejected += 1
                elif verdict.verdict == "quarantine":
                    quarantined += 1

            if rejected or quarantined:
                logger.info(
                    "validate_gate: %d rejected, %d quarantined out of %d nodes",
                    rejected, quarantined, len(graph_ctx.node_ids),
                )
        except Exception as exc:
            logger.debug("validate_gate error: %s", exc)

        return chunks
