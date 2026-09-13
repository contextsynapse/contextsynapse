"""Frozen Context — immutable snapshot of assembled context at trade decision time.

When a trade is proposed, the context the FM saw is frozen with a SHA-256 hash.
SEBI auditors can later verify the hash to confirm the snapshot hasn't been tampered with.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class FrozenContext:
    """Immutable snapshot of an assembled context tied to a trade decision."""
    frozen_id: str
    trade_id: str
    assembled_id: str
    atomics: List[str]
    content: Dict[str, Any]
    content_hash: str
    frozen_at: str
    frozen_by: str


def _compute_hash(content: Dict[str, Any]) -> str:
    """SHA-256 hash of canonical JSON (sorted keys, deterministic)."""
    canonical = json.dumps(content, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def freeze_context(
    trade_id: str,
    assembled_id: str,
    atomics: List[str],
    content: Dict[str, Any],
    frozen_by: str,
) -> FrozenContext:
    """Create a frozen context snapshot for a trade decision."""
    return FrozenContext(
        frozen_id=f"frozen:{trade_id}",
        trade_id=trade_id,
        assembled_id=assembled_id,
        atomics=list(atomics),
        content=content,
        content_hash=_compute_hash(content),
        frozen_at=datetime.now(timezone.utc).isoformat(),
        frozen_by=frozen_by,
    )


def verify_frozen(frozen: FrozenContext) -> bool:
    """Verify that a frozen context's content hasn't been tampered with."""
    return _compute_hash(frozen.content) == frozen.content_hash


def store_frozen(redis_client, frozen: FrozenContext) -> None:
    """Persist a frozen context to Redis."""
    data = json.dumps({
        "frozen_id": frozen.frozen_id,
        "trade_id": frozen.trade_id,
        "assembled_id": frozen.assembled_id,
        "atomics": frozen.atomics,
        "content": frozen.content,
        "content_hash": frozen.content_hash,
        "frozen_at": frozen.frozen_at,
        "frozen_by": frozen.frozen_by,
    }, default=str)
    redis_client.set(f"context:{frozen.frozen_id}", data)


def retrieve_frozen(redis_client, trade_id: str) -> Optional[FrozenContext]:
    """Retrieve a frozen context from Redis by trade ID."""
    raw = redis_client.get(f"context:frozen:{trade_id}")
    if raw is None:
        return None
    data = json.loads(raw)
    return FrozenContext(**data)
