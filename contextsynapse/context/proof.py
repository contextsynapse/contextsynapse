"""ProofToken — immutable proof anchoring a context bundle to a trade decision.

Created when a trade is proposed. Stores the bundle's Merkle root and chain
anchors (which version of each entity was seen). Tamper-proof via SHA-256.

Redis key for lookup: proof:{trade_id} → JSON
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .bundle import BundleToken, TokenRef


def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _canonical(data: Any) -> str:
    return json.dumps(data, sort_keys=True, default=str)


@dataclass
class ChainAnchor:
    """Reference to a specific version in an entity's chain."""
    entity: str
    version: int
    block_hash: str


@dataclass
class ProofToken:
    """Immutable proof of a trade decision's context."""
    proof_id: str
    trade_id: str

    # Bundle provenance
    bundle_id: str
    bundle_hash: str
    merkle_root: str

    # Which entity chain versions were in scope
    anchors: List[ChainAnchor]

    # The decision payload
    decision: Dict[str, Any]
    decided_by: str
    decided_at: str

    # Tamper-proof seal
    proof_hash: str

    # Filled in post-trade
    trade_pnl: Optional[float] = None
    attribution_score: Optional[float] = None


def _compute_proof_hash(bundle_hash: str, decision: Dict, decided_at: str) -> str:
    data = f"{bundle_hash}:{_canonical(decision)}:{decided_at}"
    return _sha256(data)


def create_proof_token(
    trade_id: str,
    bundle: BundleToken,
    decision: Dict[str, Any],
    decided_by: str,
) -> ProofToken:
    """Create a proof token anchoring a bundle to a trade decision."""
    decided_at = datetime.now(timezone.utc).isoformat()
    proof_hash = _compute_proof_hash(bundle.bundle_hash, decision, decided_at)

    anchors = [
        ChainAnchor(entity=ref.entity, version=ref.version, block_hash=ref.block_hash)
        for ref in bundle.token_refs
    ]

    return ProofToken(
        proof_id=f"pt:{trade_id}",
        trade_id=trade_id,
        bundle_id=bundle.bundle_id,
        bundle_hash=bundle.bundle_hash,
        merkle_root=bundle.merkle_root,
        anchors=anchors,
        decision=decision,
        decided_by=decided_by,
        decided_at=decided_at,
        proof_hash=proof_hash,
    )


def verify_proof(proof: ProofToken) -> bool:
    """Verify that a proof token hasn't been tampered with."""
    expected = _compute_proof_hash(proof.bundle_hash, proof.decision, proof.decided_at)
    return expected == proof.proof_hash


def store_proof(redis_client, proof: ProofToken) -> None:
    """Persist a proof token to Redis."""
    data = json.dumps({
        "proof_id": proof.proof_id,
        "trade_id": proof.trade_id,
        "bundle_id": proof.bundle_id,
        "bundle_hash": proof.bundle_hash,
        "merkle_root": proof.merkle_root,
        "anchors": [
            {"entity": a.entity, "version": a.version, "block_hash": a.block_hash}
            for a in proof.anchors
        ],
        "decision": proof.decision,
        "decided_by": proof.decided_by,
        "decided_at": proof.decided_at,
        "proof_hash": proof.proof_hash,
        "trade_pnl": proof.trade_pnl,
        "attribution_score": proof.attribution_score,
    }, default=str)
    redis_client.set(f"proof:{proof.trade_id}", data)


def retrieve_proof(redis_client, trade_id: str) -> Optional[ProofToken]:
    """Retrieve a proof token from Redis."""
    raw = redis_client.get(f"proof:{trade_id}")
    if raw is None:
        return None
    data = json.loads(raw)
    data["anchors"] = [ChainAnchor(**a) for a in data["anchors"]]
    return ProofToken(**data)
