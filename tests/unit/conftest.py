"""Unit test fixtures — Redis isolation between tests."""
import os
import pytest


# Graph name prefixes used by unit tests
_TEST_KEY_PREFIXES = [
    "g:test_",
    "g:tdb_",
    "g:dynamic_",
    "g:feed_test_",
]


def _delete_test_redis_keys() -> None:
    """Delete all Redis keys belonging to test-prefixed graphs.

    Runs before each test so every test starts with a clean Redis state,
    regardless of what prior tests left behind. No-op when Redis is unavailable.
    """
    try:
        import redis as _redis
        url = os.environ.get("AICONTEXTDB_REDIS_URL")
        if not url:
            return
        r = _redis.Redis.from_url(url, decode_responses=True)
        for prefix in _TEST_KEY_PREFIXES:
            cursor = 0
            while True:
                cursor, keys = r.scan(cursor, match=f"{prefix}*", count=500)
                if keys:
                    r.delete(*keys)
                if cursor == 0:
                    break
    except Exception:
        pass  # Redis unavailable — tests use disk/memory backends, no cleanup needed


@pytest.fixture(autouse=True)
def _redis_test_isolation():
    """Ensure each unit test starts with clean Redis state.

    Cleans before the test (so stale state from prior tests doesn't interfere)
    and after (so this test's state doesn't leak into the next one).
    """
    _delete_test_redis_keys()
    yield
    _delete_test_redis_keys()
