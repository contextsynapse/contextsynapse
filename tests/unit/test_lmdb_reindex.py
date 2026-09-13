"""Tests for stale-term pruning on re-index in LMDBIndex."""
import uuid
import pytest
from contextcore.search.lmdb_index import LMDBIndex


@pytest.fixture
def tmp_idx(tmp_path):
    idx = LMDBIndex(str(tmp_path / "idx"))
    yield idx
    idx.close()


class TestStaleTermPruning:

    def test_reindex_removes_stale_terms(self, tmp_idx):
        """After re-indexing, old content must not appear in search results."""
        nid = str(uuid.uuid4())
        # Index with original content
        tmp_idx.index_node(nid, "Fact", {"name": "Quantum", "statement": "quantum entanglement is real"})
        hits_before = tmp_idx.search_bm25("entanglement", limit=5)
        assert any(h["node_id"] == nid for h in hits_before), "should find before update"

        # Re-index with completely different content
        tmp_idx.index_node(nid, "Fact", {"name": "Dark Matter", "statement": "dark matter dominates the universe"})

        # Old term must be gone
        hits_old = tmp_idx.search_bm25("entanglement", limit=5)
        assert not any(h["node_id"] == nid for h in hits_old), "stale term must be removed"

        # New term must be findable
        hits_new = tmp_idx.search_bm25("dark matter", limit=5)
        assert any(h["node_id"] == nid for h in hits_new), "new term must be found"

    def test_reindex_keeps_overlapping_terms(self, tmp_idx):
        """Terms present in both old and new content are preserved."""
        nid = str(uuid.uuid4())
        tmp_idx.index_node(nid, "Fact", {"name": "Alpha", "statement": "neural network architecture"})
        tmp_idx.index_node(nid, "Fact", {"name": "Alpha", "statement": "neural network training loss"})

        # "neural" and "network" are in both — must still match
        hits = tmp_idx.search_bm25("neural network", limit=5)
        assert any(h["node_id"] == nid for h in hits)

        # "architecture" was removed — must not match for this node
        arch_hits = tmp_idx.search_bm25("architecture", limit=5)
        assert not any(h["node_id"] == nid for h in arch_hits)

    def test_reindex_in_memory_fallback(self):
        """Stale-term pruning works in the in-memory fallback path."""
        import threading
        idx = LMDBIndex.__new__(LMDBIndex)
        idx._env = None
        idx._path = "/mem-only"
        idx._map_size = 0
        idx._node_count = 0
        idx._term_count = 0
        idx._local = threading.local()
        idx._mem_terms = {}
        idx._mem_nodes = {}
        idx._mem_node_terms = {}
        idx._mem_corpus_stats = {"total_term_count": 0, "doc_count": 0}

        nid = str(uuid.uuid4())
        idx.index_node(nid, "Fact", {"name": "cats", "statement": "cats are mammals"})
        idx.index_node(nid, "Fact", {"name": "dogs", "statement": "dogs are mammals"})

        # "cats" was in first index but not second → must be pruned
        cat_hits = idx.search("cats", limit=5)
        assert not any(h["node_id"] == nid for h in cat_hits), "stale term 'cats' must be removed"

        # "dogs" was added in re-index → must be found
        dog_hits = idx.search("dogs", limit=5)
        assert any(h["node_id"] == nid for h in dog_hits), "'dogs' must be findable after re-index"

        # "mammals" was in both → must still be found
        mammal_hits = idx.search("mammals", limit=5)
        assert any(h["node_id"] == nid for h in mammal_hits), "shared term 'mammals' must be preserved"


class TestLMDBHelpers:

    def test_lmdb_delete_node_removes_from_search(self, tmp_path):
        from contextcore.search.lmdb_index import LMDBIndex, lmdb_delete_node, get_lmdb_index, _lmdb_indexes
        name = f"helper_test_{uuid.uuid4().hex[:8]}"
        # Force the singleton to use tmp_path
        idx = LMDBIndex(str(tmp_path / name))
        _lmdb_indexes[name] = idx
        try:
            nid = str(uuid.uuid4())
            idx.index_node(nid, "Fact", {"name": "solar panels", "statement": "solar energy is renewable"})
            hits = idx.search_bm25("solar", limit=5)
            assert any(h["node_id"] == nid for h in hits)

            lmdb_delete_node(name, nid)

            hits_after = idx.search_bm25("solar", limit=5)
            assert not any(h["node_id"] == nid for h in hits_after)
        finally:
            _lmdb_indexes.pop(name, None)
            idx.close()

    def test_lmdb_reindex_node_updates_index(self, tmp_path):
        from contextcore.search.lmdb_index import LMDBIndex, lmdb_reindex_node, _lmdb_indexes
        name = f"helper_test_{uuid.uuid4().hex[:8]}"
        idx = LMDBIndex(str(tmp_path / name))
        _lmdb_indexes[name] = idx
        try:
            nid = str(uuid.uuid4())
            idx.index_node(nid, "Fact", {"name": "old content", "statement": "blockchain is decentralized"})
            lmdb_reindex_node(name, nid, "Fact", {"name": "new content", "statement": "kubernetes orchestrates containers"})

            old_hits = idx.search_bm25("blockchain", limit=5)
            assert not any(h["node_id"] == nid for h in old_hits)

            new_hits = idx.search_bm25("kubernetes", limit=5)
            assert any(h["node_id"] == nid for h in new_hits)
        finally:
            _lmdb_indexes.pop(name, None)
            idx.close()

    def test_remove_lmdb_index_cleans_up(self, tmp_path):
        from contextcore.search.lmdb_index import LMDBIndex, remove_lmdb_index, _lmdb_indexes, get_lmdb_index
        name = f"drop_test_{uuid.uuid4().hex[:8]}"
        # Create an index at a known path inside tmp_path by overriding the singleton
        idx_path = str(tmp_path / name)
        idx = LMDBIndex(idx_path)
        idx.index_node(str(uuid.uuid4()), "Fact", {"name": "temporary"})
        _lmdb_indexes[name] = idx

        # We can't easily override the path constant here, so just test
        # that the function evicts the singleton and closes the env
        remove_lmdb_index(name)

        assert name not in _lmdb_indexes, "singleton must be evicted"
        # env should be closed (accessing it again should create a new one)
        idx2 = get_lmdb_index(name)  # creates fresh with default path
        assert idx2 is not idx

    def test_helpers_are_silent_on_bad_namespace(self):
        from contextcore.search.lmdb_index import lmdb_delete_node, lmdb_reindex_node, remove_lmdb_index
        # Must never raise
        lmdb_delete_node("", "any-id")
        lmdb_delete_node("nonexistent_ns_xyz", "any-id")
        lmdb_reindex_node("", "any-id", "Fact", {"name": "test"})
        remove_lmdb_index("")
        remove_lmdb_index("nonexistent_graph_xyz_abc")
