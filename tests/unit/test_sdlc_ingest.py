"""Unit tests for sdlc_ingest.ingest_sdlc_nodes."""
import pytest


NODES = [
    {"id": "req:login", "label": "Requirement",
     "properties": {"content": "Users must authenticate.", "version": 1}},
    {"id": "arch:jwt", "label": "ArchDecision",
     "properties": {"decision": "Use JWT.", "rationale": "Stateless.", "version": 1}},
    {"id": "code:auth", "label": "CodeModule",
     "properties": {"summary": "Handles auth.", "version": 1}},
]

EDGES = [
    {"label": "GOVERNS", "source": "arch:jwt", "target": "code:auth"},
]


class TestIngestSdlcNodes:
    def test_nodes_written_to_db(self, tmp_path):
        from contextcore.core.hybrid_graph_storage import AIContextDB
        from contextcore.ingestion.sdlc_ingest import ingest_sdlc_nodes

        db = AIContextDB(name="test-ingest-nodes", config={"base_path": str(tmp_path)})
        ingest_sdlc_nodes(db, "test-ingest-nodes", NODES, EDGES)

        node = db.get_node("req:login")
        assert node is not None
        assert node.label == "Requirement"
        assert node.properties["content"] == "Users must authenticate."

    def test_all_node_ids_tracked_in_graph_ctx(self, tmp_path):
        from contextcore.core.hybrid_graph_storage import AIContextDB
        from contextcore.ingestion.sdlc_ingest import ingest_sdlc_nodes

        db = AIContextDB(name="test-ingest-track", config={"base_path": str(tmp_path)})
        ctx = ingest_sdlc_nodes(db, "test-ingest-track", NODES, EDGES)

        assert "req:login" in ctx.node_ids
        assert "arch:jwt" in ctx.node_ids
        assert "code:auth" in ctx.node_ids

    def test_edges_written_to_db(self, tmp_path):
        from contextcore.core.hybrid_graph_storage import AIContextDB
        from contextcore.ingestion.sdlc_ingest import ingest_sdlc_nodes

        db = AIContextDB(name="test-ingest-edges", config={"base_path": str(tmp_path)})
        ctx = ingest_sdlc_nodes(db, "test-ingest-edges", NODES, EDGES)

        assert ctx.edge_count == 1

    def test_empty_nodes_and_edges_is_safe(self, tmp_path):
        from contextcore.core.hybrid_graph_storage import AIContextDB
        from contextcore.ingestion.sdlc_ingest import ingest_sdlc_nodes

        db = AIContextDB(name="test-ingest-empty", config={"base_path": str(tmp_path)})
        ctx = ingest_sdlc_nodes(db, "test-ingest-empty", [], [])

        assert ctx.node_ids == []
        assert ctx.edge_count == 0
        assert ctx.errors == []

    def test_labels_preserved_exactly(self, tmp_path):
        from contextcore.core.hybrid_graph_storage import AIContextDB
        from contextcore.ingestion.sdlc_ingest import ingest_sdlc_nodes

        db = AIContextDB(name="test-ingest-labels", config={"base_path": str(tmp_path)})
        ingest_sdlc_nodes(db, "test-ingest-labels", NODES, [])

        assert db.get_node("arch:jwt").label == "ArchDecision"
        assert db.get_node("code:auth").label == "CodeModule"

    def test_node_count_matches_input(self, tmp_path):
        from contextcore.core.hybrid_graph_storage import AIContextDB
        from contextcore.ingestion.sdlc_ingest import ingest_sdlc_nodes

        db = AIContextDB(name="test-ingest-count", config={"base_path": str(tmp_path)})
        ctx = ingest_sdlc_nodes(db, "test-ingest-count", NODES, EDGES)

        assert len(ctx.node_ids) == len(NODES)
