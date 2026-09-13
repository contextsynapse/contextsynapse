"""Tests for ContextToken hash chain — blockchain-inspired per-entity versioning."""
import json
import pytest
from contextcore.context.token import (
    ContextToken, ContextChain, compute_block_hash,
    canonical_json, content_hash,
)


class MockRedis:
    """Minimal Redis mock for chain storage."""
    def __init__(self):
        self._store = {}
    def get(self, key):
        return self._store.get(key)
    def set(self, key, value):
        self._store[key] = value


class TestComputeBlockHash:
    def test_deterministic(self):
        h1 = compute_block_hash(1, "abc", None, "2026-09-11T10:00:00")
        h2 = compute_block_hash(1, "abc", None, "2026-09-11T10:00:00")
        assert h1 == h2

    def test_different_version_different_hash(self):
        h1 = compute_block_hash(1, "abc", None, "2026-09-11T10:00:00")
        h2 = compute_block_hash(2, "abc", None, "2026-09-11T10:00:00")
        assert h1 != h2

    def test_different_prev_hash_different_hash(self):
        h1 = compute_block_hash(2, "abc", "prev1", "2026-09-11T10:00:00")
        h2 = compute_block_hash(2, "abc", "prev2", "2026-09-11T10:00:00")
        assert h1 != h2

    def test_genesis_uses_genesis_string(self):
        h = compute_block_hash(1, "abc", None, "2026-09-11T10:00:00")
        assert isinstance(h, str) and len(h) == 64  # SHA-256 hex


class TestCanonicalJson:
    def test_sorted_keys(self):
        assert canonical_json({"b": 2, "a": 1}) == canonical_json({"a": 1, "b": 2})

    def test_deterministic(self):
        data = {"price": 3500, "symbol": "TCS"}
        assert canonical_json(data) == canonical_json(data)


class TestContextChain:
    def setup_method(self):
        self.redis = MockRedis()

    def test_genesis_block(self):
        chain = ContextChain(self.redis, "market:tcs")
        token = chain.append({"price": 3500}, "price_sensor")
        assert token.version == 1
        assert token.prev_hash is None
        assert token.entity == "market:tcs"
        assert token.token_id == "ct:market:tcs:v1"
        assert "price_sensor" in token.pipelines
        assert token.block_hash  # non-empty

    def test_chain_append_links_to_previous(self):
        chain = ContextChain(self.redis, "market:tcs")
        v1 = chain.append({"price": 3500}, "price_sensor")
        v2 = chain.append({"sentiment": 0.7}, "news_rss")
        assert v2.version == 2
        assert v2.prev_hash == v1.block_hash
        assert v2.token_id == "ct:market:tcs:v2"

    def test_get_head(self):
        chain = ContextChain(self.redis, "market:tcs")
        chain.append({"price": 3500}, "price_sensor")
        chain.append({"sentiment": 0.7}, "news_rss")
        head = chain.get_head()
        assert head is not None
        assert head.version == 2

    def test_get_version(self):
        chain = ContextChain(self.redis, "market:tcs")
        chain.append({"price": 3500}, "price_sensor")
        chain.append({"sentiment": 0.7}, "news_rss")
        v1 = chain.get_version(1)
        assert v1 is not None
        assert v1.version == 1

    def test_get_version_missing_returns_none(self):
        chain = ContextChain(self.redis, "market:tcs")
        assert chain.get_version(99) is None

    def test_get_head_empty_chain_returns_none(self):
        chain = ContextChain(self.redis, "market:tcs")
        assert chain.get_head() is None

    def test_pipeline_merge_preserves_existing(self):
        chain = ContextChain(self.redis, "market:tcs")
        chain.append({"close": 3500}, "price_sensor")
        v2 = chain.append({"sentiment": 0.7}, "news_rss")
        assert "price_sensor" in v2.content_summary
        assert "news_rss" in v2.content_summary
        assert v2.content_summary["price_sensor"]["close"] == 3500
        assert v2.content_summary["news_rss"]["sentiment"] == 0.7

    def test_multiple_pipelines_tracked(self):
        chain = ContextChain(self.redis, "market:tcs")
        chain.append({"close": 3500}, "price_sensor")
        v2 = chain.append({"sentiment": 0.7}, "news_rss")
        assert set(v2.pipelines) == {"price_sensor", "news_rss"}
        assert "price_sensor" in v2.pipeline_freshness
        assert "news_rss" in v2.pipeline_freshness

    def test_verify_clean_chain(self):
        chain = ContextChain(self.redis, "market:tcs")
        chain.append({"price": 3500}, "price_sensor")
        chain.append({"sentiment": 0.7}, "news_rss")
        chain.append({"upgrades": 2}, "analyst_feed")
        valid, count = chain.verify()
        assert valid is True
        assert count == 3

    def test_verify_tampered_chain(self):
        chain = ContextChain(self.redis, "market:tcs")
        chain.append({"price": 3500}, "price_sensor")
        chain.append({"sentiment": 0.7}, "news_rss")
        # Tamper with v1
        raw = self.redis.get("chain:market:tcs:v1")
        data = json.loads(raw)
        data["content_summary"]["price_sensor"]["close"] = 9999
        data["content_hash"] = "tampered"
        self.redis.set("chain:market:tcs:v1", json.dumps(data))
        valid, count = chain.verify()
        assert valid is False
        assert count == 0  # broke at v1

    def test_verify_empty_chain(self):
        chain = ContextChain(self.redis, "market:tcs")
        valid, count = chain.verify()
        assert valid is True
        assert count == 0

    def test_freshness_score_computed(self):
        chain = ContextChain(self.redis, "market:tcs")
        token = chain.append({"price": 3500}, "price_sensor")
        assert 0.0 <= token.freshness_score <= 1.0
