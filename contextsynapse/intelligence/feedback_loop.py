"""Feedback Loop — collect agent/human signals, compute usefulness scores."""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .config import IntelligenceConfig

logger = logging.getLogger(__name__)

VALID_SIGNALS = {"helpful", "critical", "irrelevant", "misleading", "outdated"}


@dataclass
class FeedbackRecord:
    """One feedback signal from an agent or human."""
    node_id: str
    signal: str
    source: str             # "agent:<id>" or "human:<email>"
    task_id: Optional[str] = None
    comment: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    @property
    def is_human(self) -> bool:
        return self.source.startswith("human:")


def apply_signal(
    current_score: float,
    signal: str,
    config: IntelligenceConfig,
    is_human: bool = False,
) -> float:
    """Apply a feedback signal to a usefulness score. Returns new score."""
    delta = config.feedback_weights.get(signal, 0.0)
    if is_human:
        delta *= config.human_weight_multiplier
    new_score = current_score + delta
    return max(0.0, min(1.0, new_score))


def decay_score(
    stored_score: float,
    days_elapsed: float,
    half_life_days: int = 28,
) -> float:
    """On-read decay: score drifts toward 0.5 over time."""
    if days_elapsed <= 0 or half_life_days <= 0:
        return stored_score
    decay_factor = math.pow(0.5, days_elapsed / half_life_days)
    return 0.5 + (stored_score - 0.5) * decay_factor


class FeedbackLoop:
    """Manages feedback collection and usefulness scoring."""

    def __init__(self, config: IntelligenceConfig, event_bus):
        self._config = config
        self._bus = event_bus
        self._total_signals = 0
        self._signal_counts: Dict[str, int] = {}
        self._auto_archived = 0

    async def record_feedback(self, record: FeedbackRecord) -> Dict[str, Any]:
        """Record a feedback signal and update usefulness score."""
        if record.signal not in VALID_SIGNALS:
            return {"error": f"Invalid signal: {record.signal}. Valid: {VALID_SIGNALS}"}

        self._total_signals += 1
        self._signal_counts[record.signal] = self._signal_counts.get(record.signal, 0) + 1

        await self._bus.publish("feedback_received", {
            "node_id": record.node_id,
            "signal": record.signal,
            "source": record.source,
            "task_id": record.task_id,
            "timestamp": record.timestamp,
        })

        return {
            "node_id": record.node_id,
            "signal": record.signal,
            "recorded": True,
        }

    def get_decayed_score(self, stored_score: float, last_updated: float) -> float:
        """Get current usefulness score with on-read decay applied."""
        days = (time.time() - last_updated) / 86400
        return decay_score(stored_score, days, self._config.usefulness_decay_half_life_days)

    def get_stats(self) -> Dict[str, Any]:
        """Return feedback stats for dashboard."""
        return {
            "total_signals": self._total_signals,
            "signal_counts": dict(self._signal_counts),
            "auto_archived": self._auto_archived,
        }
