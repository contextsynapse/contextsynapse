"""Tests for Context Intelligence Engine modules."""
import asyncio
import math
import time

import pytest


def _raise_value_error(p):
    """Helper for error isolation tests."""
    raise ValueError("boom")


# ── Task 1: Event Bus & Config ──────────────────────────────────────

class TestEventBus:
    def test_local_event_bus_subscribe_and_publish(self):
        from contextcore.intelligence.event_bus import LocalEventBus
        bus = LocalEventBus()
        received = []
        bus.subscribe("test_event", lambda payload: received.append(payload))
        asyncio.run(bus.publish("test_event", {"key": "value"}))
        assert len(received) == 1
        assert received[0]["key"] == "value"

    def test_local_event_bus_unsubscribe(self):
        from contextcore.intelligence.event_bus import LocalEventBus
        bus = LocalEventBus()
        received = []
        sub_id = bus.subscribe("test_event", lambda p: received.append(p))
        bus.unsubscribe(sub_id)
        asyncio.run(bus.publish("test_event", {"key": "value"}))
        assert len(received) == 0

    def test_local_event_bus_error_isolation(self):
        from contextcore.intelligence.event_bus import LocalEventBus
        bus = LocalEventBus()
        received = []
        bus.subscribe("test_event", _raise_value_error)
        bus.subscribe("test_event", lambda p: received.append(p))
        asyncio.run(bus.publish("test_event", {"ok": True}))
        # Second subscriber still receives despite first raising
        assert len(received) == 1

    def test_local_event_bus_dead_letter(self):
        from contextcore.intelligence.event_bus import LocalEventBus
        bus = LocalEventBus()
        bus.subscribe("test_event", _raise_value_error)
        asyncio.run(bus.publish("test_event", {"data": 1}))
        assert len(bus.dead_letters) == 1


class TestAutonomyConfig:
    def test_autonomy_levels(self):
        from contextcore.intelligence.config import AutonomyLevel
        assert AutonomyLevel.AUTO.value == "auto"
        assert AutonomyLevel.SUGGEST.value == "suggest"
        assert AutonomyLevel.OFF.value == "off"

    def test_intelligence_config_defaults(self):
        from contextcore.intelligence.config import IntelligenceConfig
        cfg = IntelligenceConfig()
        assert cfg.source_watcher == "suggest"
        assert cfg.feedback_loop == "suggest"
        assert cfg.conflict_detector == "suggest"
        assert cfg.context_radar == "suggest"
        assert cfg.conflict_similarity_low == 0.6
        assert cfg.conflict_similarity_high == 0.9
        assert cfg.semantic_conflict_max_calls_per_hour == 20
        assert cfg.max_versions == 10


# ── Task 2: Source Watcher ──────────────────────────────────────────

class TestSourceWatcher:
    def test_build_source_metadata_url(self):
        from contextcore.intelligence.source_watcher import build_source_metadata
        meta = build_source_metadata("url", "https://example.com/docs", "sha256:abc")
        assert meta["type"] == "url"
        assert meta["uri"] == "https://example.com/docs"
        assert meta["content_hash"] == "sha256:abc"
        assert meta["refresh_interval"] == 3600
        assert meta["human_override"] is False

    def test_build_source_metadata_manual(self):
        from contextcore.intelligence.source_watcher import build_source_metadata
        meta = build_source_metadata("manual", None, None)
        assert meta["type"] == "manual"
        assert meta["refresh_interval"] == 0

    def test_diff_check_unchanged(self):
        from contextcore.intelligence.source_watcher import compute_content_hash
        content = "Hello world"
        h1 = compute_content_hash(content)
        h2 = compute_content_hash(content)
        assert h1 == h2

    def test_diff_check_changed(self):
        from contextcore.intelligence.source_watcher import compute_content_hash
        h1 = compute_content_hash("version 1")
        h2 = compute_content_hash("version 2")
        assert h1 != h2

    def test_should_refresh(self):
        from contextcore.intelligence.source_watcher import should_refresh
        source = {
            "type": "url",
            "last_fetched": time.time() - 7200,
            "refresh_interval": 3600,
            "human_override": False,
        }
        assert should_refresh(source) is True

    def test_should_not_refresh_human_override(self):
        from contextcore.intelligence.source_watcher import should_refresh
        source = {
            "type": "url",
            "last_fetched": time.time() - 7200,
            "refresh_interval": 3600,
            "human_override": True,
        }
        assert should_refresh(source) is False

    def test_prune_snapshots(self):
        from contextcore.intelligence.source_watcher import prune_snapshot_chain
        chain = [f"snap_{i}" for i in range(15)]
        to_archive = prune_snapshot_chain(chain, max_versions=10)
        assert len(to_archive) == 5
        assert to_archive == [f"snap_{i}" for i in range(5)]


# ── Task 3: Feedback Loop ──────────────────────────────────────────

class TestFeedbackLoop:
    def test_apply_signal_helpful(self):
        from contextcore.intelligence.feedback_loop import apply_signal
        from contextcore.intelligence.config import IntelligenceConfig
        score = apply_signal(0.5, "helpful", IntelligenceConfig())
        assert score == pytest.approx(0.6, abs=0.01)

    def test_apply_signal_misleading(self):
        from contextcore.intelligence.feedback_loop import apply_signal
        from contextcore.intelligence.config import IntelligenceConfig
        score = apply_signal(0.5, "misleading", IntelligenceConfig())
        assert score == pytest.approx(0.35, abs=0.01)

    def test_apply_signal_capped_at_bounds(self):
        from contextcore.intelligence.feedback_loop import apply_signal
        from contextcore.intelligence.config import IntelligenceConfig
        assert apply_signal(0.95, "helpful", IntelligenceConfig()) <= 1.0
        assert apply_signal(0.05, "misleading", IntelligenceConfig()) >= 0.0

    def test_apply_signal_human_weighted(self):
        from contextcore.intelligence.feedback_loop import apply_signal
        from contextcore.intelligence.config import IntelligenceConfig
        cfg = IntelligenceConfig()
        agent_score = apply_signal(0.5, "helpful", cfg, is_human=False)
        human_score = apply_signal(0.5, "helpful", cfg, is_human=True)
        assert human_score > agent_score

    def test_decay_score(self):
        from contextcore.intelligence.feedback_loop import decay_score
        decayed = decay_score(1.0, days_elapsed=28, half_life_days=28)
        assert decayed == pytest.approx(0.75, abs=0.01)

    def test_decay_score_zero_days(self):
        from contextcore.intelligence.feedback_loop import decay_score
        assert decay_score(0.8, days_elapsed=0, half_life_days=28) == pytest.approx(0.8, abs=0.01)

    def test_feedback_record(self):
        from contextcore.intelligence.feedback_loop import FeedbackRecord
        rec = FeedbackRecord(
            node_id="n1", signal="helpful", source="agent:a1",
            task_id="t1", comment="good stuff"
        )
        assert rec.is_human is False

    def test_feedback_record_human(self):
        from contextcore.intelligence.feedback_loop import FeedbackRecord
        rec = FeedbackRecord(node_id="n1", signal="misleading", source="human:user@test.com")
        assert rec.is_human is True


# ── Task 4: Conflict Detector ──────────────────────────────────────

class TestConflictDetector:
    def test_is_conflict_candidate_in_band(self):
        from contextcore.intelligence.conflict_detector import is_conflict_candidate
        from contextcore.intelligence.config import IntelligenceConfig
        cfg = IntelligenceConfig()
        assert is_conflict_candidate(0.75, cfg) is True

    def test_is_conflict_candidate_too_similar(self):
        from contextcore.intelligence.conflict_detector import is_conflict_candidate
        from contextcore.intelligence.config import IntelligenceConfig
        cfg = IntelligenceConfig()
        assert is_conflict_candidate(0.95, cfg) is False

    def test_is_conflict_candidate_too_different(self):
        from contextcore.intelligence.conflict_detector import is_conflict_candidate
        from contextcore.intelligence.config import IntelligenceConfig
        cfg = IntelligenceConfig()
        assert is_conflict_candidate(0.3, cfg) is False

    def test_version_conflict_detection(self):
        from contextcore.intelligence.conflict_detector import detect_version_conflict
        old = "The API uses Basic Auth for authentication."
        new = "The API uses OAuth2 for authentication."
        result = detect_version_conflict(old, new)
        assert result["is_conflict"] is True
        assert result["change_ratio"] > 0.05

    def test_version_conflict_whitespace_only(self):
        from contextcore.intelligence.conflict_detector import detect_version_conflict
        old = "Hello world"
        new = "Hello  world"
        result = detect_version_conflict(old, new)
        assert result["is_conflict"] is False

    def test_conflict_record(self):
        from contextcore.intelligence.conflict_detector import ConflictRecord
        c = ConflictRecord(
            node_a_id="n1", node_b_id="n2",
            conflict_type="version_drift",
            summary="Auth method changed",
            severity="high",
        )
        assert c.status == "open"
        assert c.conflict_type == "version_drift"

    def test_classify_severity(self):
        from contextcore.intelligence.conflict_detector import classify_severity
        assert classify_severity(0.5) == "high"
        assert classify_severity(0.75) == "medium"
        assert classify_severity(0.85) == "low"


# ── Task 5: Context Radar ──────────────────────────────────────────

class TestContextRadar:
    def test_suggestion_record(self):
        from contextcore.intelligence.context_radar import Suggestion
        s = Suggestion(
            agent_id="agent_1", trigger="source_changed",
            message="API docs updated", node_ids=["n1"],
        )
        assert s.status == "pending"
        assert s.trigger == "source_changed"

    def test_track_agent_activity(self):
        from contextcore.intelligence.context_radar import ContextRadar
        from contextcore.intelligence.config import IntelligenceConfig
        from contextcore.intelligence.event_bus import LocalEventBus
        radar = ContextRadar(IntelligenceConfig(), LocalEventBus())
        radar.track_activity("agent_1", "session_1", ["auth", "oauth2"])
        ctx = radar.get_agent_context("agent_1")
        assert "auth" in ctx["topics"]
        assert ctx["session_id"] == "session_1"

    def test_should_suggest(self):
        from contextcore.intelligence.context_radar import ContextRadar
        from contextcore.intelligence.config import IntelligenceConfig
        from contextcore.intelligence.event_bus import LocalEventBus
        radar = ContextRadar(IntelligenceConfig(), LocalEventBus())
        radar.track_activity("agent_1", "s1", ["authentication", "api"])
        assert radar._is_relevant_to_agent("agent_1", ["authentication", "security"]) is True

    def test_should_not_suggest_unrelated(self):
        from contextcore.intelligence.context_radar import ContextRadar
        from contextcore.intelligence.config import IntelligenceConfig
        from contextcore.intelligence.event_bus import LocalEventBus
        radar = ContextRadar(IntelligenceConfig(), LocalEventBus())
        radar.track_activity("agent_1", "s1", ["authentication", "api"])
        assert radar._is_relevant_to_agent("agent_1", ["database", "migration"]) is False

    def test_get_suggestions(self):
        from contextcore.intelligence.context_radar import ContextRadar, Suggestion
        from contextcore.intelligence.config import IntelligenceConfig
        from contextcore.intelligence.event_bus import LocalEventBus
        radar = ContextRadar(IntelligenceConfig(), LocalEventBus())
        radar._suggestions["agent_1"] = [
            Suggestion(agent_id="agent_1", trigger="test", message="Test suggestion", node_ids=[]),
        ]
        results = radar.get_suggestions("agent_1")
        assert len(results) == 1
        assert results[0].status == "delivered"

    def test_max_suggestions_per_agent(self):
        from contextcore.intelligence.context_radar import ContextRadar, Suggestion
        from contextcore.intelligence.config import IntelligenceConfig
        from contextcore.intelligence.event_bus import LocalEventBus
        cfg = IntelligenceConfig(radar_max_suggestions_per_agent=2)
        radar = ContextRadar(cfg, LocalEventBus())
        radar._suggestions["agent_1"] = [
            Suggestion(agent_id="agent_1", trigger="t", message=f"Msg {i}", node_ids=[])
            for i in range(5)
        ]
        results = radar.get_suggestions("agent_1")
        assert len(results) == 2


# ── Task 10: Integration Tests ─────────────────────────────────────

class TestIntelligenceIntegration:
    def test_full_event_flow(self):
        """source_changed → conflict_detector → conflict_found → radar notifies."""
        from contextcore.intelligence.event_bus import LocalEventBus
        from contextcore.intelligence.config import IntelligenceConfig
        from contextcore.intelligence.conflict_detector import ConflictDetector
        from contextcore.intelligence.context_radar import ContextRadar

        bus = LocalEventBus()
        cfg = IntelligenceConfig()
        detector = ConflictDetector(cfg, bus)
        radar = ContextRadar(cfg, bus)

        bus.subscribe("source_changed", detector.on_source_changed)
        bus.subscribe("conflict_found", radar.on_conflict_found)

        radar.track_activity("agent_1", "s1", ["auth"], node_ids=["n1"])

        asyncio.run(bus.publish("source_changed", {
            "node_id": "n1",
            "source_uri": "https://docs.example.com/auth",
            "change_type": "modified",
            "old_hash": "sha256:aaa",
            "new_hash": "sha256:bbb",
            "timestamp": 1000,
        }))

        conflicts = detector.list_conflicts(status="open")
        assert len(conflicts) >= 1
        assert conflicts[0].conflict_type == "version_drift"

        suggestions = radar.get_suggestions("agent_1")
        assert len(suggestions) >= 1

    def test_feedback_updates_score(self):
        """Feedback signal correctly updates usefulness score."""
        from contextcore.intelligence.feedback_loop import apply_signal
        from contextcore.intelligence.config import IntelligenceConfig
        cfg = IntelligenceConfig()

        score = 0.5
        score = apply_signal(score, "helpful", cfg)
        assert score > 0.5
        score = apply_signal(score, "misleading", cfg)
        assert score < 0.6
        score = apply_signal(score, "critical", cfg)
        assert score > 0.45

    def test_module_stats(self):
        """get_intelligence_stats returns data from all modules."""
        from contextcore.intelligence import start_intelligence, get_intelligence_stats
        from contextcore.intelligence.config import IntelligenceConfig
        from contextcore.intelligence.event_bus import reset_event_bus

        reset_event_bus()
        cfg = IntelligenceConfig()
        start_intelligence(cfg)
        stats = get_intelligence_stats()
        assert "source_watcher" in stats
        assert "conflict_detector" in stats
        assert "context_radar" in stats
