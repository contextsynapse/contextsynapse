"""Tests for AgentShield — behavioral auth + adaptive trust."""
from __future__ import annotations
import json
import pytest


class TestAgentProfile:
    def test_new_agent_empty_profile(self):
        from contextcore.shield.profile import AgentProfile
        profile = AgentProfile(redis_client=None)
        data = profile.get("agent_new")
        assert data["total_reads"] == 0
        assert data["total_writes"] == 0
        assert data["total_tool_calls"] == 0

    def test_record_read_increments(self):
        from contextcore.shield.profile import AgentProfile
        profile = AgentProfile(redis_client=None)
        profile.record_event("agent_1", "read")
        profile.record_event("agent_1", "read")
        data = profile.get("agent_1")
        assert data["total_reads"] == 2

    def test_record_write_increments(self):
        from contextcore.shield.profile import AgentProfile
        profile = AgentProfile(redis_client=None)
        profile.record_event("agent_1", "write")
        data = profile.get("agent_1")
        assert data["total_writes"] == 1

    def test_tool_distribution(self):
        from contextcore.shield.profile import AgentProfile
        profile = AgentProfile(redis_client=None)
        profile.record_tool_call("agent_1", "search_nodes")
        profile.record_tool_call("agent_1", "search_nodes")
        profile.record_tool_call("agent_1", "add_knowledge")
        data = profile.get("agent_1")
        dist = json.loads(data["tool_distribution"])
        assert dist["search_nodes"] == 2
        assert dist["add_knowledge"] == 1

    def test_read_write_ratio(self):
        from contextcore.shield.profile import AgentProfile
        profile = AgentProfile(redis_client=None)
        for _ in range(7):
            profile.record_event("agent_1", "read")
        for _ in range(3):
            profile.record_event("agent_1", "write")
        data = profile.get("agent_1")
        assert abs(data["read_write_ratio"] - 0.7) < 0.01

    def test_feedback_quality_ratio(self):
        from contextcore.shield.profile import AgentProfile
        profile = AgentProfile(redis_client=None)
        for _ in range(8):
            profile.record_feedback("agent_1", "useful")
        for _ in range(2):
            profile.record_feedback("agent_1", "incorrect")
        data = profile.get("agent_1")
        assert abs(data["feedback_quality"] - 0.8) < 0.01

    def test_hour_distribution(self):
        from contextcore.shield.profile import AgentProfile
        profile = AgentProfile(redis_client=None)
        profile.record_tool_call("agent_1", "search_nodes")
        data = profile.get("agent_1")
        hours = json.loads(data["hour_distribution"])
        assert sum(hours.values()) == 1


class TestAnomalyDetector:
    def _build_profile(self, **overrides):
        base = {
            "total_reads": 100, "total_writes": 50, "total_tool_calls": 150,
            "total_feedback_given": 20, "feedback_useful": 18, "feedback_misleading": 1,
            "feedback_incorrect": 1, "feedback_outdated": 0, "invalidations_caused": 0,
            "read_write_ratio": 0.667, "feedback_quality": 0.9,
            "tool_distribution": json.dumps({"search_nodes": 80, "add_knowledge": 50, "orient": 20}),
            "hour_distribution": json.dumps({str(h): 10 for h in range(9, 18)}),
            "sessions_count": 5, "consecutive_clean": 10,
        }
        base.update(overrides)
        return base

    def test_no_anomaly_for_new_agent(self):
        from contextcore.shield.anomaly import AnomalyDetector
        detector = AnomalyDetector()
        result = detector.analyze({"total_tool_calls": 5, "sessions_count": 1}, {})
        assert result.score == 0.0
        assert result.frozen is False

    def test_no_anomaly_for_consistent_agent(self):
        from contextcore.shield.anomaly import AnomalyDetector
        detector = AnomalyDetector()
        profile = self._build_profile()
        session = {"tool_distribution": json.dumps({"search_nodes": 8, "add_knowledge": 5, "orient": 2}),
                   "read_write_ratio": 0.65, "feedback_quality": 0.85}
        result = detector.analyze(profile, session)
        assert result.score < 0.3
        assert result.frozen is False

    def test_tool_pattern_change_detected(self):
        from contextcore.shield.anomaly import AnomalyDetector
        detector = AnomalyDetector()
        profile = self._build_profile()
        session = {"tool_distribution": json.dumps({"ws_run_command": 20}),
                   "read_write_ratio": 0.0, "feedback_quality": 0.5}
        result = detector.analyze(profile, session)
        assert "tool_pattern_change" in result.flags

    def test_quality_drop_detected(self):
        from contextcore.shield.anomaly import AnomalyDetector
        detector = AnomalyDetector()
        profile = self._build_profile(feedback_quality=0.9, total_feedback_given=20)
        session = {"feedback_quality": 0.3}
        result = detector.analyze(profile, session)
        assert "quality_drop" in result.flags

    def test_frozen_threshold(self):
        from contextcore.shield.anomaly import AnomalyDetector
        detector = AnomalyDetector()
        profile = self._build_profile()
        session = {"tool_distribution": json.dumps({"ws_run_command": 20}),
                   "read_write_ratio": 0.0, "feedback_quality": 0.2}
        result = detector.analyze(profile, session)
        assert result.score >= 0.7
        assert result.frozen is True


class TestTrustEngine:
    def test_new_agent_starts_provisional(self):
        from contextcore.shield.trust_engine import TrustEngine
        engine = TrustEngine(redis_client=None)
        assert engine.get_score("new_agent") == 0.5

    def test_useful_feedback_increases_trust(self):
        from contextcore.shield.trust_engine import TrustEngine
        engine = TrustEngine(redis_client=None)
        for _ in range(5):
            engine.on_signal("agent_1", "feedback_useful")
        assert engine.get_score("agent_1") > 0.5

    def test_incorrect_feedback_decreases_trust(self):
        from contextcore.shield.trust_engine import TrustEngine
        engine = TrustEngine(redis_client=None)
        engine.on_signal("agent_1", "feedback_incorrect")
        assert engine.get_score("agent_1") < 0.5

    def test_asymmetric_momentum(self):
        from contextcore.shield.trust_engine import TrustEngine
        engine = TrustEngine(redis_client=None)
        for _ in range(20):
            engine.on_signal("agent_1", "feedback_useful")
        high_score = engine.get_score("agent_1")
        engine.on_signal("agent_1", "feedback_incorrect")
        after = engine.get_score("agent_1")
        assert high_score - after > 0.1

    def test_trust_never_below_zero(self):
        from contextcore.shield.trust_engine import TrustEngine
        engine = TrustEngine(redis_client=None)
        for _ in range(50):
            engine.on_signal("agent_1", "feedback_incorrect")
        assert engine.get_score("agent_1") >= 0.0

    def test_trust_never_above_one(self):
        from contextcore.shield.trust_engine import TrustEngine
        engine = TrustEngine(redis_client=None)
        for _ in range(200):
            engine.on_signal("agent_1", "feedback_useful")
        assert engine.get_score("agent_1") <= 1.0

    def test_score_to_level(self):
        from contextcore.shield.trust_engine import TrustEngine
        assert TrustEngine.score_to_level(0.1) == "untrusted"
        assert TrustEngine.score_to_level(0.3) == "provisional"
        assert TrustEngine.score_to_level(0.6) == "verified"
        assert TrustEngine.score_to_level(0.9) == "trusted"

    def test_consistency_bonus(self):
        from contextcore.shield.trust_engine import TrustEngine
        engine = TrustEngine(redis_client=None)
        base = engine.get_score("agent_1")
        engine.on_consistency("agent_1", consecutive_clean=10)
        assert engine.get_score("agent_1") > base


class TestAdaptivePermissions:
    def test_untrusted_read_only(self):
        from contextcore.shield.permissions import AdaptivePermissions
        perms = AdaptivePermissions()
        assert perms.check("a1", "search_nodes", trust_score=0.1).allowed is True
        assert perms.check("a1", "add_knowledge", trust_score=0.1).allowed is False

    def test_provisional_read_write(self):
        from contextcore.shield.permissions import AdaptivePermissions
        perms = AdaptivePermissions()
        assert perms.check("a1", "add_knowledge", trust_score=0.3).allowed is True
        assert perms.check("a1", "dispatch_goal", trust_score=0.3).allowed is False

    def test_verified_full_access(self):
        from contextcore.shield.permissions import AdaptivePermissions
        perms = AdaptivePermissions()
        assert perms.check("a1", "dispatch_goal", trust_score=0.6).allowed is True

    def test_trusted_all_access(self):
        from contextcore.shield.permissions import AdaptivePermissions
        perms = AdaptivePermissions()
        assert perms.check("a1", "ws_run_command", trust_score=0.9).allowed is True

    def test_frozen_overrides_trust(self):
        from contextcore.shield.permissions import AdaptivePermissions
        perms = AdaptivePermissions()
        perms.freeze("a1")
        assert perms.check("a1", "add_knowledge", trust_score=0.9).allowed is False
        assert perms.check("a1", "search_nodes", trust_score=0.9).allowed is True

    def test_unfreeze_restores(self):
        from contextcore.shield.permissions import AdaptivePermissions
        perms = AdaptivePermissions()
        perms.freeze("a1")
        perms.unfreeze("a1")
        assert perms.check("a1", "add_knowledge", trust_score=0.3).allowed is True


class TestAgentShield:
    def test_full_lifecycle(self):
        from contextcore.shield.shield import AgentShield
        shield = AgentShield(redis_client=None)
        assert shield.get_trust_score("agent_1") == 0.5
        for _ in range(5):
            shield.on_tool_call("agent_1", "search_nodes", "context", True)
        profile = shield.get_profile("agent_1")
        assert profile["total_tool_calls"] == 5
        shield.on_feedback("agent_1", "node_1", "useful")
        assert shield.get_trust_score("agent_1") > 0.5
        decision = shield.check_permission("agent_1", "add_knowledge")
        assert decision.allowed is True

    def test_incorrect_feedback_demotes(self):
        from contextcore.shield.shield import AgentShield
        shield = AgentShield(redis_client=None)
        for _ in range(5):
            shield.on_feedback("agent_1", "node_x", "incorrect")
        score = shield.get_trust_score("agent_1")
        assert score < 0.2
        decision = shield.check_permission("agent_1", "add_knowledge")
        assert decision.allowed is False


class TestShieldIntegration:
    def test_feedback_flows_to_trust(self):
        from contextcore.shield.shield import AgentShield
        shield = AgentShield(redis_client=None)
        initial = shield.get_trust_score("agent_test")
        shield.on_feedback("agent_test", "node_1", "useful")
        assert shield.get_trust_score("agent_test") > initial

    def test_invalidation_penalizes_author(self):
        from contextcore.shield.shield import AgentShield
        shield = AgentShield(redis_client=None)
        initial = shield.get_trust_score("agent_bad")
        shield.on_invalidation_for_agent("agent_bad", count=2)
        assert shield.get_trust_score("agent_bad") < initial

    def test_permission_denied_at_low_trust(self):
        from contextcore.shield.shield import AgentShield
        shield = AgentShield(redis_client=None)
        for _ in range(5):
            shield.on_feedback("agent_x", "n1", "incorrect")
        decision = shield.check_permission("agent_x", "dispatch_goal")
        assert decision.allowed is False
        # But read still works
        decision = shield.check_permission("agent_x", "search_nodes")
        assert decision.allowed is True


class TestGracePeriod:
    def test_active_agent_not_blocked(self):
        """Mid-task agent is never blocked, even if frozen."""
        from contextcore.shield.permissions import AdaptivePermissions
        perms = AdaptivePermissions()
        perms.mark_active("agent_1", "task_123")
        perms.freeze("agent_1")
        # Still allowed — mid-task
        result = perms.check("agent_1", "add_knowledge", trust_score=0.9)
        assert result.allowed is True
        assert "WARNING" in result.reason

    def test_idle_agent_blocked_after_task(self):
        """After task completes, restrictions apply."""
        from contextcore.shield.permissions import AdaptivePermissions
        perms = AdaptivePermissions()
        perms.mark_active("agent_1", "task_123")
        perms.freeze("agent_1")
        perms.mark_idle("agent_1")
        # Now frozen applies
        result = perms.check("agent_1", "add_knowledge", trust_score=0.9)
        assert result.allowed is False

    def test_unfreeze_clears_pending(self):
        """Unfreezing clears pending restrictions."""
        from contextcore.shield.permissions import AdaptivePermissions
        perms = AdaptivePermissions()
        perms.freeze("agent_1")
        perms.unfreeze("agent_1")
        result = perms.check("agent_1", "add_knowledge", trust_score=0.3)
        assert result.allowed is True


class TestNotifications:
    def test_trust_change_emits_notification(self):
        from contextcore.shield.shield import AgentShield
        shield = AgentShield(redis_client=None)
        # Drop trust enough to change level
        for _ in range(5):
            shield.on_feedback("agent_n", "node_1", "incorrect")
        notifications = shield.get_notifications()
        trust_changes = [n for n in notifications if n["event_type"] == "trust_change"]
        assert len(trust_changes) >= 1
        assert trust_changes[0]["agent_id"] == "agent_n"

    def test_notifications_capped(self):
        from contextcore.shield.shield import AgentShield
        shield = AgentShield(redis_client=None)
        shield._max_notifications = 5
        for i in range(10):
            shield._notify(f"agent_{i}", "test", {})
        assert len(shield.get_notifications()) == 5
