"""
Promotion Path — Runtime to Atomic Context
============================================
Moves high-quality agent findings from runtime graph to atomic context.

Hybrid model:
  - Auto-promote: score >= threshold (default 0.8)
  - Review queue: score >= review_threshold (default 0.5)
  - Ignore: below review_threshold

Components:
  - PromotionScorer: scores runtime nodes for promotion readiness
  - PromotionQueue: Redis-backed review queue
  - promote_node(): copies a node from runtime to atomic
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Defaults ─────────────────────────────────────────────────────────

PROMOTION_DEFAULTS = {
    "auto_promote_threshold": 0.8,
    "review_threshold": 0.5,
    "weights": {
        "agent_references": 0.3,
        "schema_valid": 0.2,
        "atomic_overlap": 0.2,
        "agent_trust": 0.15,
        "content_quality": 0.15,
    },
    "require_human_review": False,
}

_STOP = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "have",
    "has", "had", "do", "does", "did", "will", "would", "shall", "should",
    "may", "might", "can", "could", "of", "in", "to", "for", "with", "on",
    "at", "by", "from", "as", "and", "or", "but", "not", "no", "if", "then",
    "than", "that", "this", "it", "its", "he", "she", "we", "they", "them",
})


# ── Helpers ──────────────────────────────────────────────────────────

def _extract_keywords(text: str) -> set:
    """Extract keywords from text for overlap comparison."""
    words = set(re.split(r"\W+", text.lower()))
    return {w for w in words if len(w) >= 3 and w not in _STOP}


def _node_text(node) -> str:
    """Get searchable text from a node."""
    props = node.properties if hasattr(node, "properties") else {}
    parts = []
    for f in ("name", "content", "statement", "description", "claim"):
        v = props.get(f, "")
        if v:
            parts.append(str(v))
    return " ".join(parts)


# ── Scorer ───────────────────────────────────────────────────────────

@dataclass
class PromotionScore:
    score: float
    action: str  # "auto_promote" | "review" | "ignore"
    signals: Dict[str, float] = field(default_factory=dict)
    reason: str = ""


class PromotionScorer:
    """Scores a runtime node for promotion readiness."""

    def __init__(self, config: Dict):
        self.auto_threshold = config.get("auto_promote_threshold", 0.8)
        self.review_threshold = config.get("review_threshold", 0.5)
        self.weights = config.get("weights", PROMOTION_DEFAULTS["weights"])
        self.require_human_review = config.get("require_human_review", False)

    def score(self, node, runtime_db=None, atomic_db=None,
              agent_registry=None) -> PromotionScore:
        """Score a node for promotion. Returns PromotionScore with action."""
        signals = {}
        node_text = _node_text(node)
        node_kws = _extract_keywords(node_text)
        props = node.properties if hasattr(node, "properties") else {}
        my_name = (props.get("_agent_name", "") or "").lower()

        signals["agent_references"] = self._score_agent_references(
            node_kws, my_name, runtime_db
        )
        signals["schema_valid"] = self._score_schema_valid(props)
        signals["atomic_overlap"] = self._score_atomic_overlap(
            node_kws, node_text, atomic_db
        )
        signals["agent_trust"] = self._score_agent_trust(props, agent_registry)
        signals["content_quality"] = self._score_content_quality(node_text)

        # Weighted sum
        total = sum(
            signals[k] * self.weights.get(k, 0.0)
            for k in signals
        )
        weight_sum = sum(self.weights.get(k, 0.0) for k in signals)
        score = total / weight_sum if weight_sum > 0 else 0.0
        score = max(0.0, min(1.0, score))

        # Determine action
        if score >= self.auto_threshold:
            action = "review" if self.require_human_review else "auto_promote"
        elif score >= self.review_threshold:
            action = "review"
        else:
            action = "ignore"

        top_signals = sorted(signals.items(), key=lambda x: -x[1])[:3]
        reason_parts = [f"{k}={v:.2f}" for k, v in top_signals]
        reason = f"Score {score:.2f}: {', '.join(reason_parts)}"

        return PromotionScore(
            score=score, action=action, signals=signals, reason=reason,
        )

    def _score_agent_references(self, node_kws: set, my_name: str,
                                 runtime_db) -> float:
        if not runtime_db or not node_kws:
            return 0.0

        referencing_agents = set()
        _ref_labels = ("Finding", "Insight", "Decision", "Observation")
        try:
            from contextsynapse.project.schema_manager import get_schema_manager
            _ref_labels = tuple(get_schema_manager().get_consumer_hint(
                getattr(self, '_namespace', ''), "promotion_labels", _ref_labels
            ))
        except Exception:
            pass
        for label in _ref_labels:
            try:
                for n in runtime_db.get_all_nodes(label=label):
                    p = n.properties if hasattr(n, "properties") else {}
                    author = (p.get("_agent_name", p.get("created_by", "")) or "").lower()
                    if author and author != my_name and author != "system":
                        other_kws = _extract_keywords(_node_text(n))
                        overlap = node_kws & other_kws
                        if len(overlap) >= 2:
                            referencing_agents.add(author)
            except Exception:
                pass

        count = len(referencing_agents)
        if count >= 3:
            return 1.0
        elif count == 2:
            return 0.6
        elif count == 1:
            return 0.3
        return 0.0

    def _score_schema_valid(self, props: Dict) -> float:
        status = props.get("validation_status", "")
        if status == "valid":
            return 1.0
        elif status == "warning":
            return 0.5
        elif status == "invalid":
            return 0.0
        return 0.7  # No validation ran — neutral

    def _score_atomic_overlap(self, node_kws: set, node_text: str,
                               atomic_db) -> float:
        if not atomic_db or not node_kws:
            return 0.5

        best_overlap_ratio = 0.0
        try:
            for n in atomic_db.get_all_nodes():
                other_text = _node_text(n)
                other_kws = _extract_keywords(other_text)
                if not other_kws:
                    continue
                overlap = node_kws & other_kws
                ratio = len(overlap) / max(len(node_kws), 1)
                if ratio > best_overlap_ratio:
                    best_overlap_ratio = ratio
        except Exception:
            return 0.5

        if best_overlap_ratio > 0.9:
            return 0.0  # Near-duplicate
        elif best_overlap_ratio > 0.5:
            return 0.5  # Partial overlap
        elif best_overlap_ratio > 0.1:
            return 1.0  # Some connection but mostly new — best case
        return 0.5  # No overlap — neutral

    def _score_agent_trust(self, props: Dict, agent_registry=None) -> float:
        role = props.get("_agent_role", "")
        if role == "admin":
            return 1.0

        if agent_registry and props.get("_agent_id"):
            try:
                agent = agent_registry.get(props["_agent_id"])
                if agent:
                    if agent.role == "admin":
                        return 1.0
                    elif agent.role == "agent":
                        return 0.7
            except Exception:
                pass

        return 0.5

    def _score_content_quality(self, text: str) -> float:
        text = text.strip()
        lower = text.lower()

        if "i don't have enough" in lower or "insufficient context" in lower:
            return 0.0
        if "i'm not sure" in lower or "unable to determine" in lower:
            return 0.1

        length = len(text)
        if length < 50:
            return 0.0
        elif length < 200:
            return 0.5
        else:
            return 1.0


# ── Promotion Queue ──────────────────────────────────────────────────

def _get_redis():
    """Get Redis client, or None."""
    try:
        import os, redis
        url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
        if url:
            client = redis.Redis.from_url(url, decode_responses=True)
            client.ping()
            return client
    except Exception:
        pass
    return None


class PromotionQueue:
    """Review queue for nodes awaiting human approval. Redis-backed with in-memory fallback."""

    def __init__(self, namespace: str):
        self.namespace = namespace
        self._redis = _get_redis()
        self._memory: Dict[str, Dict] = {}

    def _key(self) -> str:
        return f"promotion:queue:{self.namespace}"

    def submit(self, node_id: str, score: float, reason: str,
               submitted_by: str = "") -> bool:
        """Add node to review queue. Returns False if already queued."""
        import json as _json

        entry = {
            "node_id": node_id,
            "score": score,
            "reason": reason,
            "submitted_by": submitted_by,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "namespace": self.namespace,
        }

        if self._redis:
            try:
                if self._redis.hexists(self._key(), node_id):
                    return False
                self._redis.hset(self._key(), node_id, _json.dumps(entry))
                return True
            except Exception:
                pass

        # In-memory fallback
        if node_id in self._memory:
            return False
        self._memory[node_id] = entry
        return True

    def pending(self, limit: int = 50) -> List[Dict]:
        """Get pending items sorted by score descending."""
        import json as _json

        items = []
        if self._redis:
            try:
                raw = self._redis.hgetall(self._key())
                for v in raw.values():
                    items.append(_json.loads(v))
            except Exception:
                pass

        if not items:
            items = list(self._memory.values())

        items.sort(key=lambda x: x.get("score", 0), reverse=True)
        return items[:limit]

    def approve(self, node_id: str, reviewer_id: str) -> bool:
        """Remove from queue (promotion handled by caller)."""
        if self._redis:
            try:
                self._redis.hdel(self._key(), node_id)
            except Exception:
                pass
        self._memory.pop(node_id, None)
        return True

    def reject(self, node_id: str, reviewer_id: str, reason: str = "") -> bool:
        """Remove from queue, mark as rejected."""
        if self._redis:
            try:
                self._redis.hdel(self._key(), node_id)
            except Exception:
                pass
        self._memory.pop(node_id, None)
        return True

    def count(self) -> int:
        """Number of pending items."""
        if self._redis:
            try:
                return self._redis.hlen(self._key())
            except Exception:
                pass
        return len(self._memory)

    def _clear(self):
        """Clear the queue (testing only)."""
        if self._redis:
            try:
                self._redis.delete(self._key())
            except Exception:
                pass
        self._memory.clear()


# ── Promotion Operation ──────────────────────────────────────────────

def promote_node(node_id: str, runtime_conn, atomic_conn,
                 promoted_by: str = "system") -> Optional[str]:
    """
    Promote a runtime node to atomic context.

    1. Read node from runtime graph
    2. Create copy in atomic graph (same label, same properties)
    3. Add promotion metadata
    4. Mark runtime node as promoted
    5. Index in search (LMDB BM25 + Qdrant)
    6. Return new atomic node ID, or None if not found
    """
    # 1. Read from runtime
    rt_db = getattr(runtime_conn, "db", None)
    if not rt_db:
        return None

    node = rt_db.get_node(node_id) if hasattr(rt_db, "get_node") else None
    if not node:
        return None

    props = node.properties if hasattr(node, "properties") else {}
    label = getattr(node, "label", None) or getattr(node, "node_type", "Finding")

    # 2. Build atomic node properties
    atomic_props = dict(props)
    atomic_props["_promoted_from"] = node_id
    atomic_props["_promoted_at"] = datetime.now(timezone.utc).isoformat()
    atomic_props["_promoted_by"] = promoted_by
    atomic_props["_layer"] = "agent"
    atomic_props.pop("status", None)
    atomic_props.pop("assigned_to", None)
    atomic_props.pop("claimed_at", None)

    # 3. Create in atomic
    prop_parts = []
    for k, v in atomic_props.items():
        if isinstance(v, str):
            escaped = v.replace('"', '\\"').replace("\n", " ")
            prop_parts.append(f'{k}: "{escaped}"')
        elif isinstance(v, bool):
            prop_parts.append(f'{k}: {"TRUE" if v else "FALSE"}')
        elif isinstance(v, (int, float)):
            prop_parts.append(f'{k}: {v}')
        else:
            escaped = str(v).replace('"', '\\"').replace("\n", " ")
            prop_parts.append(f'{k}: "{escaped}"')
    prop_str = ", ".join(prop_parts)

    result = atomic_conn.query(f'CREATE NODE {label} {{{prop_str}}}')
    new_id = result.get("data", {}).get("uuid", "")

    if not new_id:
        logger.warning("Failed to create promoted node in atomic: %s", node_id)
        return None

    # 4. Mark runtime node as promoted
    try:
        runtime_conn.query(
            f'UPDATE NODE {label} SET {{status: "promoted", '
            f'_promoted_to: "{new_id}"}} WHERE uuid = "{node_id}"'
        )
    except Exception:
        pass

    # 5. Index in search (best-effort)
    try:
        namespace = getattr(atomic_conn, "namespace", "") or getattr(atomic_conn, "_namespace", "")
        if namespace:
            from ..search.lmdb_index import get_lmdb_index
            idx = get_lmdb_index(namespace)
            idx.index_node(new_id, label, atomic_props)
    except Exception:
        pass

    try:
        namespace = getattr(atomic_conn, "namespace", "")
        if namespace:
            from ..context.vector_integration import get_session_vector_store
            svs = get_session_vector_store()
            if svs.available:
                content = atomic_props.get("content", atomic_props.get("name", ""))
                svs.index_node(namespace, new_id, label, content, atomic_props)
    except Exception:
        pass

    logger.info("Promoted %s -> %s (by %s)", node_id, new_id, promoted_by)
    # Notify other agents via promotion event
    try:
        label_str = node.label if hasattr(node, "label") else str(getattr(node, "properties", {}).get("label", "Unknown"))
        title_str = (getattr(node, "properties", {}) or {}).get("name", getattr(node, "properties", {}).get("title", str(new_id)[:16]))
        _atomic_ns = getattr(atomic_conn, "namespace", "") or getattr(atomic_conn, "_namespace", "")
        if _atomic_ns:
            publish_promotion_event(_atomic_ns, str(new_id), label_str, str(title_str))
    except Exception as _e:
        import logging as _plog
        _plog.getLogger(__name__).debug("publish_promotion_event call error: %s", _e)
    return new_id


# ── Promotion Sweeper ────────────────────────────────────────────────

class PromotionSweeper:
    """Background thread that periodically promotes agent findings from runtime to atomic."""

    def __init__(self, graph_registry, session_manager, interval_s=60):
        self._graph_registry = graph_registry
        self._session_manager = session_manager
        self._interval = interval_s
        self._running = True

    def start(self):
        import threading
        t = threading.Thread(target=self._loop, name="promotion-sweeper", daemon=True)
        t.start()
        logger.info("[PROMOTE] Sweeper started (interval=%ds)", self._interval)

    def stop(self):
        self._running = False

    def _loop(self):
        import time
        while self._running:
            try:
                time.sleep(self._interval)
                self._sweep_all()
            except Exception as e:
                logger.debug("[PROMOTE] Sweep error: %s", e)

    def _sweep_all(self):
        """Sweep all active sessions for un-promoted findings."""
        if not self._session_manager:
            return
        try:
            sessions = self._session_manager.list_sessions()
        except Exception:
            return

        total_promoted = 0
        total_queued = 0

        for session in sessions:
            if getattr(session, 'status', '') != 'active':
                continue
            try:
                p, q = self._sweep_session(session)
                total_promoted += p
                total_queued += q
            except Exception:
                pass

        if total_promoted or total_queued:
            logger.info("[PROMOTE] Sweep: %d promoted, %d queued for review", total_promoted, total_queued)

    def _sweep_session(self, session):
        """Sweep one session's runtime graph."""
        rt_ns = getattr(session, 'runtime_namespace', '') or f"{session.graph_namespace}_rt"
        at_ns = session.graph_namespace

        rt_graph = self._graph_registry.get_graph(rt_ns, load_if_missing=False)
        if not rt_graph:
            return 0, 0

        scorer = PromotionScorer(PROMOTION_DEFAULTS)
        queue = PromotionQueue(at_ns)
        promoted = 0
        queued = 0

        _sweep_labels = ("Finding", "Insight", "Observation", "Note")
        try:
            from contextsynapse.project.schema_manager import get_schema_manager
            _sweep_labels = tuple(get_schema_manager().get_consumer_hint(
                at_ns, "promotion_labels", _sweep_labels
            ))
        except Exception:
            pass
        for label in _sweep_labels:
            try:
                for n in rt_graph.get_all_nodes(label=label):
                    props = n.properties if hasattr(n, 'properties') else {}
                    # Skip already scored
                    if props.get("_promotion_scored_at"):
                        continue
                    # Skip very short content
                    content = props.get("statement") or props.get("content") or props.get("name") or ""
                    if len(str(content)) < 10:
                        continue

                    score = scorer.score(n, rt_graph)

                    if score.score >= 0.3:
                        try:
                            from ..adapters._base import AIContextDBConnection
                            rt_conn = AIContextDBConnection(namespace=rt_ns, graph_registry=self._graph_registry)
                            at_conn = AIContextDBConnection(namespace=at_ns, graph_registry=self._graph_registry)
                            agent_name = props.get("_agent_name") or props.get("created_by") or "system"
                            promote_node(n.id, rt_conn, at_conn, promoted_by=agent_name)
                            promoted += 1
                        except Exception:
                            pass
                    elif score.score >= 0.1:
                        agent_name = props.get("_agent_name") or props.get("created_by") or "unknown"
                        queue.submit(n.id, score.score, score.reason, submitted_by=agent_name)
                        queued += 1

                    # Mark as scored to avoid re-processing
                    try:
                        rt_graph.update_node_properties(n.id, {"_promotion_scored_at": datetime.now(timezone.utc).isoformat()})
                    except Exception:
                        pass
            except Exception:
                pass

        return promoted, queued


# ── Promotion Events ─────────────────────────────────────────────────────────

_PROMOTION_EVENT_TTL = 3600  # 1 hour — events are short-lived signals
_PROMOTION_EVENT_MAX = 50    # max events stored per namespace

# In-memory fallback: namespace -> list of event dicts (newest first)
_promotion_events: dict = {}


def _get_redis_for_events():
    """Get Redis client for promotion events. Same pattern as projection._get_redis()."""
    try:
        import os, redis
        url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
        if url:
            client = redis.Redis.from_url(url, decode_responses=True)
            client.ping()
            return client
    except Exception as _e:
        import logging as _log
        _log.getLogger(__name__).debug("_get_redis_for_events error: %s", _e)
    return None


def publish_promotion_event(
    atomic_namespace: str,
    node_id: str,
    label: str,
    title: str,
) -> None:
    """Record that a node was promoted to atomic context.

    Stored as a short-lived list in Redis (or in-memory fallback).
    orient() reads these to tell agents about new promoted knowledge.
    """
    import time as _t
    import json as _json
    evt = {
        "node_id": node_id,
        "label": label,
        "title": title[:120],
        "promoted_at": _t.time(),
    }
    # In-memory store
    key = atomic_namespace
    _promotion_events.setdefault(key, [])
    _promotion_events[key].insert(0, evt)
    _promotion_events[key] = _promotion_events[key][:_PROMOTION_EVENT_MAX]

    # Redis store
    r = _get_redis_for_events()
    if r:
        try:
            rk = f"cgp:promotions:{atomic_namespace}"
            r.lpush(rk, _json.dumps(evt))
            r.ltrim(rk, 0, _PROMOTION_EVENT_MAX - 1)
            r.expire(rk, _PROMOTION_EVENT_TTL)
        except Exception as _e:
            import logging as _log
            _log.getLogger(__name__).debug("publish_promotion_event Redis write error: %s", _e)
