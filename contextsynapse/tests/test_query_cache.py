"""
Tests for _QueryCache and fast-path SELECT shortcuts in AIQLExecutor.
"""

import os
os.environ["CONTEXTSYNAPSE_GRAPH_BACKEND"] = "csr"  # tests need isolated in-memory graphs
os.environ["AICONTEXTDB_GRAPH_BACKEND"] = "csr"  # backward compat

import time
import tempfile
import shutil
import pytest

from contextsynapse.aiql.engine.executor import _QueryCache, _query_cache, AIQLExecutor
from contextsynapse.core.registry import GraphRegistry


# ────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────

@pytest.fixture()
def cache():
    """Fresh _QueryCache with small defaults for testing."""
    return _QueryCache(max_size=4, ttl_seconds=1.0)


@pytest.fixture()
def tmp_storage(tmp_path):
    """Temporary directory for GraphRegistry storage."""
    return str(tmp_path / "graphs")


@pytest.fixture()
def executor(tmp_storage):
    """AIQLExecutor wired to a temporary GraphRegistry with a test graph."""
    registry = GraphRegistry(storage_dir=tmp_storage)
    registry.create_graph("test")
    ex = AIQLExecutor(graph_registry=registry)
    ex.active_namespace = "test"

    # Clear the module-level cache so tests start clean
    _query_cache.clear()

    # Seed a few nodes
    ex.execute('CREATE NODE Person {name: "Alice", age: "30"}')
    ex.execute('CREATE NODE Person {name: "Bob", age: "25"}')
    ex.execute('CREATE NODE Task {name: "Deploy", status: "done"}')

    # Clear again after writes so cached CREATE results don't interfere
    _query_cache.clear()
    return ex


# ====================================================================
# 1. _QueryCache unit tests
# ====================================================================

class TestQueryCache:
    """Direct unit tests for the _QueryCache class."""

    def test_put_and_get(self, cache):
        cache.put("ns1", "SELECT * FROM Foo", {"rows": [1, 2, 3]})
        result = cache.get("ns1", "SELECT * FROM Foo")
        assert result == {"rows": [1, 2, 3]}

    def test_get_miss_returns_none(self, cache):
        assert cache.get("ns1", "SELECT 1") is None

    def test_ttl_expiration(self, cache):
        cache.put("ns1", "SELECT 1", "ok")
        # Should be retrievable immediately
        assert cache.get("ns1", "SELECT 1") == "ok"
        # Wait for TTL (1 second) to expire
        time.sleep(1.1)
        assert cache.get("ns1", "SELECT 1") is None

    def test_lru_eviction(self, cache):
        """Oldest entries are evicted when max_size is exceeded."""
        cache.put("ns", "q1", "r1")
        cache.put("ns", "q2", "r2")
        cache.put("ns", "q3", "r3")
        cache.put("ns", "q4", "r4")
        assert cache.size == 4

        # Adding a 5th entry should evict the oldest (q1)
        cache.put("ns", "q5", "r5")
        assert cache.size == 4
        assert cache.get("ns", "q1") is None
        assert cache.get("ns", "q5") == "r5"

    def test_lru_access_refreshes_order(self, cache):
        """Accessing an entry moves it to the end, protecting it from eviction."""
        cache.put("ns", "q1", "r1")
        cache.put("ns", "q2", "r2")
        cache.put("ns", "q3", "r3")
        cache.put("ns", "q4", "r4")

        # Access q1 so it becomes most-recently used
        cache.get("ns", "q1")

        # Insert two more; q2 and q3 should be evicted (oldest untouched)
        cache.put("ns", "q5", "r5")
        cache.put("ns", "q6", "r6")

        assert cache.get("ns", "q1") == "r1"  # survived because it was accessed
        assert cache.get("ns", "q2") is None   # evicted
        assert cache.get("ns", "q3") is None   # evicted

    def test_invalidate_namespace(self, cache):
        cache.put("ns1", "q1", "r1")
        cache.put("ns1", "q2", "r2")
        cache.put("ns2", "q1", "r3")
        assert cache.size == 3

        cache.invalidate("ns1")

        assert cache.get("ns1", "q1") is None
        assert cache.get("ns1", "q2") is None
        assert cache.get("ns2", "q1") == "r3"
        assert cache.size == 1

    def test_clear(self, cache):
        cache.put("ns1", "q1", "r1")
        cache.put("ns2", "q2", "r2")
        assert cache.size == 2

        cache.clear()
        assert cache.size == 0
        assert cache.get("ns1", "q1") is None

    def test_namespaces_isolated(self, cache):
        """Same query string in different namespaces returns different results."""
        cache.put("alpha", "SELECT *", {"from": "alpha"})
        cache.put("beta", "SELECT *", {"from": "beta"})

        assert cache.get("alpha", "SELECT *") == {"from": "alpha"}
        assert cache.get("beta", "SELECT *") == {"from": "beta"}

    def test_invalidate_one_namespace_keeps_other(self, cache):
        cache.put("alpha", "SELECT *", "a")
        cache.put("beta", "SELECT *", "b")

        cache.invalidate("alpha")

        assert cache.get("alpha", "SELECT *") is None
        assert cache.get("beta", "SELECT *") == "b"


# ====================================================================
# 2. Fast-path SELECT shortcut tests (via AIQLExecutor)
# ====================================================================

class TestFastPathSelects:
    """Integration tests for fast-path SELECT shortcuts and SHOW GRAPHS."""

    def test_select_star_returns_all_nodes(self, executor):
        result = executor.execute("SELECT *")
        assert result["nodes"] is not None
        assert len(result["nodes"]) == 3  # Alice, Bob, Deploy

    def test_select_star_from_label(self, executor):
        result = executor.execute("SELECT * FROM Person")
        assert len(result["nodes"]) == 2
        names = {n["properties"]["name"] for n in result["nodes"]}
        assert names == {"Alice", "Bob"}

    def test_select_star_from_label_case_insensitive(self, executor):
        result = executor.execute("SELECT * FROM person")
        assert len(result["nodes"]) == 2

    def test_select_star_where_property(self, executor):
        result = executor.execute('SELECT * WHERE name = "Alice"')
        assert len(result["nodes"]) == 1
        assert result["nodes"][0]["properties"]["name"] == "Alice"

    def test_select_star_where_no_match(self, executor):
        result = executor.execute('SELECT * WHERE name = "Nobody"')
        assert len(result["nodes"]) == 0

    def test_show_graphs(self, executor):
        result = executor.execute("SHOW GRAPHS")
        graphs = result["data"]["graphs"]
        names = [g["name"] for g in graphs]
        assert "test" in names

    def test_cache_hit_on_second_call(self, executor):
        """Second identical read should come from cache."""
        _query_cache.clear()

        r1 = executor.execute("SELECT *")
        assert _query_cache.size >= 1  # stored after first call

        r2 = executor.execute("SELECT *")
        # Results should be identical (same object from cache)
        assert r1 is r2

    def test_write_invalidates_cache(self, executor):
        """A write query should invalidate cached reads for the namespace."""
        _query_cache.clear()

        executor.execute("SELECT *")
        assert _query_cache.size >= 1

        # Write invalidates the namespace cache
        executor.execute('CREATE NODE Person {name: "Charlie", age: "40"}')
        # Cache for "test" namespace should have been cleared
        cached = _query_cache.get("test", "SELECT *")
        assert cached is None

        # Fresh read should now include 4 nodes
        result = executor.execute("SELECT *")
        assert len(result["nodes"]) == 4
