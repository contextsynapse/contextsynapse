import uuid
import pytest
from contextcore.core.hybrid_graph_storage import AIContextDB
from contextcore.core.graph_structures import GraphNode
from contextcore.core.time_travel import GraphTimeTraveler


def _db():
    return AIContextDB(name=f"test_tt_{uuid.uuid4().hex[:8]}")


def _node(nid, **props):
    return GraphNode(id=nid, label="Thing", properties=dict(props))


def _tt(db):
    return GraphTimeTraveler(db)


class TestGetNodeAtVersion:
    def test_get_at_version_after_update(self):
        db = _db()
        db.add_node(_node("n1", name="Alice", x=1))
        db.update_node("n1", {"x": 2}, author="agent:a")
        # v1 snapshot (taken before the update) should have x=1
        snap = _tt(db).get_node_at_version("n1", 1)
        assert snap is not None
        assert snap["properties"]["x"] == 1

    def test_get_nonexistent_version_returns_none(self):
        db = _db()
        db.add_node(_node("n1", name="Bob"))
        assert _tt(db).get_node_at_version("n1", 99) is None

    def test_get_unknown_node_returns_none(self):
        db = _db()
        assert _tt(db).get_node_at_version("no-such-id", 1) is None


class TestGetNodeHistory:
    def test_history_has_one_entry_after_one_update(self):
        db = _db()
        db.add_node(_node("n1", name="A", x=1))
        db.update_node("n1", {"x": 2})
        history = _tt(db).get_node_history("n1")
        # At least the UPDATE snapshot is there
        assert len(history) >= 1

    def test_history_grows_with_updates(self):
        db = _db()
        db.add_node(_node("n1", name="A", x=1))
        db.update_node("n1", {"x": 2})
        db.update_node("n1", {"x": 3})
        history = _tt(db).get_node_history("n1")
        assert len(history) >= 2

    def test_history_empty_for_unknown_node(self):
        db = _db()
        assert _tt(db).get_node_history("no-such") == []


class TestDiffNodeVersions:
    def test_diff_shows_changed_field(self):
        db = _db()
        db.add_node(_node("n1", name="A", x=1))
        db.update_node("n1", {"x": 2})
        diff = _tt(db).diff_node_versions("n1", 1, 2)
        assert "changed" in diff
        assert "x" in diff["changed"]
        assert diff["changed"]["x"]["from"] == 1
        assert diff["changed"]["x"]["to"] == 2

    def test_diff_missing_version_returns_error(self):
        db = _db()
        db.add_node(_node("n1", name="A"))
        diff = _tt(db).diff_node_versions("n1", 1, 99)
        assert "error" in diff

    def test_diff_ignores_internal_fields(self):
        db = _db()
        db.add_node(_node("n1", name="A", value="hello"))
        db.update_node("n1", {"value": "world"})
        diff = _tt(db).diff_node_versions("n1", 1, 2)
        # version, _updated_at, _created_at should not appear in changed
        assert "version" not in diff.get("changed", {})
        assert "_updated_at" not in diff.get("changed", {})


class TestRestoreNodeVersion:
    def test_restore_creates_new_version(self):
        db = _db()
        db.add_node(_node("n1", name="A", x=1))
        db.update_node("n1", {"x": 2})  # now v2
        db.update_node("n1", {"x": 3})  # now v3
        # Restore to v1 content
        restored = _tt(db).restore_node_version("n1", 1, author="agent:a", reason="rollback")
        assert restored is not None
        assert restored.properties["x"] == 1
        # New version is v4 (3+1), not destructive
        assert restored.properties["version"] == 4

    def test_restore_missing_version_returns_none(self):
        db = _db()
        db.add_node(_node("n1", name="A"))
        result = _tt(db).restore_node_version("n1", 99)
        assert result is None

    def test_restore_preserves_history(self):
        db = _db()
        db.add_node(_node("n1", name="A", x=1))
        db.update_node("n1", {"x": 2})
        _tt(db).restore_node_version("n1", 1)
        history = _tt(db).get_node_history("n1")
        # All snapshots still present
        assert len(history) >= 2
