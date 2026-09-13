"""Tests for Impact Tracker — predicted vs actual impact for weight learning."""
import pytest


class TestPredictionRecording:

    def test_record_prediction_and_outcome(self):
        from contextcore.intelligence.impact_tracker import ImpactTracker

        tracker = ImpactTracker()
        tracker.record_prediction("sig1", "TSMC", "Apple", 0.63, "sentiment_reversal", "supplies_to")

        result = tracker.record_outcome("sig1", "Apple", actual_impact=0.45,
                                         metric="stock_price_change_pct", observed_value=-2.3)

        assert result is not None
        assert result["predicted"] == 0.63
        assert result["actual"] == 0.45
        assert abs(result["error"] - 0.18) < 0.01

    def test_outcome_without_prediction_returns_none(self):
        from contextcore.intelligence.impact_tracker import ImpactTracker

        tracker = ImpactTracker()
        result = tracker.record_outcome("nonexistent", "Apple", actual_impact=0.5)
        assert result is None

    def test_wrong_target_returns_none(self):
        from contextcore.intelligence.impact_tracker import ImpactTracker

        tracker = ImpactTracker()
        tracker.record_prediction("sig1", "TSMC", "Apple", 0.63)
        result = tracker.record_outcome("sig1", "NVIDIA", actual_impact=0.5)
        assert result is None


class TestWeightAdjustment:

    def test_insufficient_samples_maintains_weight(self):
        from contextcore.intelligence.impact_tracker import ImpactTracker

        tracker = ImpactTracker(min_samples=5)
        # Only 2 samples — not enough
        tracker.record_prediction("s1", "A", "B", 0.8)
        tracker.record_outcome("s1", "B", 0.5)
        tracker.record_prediction("s2", "A", "B", 0.8)
        tracker.record_outcome("s2", "B", 0.4)

        adj = tracker.recommend_weight_adjustment("A", "B", current_weight=0.8)
        assert adj.direction == "maintain"
        assert adj.confidence == 0.0

    def test_overprediction_recommends_decrease(self):
        from contextcore.intelligence.impact_tracker import ImpactTracker

        tracker = ImpactTracker(min_samples=3, learning_rate=0.3)

        # Predict 0.8 but actual is consistently ~0.3
        for i in range(5):
            tracker.record_prediction(f"s{i}", "A", "B", 0.8)
            tracker.record_outcome(f"s{i}", "B", 0.3)

        adj = tracker.recommend_weight_adjustment("A", "B", current_weight=0.8)
        assert adj.direction == "decrease"
        assert adj.recommended_weight < 0.8
        assert adj.sample_count == 5
        assert adj.avg_prediction_error > 0.3

    def test_underprediction_recommends_increase(self):
        from contextcore.intelligence.impact_tracker import ImpactTracker

        tracker = ImpactTracker(min_samples=3, learning_rate=0.3)

        # Predict 0.3 but actual is consistently ~0.8
        for i in range(5):
            tracker.record_prediction(f"s{i}", "A", "B", 0.3)
            tracker.record_outcome(f"s{i}", "B", 0.8)

        adj = tracker.recommend_weight_adjustment("A", "B", current_weight=0.3)
        assert adj.direction == "increase"
        assert adj.recommended_weight > 0.3

    def test_accurate_prediction_maintains(self):
        from contextcore.intelligence.impact_tracker import ImpactTracker

        tracker = ImpactTracker(min_samples=3, learning_rate=0.1)

        # Predict 0.5, actual is ~0.5
        for i in range(5):
            tracker.record_prediction(f"s{i}", "A", "B", 0.5)
            tracker.record_outcome(f"s{i}", "B", 0.48 + (i * 0.01))

        adj = tracker.recommend_weight_adjustment("A", "B", current_weight=0.5)
        assert adj.direction == "maintain"


class TestApplyAdjustments:

    def test_apply_to_hierarchy(self):
        from contextcore.intelligence.impact_tracker import ImpactTracker
        from contextcore.intelligence.signal_hierarchy import SignalHierarchy, ContextLink

        h = SignalHierarchy()
        h.add_link(ContextLink(upstream="A", downstream="B", weight=0.8))

        tracker = ImpactTracker(min_samples=3, learning_rate=0.3)
        for i in range(6):
            tracker.record_prediction(f"s{i}", "A", "B", 0.8)
            tracker.record_outcome(f"s{i}", "B", 0.3)

        applied = tracker.apply_adjustments(h, min_confidence=0.1)

        assert len(applied) >= 1
        assert applied[0]["direction"] == "decrease"
        # Link weight should have been updated
        assert h._downstream_map["A"][0].weight < 0.8


class TestTrainingDataExport:

    def test_export_training_data(self):
        from contextcore.intelligence.impact_tracker import ImpactTracker

        tracker = ImpactTracker()
        tracker.record_prediction("s1", "TSMC", "Apple", 0.63, "sentiment_reversal", "supplies_to")
        tracker.record_outcome("s1", "Apple", 0.45, "stock_price_change_pct", -2.3)

        data = tracker.get_training_data()
        assert len(data) == 1
        assert data[0]["state"]["source"] == "TSMC"
        assert data[0]["state"]["target"] == "Apple"
        assert data[0]["action"] == 0.63
        assert data[0]["reward"] == -abs(0.63 - 0.45)
        assert data[0]["actual_impact"] == 0.45
        assert data[0]["metric"] == "stock_price_change_pct"

    def test_no_outcome_excluded_from_training(self):
        from contextcore.intelligence.impact_tracker import ImpactTracker

        tracker = ImpactTracker()
        tracker.record_prediction("s1", "A", "B", 0.5)
        # No outcome recorded

        data = tracker.get_training_data()
        assert len(data) == 0

    def test_link_stats(self):
        from contextcore.intelligence.impact_tracker import ImpactTracker

        tracker = ImpactTracker()
        for i in range(3):
            tracker.record_prediction(f"s{i}", "A", "B", 0.7)
            tracker.record_outcome(f"s{i}", "B", 0.5)

        stats = tracker.get_link_stats()
        assert "A:B" in stats
        assert stats["A:B"]["sample_count"] == 3
        assert abs(stats["A:B"]["avg_error"] - 0.2) < 0.01
        assert abs(stats["A:B"]["avg_actual_impact"] - 0.5) < 0.01
