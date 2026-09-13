import uuid
from datetime import datetime
from pathlib import Path
import pytest
from contextcore.temporal.storage import TemporalStorage


def _ts(tmp_path):
    return TemporalStorage(namespace=f"test_{uuid.uuid4().hex[:6]}", base_path=str(tmp_path))


class TestTemporalStorageBasics:
    def test_create_and_get_version(self, tmp_path):
        ts = _ts(tmp_path)
        ts.create_version("node-1", "node", {"name": "Alice", "version": 1},
                          "CREATE", datetime.now())
        rec = ts.get_version("node-1", 1)
        assert rec is not None
        assert rec["properties"]["name"] == "Alice"
        assert rec["entity_type"] == "node"
        assert rec["operation"] == "CREATE"

    def test_get_version_missing_returns_none(self, tmp_path):
        ts = _ts(tmp_path)
        assert ts.get_version("nonexistent", 1) is None

    def test_get_history_empty(self, tmp_path):
        ts = _ts(tmp_path)
        assert ts.get_history("nonexistent") == []

    def test_get_history_multiple_versions(self, tmp_path):
        ts = _ts(tmp_path)
        ts.create_version("n1", "node", {"version": 1, "x": "a"}, "CREATE", datetime.now())
        ts.create_version("n1", "node", {"version": 2, "x": "b"}, "UPDATE", datetime.now(),
                          author="agent:claude", reason="updated x")
        history = ts.get_history("n1")
        assert len(history) == 2
        assert history[0]["version"] == 1
        assert history[1]["version"] == 2
        assert history[1]["author"] == "agent:claude"
        assert history[1]["reason"] == "updated x"

    def test_author_and_reason_stored(self, tmp_path):
        ts = _ts(tmp_path)
        ts.create_version("n1", "node", {"version": 1}, "CREATE", datetime.now(),
                          author="user:alice", reason="initial")
        rec = ts.get_version("n1", 1)
        assert rec["author"] == "user:alice"
        assert rec["reason"] == "initial"

    def test_timestamp_stored_as_iso(self, tmp_path):
        ts = _ts(tmp_path)
        now = datetime(2026, 6, 23, 12, 0, 0)
        ts.create_version("n1", "node", {"version": 1}, "CREATE", now)
        rec = ts.get_version("n1", 1)
        assert "2026-06-23" in rec["timestamp"]

    def test_independent_entities(self, tmp_path):
        ts = _ts(tmp_path)
        ts.create_version("n1", "node", {"version": 1, "x": 1}, "CREATE", datetime.now())
        ts.create_version("n2", "node", {"version": 1, "x": 2}, "CREATE", datetime.now())
        assert ts.get_version("n1", 1)["properties"]["x"] == 1
        assert ts.get_version("n2", 1)["properties"]["x"] == 2


class TestTemporalStoragePersistence:
    def test_save_and_load(self, tmp_path):
        ts = TemporalStorage(namespace="persist_test", base_path=str(tmp_path))
        ts.create_version("n1", "node", {"version": 1, "name": "Bob"}, "CREATE", datetime.now())
        ts.save_versions()

        ts2 = TemporalStorage(namespace="persist_test", base_path=str(tmp_path))
        ts2.load_versions()
        rec = ts2.get_version("n1", 1)
        assert rec is not None
        assert rec["properties"]["name"] == "Bob"

    def test_load_nonexistent_is_noop(self, tmp_path):
        ts = TemporalStorage(namespace="no_such_ns", base_path=str(tmp_path))
        ts.load_versions()   # must not raise
        assert ts.get_history("anything") == []

    def test_file_path_under_base(self, tmp_path):
        ts = TemporalStorage(namespace="myns", base_path=str(tmp_path))
        ts.create_version("n1", "node", {"version": 1}, "CREATE", datetime.now())
        expected = tmp_path / "temporal" / "myns" / "versions.json"
        assert expected.exists()
