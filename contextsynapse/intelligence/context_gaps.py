"""
Context Gap Detection — tracks what agents searched for but couldn't find.

When a search returns empty or low-quality results, a ContextGap is recorded.
Over time, gaps reveal:
- What knowledge is missing from the graph
- What entities need better linking
- What topics need more ingestion
- Which agents consistently hit dead ends

Usage:
    from contextsynapse.intelligence.context_gaps import record_gap, get_top_gaps

    # Called automatically by search tools when results are poor
    record_gap(query="asset tag policy", label="Rule", agent="PolicyAgent", namespace="corp_kb")

    # Dashboard/API: show most common gaps
    gaps = get_top_gaps(namespace="corp_kb", limit=10)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def record_gap(
    query: str,
    label: str = "",
    agent_name: str = "",
    agent_id: str = "",
    namespace: str = "",
    result_count: int = 0,
    context: str = "",
):
    """Record a context gap — something an agent searched for but couldn't find.

    Stored in Redis sorted set for fast aggregation.
    Key: gap:{namespace}
    Score: frequency count (incremented on each occurrence)
    Value: JSON {query, label, agents, first_seen, last_seen, count}
    """
    if not query or len(query.strip()) < 3:
        return

    try:
        import redis
        url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
        if not url:
            return
        r = redis.from_url(url, decode_responses=True)

        # Normalize query for dedup
        normalized = query.strip().lower()
        gap_key = hashlib.sha256(f"{namespace}:{label}:{normalized}".encode()).hexdigest()[:16]
        redis_key = f"context_gaps:{namespace}"

        # Check if gap already exists
        existing = r.hget(f"context_gap_details:{namespace}", gap_key)
        now = datetime.now(timezone.utc).isoformat()

        if existing:
            gap = json.loads(existing)
            gap["count"] = gap.get("count", 0) + 1
            gap["last_seen"] = now
            if agent_name and agent_name not in gap.get("agents", []):
                gap["agents"].append(agent_name)
        else:
            gap = {
                "query": query.strip()[:200],
                "label": label,
                "agents": [agent_name] if agent_name else [],
                "namespace": namespace,
                "result_count": result_count,
                "context": context[:200],
                "first_seen": now,
                "last_seen": now,
                "count": 1,
                "status": "open",  # open | resolved | ignored
            }

        r.hset(f"context_gap_details:{namespace}", gap_key, json.dumps(gap))
        r.zincrby(redis_key, 1, gap_key)
        r.expire(redis_key, 2592000)  # 30 days
        r.expire(f"context_gap_details:{namespace}", 2592000)

        logger.debug("[GAP] Recorded: '%s' (label=%s, count=%d)", query[:40], label, gap["count"])

    except Exception as e:
        logger.debug("[GAP] Failed to record: %s", e)


def get_top_gaps(
    namespace: str = "",
    limit: int = 20,
    status: str = "open",
) -> List[Dict[str, Any]]:
    """Get the most frequent context gaps, sorted by frequency."""
    try:
        import redis
        url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
        if not url:
            return []
        r = redis.from_url(url, decode_responses=True)

        redis_key = f"context_gaps:{namespace}"
        # Get top gaps by frequency
        top = r.zrevrange(redis_key, 0, limit - 1, withscores=True)

        gaps = []
        for gap_key, score in top:
            detail = r.hget(f"context_gap_details:{namespace}", gap_key)
            if detail:
                gap = json.loads(detail)
                if status and gap.get("status") != status:
                    continue
                gap["frequency"] = int(score)
                gap["gap_id"] = gap_key
                gaps.append(gap)

        return gaps

    except Exception:
        return []


def resolve_gap(namespace: str, gap_id: str):
    """Mark a gap as resolved (e.g., after ingesting the missing content)."""
    try:
        import redis
        url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
        if not url:
            return
        r = redis.from_url(url, decode_responses=True)
        detail = r.hget(f"context_gap_details:{namespace}", gap_id)
        if detail:
            gap = json.loads(detail)
            gap["status"] = "resolved"
            gap["resolved_at"] = datetime.now(timezone.utc).isoformat()
            r.hset(f"context_gap_details:{namespace}", gap_id, json.dumps(gap))
    except Exception:
        pass


def get_gap_stats(namespace: str = "") -> Dict[str, Any]:
    """Get gap statistics for a namespace."""
    try:
        import redis
        url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
        if not url:
            return {}
        r = redis.from_url(url, decode_responses=True)

        total = r.zcard(f"context_gaps:{namespace}")
        details = r.hgetall(f"context_gap_details:{namespace}")

        open_count = 0
        resolved_count = 0
        top_labels = Counter()
        top_agents = Counter()

        for v in details.values():
            try:
                gap = json.loads(v)
                if gap.get("status") == "open":
                    open_count += 1
                else:
                    resolved_count += 1
                if gap.get("label"):
                    top_labels[gap["label"]] += gap.get("count", 1)
                for agent in gap.get("agents", []):
                    top_agents[agent] += 1
            except Exception:
                pass

        return {
            "total_gaps": total,
            "open": open_count,
            "resolved": resolved_count,
            "top_missing_labels": dict(top_labels.most_common(5)),
            "agents_hitting_gaps": dict(top_agents.most_common(5)),
        }
    except Exception:
        return {}
