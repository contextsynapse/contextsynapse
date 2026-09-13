"""AnomalyDetector — statistical anomaly detection for agent behavior."""
from __future__ import annotations
import json, math
from dataclasses import dataclass, field
from typing import Any, Dict, List

@dataclass
class AnomalyResult:
    score: float = 0.0
    flags: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)
    frozen: bool = False

class AnomalyDetector:
    MIN_TOOL_CALLS = 20
    MIN_SESSIONS = 2

    def analyze(self, profile: Dict, session: Dict) -> AnomalyResult:
        if profile.get("total_tool_calls", 0) < self.MIN_TOOL_CALLS:
            return AnomalyResult()
        if profile.get("sessions_count", 0) < self.MIN_SESSIONS:
            return AnomalyResult()
        flags = []
        details = {}
        hist_dist = json.loads(profile.get("tool_distribution", "{}"))
        sess_dist = json.loads(session.get("tool_distribution", "{}"))
        if hist_dist and sess_dist:
            divergence = self._cosine_distance(hist_dist, sess_dist)
            if divergence > 0.6:
                flags.append("tool_pattern_change")
                details["tool_divergence"] = round(divergence, 3)
        hist_quality = profile.get("feedback_quality", 0.5)
        sess_quality = session.get("feedback_quality")
        if sess_quality is not None and profile.get("total_feedback_given", 0) > 10:
            if sess_quality < hist_quality - 0.3:
                flags.append("quality_drop")
                details["quality_delta"] = round(sess_quality - hist_quality, 3)
        hist_ratio = profile.get("read_write_ratio", 0.5)
        sess_ratio = session.get("read_write_ratio")
        if sess_ratio is not None:
            if abs(sess_ratio - hist_ratio) > 0.4:
                flags.append("unusual_read_scope")
                details["ratio_delta"] = round(sess_ratio - hist_ratio, 3)
        weights = {"tool_pattern_change": 0.35, "quality_drop": 0.30, "unusual_read_scope": 0.20}
        score = min(sum(weights.get(f, 0.15) for f in flags), 1.0)
        return AnomalyResult(score=round(score, 3), flags=flags, details=details, frozen=score >= 0.7)

    @staticmethod
    def _cosine_distance(a: Dict[str, int], b: Dict[str, int]) -> float:
        all_keys = set(a) | set(b)
        if not all_keys:
            return 0.0
        dot = sum(a.get(k, 0) * b.get(k, 0) for k in all_keys)
        mag_a = math.sqrt(sum(v ** 2 for v in a.values())) or 1.0
        mag_b = math.sqrt(sum(v ** 2 for v in b.values())) or 1.0
        return round(1.0 - dot / (mag_a * mag_b), 4)
