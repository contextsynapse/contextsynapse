"""ContextToken — blockchain-inspired per-entity hash chain.

Each atomic context (entity) maintains a chain of versioned tokens.
Every append creates a new block linked to its predecessor via SHA-256.
Multiple pipelines feed the same entity — each pipeline writes to its
own namespace within the content dict.

Redis keys:
  chain:{entity}:head  → int (latest version number)
  chain:{entity}:v{N}  → JSON (ContextToken)
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


def canonical_json(data: Any) -> str:
    """Deterministic JSON serialization for hashing."""
    return json.dumps(data, sort_keys=True, default=str)


def content_hash(data: Any) -> str:
    """SHA-256 hash of canonical JSON."""
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def compute_block_hash(
    version: int,
    content_hash_val: str,
    prev_hash: Optional[str],
    timestamp: str,
) -> str:
    """Compute the block hash for a context token version.

    block_hash = SHA-256(version || content_hash || prev_hash || timestamp)
    Genesis blocks use the literal string 'genesis' in place of prev_hash.
    """
    block_data = f"{version}:{content_hash_val}:{prev_hash or 'genesis'}:{timestamp}"
    return hashlib.sha256(block_data.encode("utf-8")).hexdigest()


@dataclass
class ContextToken:
    """One version (block) in an entity's hash chain."""
    token_id: str
    entity: str
    version: int

    content_hash: str
    content_summary: Dict[str, Any]

    prev_hash: Optional[str]
    block_hash: str

    pipelines: List[str]
    pipeline_freshness: Dict[str, str]

    created_at: str
    freshness_score: float

    times_assembled: int = 0
    decisions_influenced: int = 0


def _token_to_json(token: ContextToken) -> str:
    return json.dumps({
        "token_id": token.token_id,
        "entity": token.entity,
        "version": token.version,
        "content_hash": token.content_hash,
        "content_summary": token.content_summary,
        "prev_hash": token.prev_hash,
        "block_hash": token.block_hash,
        "pipelines": token.pipelines,
        "pipeline_freshness": token.pipeline_freshness,
        "created_at": token.created_at,
        "freshness_score": token.freshness_score,
        "times_assembled": token.times_assembled,
        "decisions_influenced": token.decisions_influenced,
    }, default=str)


def _token_from_json(raw: str) -> ContextToken:
    data = json.loads(raw)
    return ContextToken(**data)


class ContextChain:
    """Manages the hash chain for an atomic context (entity)."""

    def __init__(self, redis_client: Any, entity: str) -> None:
        self._r = redis_client
        self._entity = entity

    def append(self, content: Dict[str, Any], pipeline: str) -> ContextToken:
        """Append a new version to the chain.

        Merges incoming content under the pipeline's namespace, computes a
        content hash over the merged state, then seals the block with a
        SHA-256 block hash that chains back to the previous block.
        """
        head_version = self._get_head_version()
        prev_token = self.get_version(head_version) if head_version > 0 else None

        merged = self._merge_content(prev_token, content, pipeline)
        new_version = head_version + 1
        c_hash = content_hash(merged)
        prev_hash = prev_token.block_hash if prev_token else None
        timestamp = datetime.now(timezone.utc).isoformat()
        block_hash = compute_block_hash(new_version, c_hash, prev_hash, timestamp)

        pipeline_freshness: Dict[str, str] = {}
        if prev_token:
            pipeline_freshness = dict(prev_token.pipeline_freshness)
        pipeline_freshness[pipeline] = timestamp

        pipelines = sorted(set(
            (prev_token.pipelines if prev_token else []) + [pipeline]
        ))

        token = ContextToken(
            token_id=f"ct:{self._entity}:v{new_version}",
            entity=self._entity,
            version=new_version,
            content_hash=c_hash,
            content_summary=merged,
            prev_hash=prev_hash,
            block_hash=block_hash,
            pipelines=pipelines,
            pipeline_freshness=pipeline_freshness,
            created_at=timestamp,
            freshness_score=self._compute_freshness(pipeline_freshness),
            times_assembled=0,
            decisions_influenced=0,
        )

        self._r.set(f"chain:{self._entity}:v{new_version}", _token_to_json(token))
        self._r.set(f"chain:{self._entity}:head", str(new_version))
        return token

    def get_head(self) -> Optional[ContextToken]:
        """Return the latest token in the chain, or None if empty."""
        head = self._get_head_version()
        if head == 0:
            return None
        return self.get_version(head)

    def get_version(self, version: int) -> Optional[ContextToken]:
        """Return a specific version from the chain, or None if not found."""
        raw = self._r.get(f"chain:{self._entity}:v{version}")
        if raw is None:
            return None
        return _token_from_json(raw)

    def verify(self) -> Tuple[bool, int]:
        """Walk the chain from genesis to head, verifying each link.

        Checks:
        - block_hash matches recomputed value (tamper detection)
        - prev_hash correctly references the previous block's hash

        Returns:
            (is_valid, verified_count) where verified_count is the number of
            blocks successfully verified before a failure (or all of them).
        """
        head = self._get_head_version()
        if head == 0:
            return (True, 0)

        prev_hash: Optional[str] = None
        for v in range(1, head + 1):
            token = self.get_version(v)
            if token is None:
                return (False, v - 1)
            expected = compute_block_hash(
                token.version, token.content_hash, prev_hash, token.created_at
            )
            if token.block_hash != expected:
                return (False, v - 1)
            if token.prev_hash != prev_hash:
                return (False, v - 1)
            prev_hash = token.block_hash

        return (True, head)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_head_version(self) -> int:
        raw = self._r.get(f"chain:{self._entity}:head")
        if raw is None:
            return 0
        return int(raw)

    def _merge_content(
        self,
        prev_token: Optional[ContextToken],
        new_content: Dict[str, Any],
        pipeline: str,
    ) -> Dict[str, Any]:
        """Merge new pipeline data into entity content.

        Each pipeline writes to its own namespace within the content dict,
        so contributions from different pipelines never collide.
        """
        if prev_token is None:
            return {pipeline: new_content}
        merged = dict(prev_token.content_summary)
        merged[pipeline] = new_content
        return merged

    def _compute_freshness(self, pipeline_freshness: Dict[str, str]) -> float:
        """Compute a freshness score in [0.0, 1.0] based on pipeline timestamps.

        Score decays linearly from 1.0 (just written) to 0.0 over 1 hour.
        Returns the mean score across all contributing pipelines.
        """
        if not pipeline_freshness:
            return 0.0
        now = time.time()
        scores = []
        for ts_str in pipeline_freshness.values():
            try:
                ts = datetime.fromisoformat(ts_str).timestamp()
                age_seconds = now - ts
                score = max(0.0, 1.0 - (age_seconds / 3600))  # decay over 1 hour
                scores.append(score)
            except (ValueError, TypeError):
                scores.append(0.0)
        return sum(scores) / len(scores) if scores else 0.0
