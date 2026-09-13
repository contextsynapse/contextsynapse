"""Tests for BundleToken — Merkle-root assembled context bundles."""
import pytest
from contextcore.context.bundle import (
    TokenRef, BundleToken, compute_merkle_root,
    generate_merkle_proof, verify_membership, create_bundle,
)
from contextcore.context.assembled import ContextPurpose


class TestComputeMerkleRoot:
    def test_single_hash(self):
        root, tree = compute_merkle_root(["aaa"])
        assert isinstance(root, str) and len(root) == 64

    def test_two_hashes(self):
        root, tree = compute_merkle_root(["aaa", "bbb"])
        assert isinstance(root, str)

    def test_deterministic(self):
        hashes = ["abc", "def", "ghi"]
        r1, _ = compute_merkle_root(hashes)
        r2, _ = compute_merkle_root(hashes)
        assert r1 == r2

    def test_order_independent(self):
        """Sorted internally, so order of input doesn't matter."""
        r1, _ = compute_merkle_root(["bbb", "aaa", "ccc"])
        r2, _ = compute_merkle_root(["aaa", "ccc", "bbb"])
        assert r1 == r2

    def test_different_hashes_different_root(self):
        r1, _ = compute_merkle_root(["aaa", "bbb"])
        r2, _ = compute_merkle_root(["aaa", "ccc"])
        assert r1 != r2

    def test_empty_hashes(self):
        root, tree = compute_merkle_root([])
        assert isinstance(root, str)
        assert tree == []

    def test_odd_number_of_hashes(self):
        root, tree = compute_merkle_root(["aaa", "bbb", "ccc"])
        assert isinstance(root, str)


class TestMerkleProof:
    def test_valid_member_proves(self):
        hashes = ["aaa", "bbb", "ccc", "ddd"]
        root, _ = compute_merkle_root(hashes)
        proof = generate_merkle_proof("bbb", hashes)
        assert verify_membership("bbb", root, proof)

    def test_non_member_fails(self):
        hashes = ["aaa", "bbb", "ccc"]
        root, _ = compute_merkle_root(hashes)
        proof = generate_merkle_proof("bbb", hashes)
        assert not verify_membership("zzz", root, proof)

    def test_single_element_proves(self):
        hashes = ["aaa"]
        root, _ = compute_merkle_root(hashes)
        proof = generate_merkle_proof("aaa", hashes)
        assert verify_membership("aaa", root, proof)


class TestCreateBundle:
    def test_creates_bundle_with_merkle_root(self):
        refs = [
            TokenRef(entity="market:tcs", version=5, block_hash="hash_tcs"),
            TokenRef(entity="market:india_economy", version=3, block_hash="hash_macro"),
        ]
        bundle = create_bundle(
            purpose=ContextPurpose.PRE_TRADE,
            token_refs=refs,
            assembled_by="fm_1",
            acl_denied=["portfolio:p999:holdings"],
        )
        assert bundle.bundle_id.startswith("bt:pre_trade:")
        assert bundle.merkle_root  # non-empty
        assert bundle.bundle_hash  # non-empty
        assert len(bundle.token_refs) == 2
        assert bundle.assembled_by == "fm_1"
        assert bundle.acl_denied == ["portfolio:p999:holdings"]

    def test_bundle_hash_includes_merkle_root(self):
        refs = [TokenRef("market:tcs", 1, "h1")]
        b1 = create_bundle(ContextPurpose.AD_HOC, refs, "fm", [])
        refs2 = [TokenRef("market:tcs", 1, "h2")]
        b2 = create_bundle(ContextPurpose.AD_HOC, refs2, "fm", [])
        assert b1.bundle_hash != b2.bundle_hash  # different Merkle roots
