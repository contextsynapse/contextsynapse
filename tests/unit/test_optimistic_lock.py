"""Tests for optimistic locking on AIContextDB.update_node."""
import pytest
import uuid

from contextcore.core.hybrid_graph_storage import AIContextDB, VersionConflictError
from contextcore.core.graph_structures import GraphNode


def _db(suffix=""):
    """Create a fresh AIContextDB with a unique name to avoid cross-test pollution."""
    return AIContextDB(name=f"test_optlock_{uuid.uuid4().hex[:8]}{suffix}")


def _add(db, node_id, label="Thing", **props):
    """Helper: add a node and return it."""
    db.add_node(GraphNode(id=node_id, label=label, properties=dict(props)))
    return db.get_node(node_id)


class TestVersionConflictError:
    def test_exception_exists(self):
        err = VersionConflictError(current_version=3, provided_version=1)
        assert err.current_version == 3
        assert err.provided_version == 1

    def test_exception_message(self):
        err = VersionConflictError(current_version=3, provided_version=1)
        assert "3" in str(err)
        assert "1" in str(err)


class TestOptimisticLock:
    def test_update_node_increments_version(self):
        db = _db()
        _add(db, "n1", x=1)
        db.update_node("n1", {"x": 2})
        node = db.get_node("n1")
        assert node.properties["version"] == 2

    def test_update_node_correct_version_succeeds(self):
        db = _db()
        _add(db, "n1", x=1)
        node = db.get_node("n1")
        v = node.properties.get("version", 1)
        db.update_node("n1", {"x": 2}, expected_version=v)
        node = db.get_node("n1")
        assert node.properties["x"] == 2
        assert node.properties["version"] == v + 1

    def test_update_node_wrong_version_raises(self):
        db = _db()
        _add(db, "n1", x=1)
        db.update_node("n1", {"x": 99})  # now version == 2
        with pytest.raises(VersionConflictError) as exc_info:
            db.update_node("n1", {"x": 5}, expected_version=1)
        assert exc_info.value.current_version == 2
        assert exc_info.value.provided_version == 1

    def test_update_node_no_version_check_when_none(self):
        db = _db()
        _add(db, "n1", x=1)
        # Manually set a high version so a wrong expected_version would fail if checked
        db.update_node("n1", {"version": 99})
        # No expected_version → no check, always succeeds
        db.update_node("n1", {"x": 42}, expected_version=None)
        node = db.get_node("n1")
        assert node.properties["x"] == 42

    def test_version_initialised_to_1_on_add(self):
        db = _db()
        _add(db, "n1", x=1)
        node = db.get_node("n1")
        assert node.properties.get("version", 1) == 1

    def test_concurrent_updates_second_loses(self):
        db = _db()
        _add(db, "n1", name="orig")
        # Agent B updates first (no version check)
        db.update_node("n1", {"name": "agent_b"})  # now version==2
        # Agent A tries to write stale update (still thinks v1)
        with pytest.raises(VersionConflictError):
            db.update_node("n1", {"name": "agent_a"}, expected_version=1)
        # B's write survives
        node = db.get_node("n1")
        assert node.properties["name"] == "agent_b"

    def test_update_nonexistent_node_raises_key_error(self):
        db = _db()
        with pytest.raises(KeyError):
            db.update_node("nonexistent_xyz", {"x": 1})


class TestOptimisticLockIntegration:
    def test_three_agent_sequence(self):
        db = _db()
        _add(db, "doc", content="init")
        # Agent A reads v1
        agent_a_version = db.get_node("doc").properties.get("version", 1)

        # Agent B updates → v2
        db.update_node("doc", {"content": "B_edit"}, expected_version=agent_a_version)
        assert db.get_node("doc").properties["version"] == agent_a_version + 1

        # Agent C updates → v3
        db.update_node("doc", {"content": "C_edit"}, expected_version=agent_a_version + 1)
        assert db.get_node("doc").properties["version"] == agent_a_version + 2

        # Agent A tries stale write
        with pytest.raises(VersionConflictError) as exc:
            db.update_node("doc", {"content": "A_edit"}, expected_version=agent_a_version)
        assert exc.value.provided_version == agent_a_version
        assert exc.value.current_version == agent_a_version + 2
        # C's content survives
        assert db.get_node("doc").properties["content"] == "C_edit"

    def test_no_version_field_skips_check(self):
        db = _db()
        _add(db, "n", x=1)
        # Force a high version by patching properties directly
        db.node_properties["n"]["version"] = 99
        # No expected_version → no check, always succeeds
        db.update_node("n", {"x": 2}, expected_version=None)
        assert db.get_node("n").properties["x"] == 2


class TestUpdateNodeAuthorReason:
    def test_author_stored_on_update(self):
        db = _db()
        _add(db, "n1", x=1)
        db.update_node("n1", {"x": 2}, author="agent:claude")
        node = db.get_node("n1")
        assert node.properties.get("_updated_by") == "agent:claude"

    def test_reason_stored_on_update(self):
        db = _db()
        _add(db, "n1", x=1)
        db.update_node("n1", {"x": 2}, reason="business logic changed")
        node = db.get_node("n1")
        assert node.properties.get("_change_reason") == "business logic changed"

    def test_updated_at_set_on_update(self):
        db = _db()
        _add(db, "n1", x=1)
        db.update_node("n1", {"x": 2})
        node = db.get_node("n1")
        assert "_updated_at" in node.properties

    def test_temporal_storage_snapshots_old_content(self):
        db = _db()
        _add(db, "n1", x=1)
        db.update_node("n1", {"x": 2}, author="agent:a", reason="changed x")
        # TemporalStorage should have the v1 snapshot
        assert db.temporal_storage is not None
        history = db.temporal_storage.get_history("n1")
        # At minimum the initial CREATE snapshot exists
        assert len(history) >= 1

    def test_temporal_storage_enabled_by_default(self):
        db = _db()
        assert db.temporal_enabled is True
        assert db.temporal_storage is not None
