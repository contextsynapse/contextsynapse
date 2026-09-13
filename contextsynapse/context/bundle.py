"""BundleToken — Merkle-root assembled context bundles.

When atomic contexts are assembled for a purpose, a BundleToken records
which entity chain versions were included, with a Merkle root for
efficient verification and selective proofs.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .assembled import ContextPurpose


def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


@dataclass
class TokenRef:
    """Reference to a specific version of an entity's chain."""
    entity: str
    version: int
    block_hash: str


@dataclass
class BundleToken:
    """Assembled context bundle with Merkle root verification."""
    bundle_id: str
    purpose: ContextPurpose

    token_refs: List[TokenRef]

    merkle_root: str
    merkle_tree: List[str]

    assembled_by: str
    assembled_at: str
    acl_denied: List[str]

    bundle_hash: str


def compute_merkle_root(block_hashes: List[str]) -> Tuple[str, List[str]]:
    """Compute Merkle root from a list of block hashes.

    Returns (root_hash, full_tree_nodes) for selective verification.
    Input order doesn't matter — hashes are sorted for determinism.
    """
    if not block_hashes:
        return (_sha256("empty"), [])

    leaves = [_sha256(h) for h in sorted(block_hashes)]
    tree = list(leaves)

    level = leaves
    while len(level) > 1:
        next_level = []
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else left
            parent = _sha256(left + right)
            next_level.append(parent)
            tree.append(parent)
        level = next_level

    return (level[0], tree)


def generate_merkle_proof(
    block_hash: str,
    all_hashes: List[str],
) -> List[Tuple[str, str]]:
    """Generate a Merkle proof path for a specific block_hash.

    Returns list of (sibling_hash, side) pairs from leaf to root.
    """
    hashed_leaf = _sha256(block_hash)
    leaves = [_sha256(h) for h in sorted(all_hashes)]
    if hashed_leaf not in leaves:
        return []

    proof = []
    level = leaves
    target = hashed_leaf

    while len(level) > 1:
        idx = level.index(target)
        if idx % 2 == 0:
            sibling_idx = idx + 1 if idx + 1 < len(level) else idx
            sibling = level[sibling_idx]
            proof.append((sibling, "right"))
            parent = _sha256(target + sibling)
        else:
            sibling = level[idx - 1]
            proof.append((sibling, "left"))
            parent = _sha256(sibling + target)

        next_level = []
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else left
            next_level.append(_sha256(left + right))
        level = next_level
        target = parent

    return proof


def verify_membership(
    block_hash: str,
    merkle_root: str,
    proof_path: List[Tuple[str, str]],
) -> bool:
    """Verify that a block_hash is part of a Merkle root via proof path."""
    current = _sha256(block_hash)
    for sibling, side in proof_path:
        if side == "left":
            current = _sha256(sibling + current)
        else:
            current = _sha256(current + sibling)
    return current == merkle_root


def create_bundle(
    purpose: ContextPurpose,
    token_refs: List[TokenRef],
    assembled_by: str,
    acl_denied: List[str],
) -> BundleToken:
    """Create a BundleToken from token references."""
    block_hashes = [ref.block_hash for ref in token_refs]
    merkle_root, merkle_tree = compute_merkle_root(block_hashes)
    timestamp = datetime.now(timezone.utc).isoformat()
    ts_compact = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

    bundle_hash = _sha256(f"{merkle_root}:{assembled_by}:{timestamp}")

    subject = token_refs[0].entity.split(":")[-1] if token_refs else "empty"
    bundle_id = f"bt:{purpose.value}:{subject}:{ts_compact}"

    return BundleToken(
        bundle_id=bundle_id,
        purpose=purpose,
        token_refs=token_refs,
        merkle_root=merkle_root,
        merkle_tree=merkle_tree,
        assembled_by=assembled_by,
        assembled_at=timestamp,
        acl_denied=acl_denied,
        bundle_hash=bundle_hash,
    )
