"""End-to-end test: pipeline feeds chain → assembly creates bundle → trade creates proof → attribution."""
import json
import os
import pytest

os.environ.setdefault("AICONTEXTDB_JWT_SECRET", "test-secret-key")

from contextcore.context.token import ContextChain
from contextcore.context.bundle import TokenRef, create_bundle, compute_merkle_root, generate_merkle_proof, verify_membership
from contextcore.context.proof import create_proof_token, verify_proof, store_proof, retrieve_proof
from contextcore.context.attribution import attribute_trade_outcome, entity_intelligence_score, trade_attribution
from contextcore.context.assembled import ContextPurpose


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


class TestTokenizationE2E:
    def setup_method(self):
        self.redis = MockRedis()

    def test_full_flow(self):
        # 1. Pipelines feed entity chains
        tcs_chain = ContextChain(self.redis, "market:tcs")
        tcs_chain.append({"close": 3500, "volume": 1000000}, "price_sensor")
        tcs_chain.append({"sentiment": 0.65, "headline": "TCS beats estimates"}, "news_rss")
        tcs_chain.append({"upgrades": 2}, "analyst_feed")

        macro_chain = ContextChain(self.redis, "market:india_economy")
        macro_chain.append({"rbi_rate": 6.0, "action": "cut"}, "rbi_feed")

        # 2. Verify chains are intact
        valid_tcs, count_tcs = tcs_chain.verify()
        valid_macro, count_macro = macro_chain.verify()
        assert valid_tcs and count_tcs == 3
        assert valid_macro and count_macro == 1

        # 3. Get chain heads for assembly
        tcs_head = tcs_chain.get_head()
        macro_head = macro_chain.get_head()
        assert tcs_head.version == 3
        assert macro_head.version == 1

        # 4. Create bundle from chain heads
        refs = [
            TokenRef(tcs_head.entity, tcs_head.version, tcs_head.block_hash),
            TokenRef(macro_head.entity, macro_head.version, macro_head.block_hash),
        ]
        bundle = create_bundle(ContextPurpose.PRE_TRADE, refs, "fm_1", [])
        assert bundle.merkle_root

        # 5. Verify Merkle membership
        proof_path = generate_merkle_proof(tcs_head.block_hash, [r.block_hash for r in refs])
        assert verify_membership(tcs_head.block_hash, bundle.merkle_root, proof_path)

        # 6. Create proof token for trade decision
        decision = {"action": "buy", "symbol": "TCS.NS", "qty": 500, "price": 3480}
        proof = create_proof_token("T-20260911-001", bundle, decision, "fm_1")
        assert verify_proof(proof)
        assert len(proof.anchors) == 2
        assert proof.anchors[0].entity == "market:tcs"
        assert proof.anchors[0].version == 3

        # 7. Store and retrieve proof
        store_proof(self.redis, proof)
        retrieved = retrieve_proof(self.redis, "T-20260911-001")
        assert retrieved is not None
        assert verify_proof(retrieved)

        # 8. Attribute trade outcome (profit)
        attribute_trade_outcome(self.redis, proof, 75000.0)

        # 9. Check entity intelligence scores
        tcs_score = entity_intelligence_score(self.redis, "market:tcs")
        assert tcs_score["total_decisions"] == 1
        assert tcs_score["profitable_decisions"] == 1
        assert tcs_score["total_pnl_attributed"] == 75000.0

        macro_score = entity_intelligence_score(self.redis, "market:india_economy")
        assert macro_score["total_decisions"] == 1

        # 10. Check trade attribution
        trade_attr = trade_attribution(self.redis, "T-20260911-001")
        assert trade_attr is not None
        assert trade_attr["pnl"] == 75000.0
        assert "market:tcs" in trade_attr["entities"]
        assert "market:india_economy" in trade_attr["entities"]
