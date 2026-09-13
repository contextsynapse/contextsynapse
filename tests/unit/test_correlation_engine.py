"""Tests for the correlation engine."""
import pytest
from datetime import datetime, timezone, timedelta


class TestCorrelationEngine:
    """Core correlation engine — processes signals and runs computations."""

    def test_process_ingest_fires_signals(self):
        from contextcore.intelligence.correlation_engine import CorrelationEngine
        from contextcore.intelligence.correlation_template import CorrelationTemplate

        template = CorrelationTemplate.from_dict({
            "name": "test",
            "entity_types": ["Company"],
            "signals": [
                {"type": "sentiment_reversal", "config": {"window_hours": 48, "min_mentions": 3}},
            ],
            "correlations": [],
        })

        engine = CorrelationEngine()

        now = datetime.now(timezone.utc)
        entities = [
            {"name": "Tesla", "label": "Company", "_sentiment": "negative"},
        ]
        history = {
            "Tesla": [
                {"sentiment": "positive", "timestamp": (now - timedelta(hours=i)).isoformat()}
                for i in range(1, 5)
            ]
        }

        signals = engine.process_ingest(entities, template, sentiment_history=history)
        assert len(signals) >= 1
        assert signals[0].type == "sentiment_reversal"

    def test_process_ingest_no_signals_when_no_reversal(self):
        from contextcore.intelligence.correlation_engine import CorrelationEngine
        from contextcore.intelligence.correlation_template import CorrelationTemplate

        template = CorrelationTemplate.from_dict({
            "name": "test",
            "entity_types": ["Company"],
            "signals": [
                {"type": "sentiment_reversal", "config": {"window_hours": 48, "min_mentions": 3}},
            ],
            "correlations": [],
        })

        engine = CorrelationEngine()
        entities = [
            {"name": "Tesla", "label": "Company", "_sentiment": "positive"},
        ]
        # No history
        signals = engine.process_ingest(entities, template, sentiment_history={})
        assert len(signals) == 0

    def test_process_ingest_filters_by_entity_type(self):
        from contextcore.intelligence.correlation_engine import CorrelationEngine
        from contextcore.intelligence.correlation_template import CorrelationTemplate

        template = CorrelationTemplate.from_dict({
            "name": "test",
            "entity_types": ["Company"],  # Only Company
            "signals": [
                {"type": "sentiment_reversal", "config": {"window_hours": 48, "min_mentions": 3}},
            ],
            "correlations": [],
        })

        engine = CorrelationEngine()
        now = datetime.now(timezone.utc)

        entities = [
            {"name": "Elon Musk", "label": "Person", "_sentiment": "negative"},  # Not Company
        ]
        history = {
            "Elon Musk": [
                {"sentiment": "positive", "timestamp": (now - timedelta(hours=i)).isoformat()}
                for i in range(1, 5)
            ]
        }

        signals = engine.process_ingest(entities, template, sentiment_history=history)
        assert len(signals) == 0  # Person not in template entity_types

    def test_get_template(self):
        from contextcore.intelligence.correlation_engine import CorrelationEngine

        engine = CorrelationEngine()
        engine.register_template("market_analysis")
        template = engine.get_template("market_analysis")
        assert template is not None
        assert template.name == "market_analysis"

    def test_register_custom_template(self):
        from contextcore.intelligence.correlation_engine import CorrelationEngine
        from contextcore.intelligence.correlation_template import CorrelationTemplate

        engine = CorrelationEngine()
        custom = CorrelationTemplate.from_dict({
            "name": "custom_test",
            "entity_types": ["Widget"],
            "signals": [],
            "correlations": [],
        })
        engine.register_template_object(custom)
        assert engine.get_template("custom_test") is not None
        assert engine.get_template("custom_test").entity_types == ["Widget"]
