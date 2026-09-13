"""Tests for versioning MCP tools: get_stale_nodes, confirm_current,
restore_version, diff_versions, and the updated get_versions."""
import uuid
import pytest
from unittest.mock import MagicMock, patch
from contextcore.core.hybrid_graph_storage import AIContextDB
from contextcore.core.graph_structures import GraphNode, GraphEdge
from contextcore.tools.registry import ToolContext


def _db():
    return AIContextDB(name=f"test_vtool_{uuid.uuid4().hex[:8]}")


def _node(nid, label="Thing", **props):
    return GraphNode(id=nid, label=label, properties=dict(props))


def _edge(src, tgt, label="LINKS"):
    return GraphEdge(id=f"{src}_{tgt}", source=src, target=tgt, label=label)


def _ctx(db):
    conn = MagicMock()
    conn.contextcore = db
    conn.agent_id = "agent:test"
    ctx = MagicMock(spec=ToolContext)
    ctx.conn = conn
    return ctx


class TestGetStaleNodesTool:
    def test_no_stale_nodes(self):
        from contextcore.tools.graph import _get_stale_nodes
        db = _db()
        db.add_node(_node("n1", name="clean"))
        result = _get_stale_nodes(_ctx(db))
        assert "No stale" in result or "0" in result

    def test_stale_node_appears(self):
        from contextcore.tools.graph import _get_stale_nodes
        db = _db()
        db.add_node(_node("src", name="source"))
        db.add_node(_node("dst", name="downstream"))
        db.add_edge(_edge("src", "dst"))
        db.update_node("src", {"x": 1})
        result = _get_stale_nodes(_ctx(db))
        assert "downstream" in result or "stale" in result.lower()


class TestConfirmCurrentTool:
    def test_confirm_clears_stale(self):
        from contextcore.tools.graph import _confirm_current
        db = _db()
        db.add_node(_node("src", name="source"))
        db.add_node(_node("dst", name="downstream"))
        db.add_edge(_edge("src", "dst"))
        db.update_node("src", {"x": 1})
        result = _confirm_current(_ctx(db), node_id="dst")
        assert "Confirmed" in result or "confirmed" in result.lower()
        assert db.get_node("dst").properties.get("_stale") is False

    def test_confirm_missing_node_id(self):
        from contextcore.tools.graph import _confirm_current
        db = _db()
        result = _confirm_current(_ctx(db), node_id="")
        assert "Error" in result

    def test_confirm_nonexistent_node(self):
        from contextcore.tools.graph import _confirm_current
        db = _db()
        result = _confirm_current(_ctx(db), node_id="no-such-node-xyz")
        assert "No node" in result or "not found" in result.lower()


class TestRestoreVersionTool:
    def test_restore_version(self):
        from contextcore.tools.graph import _restore_version
        db = _db()
        db.add_node(_node("n1", name="A", x=1))
        db.update_node("n1", {"x": 2})
        result = _restore_version(_ctx(db), node_id="n1", version="1", reason="rollback")
        assert "Restored" in result or "v1" in result
        assert db.get_node("n1").properties.get("x") == 1

    def test_restore_missing_args(self):
        from contextcore.tools.graph import _restore_version
        db = _db()
        result = _restore_version(_ctx(db), node_id="", version="")
        assert "Error" in result

    def test_restore_nonexistent_version(self):
        from contextcore.tools.graph import _restore_version
        db = _db()
        db.add_node(_node("n1", name="A"))
        result = _restore_version(_ctx(db), node_id="n1", version="99")
        assert "No version" in result or "not found" in result.lower()


class TestDiffVersionsTool:
    def test_diff_shows_change(self):
        from contextcore.tools.graph import _diff_versions
        db = _db()
        db.add_node(_node("n1", name="A", value="hello"))
        db.update_node("n1", {"value": "world"})
        result = _diff_versions(_ctx(db), node_id="n1", v1="1", v2="2")
        assert "value" in result or "hello" in result or "world" in result

    def test_diff_missing_args(self):
        from contextcore.tools.graph import _diff_versions
        db = _db()
        result = _diff_versions(_ctx(db), node_id="", v1="", v2="")
        assert "Error" in result


class TestGetVersionsToolUpdated:
    def test_get_versions_uses_temporal_storage(self):
        from contextcore.tools.graph import _get_versions
        db = _db()
        db.add_node(_node("n1", name="A", x=1))
        db.update_node("n1", {"x": 2}, author="agent:a", reason="test")
        result = _get_versions(_ctx(db), node_id="n1")
        # Should show at least one version
        assert "v" in result and ("UPDATE" in result or "history" in result.lower() or "version" in result.lower())

    def test_get_versions_missing_node_id(self):
        from contextcore.tools.graph import _get_versions
        db = _db()
        result = _get_versions(_ctx(db), node_id="")
        assert "Error" in result
