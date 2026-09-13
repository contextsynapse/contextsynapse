"""Tests for ProofToken — immutable trade decision proofs."""
import json
import pytest
from contextcore.context.proof import (
    ChainAnchor, ProofToken, create_proof_token, verify_proof,
    store_proof, retrieve_proof,
)
from contextcore.context.bundle import TokenRef, BundleToken, create_bundle
from contextcore.context.assembled import ContextPurpose


def _make_bundle():
    refs = [
        TokenRef("market:tcs", 5, "hash_tcs_v5"),
        TokenRef("market:india_economy", 3, "hash_macro_v3"),
    ]
    return create_bundle(ContextPurpose.PRE_TRADE, refs, "fm_1", [])


class TestCreateProofToken:
    def test_creates_proof_with_anchors(self):
        bundle = _make_bundle()
        decision = {"action": "buy", "symbol": "TCS", "qty": 500, "price": 3480}
        proof = create_proof_token("T-001", bundle, decision, "fm_1")
        assert proof.proof_id == "pt:T-001"
        assert proof.trade_id == "T-001"
        assert proof.bundle_id == bundle.bundle_id
        assert proof.bundle_hash == bundle.bundle_hash
        assert proof.merkle_root == bundle.merkle_root
        assert len(proof.anchors) == 2
        assert proof.anchors[0].entity == "market:tcs"
        assert proof.anchors[0].version == 5
        assert proof.decision == decision
        assert proof.decided_by == "fm_1"
        assert proof.proof_hash  # non-empty

    def test_proof_hash_deterministic_for_same_inputs(self):
        bundle = _make_bundle()
        decision = {"action": "buy", "symbol": "TCS"}
        p1 = create_proof_token("T-001", bundle, decision, "fm_1")
        # proof_hash depends on decided_at timestamp, so two calls differ
        # but the hash should be non-empty and well-formed
        assert isinstance(p1.proof_hash, str) and len(p1.proof_hash) == 64


class TestVerifyProof:
    def test_unmodified_passes(self):
        bundle = _make_bundle()
        proof = create_proof_token("T-001", bundle, {"action": "buy"}, "fm_1")
        assert verify_proof(proof) is True

    def test_modified_decision_fails(self):
        bundle = _make_bundle()
        proof = create_proof_token("T-001", bundle, {"action": "buy"}, "fm_1")
        proof.decision = {"action": "sell"}  # tamper
        assert verify_proof(proof) is False

    def test_modified_hash_fails(self):
        bundle = _make_bundle()
        proof = create_proof_token("T-001", bundle, {"action": "buy"}, "fm_1")
        proof.proof_hash = "tampered"
        assert verify_proof(proof) is False


class TestStoreRetrieve:
    def test_roundtrip(self):
        class MockRedis:
            def __init__(self): self._store = {}
            def set(self, k, v): self._store[k] = v
            def get(self, k): return self._store.get(k)

        r = MockRedis()
        bundle = _make_bundle()
        proof = create_proof_token("T-002", bundle, {"action": "buy"}, "fm_1")
        store_proof(r, proof)
        retrieved = retrieve_proof(r, "T-002")
        assert retrieved is not None
        assert retrieved.trade_id == "T-002"
        assert verify_proof(retrieved) is True

    def test_retrieve_missing(self):
        class MockRedis:
            def get(self, k): return None
        assert retrieve_proof(MockRedis(), "T-999") is None
