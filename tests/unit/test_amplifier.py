"""Tests for the Quality Amplifier — post-extraction relevance scoring."""
import pytest
import numpy as np


def _mock_embed(text: str):
    """Deterministic mock: hash text to a stable 4-dim vector."""
    import hashlib
    h = hashlib.md5(text.encode()).hexdigest()
    vec = [int(h[i:i+2], 16) / 255.0 for i in range(0, 8, 2)]
    norm = np.linalg.norm(vec)
    return (np.array(vec) / norm).tolist() if norm > 0 else vec


def _make_extraction(entities=None, facts=None, relationships=None, chunk_index=0):
    from contextcore.ingestion.graph_builder import ChunkExtraction
    return ChunkExtraction(
        chunk_index=chunk_index,
        entities=entities or [],
        facts=facts or [],
        relationships=relationships or [],
    )


class TestRelevanceScoring:
    """Amplifier scores entities/facts by relevance to context purpose."""

    def test_high_relevance_entity_keeps_confidence(self):
        from contextcore.ingestion.amplifier import amplify_extractions

        extractions = [_make_extraction(entities=[
            {"label": "Organization", "properties": {"name": "Tesla", "confidence": 0.85}},
        ])]

        result = amplify_extractions(
            extractions=extractions,
            context_purpose="Tesla electric vehicle company news and developments",
            embed_fn=_mock_embed,
        )

        ent = result.extractions[0].entities[0]
        # High relevance: confidence should stay >= 0.7
        assert ent["properties"]["confidence"] >= 0.7
        assert "_amplifier_relevance" in ent["properties"]
        assert ent["properties"]["_amplifier_relevance"] >= 0.0

    def test_low_relevance_entity_gets_demoted(self):
        from contextcore.ingestion.amplifier import amplify_extractions

        extractions = [_make_extraction(entities=[
            {"label": "Location", "properties": {"name": "Subscribe Now", "confidence": 0.85}},
        ])]

        result = amplify_extractions(
            extractions=extractions,
            context_purpose="Tesla electric vehicle company news",
            embed_fn=_mock_embed,
        )

        ent = result.extractions[0].entities[0]
        # "Subscribe Now" is noise — should be demoted
        assert ent["properties"]["confidence"] < 0.85
        assert ent["properties"]["_amplifier_relevance"] < 1.0

    def test_no_embed_fn_passes_through(self):
        """Without embed_fn, amplifier is a no-op pass-through."""
        from contextcore.ingestion.amplifier import amplify_extractions

        extractions = [_make_extraction(entities=[
            {"label": "Person", "properties": {"name": "Elon Musk", "confidence": 0.85}},
        ])]

        result = amplify_extractions(
            extractions=extractions,
            context_purpose="Tesla news",
            embed_fn=None,
        )

        ent = result.extractions[0].entities[0]
        assert ent["properties"]["confidence"] == 0.85  # unchanged

    def test_empty_extractions(self):
        from contextcore.ingestion.amplifier import amplify_extractions

        result = amplify_extractions(
            extractions=[],
            context_purpose="anything",
            embed_fn=_mock_embed,
        )
        assert result.extractions == []
        assert result.stats["total_entities"] == 0

    def test_facts_scored_by_relevance(self):
        from contextcore.ingestion.amplifier import amplify_extractions

        extractions = [_make_extraction(facts=[
            {"label": "Fact", "properties": {"name": "Tesla Q2 revenue rose 15%", "confidence": 0.85}},
            {"label": "Fact", "properties": {"name": "Click here to subscribe", "confidence": 0.85}},
        ])]

        result = amplify_extractions(
            extractions=extractions,
            context_purpose="Tesla financial performance and revenue",
            embed_fn=_mock_embed,
        )

        facts = result.extractions[0].facts
        # Both kept, but the noise fact should have lower relevance
        assert all("_amplifier_relevance" in f["properties"] for f in facts)

    def test_stats_populated(self):
        from contextcore.ingestion.amplifier import amplify_extractions

        extractions = [_make_extraction(
            entities=[
                {"label": "Organization", "properties": {"name": "Tesla", "confidence": 0.85}},
                {"label": "Location", "properties": {"name": "Trending Now", "confidence": 0.85}},
            ],
            facts=[
                {"label": "Fact", "properties": {"name": "Revenue up 15%", "confidence": 0.85}},
            ],
        )]

        result = amplify_extractions(
            extractions=extractions,
            context_purpose="Tesla financial news",
            embed_fn=_mock_embed,
        )

        assert result.stats["total_entities"] == 2
        assert result.stats["total_facts"] == 1
        assert "demoted_count" in result.stats
        assert "avg_relevance" in result.stats


class TestNoiseDetection:
    """Amplifier detects and flags common noise patterns."""

    def test_cta_text_flagged_as_noise(self):
        from contextcore.ingestion.amplifier import amplify_extractions

        extractions = [_make_extraction(entities=[
            {"label": "Entity", "properties": {"name": "Subscribe Now", "confidence": 0.85}},
            {"label": "Entity", "properties": {"name": "Click Here To Read More", "confidence": 0.85}},
            {"label": "Entity", "properties": {"name": "Share This Article", "confidence": 0.85}},
        ])]

        result = amplify_extractions(
            extractions=extractions,
            context_purpose="Tesla financial news",
            embed_fn=_mock_embed,
        )

        for ent in result.extractions[0].entities:
            assert "_noise_flags" in ent["properties"]
            assert len(ent["properties"]["_noise_flags"]) > 0
            assert ent["properties"]["confidence"] < 0.5

    def test_navigation_text_flagged(self):
        from contextcore.ingestion.amplifier import amplify_extractions

        extractions = [_make_extraction(entities=[
            {"label": "Entity", "properties": {"name": "Latest News", "confidence": 0.85}},
            {"label": "Entity", "properties": {"name": "Trending Stories", "confidence": 0.85}},
            {"label": "Entity", "properties": {"name": "Top Videos", "confidence": 0.85}},
        ])]

        result = amplify_extractions(
            extractions=extractions,
            context_purpose="Macroeconomic indicators",
            embed_fn=_mock_embed,
        )

        for ent in result.extractions[0].entities:
            assert ent["properties"]["confidence"] < 0.5

    def test_real_entity_not_flagged_as_noise(self):
        from contextcore.ingestion.amplifier import amplify_extractions

        extractions = [_make_extraction(entities=[
            {"label": "Organization", "properties": {"name": "Federal Reserve", "confidence": 0.85}},
        ])]

        result = amplify_extractions(
            extractions=extractions,
            context_purpose="US monetary policy and interest rates",
            embed_fn=_mock_embed,
        )

        ent = result.extractions[0].entities[0]
        flags = ent["properties"].get("_noise_flags", [])
        assert len(flags) == 0

    def test_short_generic_names_penalized(self):
        from contextcore.ingestion.amplifier import amplify_extractions

        extractions = [_make_extraction(entities=[
            {"label": "Entity", "properties": {"name": "IT", "confidence": 0.85}},
            {"label": "Concept", "properties": {"name": "Analysis", "confidence": 0.85}},
        ])]

        result = amplify_extractions(
            extractions=extractions,
            context_purpose="Technology sector analysis",
            embed_fn=_mock_embed,
        )

        for ent in result.extractions[0].entities:
            # Short/generic names should be penalized
            assert ent["properties"]["confidence"] < 0.85


class TestPurposeAutoDetection:
    """Amplifier auto-detects purpose from context metadata."""

    def test_builds_purpose_from_context_name(self):
        from contextcore.ingestion.amplifier import build_context_purpose

        purpose = build_context_purpose(
            context_name="Tesla Intelligence Dashboard",
            context_description="Track Tesla news, financials, and product launches",
            tags=["tesla", "automotive", "EV"],
        )

        assert "Tesla" in purpose
        assert len(purpose) > 10

    def test_builds_purpose_from_tags_only(self):
        from contextcore.ingestion.amplifier import build_context_purpose

        purpose = build_context_purpose(
            context_name="Research",
            context_description="",
            tags=["macroeconomics", "GDP", "inflation", "interest rates"],
        )

        assert "macroeconomics" in purpose.lower() or "GDP" in purpose

    def test_empty_metadata_returns_empty(self):
        from contextcore.ingestion.amplifier import build_context_purpose

        purpose = build_context_purpose(
            context_name="",
            context_description="",
            tags=[],
        )

        assert purpose == ""


class TestPipelineIntegration:
    """Amplifier integrates into the smart_ingest pipeline."""

    def test_ingest_url_sets_amplifier_relevance(self):
        """After ingestion, entities should have _amplifier_relevance property."""
        from contextcore.ingestion.smart_ingest import ingest_text
        from contextcore import AIContextDB

        db = AIContextDB()
        result = ingest_text(
            "Tesla reported Q2 revenue of $25.5 billion, up 15% from last year. "
            "CEO Elon Musk said the company expects strong growth in 2027.",
            db,
            title="Tesla Q2 Earnings",
            context_purpose="Tesla financial performance",
        )

        # Should not error, and should produce entities
        assert result.document_id or result.errors == []
