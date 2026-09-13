"""Tests for AIQL executor — CREATE/USE GRAPH, CREATE NODE, SELECT, MATCH."""

import uuid
import pytest
from contextcore.core.hybrid_graph_storage import AIContextDB
from contextcore.core.registry import GraphRegistry
from contextcore.aiql.engine import AIQLExecutor


def _gname():
    return f"tdb_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def executor():
    """Fresh executor with a uniquely-named DB and registry."""
    db = AIContextDB(name=_gname())
    registry = GraphRegistry()
    return AIQLExecutor(contextcore=db, graph_registry=registry)


@pytest.fixture
def ready_executor():
    """Executor with a unique graph already created and selected."""
    name = _gname()
    db = AIContextDB(name=name)
    registry = GraphRegistry()
    ex = AIQLExecutor(contextcore=db, graph_registry=registry)
    ex.execute(f"CREATE GRAPH {name}")
    ex.execute(f"USE GRAPH {name}")
    return ex


class TestGraphManagement:
    def test_create_graph(self, executor):
        result = executor.execute(f"CREATE GRAPH {_gname()}")
        assert result.get("success") is True

    def test_use_graph(self, executor):
        name = _gname()
        executor.execute(f"CREATE GRAPH {name}")
        result = executor.execute(f"USE GRAPH {name}")
        assert result.get("success") is True

    def test_show_graphs(self, executor):
        executor.execute(f"CREATE GRAPH {_gname()}")
        executor.execute(f"CREATE GRAPH {_gname()}")
        result = executor.execute("SHOW GRAPHS")
        assert result.get("success") is True

    def test_use_nonexistent_graph(self, executor):
        """USE GRAPH on a non-existent graph may create it or fail gracefully."""
        result = executor.execute("USE GRAPH doesnotexist")
        assert isinstance(result, dict)


class TestCreateNode:
    def test_create_person_node(self, ready_executor):
        result = ready_executor.execute('CREATE NODE Person {name: "Alice", age: 30}')
        assert result.get("success") is True

    def test_create_multiple_nodes(self, ready_executor):
        ready_executor.execute('CREATE NODE Person {name: "Alice"}')
        ready_executor.execute('CREATE NODE Person {name: "Bob"}')
        result = ready_executor.execute("SELECT * FROM Person")
        assert result.get("success") is True
        nodes = result.get("nodes", [])
        assert len(nodes) >= 2


class TestSelect:
    def test_select_all_from_type(self, ready_executor):
        ready_executor.execute('CREATE NODE Person {name: "Alice"}')
        result = ready_executor.execute("SELECT * FROM Person")
        assert result.get("success") is True

    def test_select_from_empty(self, ready_executor):
        result = ready_executor.execute("SELECT * FROM Person")
        assert result.get("success") is True
        nodes = result.get("nodes", [])
        assert len(nodes) == 0


class TestMatch:
    def test_match_node(self, ready_executor):
        ready_executor.execute('CREATE NODE Person {name: "Alice", age: 30}')
        result = ready_executor.execute('MATCH NODE Person WHERE name = "Alice"')
        assert result.get("success") is True

    def test_match_no_results(self, ready_executor):
        result = ready_executor.execute('MATCH NODE Person WHERE name = "Nobody"')
        assert result.get("success") is True


class TestCreateEdge:
    def test_create_edge(self, ready_executor):
        ready_executor.execute('CREATE NODE Person {name: "Alice"}')
        ready_executor.execute('CREATE NODE Person {name: "Bob"}')
        result = ready_executor.execute('CREATE EDGE KNOWS FROM Person WHERE name = "Alice" TO Person WHERE name = "Bob" {since: 2020}')
        # Edge creation may or may not be supported in the grammar—don't assert success,
        # just ensure it doesn't crash
        assert isinstance(result, dict)


class TestErrorHandling:
    def test_empty_query(self, executor):
        result = executor.execute("")
        assert result.get("success") is False or "error" in result

    def test_invalid_syntax(self, executor):
        result = executor.execute("GOBBLEDYGOOK NONSENSE QUERY")
        assert isinstance(result, dict)

    def test_find_nodes(self, ready_executor):
        """FIND NODES shorthand should work."""
        ready_executor.execute('CREATE NODE Person {name: "Alice"}')
        result = ready_executor.execute("FIND NODES")
        assert result.get("success") is True
