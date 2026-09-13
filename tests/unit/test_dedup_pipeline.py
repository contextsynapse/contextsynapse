# tests/unit/test_dedup_pipeline.py
"""Tests for the three-layer dedup pipeline."""
import pytest


class TestContentFingerprint:
    """Layer 1: SHA-256 fingerprint dedup."""

    def test_new_content_passes(self):
        from contextcore.intelligence.dedup import ContentFingerprint
        fp = ContentFingerprint()
        result = fp.check("This is brand new content about Tesla earnings", url="https://example.com/1")
        assert result.status == "new"
        assert result.fingerprint != ""

    def test_exact_duplicate_rejected(self):
        from contextcore.intelligence.dedup import ContentFingerprint
        fp = ContentFingerprint()
        text = "Tesla reported record Q2 revenue of $25 billion"
        fp.check(text, url="https://a.com/1")
        result = fp.check(text, url="https://b.com/2")
        assert result.status == "exact_duplicate"

    def test_normalized_duplicate_caught(self):
        from contextcore.intelligence.dedup import ContentFingerprint
        fp = ContentFingerprint()
        fp.check("Hello   World!  Extra   spaces.", url="https://a.com")
        result = fp.check("hello world! extra spaces.", url="https://b.com")
        assert result.status == "exact_duplicate"

    def test_same_url_different_content_is_changed(self):
        from contextcore.intelligence.dedup import ContentFingerprint
        fp = ContentFingerprint()
        fp.check("Version 1 of the article", url="https://a.com/article")
        result = fp.check("Version 2 of the article with updates", url="https://a.com/article")
        assert result.status == "content_changed"

    def test_same_url_same_content_is_duplicate(self):
        from contextcore.intelligence.dedup import ContentFingerprint
        fp = ContentFingerprint()
        fp.check("Same content", url="https://a.com/page")
        result = fp.check("Same content", url="https://a.com/page")
        assert result.status == "exact_duplicate"

    def test_different_content_passes(self):
        from contextcore.intelligence.dedup import ContentFingerprint
        fp = ContentFingerprint()
        fp.check("Article about Tesla", url="https://a.com/1")
        result = fp.check("Article about Apple", url="https://b.com/2")
        assert result.status == "new"


class TestSemanticDedup:
    """Layer 2: Embedding-based semantic dedup."""

    def test_identical_embeddings_duplicate(self):
        from contextcore.intelligence.dedup import SemanticDedup
        sd = SemanticDedup()
        emb = [0.1, 0.2, 0.3, 0.4]
        sd.add(emb, doc_id="doc1")
        result = sd.check(emb)
        assert result.status == "semantic_duplicate"

    def test_similar_embeddings_related(self):
        from contextcore.intelligence.dedup import SemanticDedup
        sd = SemanticDedup()
        emb1 = [0.1, 0.2, 0.3, 0.4]
        sd.add(emb1, doc_id="doc1")
        # Slightly different — similarity ~0.90
        emb2 = [0.11, 0.19, 0.31, 0.39]
        result = sd.check(emb2)
        assert result.status in ("related_coverage", "semantic_duplicate")

    def test_different_embeddings_new(self):
        from contextcore.intelligence.dedup import SemanticDedup
        sd = SemanticDedup()
        sd.add([0.1, 0.2, 0.3, 0.4], doc_id="doc1")
        result = sd.check([0.9, -0.1, 0.5, -0.3])
        assert result.status == "new"

    def test_empty_store_always_new(self):
        from contextcore.intelligence.dedup import SemanticDedup
        sd = SemanticDedup()
        result = sd.check([0.1, 0.2, 0.3, 0.4])
        assert result.status == "new"


class TestFactualUpdateDetection:
    """Semantic dedup distinguishes true duplicates from factual corrections."""

    def test_numbers_differ_detected(self):
        from contextcore.intelligence.dedup import _numbers_differ
        assert _numbers_differ(
            "TCS reported revenue of $25.5 billion",
            "TCS reported revenue of $25.8 billion",
        ) is True

    def test_same_numbers_not_flagged(self):
        from contextcore.intelligence.dedup import _numbers_differ
        assert _numbers_differ(
            "TCS reported revenue of $25.5 billion",
            "TCS reported revenue of $25.5 billion",
        ) is False

    def test_semantic_dedup_factual_update(self):
        from contextcore.intelligence.dedup import SemanticDedup
        sd = SemanticDedup()
        emb1 = [0.1, 0.2, 0.3, 0.4]
        sd.add(emb1, doc_id="doc1", text="TCS revenue was $25.5 billion in Q2")
        result = sd.check(emb1, text="TCS revenue was $25.8 billion in Q2")
        assert result.status == "factual_update"
        assert result.numbers_changed is True

    def test_semantic_dedup_true_duplicate(self):
        from contextcore.intelligence.dedup import SemanticDedup
        sd = SemanticDedup()
        emb1 = [0.1, 0.2, 0.3, 0.4]
        sd.add(emb1, doc_id="doc1", text="TCS revenue was $25.5 billion in Q2")
        result = sd.check(emb1, text="TCS revenue was $25.5 billion in Q2")
        assert result.status == "semantic_duplicate"


class TestEntityResolver:
    """Layer 3: Fuzzy entity name resolution."""

    def test_exact_match_merges(self):
        from contextcore.intelligence.dedup import EntityResolver
        er = EntityResolver()
        existing = [
            {"name": "Tesla", "label": "Company", "id": "n1"},
        ]
        result = er.resolve("Tesla", "Company", existing)
        assert result.status == "merge"
        assert result.matched_id == "n1"

    def test_case_insensitive_match(self):
        from contextcore.intelligence.dedup import EntityResolver
        er = EntityResolver()
        existing = [
            {"name": "Tesla", "label": "Company", "id": "n1"},
        ]
        result = er.resolve("tesla", "Company", existing)
        assert result.status == "merge"

    def test_partial_match_alias(self):
        from contextcore.intelligence.dedup import EntityResolver
        er = EntityResolver()
        existing = [
            {"name": "Elon Musk", "label": "Person", "id": "n1"},
        ]
        result = er.resolve("Musk", "Person", existing)
        assert result.status in ("alias", "merge")

    def test_no_match_creates_new(self):
        from contextcore.intelligence.dedup import EntityResolver
        er = EntityResolver()
        existing = [
            {"name": "Tesla", "label": "Company", "id": "n1"},
        ]
        result = er.resolve("Apple", "Company", existing)
        assert result.status == "new"

    def test_different_label_no_match(self):
        from contextcore.intelligence.dedup import EntityResolver
        er = EntityResolver()
        existing = [
            {"name": "Tesla", "label": "Company", "id": "n1"},
        ]
        result = er.resolve("Tesla", "Person", existing)
        assert result.status == "new"

    def test_empty_existing_always_new(self):
        from contextcore.intelligence.dedup import EntityResolver
        er = EntityResolver()
        result = er.resolve("Tesla", "Company", [])
        assert result.status == "new"
