"""Tests for re-query tracking to prove/disprove projection quality."""
import time
import pytest
from contextcore.context.projection import (
    record_orient_time,
    get_orient_time,
    record_requery,
    get_requery_count,
)


def test_record_and_get_orient_time():
    ns, ag = "rq_ns1", "rq_ag1"
    before = time.time()
    record_orient_time(ns, ag)
    after = time.time()

    t = get_orient_time(ns, ag)
    assert t is not None
    assert before <= t <= after


def test_get_orient_time_returns_none_when_not_recorded():
    t = get_orient_time("rq_ns_never", "rq_ag_never")
    assert t is None


def test_record_requery_increments_counter():
    ns, ag = "rq_ns2", "rq_ag2"
    # Reset by checking initial
    initial = get_requery_count(ns, ag)
    # Stamp orient time so requery window is open
    record_orient_time(ns, ag)
    v1 = record_requery(ns, ag)
    v2 = record_requery(ns, ag)

    assert v1 == initial + 1
    assert v2 == initial + 2


def test_get_requery_count_returns_zero_when_none():
    count = get_requery_count("rq_ns_fresh_xyz", "rq_ag_fresh_xyz")
    assert count == 0


def test_requery_only_increments_if_orient_was_recent():
    """record_requery must NOT increment if orient() was not called recently."""
    ns, ag = "rq_ns3", "rq_ag3"
    # Ensure no orient time is recorded for this agent
    before = get_requery_count(ns, ag)
    # Call record_requery WITHOUT a prior record_orient_time
    record_requery(ns, ag)
    after = get_requery_count(ns, ag)
    # Counter must NOT have changed — orient was not called
    assert after == before
