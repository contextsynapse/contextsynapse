"""Tests for on-ingest signal processors."""
import pytest
from datetime import datetime, timezone, timedelta


def _make_entity(name, sentiment="positive", label="Company"):
    return {
        "label": label,
        "properties": {
            "name": name,
            "_sentiment": sentiment,
            "_sentiment_confidence": 0.8,
            "_created_at": datetime.now(timezone.utc).isoformat(),
        },
    }


class TestSentimentReversalSignal:
    """Detect when entity sentiment flips."""

    def test_no_reversal_on_first_mention(self):
        from contextcore.intelligence.signals import check_sentiment_reversal

        entity = _make_entity("Tesla", sentiment="negative")
        result = check_sentiment_reversal(
            entity_name="Tesla",
            current_sentiment="negative",
            history=[],
            config={"window_hours": 48, "min_mentions": 3},
        )
        assert result is None  # No history — no reversal

    def test_reversal_detected(self):
        from contextcore.intelligence.signals import check_sentiment_reversal

        now = datetime.now(timezone.utc)
        history = [
            {"sentiment": "positive", "timestamp": (now - timedelta(hours=12)).isoformat()},
            {"sentiment": "positive", "timestamp": (now - timedelta(hours=8)).isoformat()},
            {"sentiment": "positive", "timestamp": (now - timedelta(hours=4)).isoformat()},
        ]
        result = check_sentiment_reversal(
            entity_name="Tesla",
            current_sentiment="negative",
            history=history,
            config={"window_hours": 48, "min_mentions": 3},
        )
        assert result is not None
        assert result.type == "sentiment_reversal"
        assert "Tesla" in result.entity_name
        assert result.details["from"] == "positive"
        assert result.details["to"] == "negative"

    def test_no_reversal_same_sentiment(self):
        from contextcore.intelligence.signals import check_sentiment_reversal

        now = datetime.now(timezone.utc)
        history = [
            {"sentiment": "positive", "timestamp": (now - timedelta(hours=4)).isoformat()},
            {"sentiment": "positive", "timestamp": (now - timedelta(hours=2)).isoformat()},
            {"sentiment": "positive", "timestamp": (now - timedelta(hours=1)).isoformat()},
        ]
        result = check_sentiment_reversal(
            entity_name="Tesla",
            current_sentiment="positive",
            history=history,
            config={"window_hours": 48, "min_mentions": 3},
        )
        assert result is None


class TestThresholdBreachSignal:
    """Detect when negative sentiment count exceeds threshold."""

    def test_breach_detected(self):
        from contextcore.intelligence.signals import check_threshold_breach

        now = datetime.now(timezone.utc)
        history = [
            {"sentiment": "negative", "timestamp": (now - timedelta(hours=i)).isoformat()}
            for i in range(6)
        ]
        result = check_threshold_breach(
            entity_name="Tesla",
            history=history,
            config={"metric": "negative_sentiment_count", "threshold": 5, "window_hours": 24},
        )
        assert result is not None
        assert result.type == "threshold_breach"

    def test_no_breach_below_threshold(self):
        from contextcore.intelligence.signals import check_threshold_breach

        now = datetime.now(timezone.utc)
        history = [
            {"sentiment": "negative", "timestamp": (now - timedelta(hours=i)).isoformat()}
            for i in range(3)
        ]
        result = check_threshold_breach(
            entity_name="Tesla",
            history=history,
            config={"metric": "negative_sentiment_count", "threshold": 5, "window_hours": 24},
        )
        assert result is None


class TestCoOccurrenceSignal:
    """Detect when two entities are mentioned together repeatedly."""

    def test_co_occurrence_detected(self):
        from contextcore.intelligence.signals import check_co_occurrence

        result = check_co_occurrence(
            entity_a="Tesla",
            entity_b="NVIDIA",
            co_occurrence_count=5,
            config={"min_count": 3, "window_days": 7},
        )
        assert result is not None
        assert result.type == "co_occurrence"
        assert "Tesla" in result.entity_name
        assert "NVIDIA" in result.details.get("paired_with", "")

    def test_no_co_occurrence_below_min(self):
        from contextcore.intelligence.signals import check_co_occurrence

        result = check_co_occurrence(
            entity_a="Tesla",
            entity_b="NVIDIA",
            co_occurrence_count=1,
            config={"min_count": 3, "window_days": 7},
        )
        assert result is None
