"""Tests for correlation template model and loader."""
import pytest


class TestCorrelationTemplate:
    """Template model and YAML loading."""

    def test_load_from_dict(self):
        from contextcore.intelligence.correlation_template import CorrelationTemplate

        data = {
            "name": "test_template",
            "description": "Test",
            "entity_types": ["Company"],
            "indicator_types": ["Price"],
            "signals": [
                {"type": "sentiment_reversal", "config": {"window_hours": 48, "min_mentions": 3}}
            ],
            "correlations": [
                {"type": "entity_vs_indicator", "config": {"lag_max_days": 14, "min_samples": 10}}
            ],
            "schedule": "daily",
            "quality": {"min_samples": 10, "min_confidence": 0.6, "decay_days": 30, "max_correlations": 1000},
        }

        template = CorrelationTemplate.from_dict(data)
        assert template.name == "test_template"
        assert template.entity_types == ["Company"]
        assert len(template.signals) == 1
        assert template.signals[0].type == "sentiment_reversal"
        assert template.signals[0].config["window_hours"] == 48
        assert len(template.correlations) == 1
        assert template.quality.min_samples == 10
        assert template.schedule == "daily"

    def test_load_market_analysis_template(self):
        from contextcore.intelligence.correlation_template import load_template
        template = load_template("market_analysis")
        assert template.name == "market_analysis"
        assert "Company" in template.entity_types
        assert len(template.signals) >= 1
        assert len(template.correlations) >= 1

    def test_load_geopolitical_risk_template(self):
        from contextcore.intelligence.correlation_template import load_template
        template = load_template("geopolitical_risk")
        assert template.name == "geopolitical_risk"
        assert "Country" in template.entity_types

    def test_load_competitive_intel_template(self):
        from contextcore.intelligence.correlation_template import load_template
        template = load_template("competitive_intel")
        assert template.name == "competitive_intel"

    def test_list_templates(self):
        from contextcore.intelligence.correlation_template import list_templates
        names = list_templates()
        assert "market_analysis" in names
        assert "geopolitical_risk" in names
        assert "competitive_intel" in names

    def test_load_nonexistent_returns_none(self):
        from contextcore.intelligence.correlation_template import load_template
        result = load_template("nonexistent_template_xyz")
        assert result is None

    def test_quality_defaults(self):
        from contextcore.intelligence.correlation_template import CorrelationTemplate
        data = {
            "name": "minimal",
            "entity_types": ["Entity"],
            "signals": [],
            "correlations": [],
        }
        template = CorrelationTemplate.from_dict(data)
        assert template.quality.min_samples == 10
        assert template.quality.min_confidence == 0.6
        assert template.quality.decay_days == 30
        assert template.schedule == "daily"
