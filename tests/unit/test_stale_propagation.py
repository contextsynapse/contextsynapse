import uuid
import pytest
from contextcore.core.hybrid_graph_storage import AIContextDB
from contextcore.core.graph_structures import GraphNode, GraphEdge


def _db():
    return AIContextDB(name=f"test_stale_{uuid.uuid4().hex[:8]}")


def _node(nid, label="Thing", **props):
    return GraphNode(id=nid, label=label, properties=dict(props))


def _edge(src, tgt, label="LINKS"):
    return GraphEdge(id=f"{src}_{tgt}", source=src, target=tgt, label=label)


class TestStalePropagation:
    def test_downstream_node_flagged_stale_on_update(self):
        db = _db()
        db.add_node(_node("req-1", "Requirement", name="Auth req"))
        db.add_node(_node("mod-1", "CodeModule", name="auth.py"))
        db.add_edge(_edge("req-1", "mod-1", "IMPLEMENTS"))
        db.update_node("req-1", {"content": "updated"}, author="agent:a")
        downstream = db.get_node("mod-1")
        assert downstream.properties.get("_stale") is True

    def test_stale_reason_contains_source_name(self):
        db = _db()
        db.add_node(_node("req-1", "Requirement", name="Login flow"))
        db.add_node(_node("mod-1", "CodeModule", name="auth.py"))
        db.add_edge(_edge("req-1", "mod-1", "IMPLEMENTS"))
        db.update_node("req-1", {"content": "new"})
        downstream = db.get_node("mod-1")
        reason = downstream.properties.get("_stale_reason", "")
        assert "Login flow" in reason or "req-1" in reason

    def test_stale_since_timestamp_set(self):
        db = _db()
        db.add_node(_node("n1", name="src"))
        db.add_node(_node("n2", name="dst"))
        db.add_edge(_edge("n1", "n2"))
        db.update_node("n1", {"x": 1})
        n2 = db.get_node("n2")
        assert "_stale_since" in n2.properties

    def test_no_stale_when_no_downstream(self):
        db = _db()
        db.add_node(_node("n1", name="isolated"))
        # Should not raise
        db.update_node("n1", {"x": 1})
        n1 = db.get_node("n1")
        # n1 itself is not stale — only downstream nodes are
        assert n1.properties.get("_stale") is not True

    def test_only_direct_neighbors_flagged(self):
        db = _db()
        db.add_node(_node("a", name="A"))
        db.add_node(_node("b", name="B"))
        db.add_node(_node("c", name="C"))
        db.add_edge(_edge("a", "b"))
        db.add_edge(_edge("b", "c"))
        db.update_node("a", {"x": 1})
        # b is direct neighbor — stale
        assert db.get_node("b").properties.get("_stale") is True
        # c is two hops away — NOT stale (one hop only)
        assert db.get_node("c").properties.get("_stale") is not True

    def test_multiple_downstream_nodes_all_flagged(self):
        db = _db()
        db.add_node(_node("src", name="source"))
        db.add_node(_node("d1", name="dep1"))
        db.add_node(_node("d2", name="dep2"))
        db.add_edge(_edge("src", "d1"))
        db.add_edge(_edge("src", "d2"))
        db.update_node("src", {"x": 1})
        assert db.get_node("d1").properties.get("_stale") is True
        assert db.get_node("d2").properties.get("_stale") is True


class TestConfirmCurrent:
    def test_confirm_clears_stale(self):
        db = _db()
        db.add_node(_node("n1", name="src"))
        db.add_node(_node("n2", name="dst"))
        db.add_edge(_edge("n1", "n2"))
        db.update_node("n1", {"x": 1})
        assert db.get_node("n2").properties.get("_stale") is True
        db.confirm_current("n2", author="agent:reviewer")
        n2 = db.get_node("n2")
        assert n2.properties.get("_stale") is False
        assert "_stale_reason" not in n2.properties
        assert n2.properties.get("_stale_confirmed_by") == "agent:reviewer"

    def test_confirm_sets_confirmed_at(self):
        db = _db()
        db.add_node(_node("n1", name="src"))
        db.add_node(_node("n2", name="dst"))
        db.add_edge(_edge("n1", "n2"))
        db.update_node("n1", {"x": 1})
        db.confirm_current("n2")
        n2 = db.get_node("n2")
        assert "_stale_confirmed_at" in n2.properties

    def test_confirm_nonexistent_raises(self):
        db = _db()
        with pytest.raises(KeyError):
            db.confirm_current("nonexistent_xyz")
