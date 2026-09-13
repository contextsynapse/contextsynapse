"""HallucinationTracer — root-cause analysis for incorrect agent outputs."""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from .events import CognitionEventStream
from .derivation import DerivationTracker

logger = logging.getLogger(__name__)


class HallucinationTracer:
    """Trace hallucinations back to their root-cause context."""

    def __init__(self, stream: CognitionEventStream, derivation: DerivationTracker):
        self._stream = stream
        self._derivation = derivation

    def trace(
        self,
        output_node_id: str,
        node_status_fn: Optional[Callable[[str], Dict]] = None,
    ) -> Dict[str, Any]:
        """Trace a flagged output back to its root-cause context.

        Args:
            output_node_id: The node flagged as hallucinated/incorrect.
            node_status_fn: Function that returns {invalidated, quality, valid_to}
                           for a given node_id. If None, all sources are unknown.

        Returns dict with root_causes, affected_downstream, recommendations.
        """
        # Find what this output was derived from
        sources = self._derivation.get_lineage(output_node_id)

        if not sources:
            return {
                "output_node_id": output_node_id,
                "root_causes": [],
                "affected_downstream": [],
                "recommendations": ["No derivation lineage found — cannot trace root cause."],
            }

        # Analyze each source
        root_causes = []
        for source_id in sources:
            status = node_status_fn(source_id) if node_status_fn else {}

            confidence = 0.0
            reason = ""

            if status.get("invalidated"):
                confidence = 0.9
                reason = "Source was invalidated"
            elif status.get("quality", 1.0) < 0.4:
                confidence = 0.6
                reason = f"Source has low quality ({status.get('quality', '?')})"
            elif status.get("valid_to"):
                confidence = 0.7
                reason = "Source was superseded before consumption"

            if confidence > 0:
                root_causes.append({
                    "node_id": source_id,
                    "confidence": confidence,
                    "reason": reason,
                    "recommendation": "Invalidate derived content and re-run agent"
                        if confidence > 0.7 else "Review source manually",
                })

        # Sort by confidence
        root_causes.sort(key=lambda x: x["confidence"], reverse=True)

        # Find downstream affected
        affected = self._derivation.get_derived_from(output_node_id)

        recommendations = []
        if root_causes:
            recommendations.append("Invalidate the flagged output and its downstream derivations")
            recommendations.append("Notify agents who consumed the affected context")
        else:
            recommendations.append("No obvious root cause — may be an LLM reasoning error, not a context error")

        return {
            "output_node_id": output_node_id,
            "root_causes": root_causes,
            "affected_downstream": affected,
            "recommendations": recommendations,
        }
