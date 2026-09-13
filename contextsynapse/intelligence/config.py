"""Intelligence Engine configuration — autonomy levels and tuning knobs."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict


class AutonomyLevel(Enum):
    AUTO = "auto"
    SUGGEST = "suggest"
    OFF = "off"


@dataclass
class IntelligenceConfig:
    """Per-session or global intelligence configuration."""

    # Module autonomy levels (default: suggest)
    source_watcher: str = "suggest"
    feedback_loop: str = "suggest"
    conflict_detector: str = "suggest"
    context_radar: str = "suggest"

    # Source Watcher
    watch_interval_seconds: int = 3600  # 1 hour
    max_versions: int = 10

    # Feedback Loop
    feedback_weights: Dict[str, float] = field(default_factory=lambda: {
        "helpful": 0.1, "critical": 0.1,
        "irrelevant": -0.05, "misleading": -0.15, "outdated": -0.15,
    })
    usefulness_decay_half_life_days: int = 28
    auto_archive_threshold: float = 0.2
    human_weight_multiplier: float = 2.0

    # Conflict Detector
    conflict_similarity_low: float = 0.6
    conflict_similarity_high: float = 0.9
    semantic_conflict_max_calls_per_hour: int = 20
    semantic_conflict_max_calls_per_day: int = 100

    # Context Radar
    radar_relevance_threshold: float = 0.7
    radar_max_suggestions_per_agent: int = 5

    @classmethod
    def from_env(cls) -> "IntelligenceConfig":
        """Load config from environment variables."""
        cfg = cls()
        default = os.getenv("CONTEXTSYNAPSE_INTELLIGENCE_DEFAULT") or os.getenv("AICONTEXTDB_INTELLIGENCE_DEFAULT", "suggest")
        for mod in ("source_watcher", "feedback_loop", "conflict_detector", "context_radar"):
            setattr(cfg, mod, os.getenv(f"AICONTEXTDB_INTELLIGENCE_{mod.upper()}", default))
        return cfg
