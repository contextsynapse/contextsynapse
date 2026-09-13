"""Integration test: task stream publish -> consume cycle."""
from __future__ import annotations

import threading
import time
import pytest
from contextcore.project.task_stream import TaskStreamPublisher, TaskStreamConsumer


class TestTaskStreamE2E:
    def test_publish_consume_cycle(self):
        pub = TaskStreamPublisher(redis_client=None)
        received = []
        consumer = TaskStreamConsumer(
            redis_client=None, namespace="e2e_test", publisher=pub,
            on_task=lambda t: received.append(t),
        )
        pub.publish(namespace="e2e_test", task_id="task_e2e_1", metadata={"title": "E2E test"})
        consumer.poll_once()
        assert len(received) == 1
        assert received[0]["task_id"] == "task_e2e_1"

    def test_batch_publish_consume(self):
        pub = TaskStreamPublisher(redis_client=None)
        received = []
        consumer = TaskStreamConsumer(
            redis_client=None, namespace="batch_test", publisher=pub,
            on_task=lambda t: received.append(t),
        )
        pub.publish_batch(namespace="batch_test", tasks=[
            {"task_id": "t1", "metadata": {"title": "First"}},
            {"task_id": "t2", "metadata": {"title": "Second"}},
            {"task_id": "t3", "metadata": {"title": "Third"}},
        ])
        for _ in range(3):
            consumer.poll_once()
        assert len(received) == 3
        assert {r["task_id"] for r in received} == {"t1", "t2", "t3"}

    def test_threaded_consumer(self):
        pub = TaskStreamPublisher(redis_client=None)
        received = []
        lock = threading.Lock()
        def handler(task):
            with lock:
                received.append(task)
        consumer = TaskStreamConsumer(
            redis_client=None, namespace="thread_test", publisher=pub,
            on_task=handler, block_ms=100,
        )
        t = threading.Thread(target=consumer.start, daemon=True)
        t.start()
        time.sleep(0.1)
        pub.publish(namespace="thread_test", task_id="thread_task_1")
        time.sleep(0.5)
        consumer.stop()
        assert len(received) >= 1

    def test_failed_task_retries(self):
        pub = TaskStreamPublisher(redis_client=None)
        pub.publish(namespace="retry_test", task_id="flaky_task")
        attempts = [0]
        def flaky_handler(task):
            attempts[0] += 1
            if attempts[0] < 3:
                raise RuntimeError("Transient failure")
        consumer = TaskStreamConsumer(
            redis_client=None, namespace="retry_test", publisher=pub,
            on_task=flaky_handler, max_retries=3,
        )
        for _ in range(3):
            consumer.poll_once()
        assert attempts[0] == 3
        assert len(consumer.dead_letters) == 0
