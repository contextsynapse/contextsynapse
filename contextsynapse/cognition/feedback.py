"""FeedbackProcessor — context quality signals from agents and humans."""
from __future__ import annotations

from typing import Dict, List

from .events import CognitionEvent, CognitionEventStream

_SCORE_ADJUSTMENTS = {
    "useful": 0.05,
    "misleading": -0.15,
    "outdated": -0.1,
    "incorrect": -0.3,
}


class FeedbackProcessor:
    """Process feedback signals and adjust context scores."""

    def __init__(self, stream: CognitionEventStream):
        self._stream = stream

    def record_feedback(
        self, agent_id: str, node_id: str, signal: str,
        reason: str = "", output_id: str = "",
    ):
        """Record feedback about a context node."""
        self._stream.append(CognitionEvent(
            event_type="feedback",
            agent_id=agent_id,
            session_id="",
            node_ids=[node_id],
            metadata={"signal": signal, "reason": reason, "output_id": output_id},
        ))

        # AgentShield: feed trust engine
        try:
            from contextsynapse.shield import get_agent_shield
            get_agent_shield().on_feedback(agent_id, node_id, signal)
        except Exception:
            pass

    def get_feedback(self, node_id: str, limit: int = 50) -> List[Dict]:
        """Get all feedback for a node."""
        events = self._stream.get_events(node_id=node_id, event_type="feedback", limit=limit)
        return [{"signal": e.metadata.get("signal"), "agent_id": e.agent_id,
                 "reason": e.metadata.get("reason", ""), "timestamp": e.timestamp}
                for e in events]

    @staticmethod
    def score_adjustment(signal: str) -> float:
        """How much to adjust quality score for a feedback signal."""
        return _SCORE_ADJUSTMENTS.get(signal, 0.0)
