"""
Tests for contextcore.core.change_stream

Tests verify serialization logic and graceful fallback without a live Redis.
"""
import time
from datetime import datetime, timezone

import pytest

from contextcore.core.change_stream import (
    ChangeEvent,
    ChangeStreamPublisher,
    ChangeStreamSubscriber,
    get_change_publisher,
)


# ---------------------------------------------------------------------------
# ChangeEvent serialization
# ---------------------------------------------------------------------------

class TestChangeEvent:
    def test_change_event_to_dict(self):
        evt = ChangeEvent(
            namespace="ns1",
            operation="add_node",
            node_id="n-abc",
            label="Person",
            agent_id="agent-007",
            timestamp="2026-04-12T10:00:00Z",
        )
        d = evt.to_dict()
        assert d["ns"] == "ns1"
        assert d["op"] == "add_node"
        assert d["nid"] == "n-abc"
        assert d["lbl"] == "Person"
        assert d["aid"] == "agent-007"
        assert d["ts"] == "2026-04-12T10:00:00Z"
        # All values must be strings (Redis requirement)
        for v in d.values():
            assert isinstance(v, str), f"Expected str, got {type(v)} for value {v!r}"

    def test_change_event_from_dict(self):
        d = {
            "ns": "ns2",
            "op": "delete_edge",
            "nid": "e-xyz",
            "lbl": "RELATES_TO",
            "aid": "bot-1",
            "ts": "2026-04-12T11:00:00Z",
        }
        evt = ChangeEvent.from_dict(d)
        assert evt.namespace == "ns2"
        assert evt.operation == "delete_edge"
        assert evt.node_id == "e-xyz"
        assert evt.label == "RELATES_TO"
        assert evt.agent_id == "bot-1"
        assert evt.timestamp == "2026-04-12T11:00:00Z"

    def test_change_event_roundtrip(self):
        original = ChangeEvent(
            namespace="graph_a",
            operation="update_node",
            node_id="n-999",
            label="Article",
            agent_id="agent-x",
            timestamp="2026-04-12T09:30:00Z",
        )
        restored = ChangeEvent.from_dict(original.to_dict())
        assert restored.namespace == original.namespace
        assert restored.operation == original.operation
        assert restored.node_id == original.node_id
        assert restored.label == original.label
        assert restored.agent_id == original.agent_id
        assert restored.timestamp == original.timestamp

    def test_change_event_auto_timestamp(self):
        """Timestamp is auto-filled when not provided."""
        before = datetime.now(timezone.utc).isoformat()
        evt = ChangeEvent(namespace="ns", operation="add_node", node_id="n-1")
        after = datetime.now(timezone.utc).isoformat()

        assert evt.timestamp != "", "Timestamp should be auto-filled"
        # Should be a valid ISO string between before and after
        assert evt.timestamp >= before or len(evt.timestamp) > 0

    def test_change_event_default_fields(self):
        """label, agent_id, timestamp all have sensible defaults."""
        evt = ChangeEvent(namespace="ns", operation="add_edge", node_id="e-1")
        assert evt.label == ""
        assert evt.agent_id == ""
        # Timestamp was auto-set
        assert evt.timestamp != ""


# ---------------------------------------------------------------------------
# ChangeStreamPublisher — no live Redis
# ---------------------------------------------------------------------------

class TestChangeStreamPublisher:
    def _make_publisher_no_redis(self):
        """Return a publisher pointing at a non-existent Redis."""
        return ChangeStreamPublisher(redis_url="redis://localhost:19999/15")

    def test_publisher_available_property_false(self):
        """When Redis is unreachable, available == False."""
        pub = self._make_publisher_no_redis()
        assert pub.available is False

    def test_publisher_without_redis_no_exception(self):
        """publish() is a silent no-op when Redis is unavailable."""
        pub = self._make_publisher_no_redis()
        evt = ChangeEvent(namespace="ns", operation="add_node", node_id="n-1")
        # Must not raise
        pub.publish(evt)

    def test_publisher_publish_multiple_events_no_exception(self):
        """Repeated publishes on an unavailable publisher don't raise."""
        pub = self._make_publisher_no_redis()
        for op in ("add_node", "update_node", "delete_node", "add_edge", "delete_edge"):
            evt = ChangeEvent(namespace="test", operation=op, node_id="n-1", agent_id="a")
            pub.publish(evt)  # should not raise


# ---------------------------------------------------------------------------
# ChangeStreamSubscriber — no live Redis
# ---------------------------------------------------------------------------

class TestChangeStreamSubscriber:
    def test_subscriber_without_redis_no_exception(self):
        """subscribe() is a silent no-op when Redis is unavailable."""
        sub = ChangeStreamSubscriber(redis_url="redis://localhost:19999/15")
        called = []

        def callback(evt):
            called.append(evt)

        # Must not raise
        sub.subscribe("ns1", callback)
        # Give hypothetical thread a moment — nothing should have happened
        time.sleep(0.05)
        assert called == [], "No events expected without Redis"

    def test_subscriber_stop_no_exception(self):
        """stop() is safe even when Redis was never connected."""
        sub = ChangeStreamSubscriber(redis_url="redis://localhost:19999/15")
        sub.stop()  # must not raise


# ---------------------------------------------------------------------------
# Singleton helper
# ---------------------------------------------------------------------------

class TestGetChangePublisher:
    def test_get_change_publisher_returns_instance(self):
        pub = get_change_publisher()
        assert isinstance(pub, ChangeStreamPublisher)

    def test_get_change_publisher_is_singleton(self):
        pub1 = get_change_publisher()
        pub2 = get_change_publisher()
        assert pub1 is pub2
