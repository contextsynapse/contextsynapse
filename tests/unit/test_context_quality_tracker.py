"""Tests for Context Quality Tracker — measures whether delivered context was useful."""

import time
import pytest
from contextcore.context.quality_tracker import ContextQualityTracker, ContextUsageReport


class TestContextQualityTracker:

    def test_empty_report(self):
        tracker = ContextQualityTracker()
        report = tracker.compute_quality("agent-1")
        assert report.deliveries_count == 0
        assert report.relevance_score == 0.0

    def test_delivery_recorded(self):
        tracker = ContextQualityTracker()
        did = tracker.record_delivery("agent-1", ["n1", "n2", "n3"], token_count=1500,
                                       node_labels=["Fact", "Entity"], total_graph_nodes=100)
        assert did.startswith("del_")
        report = tracker.compute_quality("agent-1")
        assert report.deliveries_count == 1
        assert report.total_tokens_delivered == 1500

    def test_waste_ratio_no_actions(self):
        """If agent receives context but does nothing, waste = 1.0."""
        tracker = ContextQualityTracker()
        tracker.record_delivery("agent-1", ["n1", "n2"], token_count=1000,
                                node_labels=["Fact"], total_graph_nodes=50)
        report = tracker.compute_quality("agent-1")
        assert report.waste_ratio == 1.0

    def test_waste_ratio_with_relevant_action(self):
        """If agent searches for delivered label, waste should decrease."""
        tracker = ContextQualityTracker()
        tracker.record_delivery("agent-1", ["n1", "n2"], token_count=1000,
                                node_labels=["Fact", "Entity"], total_graph_nodes=50)
        tracker.record_agent_action("agent-1", "search_nodes", {"label": "Fact", "query": "AQI"})
        report = tracker.compute_quality("agent-1")
        assert report.waste_ratio < 1.0
        assert report.relevance_score > 0.0

    def test_coverage_score(self):
        """Coverage = delivered_nodes / total_graph_nodes."""
        tracker = ContextQualityTracker()
        tracker.record_delivery("agent-1", ["n1", "n2", "n3", "n4", "n5"],
                                token_count=500, total_graph_nodes=100)
        report = tracker.compute_quality("agent-1")
        assert abs(report.coverage_score - 0.05) < 0.01  # 5/100 = 0.05

    def test_full_coverage(self):
        tracker = ContextQualityTracker()
        tracker.record_delivery("agent-1", [f"n{i}" for i in range(50)],
                                token_count=2000, total_graph_nodes=50)
        report = tracker.compute_quality("agent-1")
        assert report.coverage_score == 1.0

    def test_freshness_high_for_recent(self):
        """Just-delivered context should have high freshness."""
        tracker = ContextQualityTracker(window_seconds=60)
        tracker.record_delivery("agent-1", ["n1"], token_count=100)
        report = tracker.compute_quality("agent-1", window_seconds=60)
        assert report.freshness_score > 0.9

    def test_multiple_agents_independent(self):
        """Each agent's quality is tracked independently."""
        tracker = ContextQualityTracker()
        tracker.record_delivery("a1", ["n1"], token_count=100, total_graph_nodes=10)
        tracker.record_delivery("a2", ["n2", "n3"], token_count=200, total_graph_nodes=10)
        tracker.record_agent_action("a1", "search_nodes", {"query": "test"})

        r1 = tracker.compute_quality("a1")
        r2 = tracker.compute_quality("a2")
        assert r1.actions_count == 1
        assert r2.actions_count == 0
        assert r2.waste_ratio == 1.0

    def test_timeline(self):
        tracker = ContextQualityTracker()
        tracker.record_delivery("a1", ["n1"], token_count=100, total_graph_nodes=10)
        tracker.record_delivery("a2", ["n2"], token_count=200, total_graph_nodes=10)

        timeline = tracker.get_quality_timeline(experiment_id="exp-1")
        assert len(timeline) == 2
        assert all("relevance_score" in t for t in timeline)

    def test_report_to_dict(self):
        report = ContextUsageReport(
            relevance_score=0.75, freshness_score=0.9,
            coverage_score=0.3, waste_ratio=0.25,
            deliveries_count=2, actions_count=5, total_tokens_delivered=3000,
        )
        d = report.to_dict()
        assert d["relevance_score"] == 0.75
        assert d["waste_ratio"] == 0.25
        assert d["total_tokens_delivered"] == 3000
