"""Tests that hybrid engine is wired into the API search endpoints."""
import uuid
import pytest
from contextcore.core.registry import GraphRegistry
from contextcore.core.graph_structures import GraphNode
from contextcore.search.lmdb_index import get_lmdb_index


@pytest.fixture
def populated_graph():
    reg = GraphRegistry()
    name = f"api_search_{uuid.uuid4().hex[:8]}"
    g = reg.create_graph(name)
    nid = str(uuid.uuid4())
    g.add_node(GraphNode(id=nid, label="Finding",
                         properties={"name": "Photosynthesis",
                                     "statement": "plants convert sunlight to energy"}),
               write_through=True)
    idx = get_lmdb_index(name)
    idx.index_node(nid, "Finding", {"name": "Photosynthesis",
                                    "statement": "plants convert sunlight to energy"})
    yield name, g, reg, nid
    reg.delete_graph(name)


class TestHybridSearchAPI:

    def test_graph_search_returns_indexed_node(self, populated_graph):
        """graph_search() returns the LMDB-indexed node."""
        from contextcore.search.graph_search import graph_search
        name, g, reg, nid = populated_graph

        result = graph_search(g, "photosynthesis sunlight", graph_name=name, k=10)
        assert result.nodes, "hybrid search must return results from LMDB"
        assert any(sn.node_id == nid for sn in result.nodes)

    def test_graph_search_fallback_when_index_empty(self):
        """When LMDB has no entries, graph_search returns empty gracefully."""
        from contextcore.search.graph_search import graph_search
        reg = GraphRegistry()
        name = f"empty_idx_{uuid.uuid4().hex[:8]}"
        g = reg.create_graph(name)
        nid = str(uuid.uuid4())
        g.add_node(GraphNode(id=nid, label="Fact",
                             properties={"name": "Fusion energy",
                                         "statement": "fusion powers the sun"}),
                   write_through=True)
        # Do NOT index into LMDB — test that graph_search handles empty gracefully
        result = graph_search(g, "fusion", graph_name=name, k=10)
        assert result is not None
        reg.delete_graph(name)


class TestPassageStorage:

    def test_store_and_get_passage(self, tmp_path):
        """store_passage saves full text; get_passage retrieves it."""
        from contextcore.search.lmdb_index import LMDBIndex
        idx = LMDBIndex(str(tmp_path / "passage_test"))
        nid = str(uuid.uuid4())
        long_text = "The mitochondria is the powerhouse of the cell. " * 20
        idx.index_node(nid, "Fact", {"name": "Biology", "statement": long_text})
        idx.store_passage(nid, long_text)

        passage = idx.get_passage(nid)
        assert passage is not None
        assert len(passage) > 200
        assert "mitochondria" in passage
        idx.close()
