"""Tests for projection cache invalidation."""
import pytest
from contextcore.context.projection import (
    cache_projection,
    get_cached_projection,
    invalidate_projection_cache,
)


def test_invalidate_single_agent_clears_only_that_agent():
    ns = "test_ns_inv_single"
    cache_projection(ns, "agent_a", "result_a")
    cache_projection(ns, "agent_b", "result_b")

    count = invalidate_projection_cache(ns, agent_id="agent_a")

    assert count >= 1
    assert get_cached_projection(ns, "agent_a") is None
    # agent_b's cache must be untouched
    assert get_cached_projection(ns, "agent_b") == "result_b"


def test_invalidate_all_agents_clears_namespace():
    ns = "test_ns_inv_all"
    cache_projection(ns, "agent_x", "rx")
    cache_projection(ns, "agent_y", "ry")
    cache_projection(ns, "agent_z", "rz")

    count = invalidate_projection_cache(ns)

    assert count >= 3
    assert get_cached_projection(ns, "agent_x") is None
    assert get_cached_projection(ns, "agent_y") is None
    assert get_cached_projection(ns, "agent_z") is None


def test_invalidate_different_namespace_not_affected():
    ns_a = "test_ns_inv_nsa"
    ns_b = "test_ns_inv_nsb"
    cache_projection(ns_a, "ag", "val_a")
    cache_projection(ns_b, "ag", "val_b")

    invalidate_projection_cache(ns_a)

    assert get_cached_projection(ns_a, "ag") is None
    assert get_cached_projection(ns_b, "ag") == "val_b"


def test_invalidate_returns_zero_for_empty_namespace():
    count = invalidate_projection_cache("ns_that_was_never_cached_xyz123")
    assert count == 0
