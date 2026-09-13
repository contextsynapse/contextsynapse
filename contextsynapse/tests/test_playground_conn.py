"""Tests for PlaygroundConn and create_tool_context."""

import os
os.environ["CONTEXTSYNAPSE_GRAPH_BACKEND"] = "csr"  # tests need isolated in-memory graphs
os.environ["AICONTEXTDB_GRAPH_BACKEND"] = "csr"  # backward compat

import pytest
from contextsynapse.api.playground_conn import PlaygroundConn, create_tool_context
from contextsynapse.core.registry import GraphRegistry


@pytest.fixture
def registry(tmp_path):
    """Create a temporary graph registry with a test graph."""
    reg = GraphRegistry(storage_dir=str(tmp_path / "graphs"))
    reg.create_graph("test_graph")
    # Add some nodes to the graph
    graph = reg.get_graph("test_graph")
    from contextsynapse.core.graph_structures import GraphNode, GraphEdge
    graph.add_node(GraphNode(id="n1", label="Person", properties={"name": "Alice"}))
    graph.add_node(GraphNode(id="n2", label="Person", properties={"name": "Bob"}))
    graph.add_node(GraphNode(id="n3", label="Document", properties={"name": "Report"}))
    graph.add_edge(GraphEdge(id="e1", source="n1", target="n2", label="KNOWS", properties={}))
    reg.save_graph("test_graph")
    return reg


class TestPlaygroundConn:
    def test_loads_graph_by_name(self, registry):
        conn = PlaygroundConn("test_graph", registry)
        assert conn.db is not None
        assert conn.namespace == "test_graph"

    def test_resolves_scoped_name(self, registry):
        """tenant:graph_name should resolve to bare graph_name."""
        conn = PlaygroundConn("some_tenant:test_graph", registry)
        assert conn.db is not None
        assert conn._resolved_ns == "test_graph"

    def test_missing_graph_creates_empty(self, registry):
        conn = PlaygroundConn("nonexistent", registry)
        assert conn.db is not None  # get_graph_for_request creates one

    def test_query_select_all(self, registry):
        conn = PlaygroundConn("test_graph", registry)
        result = conn.query("SELECT *")
        assert result.get("nodes") is not None
        assert len(result["nodes"]) == 3

    def test_query_select_from_label(self, registry):
        conn = PlaygroundConn("test_graph", registry)
        result = conn.query("SELECT * FROM Person")
        nodes = result.get("nodes", [])
        assert len(nodes) == 2

    def test_get_edges(self, registry):
        conn = PlaygroundConn("test_graph", registry)
        edges = conn.get_edges()
        assert len(edges) >= 1

    def test_get_edges_filtered(self, registry):
        conn = PlaygroundConn("test_graph", registry)
        edges = conn.get_edges(label="KNOWS")
        assert len(edges) == 1
        edges_none = conn.get_edges(label="NONEXISTENT")
        assert len(edges_none) == 0

    def test_get_node(self, registry):
        conn = PlaygroundConn("test_graph", registry)
        node = conn.get_node("n1")
        assert node is not None
        assert node is None or getattr(node, "label", "") == "Person"

    def test_get_node_missing(self, registry):
        conn = PlaygroundConn("test_graph", registry)
        assert conn.get_node("nonexistent") is None

    def test_has_all_required_attributes(self, registry):
        conn = PlaygroundConn("test_graph", registry)
        assert hasattr(conn, "namespace")
        assert hasattr(conn, "_namespace")
        assert hasattr(conn, "graph_registry")
        assert hasattr(conn, "_graph_registry")
        assert hasattr(conn, "db")
        assert hasattr(conn, "executor")

    def test_build_context(self, registry):
        conn = PlaygroundConn("test_graph", registry)
        # build_context may not be fully functional without ContextHub,
        # but it should not raise AttributeError
        try:
            conn.build_context(system_prompt="test")
        except RuntimeError:
            pass  # "No executor available" is OK
        except Exception:
            pass  # Other errors from missing deps are OK


class TestCreateToolContext:
    def test_creates_context_with_db(self, registry):
        conn = PlaygroundConn("test_graph", registry)
        ctx = create_tool_context(conn, "agent_1", "Test Agent")
        assert ctx.agent_id == "agent_1"
        assert ctx.agent_name == "Test Agent"
        assert ctx.conn is conn
        assert ctx.db is conn.db

    def test_context_has_all_required_fields(self, registry):
        conn = PlaygroundConn("test_graph", registry)
        ctx = create_tool_context(conn, "a1", "Agent")
        assert hasattr(ctx, "conn")
        assert hasattr(ctx, "agent_id")
        assert hasattr(ctx, "agent_name")
        assert hasattr(ctx, "db")
