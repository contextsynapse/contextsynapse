"""Value Attribution — tracks which context tokens drove profitable decisions.

After a trade is executed and P&L is known, attribute_trade_outcome() records
which entity chain versions contributed. entity_intelligence_score() computes
a track record for each entity's context quality over time.

Redis keys:
  attribution:{entity}          → LIST of JSON attribution records
  attribution:trade:{trade_id}  → JSON trade-level summary
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .proof import ProofToken


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def attribute_trade_outcome(
    redis_client,
    proof: ProofToken,
    pnl: float,
) -> None:
    """Attribute trade P&L back to the context tokens that influenced it.

    For each ChainAnchor in the proof, appends an attribution record to
    the entity's list key. Also stores a trade-level summary keyed by trade_id.
    """
    proof.trade_pnl = pnl

    entities: List[str] = []
    for anchor in proof.anchors:
        attribution = {
            "trade_id": proof.trade_id,
            "entity": anchor.entity,
            "version": anchor.version,
            "block_hash": anchor.block_hash,
            "pnl": pnl,
            "attributed_at": _now_iso(),
        }
        redis_client.rpush(
            f"attribution:{anchor.entity}",
            json.dumps(attribution, default=str),
        )
        entities.append(anchor.entity)

    # Store trade-level summary
    summary = {
        "trade_id": proof.trade_id,
        "pnl": pnl,
        "entities": entities,
        "decided_by": proof.decided_by,
        "decided_at": proof.decided_at,
        "attributed_at": _now_iso(),
    }
    redis_client.set(
        f"attribution:trade:{proof.trade_id}",
        json.dumps(summary, default=str),
    )


def entity_intelligence_score(redis_client, entity: str) -> Dict[str, Any]:
    """Compute intelligence score for an entity based on attribution history.

    Returns a dict with:
      - entity: the entity name
      - total_decisions: number of trades where this entity's context was used
      - profitable_decisions: trades with pnl > 0
      - total_pnl_attributed: sum of pnl across all trades
      - win_rate: profitable_decisions / total_decisions
      - avg_pnl_per_decision: total_pnl_attributed / total_decisions
    """
    raw_list = redis_client.lrange(f"attribution:{entity}", 0, -1)
    if not raw_list:
        return {
            "entity": entity,
            "total_decisions": 0,
            "profitable_decisions": 0,
            "total_pnl_attributed": 0.0,
            "win_rate": 0.0,
            "avg_pnl_per_decision": 0.0,
        }

    attributions = [json.loads(item) for item in raw_list]
    total = len(attributions)
    profitable = sum(1 for a in attributions if a["pnl"] > 0)
    total_pnl = sum(a["pnl"] for a in attributions)

    return {
        "entity": entity,
        "total_decisions": total,
        "profitable_decisions": profitable,
        "total_pnl_attributed": total_pnl,
        "win_rate": profitable / total if total > 0 else 0.0,
        "avg_pnl_per_decision": total_pnl / total if total > 0 else 0.0,
    }


def trade_attribution(redis_client, trade_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve attribution summary for a specific trade.

    Returns the summary dict stored by attribute_trade_outcome(), or None
    if no attribution has been recorded for this trade_id.
    """
    raw = redis_client.get(f"attribution:trade:{trade_id}")
    if raw is None:
        return None
    return json.loads(raw)
