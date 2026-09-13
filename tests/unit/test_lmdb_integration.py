"""Integration tests for LMDB backend wired into AIContextDB."""

import pytest
import os


@pytest.fixture
def lmdb_db(tmp_path):
    os.environ["AICONTEXTDB_STORAGE_BACKEND"] = "lmdb"
    os.environ["AICONTEXTDB_LMDB_PATH"] = str(tmp_path / "lmdb")
    # Force CSR graph backend (not Redis) so LMDB path is reached
    old_graph_backend = os.environ.pop("AICONTEXTDB_GRAPH_BACKEND", None)
    os.environ["AICONTEXTDB_GRAPH_BACKEND"] = "csr"
    old_redis = os.environ.pop("AICONTEXTDB_REDIS_URL", None)

    from contextcore import AIContextDB
    db = AIContextDB(name="test_lmdb_integration")
    yield db

    os.environ.pop("AICONTEXTDB_STORAGE_BACKEND", None)
    os.environ.pop("AICONTEXTDB_LMDB_PATH", None)
    if old_graph_backend is not None:
        os.environ["AICONTEXTDB_GRAPH_BACKEND"] = old_graph_backend
    else:
        os.environ.pop("AICONTEXTDB_GRAPH_BACKEND", None)
    if old_redis is not None:
        os.environ["AICONTEXTDB_REDIS_URL"] = old_redis


def _skip_if_no_lmdb():
    try:
        from contextcore.storage.lmdb_graph_storage import LMDB_AVAILABLE
        if not LMDB_AVAILABLE:
            pytest.skip("lmdb/msgpack not installed")
    except ImportError:
        pytest.skip("lmdb_graph_storage module not found")


def test_lmdb_backend_active(lmdb_db):
    """Verify the LMDB backend is actually wired in."""
    _skip_if_no_lmdb()
    from contextcore.storage.lmdb_graph_storage import LMDBGraphStorage
    assert isinstance(lmdb_db.csr_storage, LMDBGraphStorage)
    assert lmdb_db.csr_adapter is lmdb_db.csr_storage
    assert lmdb_db._using_lmdb_backend is True


def test_add_node_via_lmdb(lmdb_db):
    """Add a node through AIContextDB and read it back via csr_adapter."""
    _skip_if_no_lmdb()
    from contextcore.core.graph_structures import GraphNode
    node = GraphNode(id="n1", label="Person", properties={"name": "Alice"})
    lmdb_db.add_node(node)
    result = lmdb_db.csr_adapter.get_node("n1")
    assert result is not None
    assert result.properties["name"] == "Alice"


def test_lmdb_returns_copies(lmdb_db):
    """LMDB storage must return copies, not references."""
    _skip_if_no_lmdb()
    from contextcore.core.graph_structures import GraphNode
    lmdb_db.add_node(GraphNode(id="n1", label="Person", properties={"name": "Alice"}))
    node = lmdb_db.csr_adapter.get_node("n1")
    node.properties["name"] = "MUTATED"
    fresh = lmdb_db.csr_adapter.get_node("n1")
    assert fresh.properties["name"] == "Alice"


def test_node_count(lmdb_db):
    """Node count should reflect added nodes."""
    _skip_if_no_lmdb()
    from contextcore.core.graph_structures import GraphNode
    assert lmdb_db.csr_adapter.get_node_count() == 0
    lmdb_db.add_node(GraphNode(id="a", label="X", properties={}))
    lmdb_db.add_node(GraphNode(id="b", label="Y", properties={}))
    assert lmdb_db.csr_adapter.get_node_count() == 2


def test_fallback_when_lmdb_not_requested(tmp_path):
    """Without env var, CSR backend should be used (not LMDB)."""
    os.environ.pop("AICONTEXTDB_STORAGE_BACKEND", None)
    os.environ.pop("AICONTEXTDB_REDIS_URL", None)
    os.environ["AICONTEXTDB_GRAPH_BACKEND"] = "csr"
    from contextcore import AIContextDB
    db = AIContextDB(name="test_csr_fallback")
    assert db._using_lmdb_backend is False
    os.environ.pop("AICONTEXTDB_GRAPH_BACKEND", None)
