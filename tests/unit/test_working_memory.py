"""Tests for WorkingMemory — per-agent context cache."""

import time
import json
import pytest
from unittest.mock import MagicMock, patch

from contextcore.context.working_memory import WorkingMemory


@pytest.fixture
def wm():
    """Fresh in-memory WorkingMemory with no Redis."""
    return WorkingMemory(redis_client=None, ttl_s=60)


class TestWorkingMemory:
    def test_get_miss_returns_none(self, wm):
        result = wm.get("agent-1", "ns")
        assert result is None

    def test_set_and_get(self, wm):
        wm.set("agent-1", "ns", {"nodes": [1, 2]}, topic_keywords=["health", "aqi"])
        result = wm.get("agent-1", "ns")
        assert result == {"nodes": [1, 2]}

    def test_ttl_expiry(self):
        wm = WorkingMemory(redis_client=None, ttl_s=1)
        wm.set("agent-1", "ns", {"data": True}, topic_keywords=["x"])
        assert wm.get("agent-1", "ns") is not None
        time.sleep(1.1)
        assert wm.get("agent-1", "ns") is None

    def test_invalidate_by_agent(self, wm):
        wm.set("agent-1", "ns", {"a": 1}, topic_keywords=["topic"])
        wm.set("agent-2", "ns", {"b": 2}, topic_keywords=["topic"])
        wm.invalidate("agent-1", "ns")
        assert wm.get("agent-1", "ns") is None
        assert wm.get("agent-2", "ns") == {"b": 2}

    def test_invalidate_by_keywords(self, wm):
        wm.set("agent-1", "ns", {"a": 1}, topic_keywords=["health", "aqi"])
        wm.set("agent-2", "ns", {"b": 2}, topic_keywords=["finance", "stocks"])
        wm.set("agent-3", "ns", {"c": 3}, topic_keywords=["health", "diet"])
        wm.invalidate_by_keywords("ns", ["health"])
        # agent-1 and agent-3 overlap with "health" — invalidated
        assert wm.get("agent-1", "ns") is None
        assert wm.get("agent-3", "ns") is None
        # agent-2 has no overlap — still cached
        assert wm.get("agent-2", "ns") == {"b": 2}

    def test_stats(self, wm):
        wm.set("agent-1", "ns", {"x": 1}, topic_keywords=["a"])
        wm.get("agent-1", "ns")  # hit
        wm.get("agent-1", "ns")  # hit
        wm.get("agent-2", "ns")  # miss
        s = wm.stats()
        assert s["hits"] == 2
        assert s["misses"] == 1  # constructor doesn't count, only explicit gets

    def test_redis_mock(self):
        mock_r = MagicMock()
        mock_r.hgetall.return_value = {
            b"content": json.dumps({"nodes": [1]}).encode(),
            b"topic_keywords": json.dumps(["k"]).encode(),
            b"_ts": str(time.time()).encode(),
        }
        wm = WorkingMemory(redis_client=mock_r, ttl_s=60)
        result = wm.get("agent-1", "ns")
        mock_r.hgetall.assert_called_once_with("contextcore:wm:ns:agent-1")
        assert result == {"nodes": [1]}
