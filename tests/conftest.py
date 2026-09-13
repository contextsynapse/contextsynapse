"""Shared fixtures for AIContextDB tests."""

import os
import shutil
import sys
import tempfile

import pytest

# Ensure the project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def tmp_db_path(tmp_path):
    """Return a temporary SQLite path for session/agent storage."""
    return str(tmp_path / "test_context.db")


def pytest_sessionfinish(session, exitstatus):
    """Clean up test-created graphs after the entire test session."""
    try:
        from contextcore.core.registry import GraphRegistry
        gr = GraphRegistry()
        for g in gr.list_graphs():
            name = g.get("name", "") if isinstance(g, dict) else g
            if name.startswith(("test_", "tdb_", "dynamic_")):
                try:
                    gr.delete_graph(name, delete_files=True)
                except Exception:
                    pass
        # Clean orphan dirs
        for prefix_dir in ["contextcore_data/namespaces", "contextcore_data/wal"]:
            if os.path.isdir(prefix_dir):
                for d in os.listdir(prefix_dir):
                    if d.startswith(("test_", "tdb_", "dynamic_", "feed_test_")):
                        shutil.rmtree(os.path.join(prefix_dir, d), ignore_errors=True)
        # Clean test LMDB indexes (prevent disk accumulation)
        lmdb_dir = "contextcore_data/lmdb_index"
        if os.path.isdir(lmdb_dir):
            for d in os.listdir(lmdb_dir):
                if d.startswith(("test_", "tdb_", "dynamic_", "feed_test_")):
                    shutil.rmtree(os.path.join(lmdb_dir, d), ignore_errors=True)
        # Clean test sessions
        import sqlite3
        db_path = "contextcore_data/context.db"
        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path)
            for pattern in ["SA-WS-%", "WS-Protected-%", "SA-Visible-%", "IsoA-%", "IsoB-%", "a2a_%"]:
                conn.execute(f"DELETE FROM context_sessions WHERE name LIKE '{pattern}'")
            conn.execute("DELETE FROM session_access WHERE session_id NOT IN (SELECT session_id FROM context_sessions)")
            conn.commit()
            conn.close()
    except Exception:
        pass
