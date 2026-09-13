"""Tests for embedding model warmup at server startup."""
import threading
from unittest.mock import patch, MagicMock


def test_warmup_spawns_daemon_thread():
    """_warmup_embedding must start a daemon thread so it doesn't block shutdown."""
    spawned_threads = []
    original_start = threading.Thread.start

    def capture_start(self):
        spawned_threads.append(self)
        # Don't actually run — test env has no Ollama
        return None

    with patch.object(threading.Thread, "start", capture_start):
        with patch(
            "contextcore.context.vector_integration.get_session_vector_store",
            return_value=MagicMock(),
        ):
            from contextcore.mcp.server import _warmup_embedding
            _warmup_embedding()

    assert len(spawned_threads) == 1
    assert spawned_threads[0].daemon is True


def test_warmup_does_not_raise_on_import_error():
    """If vector_integration is unavailable, _warmup_embedding must silently pass."""
    with patch(
        "contextcore.context.vector_integration.get_session_vector_store",
        side_effect=ImportError("no module"),
    ):
        from contextcore.mcp.server import _warmup_embedding
        # Must not raise
        _warmup_embedding()
