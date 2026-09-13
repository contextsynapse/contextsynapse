# tests/unit/test_intelligence_heuristics.py
"""Tests for keyword-based intelligence heuristics (LLM fallback)."""
import pytest


class TestSentimentHeuristics:
    """Keyword-based sentiment detection."""

    def test_positive_sentiment(self):
        from contextcore.intelligence.heuristics import detect_sentiment
        result = detect_sentiment("Revenue surged 25% and beat expectations across all segments")
        assert result.sentiment == "positive"
        assert result.confidence > 0.5
        assert result.evidence != ""

    def test_negative_sentiment(self):
        from contextcore.intelligence.heuristics import detect_sentiment
        result = detect_sentiment("Stock plunged after earnings miss, company announces layoffs")
        assert result.sentiment == "negative"
        assert result.confidence > 0.5

    def test_neutral_sentiment(self):
        from contextcore.intelligence.heuristics import detect_sentiment
        result = detect_sentiment("The company held its annual meeting on Tuesday")
        assert result.sentiment == "neutral"

    def test_mixed_sentiment(self):
        from contextcore.intelligence.heuristics import detect_sentiment
        result = detect_sentiment("Revenue grew 10% but profits declined sharply amid rising costs")
        assert result.sentiment == "mixed"

    def test_empty_text(self):
        from contextcore.intelligence.heuristics import detect_sentiment
        result = detect_sentiment("")
        assert result.sentiment == "neutral"
        assert result.confidence == 0.0


class TestImpactHeuristics:
    """Keyword-based impact detection."""

    def test_high_impact(self):
        from contextcore.intelligence.heuristics import detect_impact
        result = detect_impact("CEO fired after $2 billion fraud discovered, stock halted")
        assert result.impact == "high"

    def test_medium_impact(self):
        from contextcore.intelligence.heuristics import detect_impact
        result = detect_impact("Company reports 15% revenue growth in Q2")
        assert result.impact == "medium"

    def test_low_impact(self):
        from contextcore.intelligence.heuristics import detect_impact
        result = detect_impact("The office moved to a new building downtown")
        assert result.impact == "low"


class TestGeoMentionDetection:
    """Detect geographic mentions in text."""

    def test_detects_country(self):
        from contextcore.intelligence.heuristics import detect_geo_mentions
        from contextcore.intelligence.geo_reference import GeoLookup
        geo = GeoLookup()
        mentions = detect_geo_mentions("Tensions between China and the United States escalated", geo)
        names = [m.name for m in mentions]
        assert "China" in names
        assert "United States" in names

    def test_detects_city(self):
        from contextcore.intelligence.heuristics import detect_geo_mentions
        from contextcore.intelligence.geo_reference import GeoLookup
        geo = GeoLookup()
        mentions = detect_geo_mentions("Tesla's Fremont factory in California", geo)
        names = [m.name for m in mentions]
        assert "Fremont" in names
        assert "California" in names

    def test_no_false_positives_on_common_words(self):
        from contextcore.intelligence.heuristics import detect_geo_mentions
        from contextcore.intelligence.geo_reference import GeoLookup
        geo = GeoLookup()
        # "IT" is Italy's ISO code but should not match in normal text
        mentions = detect_geo_mentions("IT systems need an upgrade for the project", geo)
        names = [m.name for m in mentions]
        assert "Italy" not in names
