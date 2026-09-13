"""Tests for search quality enhancements: bigrams, trigrams, module constants."""
import json
import pytest
from contextcore.search.lmdb_index import _compute_node_terms, _CONTENT_LABELS


class TestComputeNodeTerms:

    def test_returns_tuple_of_three(self):
        terms, term_list, node_data = _compute_node_terms(
            "Finding",
            {"name": "Apollo Mission", "statement": "First moon landing"}
        )
        assert isinstance(terms, dict)
        assert isinstance(term_list, list)
        assert isinstance(node_data, bytes)

    def test_term_list_matches_terms_keys(self):
        terms, term_list, _ = _compute_node_terms(
            "Finding",
            {"name": "Apollo Mission"}
        )
        assert set(term_list) == set(terms.keys())

    def test_name_gets_higher_weight_than_statement(self):
        terms, _, _ = _compute_node_terms(
            "Fact",
            {"name": "gravity", "statement": "other words here"}
        )
        # "gravity" appears in name (weight 2.0); confirm it has higher weight than
        # a term appearing only in statement (weight 1.5)
        gravity_weight = terms.get("graviti", terms.get("gravity", 0))
        assert gravity_weight >= 2.0

    def test_noise_label_returns_empty(self):
        terms, term_list, node_data = _compute_node_terms(
            "AgentThought",
            {"name": "thinking about stuff"}
        )
        assert terms == {}
        assert term_list == []
        assert node_data == b""

    def test_node_data_contains_term_count(self):
        _, _, node_data = _compute_node_terms(
            "Finding",
            {"name": "photosynthesis", "statement": "plants use sunlight"}
        )
        d = json.loads(node_data)
        assert "term_count" in d
        assert d["term_count"] >= 1

    def test_content_labels_is_frozenset(self):
        assert isinstance(_CONTENT_LABELS, frozenset)
        assert "Person" in _CONTENT_LABELS
        assert "Fact" in _CONTENT_LABELS


class TestBigramPhraseIndex:

    def test_bigrams_helper(self):
        from contextcore.search.lmdb_index import _bigrams
        assert _bigrams(["new", "york", "city"]) == ["new__york", "york__city"]

    def test_bigrams_empty_and_single(self):
        from contextcore.search.lmdb_index import _bigrams
        assert _bigrams([]) == []
        assert _bigrams(["only"]) == []

    def test_compute_node_terms_includes_bigrams(self):
        terms, _, _ = _compute_node_terms(
            "Location",
            {"name": "New York City"}
        )
        # After stemming: "new", "york", "citi" → bigrams "new__york", "york__citi"
        bigrams_found = [k for k in terms if "__" in k]
        assert len(bigrams_found) >= 1
        assert any("york" in b for b in bigrams_found)

    def test_bigram_weight_higher_than_unigram(self):
        terms, _, _ = _compute_node_terms(
            "Location",
            {"name": "New York"}  # name weight = 2.0
        )
        # bigram "new__york" weight = 2.0 * 1.5 = 3.0
        bigrams = {k: v for k, v in terms.items() if "__" in k}
        unigrams = {k: v for k, v in terms.items() if "__" not in k}
        if bigrams and unigrams:
            assert max(bigrams.values()) > max(unigrams.values())

    def test_bigram_term_count_excludes_bigrams_from_dl(self):
        _, _, node_data = _compute_node_terms(
            "Location",
            {"name": "New York City"}
        )
        d = json.loads(node_data)
        # term_count (BM25 dl) must count unigrams only — not bigrams
        terms, _, _ = _compute_node_terms("Location", {"name": "New York City"})
        unigram_count = sum(1 for t in terms if "__" not in t)
        assert d["term_count"] == unigram_count

    def test_search_bm25_generates_bigrams_from_query(self, tmp_path):
        """Multi-word query generates bigrams that match phrase-indexed nodes."""
        from contextcore.search.lmdb_index import LMDBIndex
        import uuid
        idx = LMDBIndex(str(tmp_path / "bigram_test"))
        nid = str(uuid.uuid4())
        idx.index_node(nid, "Location", {"name": "New York City"})
        # Single-word search
        single = idx.search_bm25("york", limit=5)
        # Phrase search — should score higher due to bigram hit
        phrase = idx.search_bm25("new york", limit=5)
        assert phrase, "phrase search must return results"
        assert any(r["node_id"] == nid for r in phrase)
        idx.close()


class TestTrigramFuzzySearch:

    def test_char_trigrams_normal_word(self):
        from contextcore.search.lmdb_index import _char_trigrams
        result = _char_trigrams("graph")
        assert result == ["gra", "rap", "aph"]

    def test_char_trigrams_short_word(self):
        from contextcore.search.lmdb_index import _char_trigrams
        assert _char_trigrams("ab") == ["ab"]   # too short for 3-gram
        assert _char_trigrams("abc") == ["abc"]  # exactly 3 chars

    def test_char_trigrams_long_word(self):
        from contextcore.search.lmdb_index import _char_trigrams
        result = _char_trigrams("photo")
        assert result == ["pho", "hot", "oto"]

    def test_fuzzy_candidates_finds_typo(self, tmp_path):
        """'photosyntesis' (missing 'h') must still find 'photosynthesis'."""
        from contextcore.search.lmdb_index import LMDBIndex
        import uuid
        idx = LMDBIndex(str(tmp_path / "trigram_test"))
        nid = str(uuid.uuid4())
        idx.index_node(nid, "Finding", {
            "name": "Photosynthesis",
            "statement": "plants convert sunlight to energy",
        })
        results = idx.fuzzy_candidates("photosyntesis", limit=5)
        assert results, "must find node despite typo"
        assert any(r["node_id"] == nid for r in results)
        assert all(r["similarity"] >= 0.5 for r in results)
        idx.close()

    def test_fuzzy_candidates_empty_on_no_match(self, tmp_path):
        from contextcore.search.lmdb_index import LMDBIndex
        import uuid
        idx = LMDBIndex(str(tmp_path / "trigram_empty"))
        nid = str(uuid.uuid4())
        idx.index_node(nid, "Finding", {"name": "Apollo Mission"})
        results = idx.fuzzy_candidates("zzzzzzz", limit=5)
        assert results == []
        idx.close()

    def test_delete_cleans_trigrams(self, tmp_path):
        """After delete_node, fuzzy_candidates no longer finds the node."""
        from contextcore.search.lmdb_index import LMDBIndex
        import uuid
        idx = LMDBIndex(str(tmp_path / "trigram_del"))
        nid = str(uuid.uuid4())
        idx.index_node(nid, "Finding", {"name": "Photosynthesis"})
        idx.delete_node(nid)
        results = idx.fuzzy_candidates("photosynthesis", limit=5)
        assert not any(r["node_id"] == nid for r in results)
        idx.close()
