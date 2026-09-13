"""Tests for AIContextDB core graph storage (CSR backend)."""

import uuid
import pytest
from contextcore.core.hybrid_graph_storage import AIContextDB
from contextcore.core.graph_structures import GraphNode, GraphEdge


@pytest.fixture
def db():
    """Fresh AIContextDB instance with a unique name to avoid cross-test pollution."""
    return AIContextDB(name=f"test_graph_{uuid.uuid4().hex[:8]}")


def _make_node(label="Person", **props):
    return GraphNode(id=str(uuid.uuid4()), label=label, properties=props)


def _make_edge(source, target, rel="KNOWS", **props):
    return GraphEdge(id=str(uuid.uuid4()), source=source, target=target, label=rel, properties=props)


# ── Node CRUD ───────────────────────────────────────────────────────────

class TestNodeCRUD:
    def test_add_and_get_node(self, db):
        node = _make_node(name="Alice", age=30)
        db.add_node(node)
        retrieved = db.get_node(node.id)
        assert retrieved is not None
        assert retrieved.properties["name"] == "Alice"
        assert retrieved.properties["age"] == 30

    def test_get_nonexistent_node(self, db):
        assert db.get_node("nonexistent-id") is None

    def test_get_all_nodes_empty(self, db):
        nodes = db.get_all_nodes()
        assert nodes == []

    def test_get_all_nodes_returns_all(self, db):
        n1 = _make_node(name="A")
        n2 = _make_node(name="B")
        db.add_node(n1)
        db.add_node(n2)
        all_nodes = db.get_all_nodes()
        ids = {n.id for n in all_nodes}
        assert n1.id in ids
        assert n2.id in ids

    def test_get_all_nodes_by_label(self, db):
        db.add_node(_make_node(label="Person", name="A"))
        db.add_node(_make_node(label="Company", name="ACME"))
        persons = db.get_all_nodes(label="Person")
        assert all(n.label == "Person" for n in persons)
        assert len(persons) == 1

    def test_no_duplicate_nodes(self, db):
        """get_all_nodes should not return duplicates (regression test)."""
        for i in range(5):
            db.add_node(_make_node(name=f"N{i}"))
        all_nodes = db.get_all_nodes()
        ids = [n.id for n in all_nodes]
        assert len(ids) == len(set(ids)), "Duplicate node IDs returned"

    def test_add_multiple_labels(self, db):
        db.add_node(_make_node(label="Person", name="A"))
        db.add_node(_make_node(label="Person", name="B"))
        db.add_node(_make_node(label="Company", name="ACME"))
        all_nodes = db.get_all_nodes()
        assert len(all_nodes) == 3

    def test_node_properties_preserved(self, db):
        node = _make_node(name="Alice", age=30, email="alice@example.com")
        db.add_node(node)
        retrieved = db.get_node(node.id)
        assert retrieved.properties["email"] == "alice@example.com"


# ── Edge CRUD ───────────────────────────────────────────────────────────

class TestEdgeCRUD:
    def test_add_and_get_edge(self, db):
        n1 = _make_node(name="A")
        n2 = _make_node(name="B")
        db.add_node(n1)
        db.add_node(n2)
        edge = _make_edge(n1.id, n2.id, rel="KNOWS", since=2020)
        db.add_edge(edge)
        edges = db.get_all_edges()
        assert len(edges) >= 1
        found = [e for e in edges if e.source == n1.id and e.target == n2.id]
        assert len(found) == 1

    def test_get_all_edges_empty(self, db):
        edges = db.get_all_edges()
        assert edges == [] or len(edges) == 0

    def test_edge_properties_preserved(self, db):
        n1 = _make_node(name="A")
        n2 = _make_node(name="B")
        db.add_node(n1)
        db.add_node(n2)
        edge = _make_edge(n1.id, n2.id, rel="WORKS_AT", role="engineer")
        db.add_edge(edge)
        edges = db.get_all_edges()
        found = [e for e in edges if e.label == "WORKS_AT"]
        assert len(found) == 1
        assert found[0].properties.get("role") == "engineer"
