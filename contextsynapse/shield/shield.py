"""AgentShield — facade wiring profile + anomaly + trust + permissions."""
from __future__ import annotations
import logging
from typing import Any, Dict
from .profile import AgentProfile
from .anomaly import AnomalyDetector, AnomalyResult
from .trust_engine import TrustEngine
from .permissions import AdaptivePermissions, PermissionDecision

logger = logging.getLogger(__name__)

class AgentShield:
    def __init__(self, redis_client=None):
        self.profile = AgentProfile(redis_client=redis_client)
        self.anomaly = AnomalyDetector()
        self.trust = TrustEngine(redis_client=redis_client)
        self.permissions = AdaptivePermissions()
        self._notifications: list = []  # recent notifications (ring buffer)
        self._max_notifications = 100
        self._redis = redis_client

    def check_permission(self, agent_id: str, tool_name: str) -> PermissionDecision:
        score = self.trust.get_score(agent_id)
        return self.permissions.check(agent_id, tool_name, trust_score=score)

    def on_tool_call(self, agent_id: str, tool_name: str, category: str, success: bool):
        self.profile.record_tool_call(agent_id, tool_name)
        if success:
            self.profile.increment_clean(agent_id)
            p = self.profile.get(agent_id)
            self.trust.on_consistency(agent_id, p.get("consecutive_clean", 0))

    def on_feedback(self, agent_id: str, node_id: str, signal: str):
        signal_map = {"useful": "feedback_useful", "misleading": "feedback_misleading",
                      "incorrect": "feedback_incorrect", "outdated": "feedback_outdated"}
        trust_signal = signal_map.get(signal)
        if trust_signal:
            old_level = self.get_trust_level(agent_id)
            self.profile.record_feedback(agent_id, signal)
            self.trust.on_signal(agent_id, trust_signal)
            new_level = self.get_trust_level(agent_id)
            if old_level != new_level:
                self._notify(agent_id, "trust_change", {
                    "old_level": old_level, "new_level": new_level,
                    "trigger": f"feedback:{signal}", "score": self.get_trust_score(agent_id),
                })

    def on_invalidation(self, invalidated_node_ids: list, reason: str = ""):
        pass  # wired via on_invalidation_for_agent when called from InvalidationEngine

    def on_invalidation_for_agent(self, agent_id: str, count: int = 1):
        old_level = self.get_trust_level(agent_id)
        self.profile.record_invalidation(agent_id, count)
        for _ in range(count):
            self.trust.on_signal(agent_id, "content_invalidated")
        new_level = self.get_trust_level(agent_id)
        if old_level != new_level:
            self._notify(agent_id, "trust_change", {
                "old_level": old_level, "new_level": new_level,
                "trigger": f"invalidation (x{count})", "score": self.get_trust_score(agent_id),
            })

    def get_trust_score(self, agent_id: str) -> float:
        return self.trust.get_score(agent_id)

    def get_trust_level(self, agent_id: str) -> str:
        return TrustEngine.score_to_level(self.trust.get_score(agent_id))

    def get_profile(self, agent_id: str) -> Dict[str, Any]:
        return self.profile.get(agent_id)

    def get_anomaly(self, agent_id: str, session_data: Dict = None) -> AnomalyResult:
        profile = self.profile.get(agent_id)
        return self.anomaly.analyze(profile, session_data or {})

    def mark_active(self, agent_id: str, task_id: str):
        """Mark agent as mid-workflow."""
        self.permissions.mark_active(agent_id, task_id)

    def mark_idle(self, agent_id: str):
        """Mark agent as idle."""
        self.permissions.mark_idle(agent_id)

    def get_notifications(self, limit: int = 50) -> list:
        """Get recent shield notifications."""
        return self._notifications[-limit:]

    def _notify(self, agent_id: str, event_type: str, details: dict):
        """Emit a shield notification."""
        from datetime import datetime, timezone
        notification = {
            "agent_id": agent_id,
            "event_type": event_type,
            "details": details,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._notifications.append(notification)
        if len(self._notifications) > self._max_notifications:
            self._notifications = self._notifications[-self._max_notifications:]
        # Also publish to Redis for cross-process visibility
        if self._redis:
            try:
                import json
                self._redis.lpush("contextcore:shield:notifications", json.dumps(notification))
                self._redis.ltrim("contextcore:shield:notifications", 0, self._max_notifications - 1)
            except Exception:
                pass
        logger.info("[SHIELD] %s: %s — %s", event_type, agent_id[:12], details)
