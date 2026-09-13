"""Tests for promotion event publishing and consumption in orient()."""
import time
import pytest
from contextcore.context.projection import get_recent_promotions
from contextcore.context.promotion import publish_promotion_event


def test_published_event_is_retrievable():
    ns = "pe_ns1"
    before = time.time() - 1
    publish_promotion_event(ns, "node_abc", "Finding", "Auth token leak detected")

    events = get_recent_promotions(ns, since_ts=before)

    assert len(events) >= 1
    evt = next((e for e in events if e["node_id"] == "node_abc"), None)
    assert evt is not None
    assert evt["label"] == "Finding"
    assert evt["title"] == "Auth token leak detected"


def test_events_older_than_since_ts_are_excluded():
    ns = "pe_ns2"
    publish_promotion_event(ns, "node_old", "Decision", "Old decision")
    future_ts = time.time() + 10  # all events are older than this

    events = get_recent_promotions(ns, since_ts=future_ts)

    node_ids = [e["node_id"] for e in events]
    assert "node_old" not in node_ids


def test_events_from_different_namespace_not_returned():
    ns_a = "pe_ns3a"
    ns_b = "pe_ns3b"
    before = time.time() - 1
    publish_promotion_event(ns_a, "node_in_a", "Insight", "Insight from A")

    events = get_recent_promotions(ns_b, since_ts=before)

    node_ids = [e["node_id"] for e in events]
    assert "node_in_a" not in node_ids


def test_get_recent_promotions_returns_empty_when_none():
    events = get_recent_promotions("pe_ns_empty_xyz789", since_ts=time.time() - 60)
    assert events == []


def test_multiple_events_all_returned():
    ns = "pe_ns4"
    before = time.time() - 1
    publish_promotion_event(ns, "node_1", "Finding", "Finding 1")
    publish_promotion_event(ns, "node_2", "Insight", "Insight 2")

    events = get_recent_promotions(ns, since_ts=before)
    node_ids = [e["node_id"] for e in events]

    assert "node_1" in node_ids
    assert "node_2" in node_ids
