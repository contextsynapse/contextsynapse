"""Tests for TaskLock and TaskEventBus — atomic task coordination."""

import os
import threading
import uuid
import pytest
from contextcore.project.task_lock import TaskLock, TaskEventBus


class TestTaskLockFallback:
    """Test TaskLock using in-memory threading fallback (no Redis)."""

    def setup_method(self):
        # Force no-Redis mode by temporarily clearing env var
        self._orig_redis = os.environ.pop("AICONTEXTDB_REDIS_URL", None)
        # Unique namespace per test to prevent state leak
        self.lock = TaskLock(namespace=f"test-{uuid.uuid4().hex[:8]}", redis_url=None)

    def teardown_method(self):
        if self._orig_redis is not None:
            os.environ["AICONTEXTDB_REDIS_URL"] = self._orig_redis

    def test_acquire_claim_succeeds(self):
        assert self.lock.acquire_claim("task-1", "agent-a") is True

    def test_acquire_claim_blocks_second_agent(self):
        self.lock.acquire_claim("task-1", "agent-a")
        assert self.lock.acquire_claim("task-1", "agent-b") is False

    def test_same_agent_can_reacquire(self):
        self.lock.acquire_claim("task-1", "agent-a")
        assert self.lock.acquire_claim("task-1", "agent-a") is True

    def test_get_holder(self):
        assert self.lock.get_holder("task-1") is None
        self.lock.acquire_claim("task-1", "agent-a")
        assert self.lock.get_holder("task-1") == "agent-a"

    def test_release_by_holder_succeeds(self):
        self.lock.acquire_claim("task-1", "agent-a")
        assert self.lock.release_claim("task-1", "agent-a") is True
        assert self.lock.get_holder("task-1") is None

    def test_release_by_non_holder_fails(self):
        self.lock.acquire_claim("task-1", "agent-a")
        assert self.lock.release_claim("task-1", "agent-b") is False
        assert self.lock.get_holder("task-1") == "agent-a"

    def test_release_unclaimed_is_noop(self):
        assert self.lock.release_claim("task-1", "agent-a") is False

    def test_force_release(self):
        self.lock.acquire_claim("task-1", "agent-a")
        assert self.lock.force_release("task-1") is True
        assert self.lock.get_holder("task-1") is None

    def test_independent_tasks(self):
        self.lock.acquire_claim("task-1", "agent-a")
        assert self.lock.acquire_claim("task-2", "agent-b") is True

    def test_concurrent_claim_race(self):
        """Two threads race to claim same task — exactly one wins."""
        results = {"a": None, "b": None}

        def claim(agent):
            results[agent] = self.lock.acquire_claim("task-race", f"agent-{agent}")

        t1 = threading.Thread(target=claim, args=("a",))
        t2 = threading.Thread(target=claim, args=("b",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        wins = [k for k, v in results.items() if v is True]
        losses = [k for k, v in results.items() if v is False]
        assert len(wins) == 1, f"Expected exactly 1 winner, got {wins}"
        assert len(losses) == 1, f"Expected exactly 1 loser, got {losses}"


class TestTaskEventBus:
    """Test TaskEventBus in-memory mode (no Redis)."""

    def setup_method(self):
        self.bus = TaskEventBus(namespace="test-project", redis_url=None)

    def test_emit_stores_in_ring_buffer(self):
        self.bus.emit("task_claimed", "task-1", "agent-a", task_title="Auth")
        events = self.bus.recent(limit=10)
        assert len(events) == 1
        assert events[0]["type"] == "task_claimed"
        assert events[0]["task_id"] == "task-1"
        assert events[0]["agent_id"] == "agent-a"
        assert events[0]["task_title"] == "Auth"
        assert "timestamp" in events[0]
        assert events[0]["namespace"] == "test-project"

    def test_emit_calls_subscribers(self):
        received = []
        self.bus.subscribe(lambda e: received.append(e))
        self.bus.emit("task_completed", "task-1", "agent-a")
        assert len(received) == 1
        assert received[0]["type"] == "task_completed"

    def test_multiple_subscribers(self):
        r1, r2 = [], []
        self.bus.subscribe(lambda e: r1.append(e))
        self.bus.subscribe(lambda e: r2.append(e))
        self.bus.emit("task_handed_off", "task-1", "agent-a", to_agent="agent-b")
        assert len(r1) == 1
        assert len(r2) == 1

    def test_ring_buffer_caps_at_max(self):
        bus = TaskEventBus(namespace="test", redis_url=None, max_history=5)
        for i in range(10):
            bus.emit("task_claimed", f"task-{i}", "agent-a")
        events = bus.recent(limit=100)
        assert len(events) == 5
        assert events[0]["task_id"] == "task-5"

    def test_recent_respects_limit(self):
        for i in range(10):
            self.bus.emit("task_claimed", f"task-{i}", "agent-a")
        events = self.bus.recent(limit=3)
        assert len(events) == 3

    def test_subscriber_error_does_not_break_emit(self):
        def bad_sub(e):
            raise ValueError("boom")
        self.bus.subscribe(bad_sub)
        received = []
        self.bus.subscribe(lambda e: received.append(e))
        self.bus.emit("task_claimed", "task-1", "agent-a")
        assert len(received) == 1  # second subscriber still called
