"""Tests for value attribution — tracking which context tokens drive profitable decisions."""
import json
import pytest
from contextcore.context.attribution import (
    attribute_trade_outcome, entity_intelligence_score, trade_attribution,
)
from contextcore.context.proof import ProofToken, ChainAnchor


class MockRedis:
    def __init__(self):
        self._store = {}
        self._lists = {}
    def get(self, k):
        return self._store.get(k)
    def set(self, k, v):
        self._store[k] = v
    def rpush(self, k, v):
        self._lists.setdefault(k, []).append(v)
    def lrange(self, k, start, end):
        lst = self._lists.get(k, [])
        if end == -1:
            return lst[start:]
        return lst[start:end + 1]


def _make_proof(trade_id="T-001", pnl=None):
    return ProofToken(
        proof_id=f"pt:{trade_id}",
        trade_id=trade_id,
        bundle_id="bt:pre_trade:tcs:20260911",
        bundle_hash="bhash",
        merkle_root="mroot",
        anchors=[
            ChainAnchor("market:tcs", 5, "hash_tcs"),
            ChainAnchor("market:india_economy", 3, "hash_macro"),
        ],
        decision={"action": "buy", "symbol": "TCS"},
        decided_by="fm_1",
        decided_at="2026-09-11T10:30:00",
        proof_hash="phash",
        trade_pnl=pnl,
    )


class TestAttributeTradeOutcome:
    def test_records_attribution(self):
        r = MockRedis()
        proof = _make_proof("T-001")
        attribute_trade_outcome(r, proof, 50000.0)
        # Check attribution lists exist for both entities
        tcs_attrs = r.lrange("attribution:market:tcs", 0, -1)
        macro_attrs = r.lrange("attribution:market:india_economy", 0, -1)
        assert len(tcs_attrs) == 1
        assert len(macro_attrs) == 1
        attr = json.loads(tcs_attrs[0])
        assert attr["trade_id"] == "T-001"
        assert attr["pnl"] == 50000.0
        assert attr["entity"] == "market:tcs"

    def test_trade_summary_stored(self):
        r = MockRedis()
        proof = _make_proof("T-001")
        attribute_trade_outcome(r, proof, 50000.0)
        summary = r.get("attribution:trade:T-001")
        assert summary is not None
        data = json.loads(summary)
        assert data["trade_id"] == "T-001"
        assert data["pnl"] == 50000.0
        assert len(data["entities"]) == 2


class TestEntityIntelligenceScore:
    def test_score_from_attributions(self):
        r = MockRedis()
        # Simulate 3 trades: 2 profitable, 1 loss
        for trade_id, pnl in [("T-1", 10000), ("T-2", -5000), ("T-3", 20000)]:
            proof = _make_proof(trade_id)
            attribute_trade_outcome(r, proof, pnl)
        score = entity_intelligence_score(r, "market:tcs")
        assert score["entity"] == "market:tcs"
        assert score["total_decisions"] == 3
        assert score["profitable_decisions"] == 2
        assert score["total_pnl_attributed"] == 25000.0
        assert abs(score["win_rate"] - 2/3) < 0.01

    def test_score_empty_entity(self):
        r = MockRedis()
        score = entity_intelligence_score(r, "market:unknown")
        assert score["total_decisions"] == 0
        assert score["win_rate"] == 0.0


class TestTradeAttribution:
    def test_retrieves_trade_summary(self):
        r = MockRedis()
        proof = _make_proof("T-001")
        attribute_trade_outcome(r, proof, 75000.0)
        result = trade_attribution(r, "T-001")
        assert result is not None
        assert result["pnl"] == 75000.0

    def test_missing_trade_returns_none(self):
        r = MockRedis()
        assert trade_attribution(r, "T-999") is None
