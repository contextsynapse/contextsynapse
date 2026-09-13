"""Shared fixtures for benchmarks."""
import os
import pytest
import tempfile


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def graph_db():
    """Fresh in-memory AIContextDB instance."""
    os.environ.setdefault("AICONTEXTDB_ADMIN_KEY", "bench-key")
    from contextsynapse.core.hybrid_graph_storage import AIContextDB
    db = AIContextDB(name="benchmark")
    yield db
    db.clear() if hasattr(db, 'clear') else None
