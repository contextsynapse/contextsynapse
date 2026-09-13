"""
Context Quality Scorer
======================
Staleness detection, completeness metrics, and quality indicators
for session context.

Also exposes score_node() — the Universal Context Layer ingest gate
for per-node quality scoring (0-100, no I/O, <1ms).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Gate 1: Ingest ─────────────────────────────────────────────

# Expected properties per label (for completeness scoring)
_EXPECTED_PROPS = {
    "Person": {"name", "confidence", "source_url", "created_by", "mention_count"},
    "Organization": {"name", "confidence", "source_url", "created_by"},
    "Location": {"name", "confidence", "source_url"},
    "Event": {"name", "confidence", "source_url", "created_by"},
    "Fact": {"statement", "subject", "source_url", "created_by", "confidence"},
    "Document": {"title", "source_url", "content_type"},
    "Passage": {"content", "source_url"},
}

# Pattern: 2+ capitalized words (real names like "Benjamin Netanyahu")
_REAL_NAME_RE = re.compile(r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+$")

# Pattern: generic/garbage text — short, no caps, or known noise patterns
_NOISE_PATTERNS = [
    re.compile(r"^Edition\b", re.IGNORECASE),
    re.compile(r"^(IN|US|GCC|English|Hindi)\b"),
    re.compile(r"^(Match|Results|Click|Share|Subscribe|Download)\b", re.IGNORECASE),
    re.compile(r"^(Latest|Breaking|Trending|Top)\s+(News|Videos|Stories)", re.IGNORECASE),
]

# Pattern: content with specific claims (numbers, dates, named entities)
_SPECIFIC_RE = re.compile(
    r"\d{2,}|%|\$|Rs\.?|USD|January|February|March|April|May|June|July|August|September|October|November|December|20\d\d"
)


def score_node(node: Dict[str, Any]) -> int:
    """Score a node 0-100 for quality. <1ms, no I/O.

    Parameters
    ----------
    node : dict
        Must have 'label', 'properties', and optionally 'edge_count' and '_is_duplicate'.

    Returns
    -------
    int
        Quality score clamped to 0-100.
    """
    label = node.get("label", "")
    props = node.get("properties") or {}
    edge_count = node.get("edge_count", 0)
    is_duplicate = node.get("_is_duplicate", False)

    score = 50  # base

    # ── Connectivity ──
    if edge_count == 0:
        score -= 30
    elif edge_count <= 2:
        pass  # neutral
    elif edge_count <= 5:
        score += 10
    else:
        score += 20

    # ── Name quality ──
    name = props.get("name", "")
    statement = props.get("statement", name)
    text = statement or name

    if name:
        # Noise check takes priority — catches "Match Results" etc.
        _noise_hit = False
        for pat in _NOISE_PATTERNS:
            if pat.search(name):
                score -= 20
                _noise_hit = True
                break

        if not _noise_hit:
            if _REAL_NAME_RE.match(name):
                score += 15
            else:
                # Short generic name (< 3 chars or single lowercase word)
                if len(name) < 3 or (len(name.split()) == 1 and name[0].islower()):
                    score -= 10

    # ── Content specificity ──
    if text and len(text) > 30:
        if _SPECIFIC_RE.search(text):
            score += 15
        elif len(text) < 20:
            score -= 20
    elif text:
        if len(text) < 10:
            score -= 20

    # ── Source reliability ──
    if props.get("source_url"):
        score += 10
    if props.get("created_by", "").startswith("pipeline:"):
        score += 5

    # ── Duplication ──
    if is_duplicate:
        score -= 40

    # ── Property completeness ──
    expected = _EXPECTED_PROPS.get(label, set())
    if expected:
        filled = sum(1 for k in expected if props.get(k))
        completeness = filled / len(expected)
        score += int(completeness * 20)

    return max(0, min(100, score))


@dataclass
class QualityItem:
    item_id: str
    label: str
    age_seconds: float
    staleness: float  # 0.0 (fresh) to 1.0 (stale)
    confidence: float  # 0.0 to 1.0
    has_content: bool = True

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "label": self.label,
            "age_seconds": self.age_seconds,
            "staleness": round(self.staleness, 3),
            "confidence": round(self.confidence, 3),
            "has_content": self.has_content,
        }


@dataclass
class QualityReport:
    session_id: str
    overall_score: float  # 0.0 to 1.0
    grade: str  # A, B, C, D, F
    total_items: int
    stale_items: int
    fresh_items: int
    avg_staleness: float
    avg_confidence: float
    items: List[QualityItem] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "overall_score": round(self.overall_score, 3),
            "grade": self.grade,
            "total_items": self.total_items,
            "stale_items": self.stale_items,
            "fresh_items": self.fresh_items,
            "avg_staleness": round(self.avg_staleness, 3),
            "avg_confidence": round(self.avg_confidence, 3),
            "items": [i.to_dict() for i in self.items],
            "warnings": self.warnings,
        }


class ContextQualityScorer:
    """Score context items for staleness, completeness, and quality."""

    def __init__(self, staleness_threshold_hours: float = 24.0):
        self.staleness_threshold = staleness_threshold_hours * 3600

    def score_items(self, items: List[Dict[str, Any]], session_id: str = "") -> QualityReport:
        """Score a list of context items."""
        now = time.time()
        quality_items = []
        warnings = []

        for item in items:
            created_at = item.get("created_at", item.get("timestamp", now))
            if isinstance(created_at, str):
                try:
                    from datetime import datetime
                    created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
                except Exception:
                    created_at = now

            age = now - created_at
            staleness = min(age / self.staleness_threshold, 1.0) if self.staleness_threshold > 0 else 0.0
            confidence = item.get("confidence", 1.0 - (staleness * 0.5))
            has_content = bool(item.get("content") or item.get("properties") or item.get("data"))

            qi = QualityItem(
                item_id=item.get("id", item.get("node_id", str(len(quality_items)))),
                label=item.get("label", item.get("type", item.get("content_type", "unknown"))),
                age_seconds=age,
                staleness=staleness,
                confidence=max(0.0, min(1.0, confidence)),
                has_content=has_content,
            )
            quality_items.append(qi)

        if not quality_items:
            return QualityReport(
                session_id=session_id,
                overall_score=0.0,
                grade="F",
                total_items=0,
                stale_items=0,
                fresh_items=0,
                avg_staleness=0.0,
                avg_confidence=0.0,
                warnings=["No context items found"],
            )

        stale_count = sum(1 for q in quality_items if q.staleness > 0.7)
        fresh_count = sum(1 for q in quality_items if q.staleness < 0.3)
        avg_staleness = sum(q.staleness for q in quality_items) / len(quality_items)
        avg_confidence = sum(q.confidence for q in quality_items) / len(quality_items)

        # Overall score: weighted combination
        freshness_score = 1.0 - avg_staleness
        completeness_score = sum(1 for q in quality_items if q.has_content) / len(quality_items)
        overall = (freshness_score * 0.5) + (avg_confidence * 0.3) + (completeness_score * 0.2)

        if stale_count > len(quality_items) * 0.5:
            warnings.append(f"{stale_count} of {len(quality_items)} items are stale")
        if not any(q.has_content for q in quality_items):
            warnings.append("No items have content")

        grade = "A" if overall >= 0.9 else "B" if overall >= 0.75 else "C" if overall >= 0.6 else "D" if overall >= 0.4 else "F"

        return QualityReport(
            session_id=session_id,
            overall_score=overall,
            grade=grade,
            total_items=len(quality_items),
            stale_items=stale_count,
            fresh_items=fresh_count,
            avg_staleness=avg_staleness,
            avg_confidence=avg_confidence,
            items=quality_items[:20],  # Limit to 20 items in report
            warnings=warnings,
        )

    def score_session(self, session, graph=None) -> QualityReport:
        """Score a session's context quality from its graph nodes."""
        items = []
        if graph:
            try:
                nodes = graph.get_all_nodes()
                for n in (nodes or []):
                    items.append({
                        "id": n.get("node_id", ""),
                        "type": n.get("node_type", ""),
                        "content": str(n.get("properties", {})),
                        "properties": n.get("properties", {}),
                        "created_at": n.get("created_at", time.time()),
                    })
            except Exception:
                pass

        session_id = session.session_id if hasattr(session, 'session_id') else str(session)
        return self.score_items(items, session_id)


import json  # noqa: E402 — appended after class definitions


class EntityIndex:
    """O(1) entity lookup via Redis hash, with in-memory fallback."""

    _TITLES = re.compile(
        r"^(Mr\.?|Mrs\.?|Ms\.?|Dr\.?|Prof\.?|PM|President|Minister|Gen\.?|Col\.?|Sgt\.?)\s+",
        re.IGNORECASE,
    )

    def __init__(self, redis_client=None):
        self._redis = redis_client
        self._mem: Dict[str, Dict[str, Dict]] = {}

    @staticmethod
    def normalize_name(name: str) -> str:
        n = name.strip()
        n = EntityIndex._TITLES.sub("", n)
        n = re.sub(r"\s+", " ", n).strip().lower()
        return n

    def _key(self, graph: str) -> str:
        return f"entity_idx:{graph}"

    def _field(self, label: str, name: str) -> str:
        return f"{label}:{self.normalize_name(name)}"

    def put(self, graph: str, label: str, name: str, node_id: str, quality: int = 50) -> Dict:
        field = self._field(label, name)
        existing = self.get(graph, label, name)
        if existing:
            existing["mention_count"] += 1
            if quality > existing.get("quality", 0):
                existing["quality"] = quality
            data = existing
        else:
            data = {"node_id": node_id, "mention_count": 1, "quality": quality,
                    "name": name.strip(), "label": label}
        if self._redis:
            self._redis.hset(self._key(graph), field, json.dumps(data))
        else:
            self._mem.setdefault(graph, {})[field] = data
        return data

    def get(self, graph: str, label: str, name: str) -> Optional[Dict]:
        field = self._field(label, name)
        if self._redis:
            raw = self._redis.hget(self._key(graph), field)
            return json.loads(raw) if raw else None
        return self._mem.get(graph, {}).get(field)

    def top(self, graph: str, label: str, limit: int = 5) -> List[Dict]:
        if self._redis:
            raw = self._redis.hgetall(self._key(graph))
            entries = []
            prefix = f"{label}:"
            for field, val in raw.items():
                f = field.decode() if isinstance(field, bytes) else field
                if f.startswith(prefix):
                    entries.append(json.loads(val))
        else:
            entries = [v for f, v in self._mem.get(graph, {}).items()
                       if f.startswith(f"{label}:")]
        entries.sort(key=lambda e: e.get("mention_count", 0) * e.get("quality", 50), reverse=True)
        return entries[:limit]

    def all_labels(self, graph: str) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        if self._redis:
            raw = self._redis.hgetall(self._key(graph))
            for field in raw:
                f = field.decode() if isinstance(field, bytes) else field
                label = f.split(":")[0]
                counts[label] = counts.get(label, 0) + 1
        else:
            for f in self._mem.get(graph, {}):
                label = f.split(":")[0]
                counts[label] = counts.get(label, 0) + 1
        return counts


# ── Gate 2: Search ─────────────────────────────────────────────

_RANKING_WEIGHTS = {
    "news":      {"relevance": 0.50, "quality": 0.20, "freshness": 0.30},
    "knowledge": {"relevance": 0.40, "quality": 0.50, "freshness": 0.10},
    "code":      {"relevance": 0.50, "quality": 0.40, "freshness": 0.10},
    "mixed":     {"relevance": 0.60, "quality": 0.25, "freshness": 0.15},
}


def _jaccard(a: str, b: str) -> float:
    wa = set(a.lower().split())
    wb = set(b.lower().split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


class QualityFilter:
    """Search gate — filter, dedup, and rank results by quality."""

    @staticmethod
    def threshold(context_quality: int) -> int:
        return max(20, round(context_quality * 0.4))

    @staticmethod
    def filter_results(results: list, context_quality: int = 50) -> list:
        t = QualityFilter.threshold(context_quality)
        return [r for r in results if r.get("_quality", 50) >= t]

    @staticmethod
    def dedup_results(results: list, similarity_threshold: float = 0.8) -> list:
        if not results:
            return results
        kept = []
        for r in results:
            text = r.get("name", "") or r.get("statement", "")
            is_dup = False
            for k in kept:
                k_text = k.get("name", "") or k.get("statement", "")
                if text and k_text and _jaccard(text, k_text) >= similarity_threshold:
                    if r.get("_quality", 0) > k.get("_quality", 0):
                        kept.remove(k)
                        kept.append(r)
                    is_dup = True
                    break
            if not is_dup:
                kept.append(r)
        return kept

    @staticmethod
    def cap_per_label(results: list, max_per_label: int = 3) -> list:
        counts: Dict[str, int] = {}
        capped = []
        for r in results:
            label = r.get("label", "")
            counts[label] = counts.get(label, 0) + 1
            if counts[label] <= max_per_label:
                capped.append(r)
        return capped

    @staticmethod
    def rank(results: list, content_type: str = "mixed", freshness_scores=None) -> list:
        weights = _RANKING_WEIGHTS.get(content_type, _RANKING_WEIGHTS["mixed"])
        for r in results:
            relevance = r.get("score", 0.5)
            quality = r.get("_quality", 50) / 100.0
            freshness = (freshness_scores or {}).get(r.get("node_id", ""), 0.5)
            r["_final_score"] = (
                relevance * weights["relevance"]
                + quality * weights["quality"]
                + freshness * weights["freshness"]
            )
        results.sort(key=lambda r: r.get("_final_score", 0), reverse=True)
        return results

    @staticmethod
    def apply(results: list, context_quality: int = 50,
              content_type: str = "mixed", max_per_label: int = 3) -> list:
        filtered = QualityFilter.filter_results(results, context_quality)
        deduped = QualityFilter.dedup_results(filtered)
        ranked = QualityFilter.rank(deduped, content_type)
        return QualityFilter.cap_per_label(ranked, max_per_label)


from datetime import datetime, timezone  # noqa: E402 — appended after class definitions

# ── Gate 3: Delivery ───────────────────────────────────────────

# Core entity types + extensible via graph discovery
_ENTITY_LABELS_CORE = ("Person", "Organization", "Location", "Event")
# Extended types that may appear in domain-specific graphs
_ENTITY_LABELS_EXTENDED = (
    "Technology", "Product", "Concept", "Topic", "Disease",
    "Policy", "Law", "Currency", "Metric", "Skill",
)
_ENTITY_LABELS = _ENTITY_LABELS_CORE + _ENTITY_LABELS_EXTENDED


class ManifestBuilder:
    """Build and cache the context manifest."""

    def __init__(self, entity_index: EntityIndex, redis_client=None):
        self._idx = entity_index
        self._redis = redis_client

    @staticmethod
    def detect_content_type(label_counts: Dict[str, int]) -> str:
        # Exclude system/structural labels from content type detection
        _SYSTEM_LABELS = {
            "CodeBase", "VectorIndex", "BM25Index", "MemoryStore", "ToolStore",
            "UserStore", "WebStore", "GeneratedStore", "KnowledgeBase",
            "ArtifactStore", "SystemStore", "Session", "Context", "Project",
            "ContextIntelligence", "ExperimentRun", "ExperimentScore",
            "AgentMessage", "Action", "Link",
        }
        content_counts = {k: v for k, v in label_counts.items() if k not in _SYSTEM_LABELS}
        total = sum(content_counts.values()) or 1
        facts = content_counts.get("Fact", 0) + content_counts.get("Document", 0) + content_counts.get("Passage", 0)
        entities = sum(content_counts.get(l, 0) for l in _ENTITY_LABELS)
        if content_counts.get("Feature", 0) > 0 and content_counts.get("Requirement", 0) > 0:
            return "code"
        if facts / total > 0.5:
            return "news"
        if entities / total > 0.6:
            return "knowledge"
        return "mixed"

    def build(self, graph_name: str, context_id: str,
              node_qualities=None, label_counts=None, themes=None) -> Dict:
        all_labels = self._idx.all_labels(graph_name)
        if label_counts:
            all_labels.update({k: v for k, v in label_counts.items() if k not in all_labels})

        # Discover entity types: hardcoded + any label found in the entity index
        entity_labels_to_check = set(_ENTITY_LABELS)
        idx_labels = self._idx.all_labels(graph_name)
        entity_labels_to_check.update(idx_labels.keys())
        # Exclude non-entity labels
        _NON_ENTITY = {"Fact", "Document", "Passage", "Finding", "Insight", "Task",
                        "Action", "Link", "AgentMessage", "ExperimentRun", "ExperimentScore",
                        "ContextIntelligence", "Project", "Session", "Context",
                        "CodeBase", "VectorIndex", "BM25Index", "MemoryStore", "ToolStore",
                        "UserStore", "WebStore", "GeneratedStore", "KnowledgeBase",
                        "ArtifactStore", "SystemStore"}
        entity_labels_to_check -= _NON_ENTITY

        top_entities = {}
        for label in sorted(entity_labels_to_check):
            top = self._idx.top(graph_name, label, limit=5)
            if top:
                top_entities[label] = [
                    {"name": e["name"], "mentions": e["mention_count"], "quality": e["quality"]}
                    for e in top
                ]

        quals = node_qualities or []
        total = len(quals) or 1
        stats = {
            "total_nodes": total if quals else sum(all_labels.values()),
            "high_quality": sum(1 for q in quals if q > 70),
            "medium_quality": sum(1 for q in quals if 30 <= q <= 70),
            "low_quality": sum(1 for q in quals if q < 30),
        }
        overall_quality = int(sum(quals) / total) if quals else 50
        content_type = self.detect_content_type(label_counts or all_labels)

        tool_hints = []
        for label, count in sorted(all_labels.items(), key=lambda x: x[1], reverse=True):
            if label in _ENTITY_LABELS and count > 0:
                tool_hints.append({"tool": "search_nodes", "example": f'label:"{label}"', "result_count": count})
        tool_hints.append({"tool": "search", "example": '"keyword"', "description": "text search across all nodes"})
        tool_hints.append({"tool": "rag_query", "example": '"question"', "description": "semantic search with sources"})

        manifest = {
            "context_id": context_id,
            "context_quality": overall_quality,
            "content_type": content_type,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "stats": stats,
            "top_entities": top_entities,
            "themes": themes or [],
            "tool_hints": tool_hints,
        }
        if self._redis:
            self._redis.set(f"manifest:{context_id}", json.dumps(manifest), ex=86400)  # 24h TTL
            # Also update GraphMeta for fast dashboard reads
            try:
                GraphMeta(self._redis).build_from_manifest(graph_name, manifest)
            except Exception:
                pass
        return manifest

    @staticmethod
    def to_text(manifest: Dict) -> str:
        lines = []
        q = manifest.get("context_quality", 0)
        ct = manifest.get("content_type", "mixed")
        stats = manifest.get("stats", {})
        lines.append(f"CONTEXT QUALITY: {q}/100 ({ct})")
        lines.append(f"Nodes: {stats.get('total_nodes', 0)} "
                      f"(high: {stats.get('high_quality', 0)}, "
                      f"medium: {stats.get('medium_quality', 0)}, "
                      f"low: {stats.get('low_quality', 0)})")
        cu_count = manifest.get("cu_count", 0)
        if cu_count:
            lines.append(f"Context Units: {cu_count} pre-assembled topics")
        lines.append("")
        top = manifest.get("top_entities", {})
        if top:
            lines.append("TOP ENTITIES:")
            for label, entities in top.items():
                names = ", ".join(f"{e['name']} ({e['mentions']})" for e in entities[:5])
                lines.append(f"  {label}: {names}")
            lines.append("")
        themes = manifest.get("themes", [])
        if themes:
            lines.append("THEMES:")
            for i, t in enumerate(themes[:5], 1):
                lines.append(f"  {i}. {t['name']} ({t.get('fact_count', 0)} facts)")
                actors = ", ".join(t.get("key_actors", [])[:3])
                if actors:
                    lines.append(f"     Key actors: {actors}")
                sample = t.get("sample_fact", "")
                if sample:
                    lines.append(f'     e.g. "{sample[:80]}"')
            lines.append("")
        # Suggested research tasks (for leader agents)
        if top or themes:
            lines.append("SUGGESTED RESEARCH TASKS:")
            task_num = 0
            # Generate tasks from themes
            for t in (themes or [])[:3]:
                task_num += 1
                actors = ", ".join(t.get("key_actors", [])[:3])
                actor_hint = f" (key actors: {actors})" if actors else ""
                lines.append(f"  {task_num}. Research: {t['name']}{actor_hint} — {t.get('fact_count', 0)} facts available")
                lines.append(f"     Query: search_nodes(label=\"Fact\", query=\"{t['name'][:30]}\")")
            # Generate tasks from top entity types if no themes
            if not themes:
                for label, entities in list(top.items())[:3]:
                    task_num += 1
                    names = ", ".join(e["name"] for e in entities[:3])
                    lines.append(f"  {task_num}. Research {label} entities: {names}")
                    lines.append(f"     Query: search_nodes(label=\"{label}\")")
            lines.append("")

        # Show actionable queries with example names from the data
        lines.append("READY-TO-USE QUERIES:")
        if top:
            for label, entities in list(top.items())[:4]:
                top_names = [e["name"] for e in entities[:3]]
                example_name = top_names[0] if top_names else ""
                lines.append(f"  search_nodes(label=\"{label}\", query=\"{example_name}\") — {len(entities)} top {label}s: {', '.join(top_names)}")
        lines.append(f"  search_nodes(label=\"Fact\", query=\"your topic\") — find facts about any topic")
        lines.append(f"  search(query=\"specific keywords\") — text search across all {stats.get('total_nodes', 0)} nodes")
        lines.append(f"  rag_query(question=\"your question\") — semantic search (finds similar meaning)")
        return "\n".join(lines)


def get_manifest(context_id: str, redis_client=None):
    """Read cached manifest. Returns None if not cached."""
    if redis_client:
        raw = redis_client.get(f"manifest:{context_id}")
        if raw:
            return json.loads(raw)
    return None


# ── Graph Metadata Store ──────────────────────────────────────

class GraphMeta:
    """Single Redis hash per graph for all metadata — microsecond reads.

    Key: ``graph_meta:{name}``
    Fields: node_count, edge_count, quality, content_type, updated_at, label_counts

    Updated by: ingest gate, manifest builder, node create/delete.
    Read by: all dashboard pages.
    """

    def __init__(self, redis_client):
        self._redis = redis_client

    def _key(self, graph_name: str) -> str:
        return f"graph_meta:{graph_name}"

    def update(self, graph_name: str, **fields):
        """Update specific metadata fields. Only writes provided fields."""
        if not self._redis:
            return
        key = self._key(graph_name)
        # Serialize complex values
        to_store = {}
        for k, v in fields.items():
            if isinstance(v, dict):
                to_store[k] = json.dumps(v)
            elif isinstance(v, (list, tuple)):
                to_store[k] = json.dumps(v)
            else:
                to_store[k] = str(v)
        if to_store:
            self._redis.hset(key, mapping=to_store)
            self._redis.expire(key, 86400 * 7)  # 7 day TTL

    def get(self, graph_name: str) -> Optional[Dict]:
        """Read all metadata for a graph. Returns dict or None."""
        if not self._redis:
            return None
        raw = self._redis.hgetall(self._key(graph_name))
        if not raw:
            return None
        result = {}
        for k, v in raw.items():
            k = k.decode() if isinstance(k, bytes) else k
            v = v.decode() if isinstance(v, bytes) else v
            # Try to parse JSON values
            try:
                result[k] = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                # Try numeric
                try:
                    result[k] = int(v)
                except ValueError:
                    result[k] = v
        return result

    def get_field(self, graph_name: str, field: str):
        """Read a single field. Returns value or None."""
        if not self._redis:
            return None
        raw = self._redis.hget(self._key(graph_name), field)
        if raw is None:
            return None
        v = raw.decode() if isinstance(raw, bytes) else raw
        try:
            return json.loads(v)
        except (json.JSONDecodeError, TypeError):
            try:
                return int(v)
            except ValueError:
                return v

    def build_from_manifest(self, graph_name: str, manifest: Dict):
        """Populate metadata from a manifest."""
        stats = manifest.get("stats", {})
        self.update(
            graph_name,
            node_count=stats.get("total_nodes", 0),
            high_quality=stats.get("high_quality", 0),
            medium_quality=stats.get("medium_quality", 0),
            low_quality=stats.get("low_quality", 0),
            quality=manifest.get("context_quality", 0),
            content_type=manifest.get("content_type", "mixed"),
            updated_at=manifest.get("updated_at", ""),
            top_entities=manifest.get("top_entities", {}),
        )

    def list_all(self) -> List[Dict]:
        """List metadata for all graphs. Fast — one SCAN + pipeline HGETALL."""
        if not self._redis:
            return []
        cursor = 0
        keys = []
        while True:
            cursor, batch = self._redis.scan(cursor, match="graph_meta:*", count=100)
            keys.extend(batch)
            if cursor == 0:
                break
        if not keys:
            return []
        pipe = self._redis.pipeline()
        for k in keys:
            pipe.hgetall(k)
        results = pipe.execute()
        out = []
        for k, raw in zip(keys, results):
            if not raw:
                continue
            name = (k.decode() if isinstance(k, bytes) else k).replace("graph_meta:", "")
            entry = {"name": name}
            for field, val in raw.items():
                f = field.decode() if isinstance(field, bytes) else field
                v = val.decode() if isinstance(val, bytes) else val
                try:
                    entry[f] = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    try:
                        entry[f] = int(v)
                    except ValueError:
                        entry[f] = v
            out.append(entry)
        return out


# ── Pre-computed Result Cache ──────────────────────────────────

class ResultCache:
    """Pre-computed search results cached in Redis for <1ms lookups.

    Built after ingest alongside the manifest. Stores top results
    per entity type and per theme keyword so search_nodes() becomes
    a Redis hash read instead of a graph scan.

    Keys:
        results:{graph}:label:{Label}  → JSON list of top nodes
        results:{graph}:query:{keyword} → JSON list of matching nodes
    """

    def __init__(self, redis_client):
        self._redis = redis_client

    def build(self, graph_name: str, db, entity_index: EntityIndex,
              max_per_label: int = 20, max_per_query: int = 10):
        """Pre-compute and cache results for all entity types + top keywords."""
        if not self._redis:
            return

        pipe = self._redis.pipeline()
        adapter = getattr(db, 'csr_adapter', None) or db

        # 1. Cache top nodes per entity label
        all_labels = entity_index.all_labels(graph_name)
        for label in all_labels:
            top = entity_index.top(graph_name, label, limit=max_per_label)
            if top:
                # Enrich with node content
                results = []
                for entry in top:
                    try:
                        node = adapter.get_node(entry["node_id"])
                        if node:
                            props = node.properties or {}
                            results.append({
                                "node_id": entry["node_id"],
                                "label": label,
                                "name": entry["name"],
                                "content": props.get("statement", props.get("content", props.get("description", "")))[:200],
                                "mentions": entry["mention_count"],
                                "_quality": props.get("_quality", entry.get("quality", 50)),
                            })
                    except Exception:
                        results.append({
                            "node_id": entry["node_id"], "label": label,
                            "name": entry["name"], "mentions": entry["mention_count"],
                            "_quality": entry.get("quality", 50),
                        })
                key = f"results:{graph_name}:label:{label}"
                pipe.set(key, json.dumps(results), ex=86400)

        # 2. Cache top facts (most common search target)
        try:
            all_nodes = adapter.get_all_nodes()
            facts = []
            for n in all_nodes:
                lbl = getattr(n, 'label', getattr(n, 'node_type', ''))
                if lbl == "Fact":
                    props = n.properties or {}
                    q = props.get("_quality", 50)
                    if q >= 40:
                        facts.append({
                            "node_id": n.id,
                            "label": "Fact",
                            "name": props.get("name", "")[:120],
                            "content": props.get("statement", "")[:200],
                            "_quality": q,
                        })
            # Sort by quality, cache top N
            facts.sort(key=lambda f: f.get("_quality", 0), reverse=True)
            pipe.set(f"results:{graph_name}:label:Fact", json.dumps(facts[:max_per_label]), ex=86400)
        except Exception:
            pass

        pipe.execute()
        logger.info("[RESULT_CACHE] Pre-computed results for %s (%d labels)", graph_name, len(all_labels))

    def get_by_label(self, graph_name: str, label: str) -> Optional[list]:
        """Get cached results for a label. Returns list or None."""
        if not self._redis:
            return None
        raw = self._redis.get(f"results:{graph_name}:label:{label}")
        if raw:
            return json.loads(raw)
        return None

    def format_results(self, results: list, query: str = "") -> str:
        """Format cached results as tool output string."""
        if not results:
            return ""
        lines = [f"Found {len(results)} node(s):"]
        for r in results:
            name = r.get("name", "")
            content = r.get("content", "")
            text = content if content and content != name else name
            lines.append(f"  [{r.get('label', '?')}] {text[:150]}")
        return "\n".join(lines)


# ── Graph Quality Scorer ──────────────────────────────────────────

def score_graph(db, graph_name: str = "") -> Dict[str, Any]:
    """Score the overall quality of an ingested graph.

    Returns a report card with scores (0-100) for:
      - completeness: Do nodes have proper names, descriptions, edges?
      - connectivity: How well-connected is the graph? Orphan ratio?
      - richness: Entity diversity, relationship variety
      - structure: Session→Turn→Topic→Entity chains intact?
      - overall: Weighted average

    No LLM calls — pure graph analysis, fast (<500ms).
    """
    all_nodes = list(db.get_all_nodes())
    all_edges = list(db.get_all_edges())

    if not all_nodes:
        return {
            "overall": 0, "grade": "F",
            "completeness": 0, "connectivity": 0, "richness": 0, "structure": 0,
            "node_count": 0, "edge_count": 0, "issues": ["Graph is empty"],
            "label_distribution": {},
        }

    n_nodes = len(all_nodes)
    n_edges = len(all_edges)
    issues = []
    suggestions = []

    # ── Label distribution ──
    label_counts: Dict[str, int] = {}
    for n in all_nodes:
        label = getattr(n, "label", "") or "Unknown"
        label_counts[label] = label_counts.get(label, 0) + 1

    # Infrastructure vs knowledge nodes
    infra_labels = {"Session", "Turn", "ContextUnit", "ContextMeta", "BM25Index", "VectorIndex", "ContextRef"}
    knowledge_labels = {"Topic", "Person", "Technology", "Organization", "Concept",
                        "Location", "Event", "Fact", "Document", "Finding", "Insight",
                        "Decision", "Action", "Question", "Problem", "Solution"}
    n_knowledge = sum(label_counts.get(l, 0) for l in knowledge_labels)
    n_infra = sum(label_counts.get(l, 0) for l in infra_labels)
    n_turns = label_counts.get("Turn", 0)

    # ── 1. Completeness (0-100) ──
    # Do nodes have names? Do they have meaningful properties?
    named_count = 0
    quality_scores = []
    for n in all_nodes:
        props = dict(getattr(n, "properties", {}) or {})
        label = getattr(n, "label", "")
        if label in infra_labels:
            continue
        name = props.get("name", "")
        if name and len(name) > 2:
            named_count += 1
        quality_scores.append(score_node({
            "label": label, "properties": props,
            "edge_count": 0,  # Approximate
        }))

    n_scoreable = max(len(quality_scores), 1)
    avg_quality = sum(quality_scores) / n_scoreable if quality_scores else 0
    named_ratio = named_count / n_scoreable if n_scoreable > 0 else 0

    completeness = int(named_ratio * 50 + min(avg_quality, 100) / 2)
    if named_ratio < 0.5:
        issues.append(f"{int((1 - named_ratio) * 100)}% of knowledge nodes have no name")
        suggestions.append("Ensure entity extraction assigns proper names")

    # ── 2. Connectivity (0-100) ──
    # Edge-to-node ratio, orphan detection
    node_ids = {getattr(n, "id", "") for n in all_nodes}
    connected_ids = set()
    for e in all_edges:
        connected_ids.add(getattr(e, "source", ""))
        connected_ids.add(getattr(e, "target", ""))
    connected_ids &= node_ids

    orphan_count = len(node_ids - connected_ids)
    # Exclude infrastructure from orphan calculation
    knowledge_ids = {getattr(n, "id", "") for n in all_nodes
                     if getattr(n, "label", "") not in infra_labels}
    orphan_knowledge = len(knowledge_ids - connected_ids)
    n_knowledge_ids = max(len(knowledge_ids), 1)

    edge_ratio = n_edges / max(n_nodes, 1)
    orphan_ratio = orphan_knowledge / n_knowledge_ids

    connectivity = 100
    if edge_ratio < 0.5:
        connectivity -= 40
        issues.append(f"Low edge density ({edge_ratio:.1f} edges per node)")
        suggestions.append("Build more edges between entities (co-occurrence, schema-driven)")
    elif edge_ratio < 1.0:
        connectivity -= 15
    if orphan_ratio > 0.3:
        connectivity -= 30
        issues.append(f"{int(orphan_ratio * 100)}% knowledge nodes are orphans (no connections)")
        suggestions.append("Run entity resolution and edge building stages")
    elif orphan_ratio > 0.1:
        connectivity -= 10
    connectivity = max(0, connectivity)

    # ── 3. Richness (0-100) ──
    # How many different entity types? How diverse?
    knowledge_label_set = {l for l in label_counts if l in knowledge_labels}
    n_entity_types = len(knowledge_label_set)

    edge_labels = set()
    for e in all_edges:
        edge_labels.add(getattr(e, "label", "") or "")
    edge_labels.discard("")
    n_edge_types = len(edge_labels)

    # Score: more types = richer
    richness = min(100, n_entity_types * 15 + n_edge_types * 10)
    if n_knowledge == 0:
        richness = 0
        issues.append("No knowledge entities extracted")
        suggestions.append("Check LLM model configuration for entity extraction")
    elif n_entity_types <= 1:
        richness = max(richness, 20)
        issues.append(f"Only {n_entity_types} entity type(s): {', '.join(knowledge_label_set)}")
        suggestions.append("Enable LLM extraction for Person, Concept, Location entities")

    # Knowledge-to-turn ratio
    if n_turns > 0:
        k_per_turn = n_knowledge / n_turns
        if k_per_turn < 0.3:
            issues.append(f"Low extraction rate: {k_per_turn:.1f} entities per turn")
            suggestions.append("Review extraction quality — most turns should produce entities")

    # ── 4. Structure (0-100) ──
    # Are the expected chains present? Session→Turn, Turn→Topic, Topic→Entity
    has_sessions = label_counts.get("Session", 0) > 0
    has_turns = n_turns > 0
    has_topics = label_counts.get("Topic", 0) > 0
    has_entities = n_knowledge > 0

    structure_checks = [has_sessions, has_turns, has_topics, has_entities]
    structure = int(sum(structure_checks) / len(structure_checks) * 100)

    # Check for expected edge types
    expected_edges = {"CONTAINS_TURN", "NEXT", "RAISES", "MENTIONS", "INVOLVES"}
    found_expected = expected_edges & edge_labels
    if expected_edges:
        structure = int(structure * 0.5 + len(found_expected) / len(expected_edges) * 50)

    if not has_topics:
        issues.append("No Topic nodes — topic clustering may have failed")
    if not has_entities:
        issues.append("No entity nodes extracted")

    # ── Overall (weighted average) ──
    overall = int(
        completeness * 0.25 +
        connectivity * 0.25 +
        richness * 0.30 +
        structure * 0.20
    )

    # Grade
    if overall >= 85:
        grade = "A"
    elif overall >= 70:
        grade = "B"
    elif overall >= 50:
        grade = "C"
    elif overall >= 30:
        grade = "D"
    else:
        grade = "F"

    return {
        "overall": overall,
        "grade": grade,
        "completeness": completeness,
        "connectivity": connectivity,
        "richness": richness,
        "structure": structure,
        "node_count": n_nodes,
        "edge_count": n_edges,
        "knowledge_nodes": n_knowledge,
        "orphan_nodes": orphan_knowledge,
        "edge_ratio": round(edge_ratio, 2),
        "entity_types": sorted(knowledge_label_set),
        "edge_types": sorted(edge_labels),
        "label_distribution": label_counts,
        "avg_node_quality": round(avg_quality, 1),
        "issues": issues,
        "suggestions": suggestions,
    }
