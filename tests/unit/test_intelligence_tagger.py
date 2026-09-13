# tests/unit/test_intelligence_tagger.py
"""Tests for the Intelligence Tagger."""
import pytest


def _mock_llm(prompt: str) -> str:
    """Mock LLM that returns structured tagging JSON."""
    return '''{
        "sentiment": "negative",
        "sentiment_confidence": 0.85,
        "sentiment_evidence": "stock plunged after earnings miss",
        "geographies": [
            {"name": "United States", "level": "country"}
        ],
        "domains": ["technology"],
        "categories": ["earnings"],
        "impact": "high",
        "impact_reason": "major earnings miss"
    }'''


def _make_extraction(entities=None, facts=None, chunk_index=0):
    from contextcore.ingestion.graph_builder import ChunkExtraction
    return ChunkExtraction(
        chunk_index=chunk_index,
        entities=entities or [],
        facts=facts or [],
        relationships=[],
    )


class TestIntelligenceTagger:
    """Intelligence tagger enriches extractions with 5 tag dimensions."""

    def test_tag_with_llm(self):
        from contextcore.intelligence.tagger import tag_extractions

        extractions = [_make_extraction(
            entities=[{"label": "Organization", "properties": {"name": "Tesla"}}],
            facts=[{"label": "Fact", "properties": {"name": "Stock plunged 15%"}}],
        )]

        result = tag_extractions(
            extractions=extractions,
            chunk_texts=["Tesla stock plunged 15% after the company missed earnings expectations in the United States"],
            llm_fn=_mock_llm,
        )

        assert len(result.tags) > 0
        sentiments = [t for t in result.tags if t.dimension == "sentiment"]
        assert len(sentiments) >= 1
        assert sentiments[0].value == "negative"

    def test_tag_with_heuristics_only(self):
        from contextcore.intelligence.tagger import tag_extractions

        extractions = [_make_extraction(
            entities=[{"label": "Organization", "properties": {"name": "Apple"}}],
        )]

        result = tag_extractions(
            extractions=extractions,
            chunk_texts=["Apple revenue surged 30% beating all expectations"],
            llm_fn=None,  # no LLM — heuristics only
        )

        sentiments = [t for t in result.tags if t.dimension == "sentiment"]
        assert len(sentiments) >= 1
        assert sentiments[0].value == "positive"

    def test_geo_resolution(self):
        from contextcore.intelligence.tagger import tag_extractions

        extractions = [_make_extraction()]

        result = tag_extractions(
            extractions=extractions,
            chunk_texts=["New factory opens in Fremont, California"],
            llm_fn=None,
        )

        assert len(result.geo_entries) > 0
        geo_names = [g.name for g in result.geo_entries]
        assert "Fremont" in geo_names or "California" in geo_names

    def test_custom_taxonomy(self):
        from contextcore.intelligence.tagger import tag_extractions

        extractions = [_make_extraction()]

        result = tag_extractions(
            extractions=extractions,
            chunk_texts=["New cancer drug shows promising results in clinical trials"],
            llm_fn=_mock_llm,
            taxonomy={"domains": ["oncology", "cardiology", "neurology"],
                       "categories": ["clinical_trial", "drug_approval", "research"]},
        )

        # Should have tags (LLM returns its mock response regardless)
        assert len(result.tags) > 0

    def test_empty_extractions(self):
        from contextcore.intelligence.tagger import tag_extractions

        result = tag_extractions(
            extractions=[],
            chunk_texts=[],
            llm_fn=None,
        )

        assert result.tags == []
        assert result.stats["total_chunks"] == 0

    def test_stats_populated(self):
        from contextcore.intelligence.tagger import tag_extractions

        extractions = [_make_extraction()]

        result = tag_extractions(
            extractions=extractions,
            chunk_texts=["Oil prices rose sharply in the Middle East"],
            llm_fn=None,
        )

        assert "total_chunks" in result.stats
        assert "sentiment_count" in result.stats
        assert "geo_count" in result.stats

    def test_llm_parse_failure_falls_back_to_heuristics(self):
        """If LLM returns garbage, fall back to heuristics gracefully."""
        from contextcore.intelligence.tagger import tag_extractions

        def bad_llm(prompt):
            return "this is not json at all"

        extractions = [_make_extraction()]

        result = tag_extractions(
            extractions=extractions,
            chunk_texts=["Revenue surged 25% beating expectations"],
            llm_fn=bad_llm,
        )

        # Should still produce tags via heuristic fallback
        sentiments = [t for t in result.tags if t.dimension == "sentiment"]
        assert len(sentiments) >= 1
        assert sentiments[0].value == "positive"


class TestPipelineIntegration:
    """Tagger integrates into the smart_ingest pipeline."""

    def test_ingest_text_produces_tags(self):
        """After ingestion, the tagger should have run and produced tags."""
        from contextcore.intelligence.tagger import tag_extractions

        extractions = [_make_extraction(
            entities=[
                {"label": "Organization", "properties": {"name": "Apple", "confidence": 0.85}},
            ],
            facts=[
                {"label": "Fact", "properties": {"name": "Revenue grew 20% in California", "confidence": 0.85}},
            ],
        )]

        result = tag_extractions(
            extractions=extractions,
            chunk_texts=["Apple reported revenue grew 20% in California, beating Wall Street expectations"],
            llm_fn=None,
        )

        # Should have sentiment, impact, and geo tags
        dimensions = {t.dimension for t in result.tags}
        assert "sentiment" in dimensions
        assert "impact" in dimensions
        # Geo should detect California
        geo_names = [g.name for g in result.geo_entries]
        assert "California" in geo_names
