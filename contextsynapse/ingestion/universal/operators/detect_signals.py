"""DetectSignalsOperator -- detect decisions, actions, questions, problems, solutions."""
from __future__ import annotations

import logging
from typing import List, Optional, TYPE_CHECKING

from .base import StageOperator
from ..shared_signals import detect_signals, significance_score

if TYPE_CHECKING:
    from ..ingest_content import Chunk
    from ..stage_executor import GraphContext

logger = logging.getLogger(__name__)

# Signal type -> graph node label
_SIGNAL_LABEL: dict = {
    "decision": "Decision",
    "action": "Action",
    "question": "Question",
    "problem": "Problem",
    "solution": "Solution",
    "clinical_finding": "Finding",
    "contraindication": "Contraindication",
    "guideline": "Guideline",
}

# Signal type -> edge label from turn node to signal node
_SIGNAL_EDGE: dict = {
    "decision": "DECIDES",
    "action": "ASSIGNS",
    "question": "RAISES",
    "problem": "RAISES",
    "solution": "PROPOSES",
    "clinical_finding": "REPORTS",
    "contraindication": "WARNS",
    "guideline": "REFERENCES",
}


class DetectSignalsOperator(StageOperator):
    """Detect signals in each chunk and create signal nodes + edges."""

    name = "detect_signals"

    def __init__(
        self,
        signal_types: Optional[List[str]] = None,
        significance_threshold: float = 0.3,
    ):
        self.signal_types = signal_types
        self.significance_threshold = significance_threshold

    def process(self, chunks: List["Chunk"], graph_ctx: "GraphContext") -> List["Chunk"]:
        for chunk in chunks:
            score = significance_score(chunk.content)
            chunk.metadata["significance"] = score

            if score < self.significance_threshold:
                continue

            signals = detect_signals(chunk.content, types=self.signal_types)
            if not signals:
                continue

            chunk.metadata["signals"] = [
                {"type": s.type, "text": s.text, "confidence": s.confidence}
                for s in signals
            ]

            signal_node_ids: List[str] = []
            turn_node_id = chunk.metadata.get("turn_node_id", "")

            for sig in signals:
                label = _SIGNAL_LABEL.get(sig.type, "Signal")
                node_id = graph_ctx.add_node(label, {
                    "name": sig.text[:80],
                    "signal_type": sig.type,
                    "confidence": sig.confidence,
                    "source_chunk": chunk.index,
                })
                signal_node_ids.append(node_id)

                if turn_node_id:
                    edge_label = _SIGNAL_EDGE.get(sig.type, "HAS_SIGNAL")
                    graph_ctx.add_edge(turn_node_id, node_id, edge_label)

            chunk.metadata["signal_node_ids"] = signal_node_ids

        return chunks
