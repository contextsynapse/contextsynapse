"""TrustEngine — auto-adjust trust score from quality signals."""
from __future__ import annotations
import logging
from typing import Dict

logger = logging.getLogger(__name__)

_ADJUSTMENTS = {
    "feedback_useful": 0.02, "feedback_misleading": -0.08,
    "feedback_incorrect": -0.15, "feedback_outdated": -0.05,
    "content_invalidated": -0.10, "anomaly_detected": -0.20,
    "anomaly_cleared": 0.05, "consistency_bonus": 0.01,
}
_DEFAULT_SCORE = 0.5

class TrustEngine:
    def __init__(self, redis_client=None):
        self._redis = redis_client
        self._scores: Dict[str, float] = {}
        self._prefix = "contextcore:shield:trust"

    def get_score(self, agent_id: str) -> float:
        if agent_id in self._scores:
            return self._scores[agent_id]
        if self._redis:
            try:
                val = self._redis.hget(f"{self._prefix}:{agent_id}", "score")
                if val is not None:
                    score = float(val.decode() if isinstance(val, bytes) else val)
                    self._scores[agent_id] = score
                    return score
            except Exception:
                pass
        self._scores[agent_id] = _DEFAULT_SCORE
        return _DEFAULT_SCORE

    def on_signal(self, agent_id: str, signal: str):
        delta = _ADJUSTMENTS.get(signal, 0.0)
        if delta == 0.0:
            return
        current = self.get_score(agent_id)
        if delta > 0:
            delta *= (1.0 - current)
        new_score = max(0.0, min(1.0, current + delta))
        self._set_score(agent_id, new_score)
        old_level = self.score_to_level(current)
        new_level = self.score_to_level(new_score)
        if old_level != new_level:
            logger.info("[SHIELD] Agent %s trust: %s -> %s (%.3f, %s)", agent_id[:12], old_level, new_level, new_score, signal)

    def on_consistency(self, agent_id: str, consecutive_clean: int):
        if consecutive_clean > 0 and consecutive_clean % 10 == 0:
            self.on_signal(agent_id, "consistency_bonus")

    @staticmethod
    def score_to_level(score: float) -> str:
        if score >= 0.8: return "trusted"
        if score >= 0.5: return "verified"
        if score >= 0.2: return "provisional"
        return "untrusted"

    def _set_score(self, agent_id: str, score: float):
        score = round(score, 4)
        self._scores[agent_id] = score
        if self._redis:
            try:
                self._redis.hset(f"{self._prefix}:{agent_id}", mapping={"score": str(score), "level": self.score_to_level(score)})
            except Exception:
                pass
