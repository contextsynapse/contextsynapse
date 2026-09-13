"""Tests for frozen context snapshots (SEBI trade audit)."""
import json
import pytest
from contextcore.context.frozen import (
    FrozenContext, freeze_context, verify_frozen, store_frozen, retrieve_frozen,
)


class TestFreezeContext:
    def test_creates_frozen_with_hash(self):
        content = {"signals": [{"source": "tcs", "score": 0.7}], "price": 3500}
        frozen = freeze_context(
            trade_id="T-001",
            assembled_id="ac_001",
            atomics=["market:tcs", "market:tcs_price"],
            content=content,
            frozen_by="fm_user_1",
        )
        assert frozen.trade_id == "T-001"
        assert frozen.assembled_id == "ac_001"
        assert frozen.atomics == ["market:tcs", "market:tcs_price"]
        assert frozen.content == content
        assert frozen.content_hash  # non-empty
        assert frozen.frozen_by == "fm_user_1"
        assert frozen.frozen_id == "frozen:T-001"

    def test_hash_is_deterministic(self):
        content = {"a": 1, "b": [2, 3]}
        f1 = freeze_context("T-1", "ac_1", [], content, "user")
        f2 = freeze_context("T-2", "ac_2", [], content, "user2")
        assert f1.content_hash == f2.content_hash  # same content, same hash


class TestVerifyFrozen:
    def test_unmodified_content_passes(self):
        frozen = freeze_context("T-1", "ac_1", ["market:tcs"], {"price": 100}, "fm")
        assert verify_frozen(frozen) is True

    def test_modified_content_fails(self):
        frozen = freeze_context("T-1", "ac_1", ["market:tcs"], {"price": 100}, "fm")
        frozen.content["price"] = 999  # tamper
        assert verify_frozen(frozen) is False

    def test_modified_hash_fails(self):
        frozen = freeze_context("T-1", "ac_1", [], {"x": 1}, "fm")
        frozen.content_hash = "badhash"
        assert verify_frozen(frozen) is False


class TestStoreRetrieve:
    def test_roundtrip(self):
        """Store and retrieve a frozen context from a mock Redis."""

        class MockRedis:
            def __init__(self):
                self._store = {}
            def set(self, key, value):
                self._store[key] = value
            def get(self, key):
                return self._store.get(key)

        r = MockRedis()
        frozen = freeze_context("T-1", "ac_1", ["market:tcs"], {"price": 42}, "fm")
        store_frozen(r, frozen)
        retrieved = retrieve_frozen(r, "T-1")

        assert retrieved is not None
        assert retrieved.trade_id == "T-1"
        assert retrieved.content == {"price": 42}
        assert verify_frozen(retrieved) is True

    def test_retrieve_missing_returns_none(self):
        class MockRedis:
            def get(self, key):
                return None
        assert retrieve_frozen(MockRedis(), "T-999") is None
