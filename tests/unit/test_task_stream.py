"""Tests for Redis Streams task delivery."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock


class TestTaskStreamPublisher:
    def test_publish_adds_to_memory_stream(self):
        from contextcore.project.task_stream import TaskStreamPublisher
        pub = TaskStreamPublisher(redis_client=None)
        pub.publish(namespace="test", task_id="task_1", priority=1, metadata={"title": "Do something"})
        entries = pub.get_pending("test")
        assert len(entries) == 1
        assert entries[0]["task_id"] == "task_1"
        assert entries[0]["priority"] == "1"

    def test_publish_batch(self):
        from contextcore.project.task_stream import TaskStreamPublisher
        pub = TaskStreamPublisher(redis_client=None)
        tasks = [
            {"task_id": "t1", "priority": 2, "metadata": {"title": "First"}},
            {"task_id": "t2", "priority": 1, "metadata": {"title": "Second"}},
        ]
        pub.publish_batch(namespace="test", tasks=tasks)
        entries = pub.get_pending("test")
        assert len(entries) == 2

    def test_publish_with_redis(self):
        from contextcore.project.task_stream import TaskStreamPublisher
        mock_redis = MagicMock()
        mock_redis.xadd = MagicMock(return_value=b"1234-0")
        mock_redis.xgroup_create = MagicMock()
        pub = TaskStreamPublisher(redis_client=mock_redis)
        pub.publish(namespace="proj", task_id="task_1", priority=0)
        mock_redis.xadd.assert_called_once()
        call_args = mock_redis.xadd.call_args
        assert call_args[0][0] == "tasks:proj"
        assert call_args[0][1]["task_id"] == "task_1"


class TestTaskStreamConsumer:
    def test_consumer_processes_memory_tasks(self):
        from contextcore.project.task_stream import TaskStreamPublisher, TaskStreamConsumer
        pub = TaskStreamPublisher(redis_client=None)
        pub.publish(namespace="test", task_id="task_1", metadata={"title": "Test task"})
        received = []
        consumer = TaskStreamConsumer(
            redis_client=None, namespace="test", publisher=pub,
            on_task=lambda task: received.append(task),
        )
        consumer.poll_once()
        assert len(received) == 1
        assert received[0]["task_id"] == "task_1"

    def test_consumer_stops_gracefully(self):
        from contextcore.project.task_stream import TaskStreamConsumer
        consumer = TaskStreamConsumer(redis_client=None, namespace="test", on_task=lambda t: None)
        consumer.stop()
        assert consumer._running is False

    def test_consumer_with_redis_mock(self):
        from contextcore.project.task_stream import TaskStreamConsumer
        mock_redis = MagicMock()
        mock_redis.xreadgroup = MagicMock(return_value=[])
        mock_redis.xautoclaim = MagicMock(return_value=(b"0-0", []))
        consumer = TaskStreamConsumer(
            redis_client=mock_redis, namespace="test",
            on_task=lambda t: None, block_ms=100,
        )
        consumer.poll_once()
        mock_redis.xreadgroup.assert_called_once()

    def test_dead_letter_after_max_retries(self):
        from contextcore.project.task_stream import TaskStreamPublisher, TaskStreamConsumer
        pub = TaskStreamPublisher(redis_client=None)
        pub.publish(namespace="test", task_id="bad_task")
        fail_count = [0]
        def failing_handler(task):
            fail_count[0] += 1
            raise ValueError("Simulated failure")
        consumer = TaskStreamConsumer(
            redis_client=None, namespace="test", publisher=pub,
            on_task=failing_handler, max_retries=3,
        )
        for _ in range(4):
            consumer.poll_once()
        assert fail_count[0] == 3
        assert len(consumer.dead_letters) == 1
        assert consumer.dead_letters[0]["task_id"] == "bad_task"
