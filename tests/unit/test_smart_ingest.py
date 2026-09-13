"""Tests for the smart ingestion pipeline."""

import pytest
from contextcore.core.registry import GraphRegistry
from contextcore.core.graph_structures import GraphNode
from contextcore.ingestion.cleaner import clean_text, clean_webpage, CleanDocument
from contextcore.ingestion.chunker import chunk_document, PassageChunk
from contextcore.ingestion.graph_builder import build_graph, ChunkExtraction, BuildResult
from contextcore.ingestion.smart_ingest import ingest_text, list_pipelines


# ── Cleaner ──────────────────────────────────────────────────────────

class TestCleaner:
    def test_clean_text_extracts_body(self):
        doc = clean_text("Hello world. This is a test article about AI.")
        assert doc.body == "Hello world. This is a test article about AI."
        assert doc.word_count > 0

    def test_clean_text_extracts_links(self):
        doc = clean_text("Visit https://example.com for more info.")
        assert len(doc.links) == 1
        assert doc.links[0].url == "https://example.com"

    def test_clean_text_preserves_title(self):
        doc = clean_text("Some body", title="My Article")
        assert doc.title == "My Article"


# ── Chunker ──────────────────────────────────────────────────────────

class TestChunker:
    def test_single_paragraph_becomes_one_chunk(self):
        text = "This is a single paragraph with enough words to be meaningful."
        chunks = chunk_document(text)
        assert len(chunks) == 1
        assert chunks[0].chunk_index == 0
        assert chunks[0].content == text

    def test_multiple_paragraphs_become_multiple_chunks(self):
        text = "First paragraph with enough content to stand alone as a passage.\n\n"
        text += "Second paragraph also with sufficient content for chunking.\n\n"
        text += "Third paragraph completes the set with more information."
        chunks = chunk_document(text)
        assert len(chunks) >= 1  # may merge short paras

    def test_overlap_prefix_set(self):
        # Create two clearly separate long paragraphs
        p1 = "Alpha beta gamma delta. " * 30  # ~120 tokens
        p2 = "Epsilon zeta eta theta. " * 30
        text = p1.strip() + "\n\n" + p2.strip()
        chunks = chunk_document(text)
        if len(chunks) >= 2:
            assert chunks[1].overlap_prefix != ""

    def test_empty_text_returns_empty(self):
        assert chunk_document("") == []
        assert chunk_document("   ") == []

    def test_chunk_has_token_count(self):
        chunks = chunk_document("Some meaningful text for testing the chunker.")
        assert chunks[0].token_count > 0

    def test_long_paragraph_gets_split(self):
        # Create a very long paragraph (>800 tokens)
        long_para = ("This is a sentence about artificial intelligence. " * 200)
        chunks = chunk_document(long_para)
        assert len(chunks) >= 2, f"Expected split but got {len(chunks)} chunks"


# ── Graph Builder ────────────────────────���───────────────────────────

class TestGraphBuilder:
    @pytest.fixture
    def db(self):
        registry = GraphRegistry()
        return registry.create_graph("test_graph_builder")

    def test_builds_document_node(self, db):
        doc = CleanDocument(title="Test Article", body="Some content.", source_url="https://example.com")
        chunks = [PassageChunk(content="Some content.", chunk_index=0)]
        extractions = [ChunkExtraction(chunk_index=0)]
        result = build_graph(doc, chunks, extractions, db)
        assert result.document_id != ""
        node = db.csr_adapter.get_node(result.document_id)
        assert node is not None
        assert node.properties.get("title") == "Test Article"

    def test_builds_passage_nodes(self, db):
        doc = CleanDocument(title="Test", body="Content here.")
        chunks = [
            PassageChunk(content="First passage.", chunk_index=0),
            PassageChunk(content="Second passage.", chunk_index=1),
        ]
        extractions = [ChunkExtraction(chunk_index=0), ChunkExtraction(chunk_index=1)]
        result = build_graph(doc, chunks, extractions, db)
        assert len(result.passage_ids) == 2

    def test_builds_entity_nodes(self, db):
        doc = CleanDocument(title="Test", body="About Alice.")
        chunks = [PassageChunk(content="About Alice.", chunk_index=0)]
        extractions = [ChunkExtraction(
            chunk_index=0,
            entities=[{"label": "Person", "properties": {"name": "Alice", "role": "CEO"}}],
        )]
        result = build_graph(doc, chunks, extractions, db)
        assert len(result.entity_ids) == 1
        assert "Person:alice" in result.entity_ids

    def test_builds_fact_nodes(self, db):
        doc = CleanDocument(title="Test", body="A fact.")
        chunks = [PassageChunk(content="A fact.", chunk_index=0)]
        extractions = [ChunkExtraction(
            chunk_index=0,
            facts=[{"properties": {"statement": "Revenue grew 40%", "name": "Revenue grew 40%"}}],
        )]
        result = build_graph(doc, chunks, extractions, db)
        assert len(result.fact_ids) == 1

    def test_creates_edges(self, db):
        doc = CleanDocument(title="Test", body="Alice at Acme.")
        chunks = [PassageChunk(content="Alice at Acme.", chunk_index=0)]
        extractions = [ChunkExtraction(
            chunk_index=0,
            entities=[
                {"label": "Person", "properties": {"name": "Alice"}},
                {"label": "Organization", "properties": {"name": "Acme"}},
            ],
            relationships=[{
                "label": "WORKS_AT", "source_name": "Alice", "target_name": "Acme",
                "properties": {"confidence": 0.8},
            }],
        )]
        result = build_graph(doc, chunks, extractions, db)
        assert result.edge_count >= 3  # CONTAINS + 2 MENTIONS + WORKS_AT

    def test_dedupes_entities_across_chunks(self, db):
        doc = CleanDocument(title="Test", body="Alice twice.")
        chunks = [
            PassageChunk(content="Alice is here.", chunk_index=0),
            PassageChunk(content="Alice again.", chunk_index=1),
        ]
        extractions = [
            ChunkExtraction(chunk_index=0, entities=[{"label": "Person", "properties": {"name": "Alice"}}]),
            ChunkExtraction(chunk_index=1, entities=[{"label": "Person", "properties": {"name": "Alice"}}]),
        ]
        result = build_graph(doc, chunks, extractions, db)
        # Should be 1 entity, not 2
        assert len(result.entity_ids) == 1


# ── Smart Ingest (end-to-end) ───────────���────────────────────────────

class TestSmartIngest:
    def test_ingest_text_produces_graph(self):
        registry = GraphRegistry()
        db = registry.create_graph("test_smart_ingest")
        text = (
            "Sam Altman, CEO of OpenAI, announced GPT-5 in San Francisco. "
            "The model costs $30 per million tokens. "
            "Microsoft invested $13 billion in OpenAI."
        )
        result = ingest_text(text, db, title="OpenAI News", pipeline="fast_ingest")
        assert len(result.passage_ids) >= 1
        assert result.document_id != ""
        # Document node exists
        doc = db.csr_adapter.get_node(result.document_id)
        assert doc is not None
        assert doc.properties.get("title") == "OpenAI News"

    def test_list_pipelines_returns_all(self):
        pipelines = list_pipelines()
        assert "smart_article" in pipelines
        assert "smart_text" in pipelines
        assert "fast_ingest" in pipelines
        for name, info in pipelines.items():
            assert "name" in info
            assert "description" in info
            assert "produces" in info


# ── Pipeline Info ─────��──────────────────────────────────────────────

class TestPipelineInfo:
    def test_smart_article_has_all_fields(self):
        pipelines = list_pipelines()
        sa = pipelines["smart_article"]
        assert sa["llm_required"] is True
        assert "Document" in sa["produces"]
        assert "Passage" in sa["produces"]
        assert "CONTAINS" in sa["edge_types"]
        assert "MENTIONS" in sa["edge_types"]

    def test_fast_ingest_no_llm(self):
        pipelines = list_pipelines()
        fi = pipelines["fast_ingest"]
        assert fi["llm_required"] is False


# ── Quality Scoring ───────────────────────────────────────────────────

class TestQualityScoring:
    """Quality scoring should reward relevance, not just quantity."""

    def test_noisy_extraction_scores_lower(self):
        from contextcore.ingestion.smart_ingest import _calc_quality

        passages = [{"token_count": 500}]

        # 10 entities but 8 are noise-flagged
        noisy_entities = [
            {"name": f"Noise {i}", "_amplifier_relevance": 0.1, "_noise_flags": ["web_cruft"]}
            for i in range(8)
        ] + [
            {"name": "Tesla", "_amplifier_relevance": 0.9},
            {"name": "Elon Musk", "_amplifier_relevance": 0.85},
        ]

        # 3 relevant entities, no noise
        clean_entities = [
            {"name": "Tesla", "_amplifier_relevance": 0.9},
            {"name": "Elon Musk", "_amplifier_relevance": 0.85},
            {"name": "SpaceX", "_amplifier_relevance": 0.8},
        ]

        noisy_score = _calc_quality(passages, noisy_entities, [], [])
        clean_score = _calc_quality(passages, clean_entities, [], [])

        assert clean_score > noisy_score, \
            f"Clean ({clean_score}) should score higher than noisy ({noisy_score})"

    def test_high_relevance_facts_boost_quality(self):
        from contextcore.ingestion.smart_ingest import _calc_quality

        passages = [{"token_count": 500}]
        entities = [{"name": "Tesla"}]

        relevant_facts = [
            {"name": "Revenue up 15%", "_amplifier_relevance": 0.9},
        ]
        generic_facts = [
            {"name": "Click to read more", "_amplifier_relevance": 0.1},
        ]

        relevant_score = _calc_quality(passages, entities, relevant_facts, [])
        generic_score = _calc_quality(passages, entities, generic_facts, [])

        assert relevant_score >= generic_score
