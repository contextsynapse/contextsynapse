"""Tests that search index stays consistent across UPDATE and DELETE operations."""
import uuid
import pytest
from contextcore.core.registry import GraphRegistry
from contextcore.core.graph_structures import GraphNode
from contextcore.search.lmdb_index import get_lmdb_index
from contextcore.aiql import AIQLExecutor


@pytest.fixture
def graph_and_executor():
    reg = GraphRegistry()
    name = f"sync_test_{uuid.uuid4().hex[:8]}"
    g = reg.create_graph(name)
    ex = AIQLExecutor(contextcore=g, graph_registry=reg)
    ex.active_namespace = name
    yield name, g, ex
    reg.delete_graph(name)


class TestUpdateReindex:

    def test_update_node_new_content_is_searchable(self, graph_and_executor):
        name, g, ex = graph_and_executor
        nid = str(uuid.uuid4())
        g.add_node(GraphNode(id=nid, label="Fact",
                             properties={"name": "Old", "statement": "quantum entanglement is real"}),
                   write_through=True)
        idx = get_lmdb_index(name)
        idx.index_node(nid, "Fact", {"name": "Old", "statement": "quantum entanglement is real"})

        # Update via AIQL (grammar requires braces around SET clause)
        ex.execute(f'UPDATE NODE Fact SET {{ statement: "dark matter dominates the universe" }} WHERE id = "{nid}"')

        # New content must be findable
        hits = idx.search_bm25("dark matter", limit=5)
        assert any(h["node_id"] == nid for h in hits), "updated content must be searchable"

    def test_update_node_old_content_not_returned(self, graph_and_executor):
        name, g, ex = graph_and_executor
        nid = str(uuid.uuid4())
        g.add_node(GraphNode(id=nid, label="Fact",
                             properties={"name": "Fact", "statement": "blockchain distributed ledger"}),
                   write_through=True)
        idx = get_lmdb_index(name)
        idx.index_node(nid, "Fact", {"name": "Fact", "statement": "blockchain distributed ledger"})

        ex.execute(f'UPDATE NODE Fact SET {{ statement: "neural network deep learning" }} WHERE id = "{nid}"')

        old_hits = idx.search_bm25("blockchain", limit=5)
        assert not any(h["node_id"] == nid for h in old_hits), "stale content must be removed"


class TestDeleteCleanup:

    def test_shortcut_delete_removes_from_lmdb(self, graph_and_executor):
        name, g, ex = graph_and_executor
        nid = str(uuid.uuid4())
        g.add_node(GraphNode(id=nid, label="Fact",
                             properties={"name": "Gravitons", "statement": "gravitons mediate gravity"}),
                   write_through=True)
        idx = get_lmdb_index(name)
        idx.index_node(nid, "Fact", {"name": "Gravitons", "statement": "gravitons mediate gravity"})

        # Shortcut delete: DELETE NODE "uuid"
        ex.execute(f'DELETE NODE "{nid}"')

        hits = idx.search_bm25("gravitons", limit=5)
        assert not any(h["node_id"] == nid for h in hits), "deleted node must not appear in search"

    def test_bulk_delete_removes_from_lmdb(self, graph_and_executor):
        name, g, ex = graph_and_executor
        nid1, nid2 = str(uuid.uuid4()), str(uuid.uuid4())
        idx = get_lmdb_index(name)
        for nid in (nid1, nid2):
            g.add_node(GraphNode(id=nid, label="Observation",
                                 properties={"name": f"obs_{nid[:4]}", "statement": "supernova remnants expand rapidly"}),
                       write_through=True)
            idx.index_node(nid, "Observation", {"name": f"obs_{nid[:4]}", "statement": "supernova remnants expand rapidly"})

        # Bulk delete: DELETE NODE Observation WHERE ...
        ex.execute('DELETE NODE Observation WHERE statement CONTAINS "supernova"')

        hits = idx.search_bm25("supernova", limit=10)
        ids_in_hits = {h["node_id"] for h in hits}
        assert nid1 not in ids_in_hits
        assert nid2 not in ids_in_hits


class TestRestApiDeleteCleanup:

    def test_api_delete_node_removes_from_lmdb(self):
        """REST DELETE /graph/nodes/{id} must clean LMDB (tests the helper path)."""
        from contextcore.search.lmdb_index import lmdb_delete_node, get_lmdb_index
        name = f"api_del_{uuid.uuid4().hex[:8]}"
        nid = str(uuid.uuid4())
        idx = get_lmdb_index(name)
        idx.index_node(nid, "Finding", {"name": "API node", "statement": "this was added via api"})

        hits_before = idx.search_bm25("api", limit=5)
        assert any(h["node_id"] == nid for h in hits_before)

        lmdb_delete_node(name, nid)

        hits_after = idx.search_bm25("api", limit=5)
        assert not any(h["node_id"] == nid for h in hits_after)

    def test_remove_lmdb_index_evicts_and_cleans(self, tmp_path):
        """remove_lmdb_index evicts singleton and clears disk path."""
        from contextcore.search.lmdb_index import (
            LMDBIndex, remove_lmdb_index, get_lmdb_index, _lmdb_indexes
        )
        name = f"graph_drop_{uuid.uuid4().hex[:8]}"
        idx = LMDBIndex(str(tmp_path / name))
        idx.index_node(str(uuid.uuid4()), "Fact", {"name": "temporary node"})
        _lmdb_indexes[name] = idx

        remove_lmdb_index(name)

        assert name not in _lmdb_indexes, "singleton must be evicted after graph drop"


class TestPruningCleanup:

    def test_pruner_removes_from_lmdb(self):
        from contextcore.core.pruning import ContextPruner, PruningPolicy
        from datetime import datetime, timezone, timedelta

        reg = GraphRegistry()
        name = f"prune_test_{uuid.uuid4().hex[:8]}"
        g = reg.create_graph(name)

        # Add a stale noise node (100h old — beyond the 72h noise cutoff)
        # Use "SearchResult" which is in the noise set but not filtered by LMDB
        # (LMDB _NOISE_LABELS filters AgentThought etc.; use a pruner-noise label that LMDB indexes)
        # Easiest: use a label that the pruner treats as noise but LMDB doesn't filter.
        # PruningPolicy.noise_labels defaults: check what's in there vs _NOISE_LABELS.
        # Simplest: use a custom label via policy.noise_labels=['Observation'].
        nid = str(uuid.uuid4())
        old_time = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()
        g.add_node(GraphNode(id=nid, label="Observation",
                             properties={"name": "stale obs",
                                         "statement": "ephemeral observation content",
                                         "_created_at": old_time}),
                   write_through=True)

        idx = get_lmdb_index(name)
        idx.index_node(nid, "Observation", {"name": "stale obs",
                                             "statement": "ephemeral observation content",
                                             "_created_at": old_time})
        assert any(h["node_id"] == nid for h in idx.search_bm25("ephemeral", limit=5))

        policy = PruningPolicy(noise_labels=["Observation"])
        pruner = ContextPruner()
        pruner.prune(g, policy=policy)

        hits = idx.search_bm25("ephemeral", limit=5)
        assert not any(h["node_id"] == nid for h in hits), "pruned node must not appear in search"
        reg.delete_graph(name)
