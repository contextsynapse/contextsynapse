"""Tests for LMDB-backed keyword index and embedding cache."""
import pytest
import tempfile
import os


class TestEmbeddingCache:
    """Embedding vector cache in LMDB."""

    def test_put_and_get(self):
        from contextcore.search.embedding_cache import EmbeddingCache
        cache = EmbeddingCache(os.path.join(tempfile.mkdtemp(), "emb"))
        vec = [0.1, 0.2, 0.3, 0.4]
        cache.put("iran ceasefire", vec)
        result = cache.get("iran ceasefire")
        assert result is not None
        assert len(result) == 4
        assert abs(result[0] - 0.1) < 0.001

    def test_case_insensitive(self):
        from contextcore.search.embedding_cache import EmbeddingCache
        cache = EmbeddingCache(os.path.join(tempfile.mkdtemp(), "emb"))
        cache.put("Iran Ceasefire", [1.0, 2.0])
        assert cache.get("iran ceasefire") is not None
        assert cache.get("IRAN CEASEFIRE") is not None

    def test_miss_returns_none(self):
        from contextcore.search.embedding_cache import EmbeddingCache
        cache = EmbeddingCache(os.path.join(tempfile.mkdtemp(), "emb"))
        assert cache.get("nonexistent") is None

    def test_stats(self):
        from contextcore.search.embedding_cache import EmbeddingCache
        cache = EmbeddingCache(os.path.join(tempfile.mkdtemp(), "emb"))
        cache.put("test", [1.0])
        cache.get("test")  # hit
        cache.get("miss")  # miss
        stats = cache.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1


class TestLMDBIndex:
    """LMDB keyword index — persistent, incremental, microsecond reads."""

    def _make_index(self, tmp_path=None):
        from contextcore.search.lmdb_index import LMDBIndex
        path = tmp_path or tempfile.mkdtemp()
        return LMDBIndex(os.path.join(path, "test_idx"))

    def test_index_and_search(self):
        idx = self._make_index()
        idx.index_node("n1", "Person", {"name": "Benjamin Netanyahu"})
        idx.index_node("n2", "Fact", {"statement": "Iran warns fingers on trigger"})

        results = idx.search("netanyahu")
        assert len(results) >= 1
        assert results[0]["node_id"] == "n1"

    def test_search_returns_ranked(self):
        idx = self._make_index()
        idx.index_node("n1", "Person", {"name": "Benjamin Netanyahu", "title": "Israeli PM Netanyahu"})
        idx.index_node("n2", "Fact", {"statement": "Netanyahu met with officials"})

        results = idx.search("netanyahu")
        # n1 should rank higher (name weight 2.0 + title weight 2.0 vs statement 1.5)
        assert results[0]["node_id"] == "n1"

    def test_label_filter(self):
        idx = self._make_index()
        idx.index_node("n1", "Person", {"name": "Netanyahu"})
        idx.index_node("n2", "Fact", {"statement": "Netanyahu said ceasefire"})

        facts = idx.search("netanyahu", label_filter="Fact")
        assert all(r["label"] == "Fact" for r in facts)

    def test_noise_labels_excluded(self):
        idx = self._make_index()
        idx.index_node("n1", "AgentThought", {"content": "thinking about iran"})
        idx.index_node("n2", "Fact", {"statement": "Iran warns"})

        results = idx.search("iran")
        assert all(r["label"] != "AgentThought" for r in results)

    def test_incremental_update(self):
        idx = self._make_index()
        idx.index_node("n1", "Person", {"name": "Netanyahu"})
        r1 = idx.search("netanyahu")
        assert len(r1) == 1

        # Add another node
        idx.index_node("n2", "Person", {"name": "Trump"})
        r2 = idx.search("trump")
        assert len(r2) == 1

        # Original still works
        r3 = idx.search("netanyahu")
        assert len(r3) == 1

    def test_batch_index(self):
        idx = self._make_index()
        nodes = [
            {"id": "n1", "label": "Person", "properties": {"name": "Alice"}},
            {"id": "n2", "label": "Person", "properties": {"name": "Bob"}},
            {"id": "n3", "label": "Fact", "properties": {"statement": "Alice met Bob"}},
        ]
        idx.index_batch(nodes)

        results = idx.search("alice")
        assert len(results) >= 1

    def test_stats(self):
        idx = self._make_index()
        idx.index_node("n1", "Person", {"name": "Netanyahu"})
        idx.index_node("n2", "Fact", {"statement": "Iran ceasefire"})

        stats = idx.stats()
        assert stats["nodes"] >= 2
        assert stats["terms"] >= 2

    def test_stop_words_filtered(self):
        idx = self._make_index()
        idx.index_node("n1", "Fact", {"statement": "the president said this is important"})

        # "the", "said", "this", "is" are stop words — should not be indexed
        results = idx.search("the")
        assert len(results) == 0

        results = idx.search("president")
        assert len(results) == 1

    def test_multi_term_query(self):
        idx = self._make_index()
        idx.index_node("n1", "Fact", {"statement": "Iran warns about ceasefire"})
        idx.index_node("n2", "Fact", {"statement": "Ukraine conflict escalates"})

        # Multi-term: both terms match n1
        results = idx.search("iran ceasefire")
        assert results[0]["node_id"] == "n1"

    def test_bm25_search(self):
        """BM25-scored search ranks by TF-IDF."""
        idx = self._make_index()
        idx.index_node("n1", "Fact", {"statement": "Iran warns about ceasefire"})
        idx.index_node("n2", "Fact", {"statement": "Iran Iran Iran nuclear deal"})  # higher TF for "iran"
        idx.index_node("n3", "Person", {"name": "Netanyahu"})

        results = idx.search_bm25("iran")
        assert len(results) >= 1
        # n2 has higher term frequency for "iran"
        iran_ids = [r["node_id"] for r in results if r["label"] == "Fact"]
        assert "n2" in iran_ids

    def test_bm25_multi_term(self):
        idx = self._make_index()
        idx.index_node("n1", "Fact", {"statement": "Iran ceasefire negotiations stalled"})
        idx.index_node("n2", "Fact", {"statement": "Ukraine conflict escalates further"})

        results = idx.search_bm25("iran ceasefire")
        assert results[0]["node_id"] == "n1"  # matches both terms

    def test_get_node(self):
        idx = self._make_index()
        idx.index_node("n1", "Person", {"name": "Netanyahu"})

        node = idx.get_node("n1")
        assert node is not None
        assert node["name"] == "Netanyahu"
        assert node["label"] == "Person"

        assert idx.get_node("nonexistent") is None
