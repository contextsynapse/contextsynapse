"""
Tests for LMDBGraphStorage — node CRUD with msgpack serialization.
"""
import pytest

# Skip entire module if lmdb is not installed
lmdb = pytest.importorskip("lmdb")
msgpack = pytest.importorskip("msgpack")

from contextcore.storage.lmdb_graph_storage import LMDBGraphStorage, LMDBNode, LMDB_AVAILABLE


def test_lmdb_available():
    assert LMDB_AVAILABLE is True


def test_add_and_get_node(tmp_path):
    store = LMDBGraphStorage(path=str(tmp_path / "db"), namespace="test")
    try:
        result = store.add_node("n1", "Person", {"name": "Alice", "age": 30})
        assert result is True

        node = store.get_node("n1")
        assert node is not None
        assert node.id == "n1"
        assert node.node_type == "Person"
        assert node.properties["name"] == "Alice"
        assert node.properties["age"] == 30
        assert node.label == "Person"
    finally:
        store.close()


def test_add_duplicate_node_returns_false(tmp_path):
    store = LMDBGraphStorage(path=str(tmp_path / "db"))
    try:
        store.add_node("n1", "Person", {"name": "Alice"})
        result = store.add_node("n1", "Person", {"name": "Bob"})
        assert result is False
        # Original node should be unchanged
        node = store.get_node("n1")
        assert node.properties["name"] == "Alice"
    finally:
        store.close()


def test_get_nonexistent_node(tmp_path):
    store = LMDBGraphStorage(path=str(tmp_path / "db"))
    try:
        node = store.get_node("does_not_exist")
        assert node is None
    finally:
        store.close()


def test_update_node_properties(tmp_path):
    store = LMDBGraphStorage(path=str(tmp_path / "db"))
    try:
        store.add_node("n1", "Person", {"name": "Alice", "age": 30})
        store.update_node_properties("n1", {"age": 31, "city": "NYC"})

        node = store.get_node("n1")
        assert node.properties["name"] == "Alice"   # unchanged
        assert node.properties["age"] == 31          # updated
        assert node.properties["city"] == "NYC"      # added
    finally:
        store.close()


def test_delete_node(tmp_path):
    store = LMDBGraphStorage(path=str(tmp_path / "db"))
    try:
        store.add_node("n1", "Person", {"name": "Alice"})
        result = store.delete_node("n1")
        assert result is True

        node = store.get_node("n1")
        assert node is None

        # Deleting again returns False
        result2 = store.delete_node("n1")
        assert result2 is False
    finally:
        store.close()


def test_get_all_nodes(tmp_path):
    store = LMDBGraphStorage(path=str(tmp_path / "db"))
    try:
        store.add_node("n1", "Person", {"name": "Alice"})
        store.add_node("n2", "Company", {"name": "Acme"})

        nodes = list(store.get_all_nodes())
        assert len(nodes) == 2

        ids = {n.id for n in nodes}
        assert "n1" in ids
        assert "n2" in ids

        labels = {n.label for n in nodes}
        assert "Person" in labels
        assert "Company" in labels
    finally:
        store.close()


def test_node_count(tmp_path):
    store = LMDBGraphStorage(path=str(tmp_path / "db"))
    try:
        assert store.get_node_count() == 0

        store.add_node("n1", "Person", {"name": "Alice"})
        assert store.get_node_count() == 1

        store.add_node("n2", "Person", {"name": "Bob"})
        assert store.get_node_count() == 2

        store.delete_node("n1")
        assert store.get_node_count() == 1
    finally:
        store.close()


def test_returns_copies_not_references(tmp_path):
    store = LMDBGraphStorage(path=str(tmp_path / "db"))
    try:
        store.add_node("n1", "Person", {"name": "Alice", "score": 10})

        node = store.get_node("n1")
        assert node is not None

        # Mutate the returned node's properties
        node.properties["score"] = 999
        node.properties["extra"] = "injected"

        # Re-fetch — storage must be unaffected
        node2 = store.get_node("n1")
        assert node2.properties["score"] == 10
        assert "extra" not in node2.properties
    finally:
        store.close()


def test_stats_returns_dict(tmp_path):
    store = LMDBGraphStorage(path=str(tmp_path / "db"))
    try:
        store.add_node("n1", "Person", {"name": "Alice"})
        s = store.stats()
        assert isinstance(s, dict)
        assert "node_count" in s
        assert s["node_count"] == 1
    finally:
        store.close()


def test_type_index_on_startup(tmp_path):
    """Type index is rebuilt correctly when re-opening an existing store."""
    db_path = str(tmp_path / "db")

    store = LMDBGraphStorage(path=db_path)
    store.add_node("n1", "Person", {"name": "Alice"})
    store.add_node("n2", "Company", {"name": "Acme"})
    store.close()

    # Re-open — index must be rebuilt from persisted data
    store2 = LMDBGraphStorage(path=db_path)
    try:
        assert store2.get_node_count() == 2
        nodes = list(store2.get_all_nodes())
        ids = {n.id for n in nodes}
        assert "n1" in ids
        assert "n2" in ids
    finally:
        store2.close()


# ------------------------------------------------------------------
# Edge CRUD tests
# ------------------------------------------------------------------

def test_add_and_get_edge(tmp_path):
    store = LMDBGraphStorage(path=str(tmp_path / "db"))
    try:
        store.add_node("a", "Person", {"name": "Alice"})
        store.add_node("b", "Person", {"name": "Bob"})
        result = store.add_edge("a", "b", "KNOWS", {"since": 2020})
        assert result is True

        edges = store.get_edges()
        assert len(edges) == 1
        e = edges[0]
        assert e["source"] == "a"
        assert e["target"] == "b"
        assert e["label"] == "KNOWS"
        assert e["properties"]["since"] == 2020
    finally:
        store.close()


def test_add_edge_missing_node(tmp_path):
    store = LMDBGraphStorage(path=str(tmp_path / "db"))
    try:
        store.add_node("a", "Person", {})
        # target "z" does not exist
        result = store.add_edge("a", "z", "KNOWS")
        assert result is False
        # source "z" does not exist
        result2 = store.add_edge("z", "a", "KNOWS")
        assert result2 is False
        # confirm no edges were stored
        assert store.get_edge_count() == 0
    finally:
        store.close()


def test_get_edges_for_node(tmp_path):
    store = LMDBGraphStorage(path=str(tmp_path / "db"))
    try:
        store.add_node("a", "Person", {})
        store.add_node("b", "Person", {})
        store.add_node("c", "Company", {})

        store.add_edge("a", "b", "KNOWS")       # involves a and b
        store.add_edge("b", "c", "WORKS_AT")    # involves b and c
        store.add_edge("a", "c", "LOCATED_IN")  # involves a and c

        # edges for node "a": should be KNOWS and LOCATED_IN
        edges_a = store.get_edges(node_id="a")
        assert len(edges_a) == 2
        labels_a = {e["label"] for e in edges_a}
        assert labels_a == {"KNOWS", "LOCATED_IN"}

        # edges for node "b": should be KNOWS and WORKS_AT
        edges_b = store.get_edges(node_id="b")
        assert len(edges_b) == 2
        labels_b = {e["label"] for e in edges_b}
        assert labels_b == {"KNOWS", "WORKS_AT"}

        # edges for node "c": should be WORKS_AT and LOCATED_IN
        edges_c = store.get_edges(node_id="c")
        assert len(edges_c) == 2
        labels_c = {e["label"] for e in edges_c}
        assert labels_c == {"WORKS_AT", "LOCATED_IN"}

        # all edges
        all_edges = store.get_edges()
        assert len(all_edges) == 3
    finally:
        store.close()


def test_get_edge_count(tmp_path):
    store = LMDBGraphStorage(path=str(tmp_path / "db"))
    try:
        assert store.get_edge_count() == 0

        store.add_node("a", "Person", {})
        store.add_node("b", "Person", {})
        store.add_edge("a", "b", "KNOWS")
        assert store.get_edge_count() == 1

        store.add_node("c", "Company", {})
        store.add_edge("b", "c", "WORKS_AT")
        assert store.get_edge_count() == 2
    finally:
        store.close()


def test_delete_node_removes_edges(tmp_path):
    store = LMDBGraphStorage(path=str(tmp_path / "db"))
    try:
        store.add_node("a", "Person", {})
        store.add_node("b", "Person", {})
        store.add_edge("a", "b", "KNOWS")
        assert store.get_edge_count() == 1

        # deleting "a" must also remove the edge
        store.delete_node("a")
        assert store.get_edge_count() == 0
        assert store.get_edges() == []
    finally:
        store.close()


def test_remove_edge(tmp_path):
    store = LMDBGraphStorage(path=str(tmp_path / "db"))
    try:
        store.add_node("a", "Person", {})
        store.add_node("b", "Person", {})
        store.add_edge("a", "b", "KNOWS")
        assert store.get_edge_count() == 1

        result = store.remove_edge("a", "b", "KNOWS")
        assert result is True
        assert store.get_edge_count() == 0

        # removing again returns False
        result2 = store.remove_edge("a", "b", "KNOWS")
        assert result2 is False
    finally:
        store.close()
