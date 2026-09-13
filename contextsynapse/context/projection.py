"""
Contextual Graph Projection (CGP)
==================================
Queryless context delivery for multi-agent systems.

Instead of agents searching for knowledge, the system projects a relevant
subgraph from the atomic context into the agent's view — automatically,
based on their live work state in the runtime graph.

Three components:
  - IntentExtractor: reads runtime graph, produces weighted intent vector
  - SubgraphProjector: queries atomic graph, returns relevant subgraph
  - ProjectionAssembler: formats projection for agent context window
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

_STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "can", "could", "of", "in", "to", "for",
    "with", "on", "at", "by", "from", "as", "into", "through", "during",
    "and", "or", "but", "not", "no", "if", "then", "than", "that", "this",
    "it", "its", "he", "she", "his", "her", "we", "they", "them", "our",
    "said", "also", "new", "one", "two", "who", "what", "when", "where",
    "how", "all", "each", "every", "both", "few", "more", "most", "other",
    "some", "such", "only", "own", "same", "so", "very", "just", "about",
    "https", "http", "www", "com", "org", "html", "htm",
})

import time as _time


# ── Tier Definitions ────────────────────────────────────────────────

_TIER_DEFAULTS = {
    "instant":  {"max_tokens": 500,  "max_time_ms": 50,   "stages": {"cache"}},
    "fast":     {"max_tokens": 1500, "max_time_ms": 200,  "stages": {"cache", "bm25"}},
    "standard": {"max_tokens": 3000, "max_time_ms": 1000, "stages": {"cache", "bm25", "qdrant", "hops"}},
    "deep":     {"max_tokens": 8000, "max_time_ms": 5000, "stages": {"cache", "bm25", "qdrant", "hops", "signals", "extended"}},
}


class ProjectionBudget:
    """Tracks token and time budgets for progressive projection pipeline."""

    def __init__(self, max_tokens: int, max_time_ms: int, tier: str = "standard"):
        self.max_tokens = max_tokens
        self.max_time_ms = max_time_ms
        self.tier = tier
        self.tokens_used = 0
        self._start = _time.monotonic()
        self._stages = _TIER_DEFAULTS.get(tier, _TIER_DEFAULTS["standard"])["stages"]

    @classmethod
    def from_tier(cls, tier: str) -> "ProjectionBudget":
        """Create a budget from tier defaults."""
        defaults = _TIER_DEFAULTS.get(tier, _TIER_DEFAULTS["standard"])
        return cls(
            max_tokens=defaults["max_tokens"],
            max_time_ms=defaults["max_time_ms"],
            tier=tier,
        )

    def has_remaining(self) -> bool:
        elapsed_ms = (_time.monotonic() - self._start) * 1000
        return self.tokens_used < self.max_tokens and elapsed_ms < self.max_time_ms

    @property
    def remaining_tokens(self) -> int:
        return self.max_tokens - self.tokens_used

    def consume_tokens(self, n: int) -> None:
        self.tokens_used += n

    def exhausted(self) -> bool:
        return not self.has_remaining()

    def should_run_stage(self, stage: str) -> bool:
        """Check if this tier includes the given pipeline stage."""
        return stage in self._stages and self.has_remaining()


def _extract_keywords(text: str) -> Dict[str, float]:
    """Extract weighted keywords from text. Returns {term: weight}."""
    words = re.split(r"\W+", text.lower())
    counts: Dict[str, int] = {}
    for w in words:
        if len(w) >= 3 and w not in _STOP_WORDS:
            counts[w] = counts.get(w, 0) + 1
    if not counts:
        return {}
    max_count = max(counts.values())
    return {w: c / max_count for w, c in counts.items()}


def _merge_keywords(base: Dict[str, float], new: Dict[str, float], weight: float) -> Dict[str, float]:
    """Merge new keywords into base with a source weight multiplier."""
    merged = dict(base)
    for k, v in new.items():
        merged[k] = merged.get(k, 0.0) + v * weight
    return merged


@dataclass
class IntentVector:
    """Weighted intent extracted from an agent's runtime state."""
    keywords: Dict[str, float] = field(default_factory=dict)
    entity_ids: List[str] = field(default_factory=list)
    edge_types: Set[str] = field(default_factory=set)
    task_title: str = ""
    agent_id: str = ""
    confidence: float = 0.0


class IntentExtractor:
    """Reads the runtime graph and produces an IntentVector."""

    @staticmethod
    def extract(
        tasks: List[Dict],
        runtime_db: Any = None,
        agent_name: str = "",
        agent_id: str = "",
    ) -> IntentVector:
        if not tasks:
            return IntentVector(agent_id=agent_id)

        task = tasks[0]
        task_text = f"{task.get('title', '')} {task.get('description', '')}"
        keywords = _extract_keywords(task_text)
        confidence = 0.3

        own_keywords: Dict[str, float] = {}
        other_keywords: Dict[str, float] = {}

        if runtime_db:
            my_name = (agent_name or "").lower()
            own_nodes = []
            other_nodes = []

            for label in ("Finding", "Insight", "AgentAction", "Decision", "Observation"):
                try:
                    for n in runtime_db.get_all_nodes(label=label):
                        props = n.properties if hasattr(n, "properties") else {}
                        author = (
                            props.get("_agent_name", props.get("created_by", props.get("agent", ""))) or ""
                        ).lower()
                        text = props.get("name", props.get("description", props.get("content", ""))) or ""
                        if not text:
                            continue
                        if author == my_name:
                            own_nodes.append(text)
                        elif author and author != "system" and not author.startswith("pipeline"):
                            other_nodes.append(text)
                except Exception:
                    pass

            for text in own_nodes[:5]:
                own_keywords = _merge_keywords(own_keywords, _extract_keywords(text), 0.8)
            if own_keywords:
                keywords = _merge_keywords(keywords, own_keywords, 1.0)
                confidence = 0.6

            for text in other_nodes[:3]:
                other_keywords = _merge_keywords(other_keywords, _extract_keywords(text), 0.4)
            if other_keywords:
                keywords = _merge_keywords(keywords, other_keywords, 1.0)
                confidence = 0.8

        return IntentVector(
            keywords=keywords,
            task_title=task.get("title", ""),
            agent_id=agent_id,
            confidence=confidence,
        )


# ── ProjectedSubgraph ────────────────────────────────────────────────

@dataclass
class ProjectedSubgraph:
    """Result of projecting the atomic graph through an intent vector."""
    nodes: List[Dict] = field(default_factory=list)
    edges: List[Dict] = field(default_factory=list)
    seed_count: int = 0
    hop_depth: int = 0
    token_estimate: int = 0
    intent_keywords: List[str] = field(default_factory=list)


_INFRA_LABELS = frozenset({
    "ContextMeta", "VectorIndex", "BM25Index", "Session", "ContextRef",
    "Context", "Project", "ContextIntelligence", "CodeBase", "SystemStore",
    "UserStore", "WebStore", "GeneratedStore", "MemoryStore", "ArtifactStore",
    "ToolStore", "KnowledgeBase", "AgentThought", "AgentPresence",
    "ExperimentRun", "PipelineRun",
})

_CHARS_PER_TOKEN = 4  # rough estimate for English text


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English."""
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _node_text(node) -> str:
    """Extract searchable text from a node."""
    props = node.properties if hasattr(node, "properties") else {}
    return " ".join(str(props.get(f, "")) for f in
                    ("name", "statement", "content", "description", "topic", "claim",
                     "questions_answered", "next_clues")).lower()


def _build_idf(nodes: list, keywords: Dict[str, float]) -> Dict[str, float]:
    """Compute IDF for keywords across the node corpus.

    IDF = log(N / df) where df = number of nodes containing the term.
    Words appearing in many nodes get downweighted.
    """
    import math
    n = max(len(nodes), 1)
    doc_freq: Dict[str, int] = {}
    for kw in keywords:
        doc_freq[kw] = 0
    for node in nodes:
        text = _node_text(node)
        for kw in keywords:
            if kw in text:
                doc_freq[kw] += 1
    return {kw: math.log(n / max(df, 1)) + 1.0 for kw, df in doc_freq.items()}


# Module-level IDF cache (recomputed when namespace changes)
_idf_cache: Dict[str, Dict[str, float]] = {}  # namespace → {keyword → idf}
_idf_namespace: str = ""


def _node_keyword_score(node, keywords: Dict[str, float], idf: Optional[Dict[str, float]] = None) -> float:
    """Score a node against intent keywords with IDF weighting.

    IDF downweights words that appear in many nodes (generic terms like
    'module', 'service') and boosts words that appear in few (specific
    terms like 'JWT', 'bcrypt').
    """
    text = _node_text(node)
    hits = 0
    score = 0.0
    max_idf_hit = 0.0
    for kw, weight in keywords.items():
        if kw in text:
            idf_boost = idf.get(kw, 1.0) if idf else 1.0
            score += weight * idf_boost
            hits += 1
            if idf_boost > max_idf_hit:
                max_idf_hit = idf_boost
    # 2+ hits: always include (multiple keyword overlap = strong match)
    # 1 hit: only include if the matching keyword is specific (high IDF)
    #   IDF > 2.0 means the word appears in <37% of nodes — it's distinctive
    if hits == 0:
        return 0.0
    if hits == 1 and max_idf_hit < 2.0:
        return 0.0
    return score


def _node_to_dict(node) -> Dict:
    """Convert a graph node to a projection dict. Keeps full content for budget-aware assembly."""
    props = node.properties if hasattr(node, "properties") else {}
    label = getattr(node, "node_type", None) or getattr(node, "label", None) or props.get("label", "?")
    name = props.get("name", props.get("title", props.get("topic", "")))
    # CUs have 'claim' as their main content; regular nodes use statement/content
    content = props.get("claim", props.get("statement", props.get("content", props.get("description", "")))) or ""
    # Don't repeat name in content if they're the same
    if content == name:
        content = ""
    d = {
        "id": node.id if hasattr(node, "id") else props.get("id", "?"),
        "label": label,
        "name": name,
        "content": content,
        "token_estimate": _estimate_tokens(f"{label} {name} {content}"),
    }
    # Preserve CU-specific fields for zoom-level rendering
    if label == "ContextUnit":
        d["confidence"] = props.get("confidence", 0.0)
        d["questions_answered"] = props.get("questions_answered", [])
        d["next_clues"] = props.get("next_clues", [])
        d["evidence_ids"] = props.get("evidence_ids", [])
    return d


class SubgraphProjector:
    """Projects a relevant subgraph from the atomic graph based on intent."""

    @staticmethod
    def project(
        intent: IntentVector,
        atomic_db: Any = None,
        namespace: str = "",
        max_tokens: int = 2000,
        budget: Optional["ProjectionBudget"] = None,
        priority_labels: Optional[List[str]] = None,
    ) -> ProjectedSubgraph:
        if not intent.keywords or intent.confidence == 0.0 or atomic_db is None:
            return ProjectedSubgraph()

        if budget is None:
            budget = ProjectionBudget(max_tokens=max_tokens, max_time_ms=10000, tier="deep")

        adapter = getattr(atomic_db, "csr_adapter", None) or atomic_db
        top_keywords = sorted(intent.keywords.items(), key=lambda x: -x[1])[:10]

        seeds: List = []
        seed_scores: Dict[str, float] = {}
        idf: Optional[Dict[str, float]] = None
        used_index = False

        # Step 1a: LMDB BM25 — O(K) index lookup, not O(N) scan
        if budget.should_run_stage("bm25"):
            try:
                import os
                idx_path = f"contextcore_data/lmdb_index/{namespace}"
                if os.path.exists(idx_path):
                    from ..search.lmdb_index import get_lmdb_index
                    lmdb_idx = get_lmdb_index(namespace)
                    idx_stats = lmdb_idx.stats()
                    if idx_stats.get("nodes", 0) > 0:
                        used_index = True
                        # Get IDF directly from index — O(K) per keyword
                        idf = lmdb_idx.get_idf(list(intent.keywords.keys()))
                        # BM25 search — O(K) postings lookup
                        query = " ".join(kw for kw, _ in top_keywords[:5])
                        lmdb_results = lmdb_idx.search(query, limit=50)
                        for r in lmdb_results:
                            nid = r.get("node_id", "")  # LMDB returns "node_id", not "id"
                            if nid and r.get("label", "") not in _INFRA_LABELS:
                                node = adapter.get_node(nid) if hasattr(adapter, "get_node") else None
                                if node:
                                    score = _node_keyword_score(node, intent.keywords, idf=idf)
                                    if score > 0:
                                        seeds.append(node)
                                        seed_scores[nid] = score
            except Exception as _e:
                logger.warning("[CGP] LMDB BM25 seed retrieval failed for ns=%s: %s", namespace, _e)

        # Step 1b: Qdrant vectors — O(K) ANN search, not O(N)
        if budget.should_run_stage("qdrant"):
            try:
                from ..context.vector_integration import get_session_vector_store
                svs = get_session_vector_store()
                if svs.available and svs.has_vectors(namespace):
                    used_index = True
                    query_text = intent.task_title or " ".join(kw for kw, _ in top_keywords[:5])
                    vec_results = svs.search(namespace, query_text, k=30)
                    for r in vec_results:
                        nid = r.get("node_id", "")
                        if nid and nid not in seed_scores:
                            meta = r.get("metadata", {})
                            if meta.get("label", "") not in _INFRA_LABELS:
                                node = adapter.get_node(nid) if hasattr(adapter, "get_node") else None
                                if node:
                                    vec_score = r.get("score", 0.5) * 2.0
                                    seeds.append(node)
                                    seed_scores[nid] = vec_score
            except Exception as _e:
                logger.warning("[CGP] Qdrant vector seed retrieval failed for ns=%s: %s", namespace, _e)

        # Step 1c: Full scan fallback — ONLY when no index available (small/test graphs)
        content_nodes: list = []  # defined here so step 1d can reference it
        if "bm25" in budget._stages and (not used_index or len(seeds) < 3):
            try:
                all_nodes = list(adapter.get_all_nodes()) if hasattr(adapter, "get_all_nodes") else []
                content_nodes = [n for n in all_nodes
                                if (getattr(n, "node_type", None) or getattr(n, "label", "")) not in _INFRA_LABELS]
                # Compute IDF from full scan only if index didn't provide it
                if not idf and content_nodes:
                    idf = _build_idf(content_nodes, intent.keywords)
                for n in content_nodes:
                    nid = n.id if hasattr(n, "id") else ""
                    if nid in seed_scores:
                        continue
                    score = _node_keyword_score(n, intent.keywords, idf=idf)
                    if score > 0:
                        seeds.append(n)
                        seed_scores[nid] = score
            except Exception:
                pass

        # Step 1d: ContextUnit retrieval — pre-assembled intelligence objects
        # CUs are the richest nodes (bundled facts + claim + actors). Prioritize them.
        cu_nodes = []
        try:
            for n in content_nodes:
                nt = getattr(n, "node_type", None) or getattr(n, "label", "")
                if nt != "ContextUnit":
                    continue
                nid = n.id if hasattr(n, "id") else ""
                if nid in seed_scores:
                    continue
                score = _node_keyword_score(n, intent.keywords, idf=idf)
                if score > 0:
                    cu_nodes.append(n)
                    seed_scores[nid] = score * 1.5  # boost CUs — they're pre-synthesized
        except Exception:
            pass

        # CUs go first, then regular seeds
        seeds = cu_nodes + seeds

        if not seeds:
            return ProjectedSubgraph(intent_keywords=[kw for kw, _ in top_keywords])

        # Apply schema priority label boosts — domain-critical node types always surface
        if priority_labels:
            _priority_set = set(priority_labels)
            for seed in seeds:
                nid = seed.id if hasattr(seed, "id") else ""
                label = getattr(seed, "node_type", None) or getattr(seed, "label", "")
                if label in _priority_set and nid in seed_scores:
                    seed_scores[nid] *= 1.8  # strong boost for domain-priority types

        # Apply feedback boosts — nodes that were useful in past projections rank higher
        for seed in seeds:
            nid = seed.id if hasattr(seed, "id") else ""
            if nid in seed_scores:
                seed_scores[nid] *= get_feedback_boost(namespace, nid)

        # Sort seeds by score, add within budget (using actual token estimates)
        seeds.sort(key=lambda n: seed_scores.get(n.id if hasattr(n, "id") else "", 0), reverse=True)

        projected_nodes = []
        projected_ids = set()
        tokens_used = 0

        for seed in seeds:
            nd = _node_to_dict(seed)
            if tokens_used + nd["token_estimate"] > budget.max_tokens:
                break
            projected_nodes.append(nd)
            projected_ids.add(nd["id"])
            tokens_used += nd["token_estimate"]
            budget.consume_tokens(nd["token_estimate"])

        seed_count = len(projected_nodes)
        hop_depth = 0

        # Step 2: Adaptive hop expansion
        if budget.should_run_stage("hops") and budget.has_remaining() and budget.remaining_tokens > 200 and hasattr(adapter, "get_neighbors"):
            hop_depth = 1
            neighbors = []
            for seed in seeds[:5]:
                if budget.exhausted():
                    break
                sid = seed.id if hasattr(seed, "id") else ""
                try:
                    for neighbor in adapter.get_neighbors(sid):
                        nid = neighbor.id if hasattr(neighbor, "id") else ""
                        label = getattr(neighbor, "node_type", None) or getattr(neighbor, "label", "")
                        if nid not in projected_ids and label not in _INFRA_LABELS:
                            neighbors.append(neighbor)
                            projected_ids.add(nid)
                except Exception:
                    pass

            for neighbor in neighbors:
                nd = _node_to_dict(neighbor)
                if tokens_used + nd["token_estimate"] > budget.max_tokens:
                    break
                projected_nodes.append(nd)
                tokens_used += nd["token_estimate"]
                budget.consume_tokens(nd["token_estimate"])

            remaining = budget.remaining_tokens

            if remaining > 500 and len(seeds) >= 2 and budget.has_remaining():
                hop_depth = 2
                hop2_neighbors = []
                for neighbor in neighbors[:3]:
                    if budget.exhausted():
                        break
                    nid = neighbor.id if hasattr(neighbor, "id") else ""
                    try:
                        for n2 in adapter.get_neighbors(nid):
                            n2id = n2.id if hasattr(n2, "id") else ""
                            label = getattr(n2, "node_type", None) or getattr(n2, "label", "")
                            if n2id not in projected_ids and label not in _INFRA_LABELS:
                                hop2_neighbors.append(n2)
                                projected_ids.add(n2id)
                    except Exception:
                        pass
                for n2 in hop2_neighbors:
                    nd = _node_to_dict(n2)
                    if tokens_used + nd["token_estimate"] > budget.max_tokens:
                        break
                    projected_nodes.append(nd)
                    tokens_used += nd["token_estimate"]
                    budget.consume_tokens(nd["token_estimate"])

        # Step 3: Collect edges between projected nodes
        # O(projected × degree) via adjacency, not O(E) full scan
        projected_edges = []
        try:
            if hasattr(adapter, "get_neighbors"):
                # Fast path: use adjacency dict — O(projected_nodes × avg_degree)
                seen_edges = set()
                for nid in projected_ids:
                    try:
                        for neighbor_id, edge in adapter.get_neighbors(nid):
                            if neighbor_id in projected_ids:
                                eid = getattr(edge, "id", None) or f"{nid}-{neighbor_id}"
                                if eid not in seen_edges:
                                    seen_edges.add(eid)
                                    projected_edges.append({
                                        "source": nid,
                                        "target": neighbor_id,
                                        "label": getattr(edge, "label", "RELATED"),
                                    })
                    except Exception:
                        pass
            else:
                # Fallback: full scan (only for adapters without get_neighbors)
                all_edges = adapter.get_all_edges() if hasattr(adapter, "get_all_edges") else []
                for e in all_edges:
                    src = str(getattr(e, "source", getattr(e, "from_id", "")))
                    tgt = str(getattr(e, "target", getattr(e, "to_id", "")))
                    if src in projected_ids and tgt in projected_ids:
                        projected_edges.append({
                            "source": src,
                            "target": tgt,
                            "label": getattr(e, "label", "RELATED"),
                        })
        except Exception:
            pass

        return ProjectedSubgraph(
            nodes=projected_nodes,
            edges=projected_edges,
            seed_count=seed_count,
            hop_depth=hop_depth,
            token_estimate=tokens_used,
            intent_keywords=[kw for kw, _ in top_keywords],
        )


class ProjectionAssembler:
    """Formats a ProjectedSubgraph into agent-readable context."""

    # Tier → CU zoom level mapping
    TIER_ZOOM = {
        "instant": "headline",
        "fast": "headline",
        "standard": "summary",
        "deep": "full",
    }

    @staticmethod
    def assemble(
        subgraph: ProjectedSubgraph,
        task_title: str = "",
        total_nodes: int = 0,
        cross_agent_signals: Optional[List[Dict]] = None,
        tier: str = "standard",
        cu_config: Any = None,
    ) -> str:
        if not subgraph.nodes:
            kw_str = ", ".join(subgraph.intent_keywords[:5]) if subgraph.intent_keywords else "none"
            return f"No relevant context found for task: \"{task_title}\" (keywords: {kw_str})"

        lines = []

        kw_display = ", ".join(subgraph.intent_keywords[:5])
        lines.append(f"PROJECTED CONTEXT (for task: \"{task_title}\")")
        lines.append(f"Based on: {kw_display} | {subgraph.seed_count} direct matches")
        lines.append("")

        direct = subgraph.nodes[:subgraph.seed_count]
        connected = subgraph.nodes[subgraph.seed_count:]

        # Resolve CU zoom level from tier
        zoom = ProjectionAssembler.TIER_ZOOM.get(tier, "summary")

        if direct:
            lines.append("DIRECT MATCHES:")
            for n in direct:
                content = n.get("content", "")
                if n.get("label") == "ContextUnit":
                    # ContextUnit: render at tier-appropriate zoom level
                    from ..context.context_units import format_cu_for_agent
                    cu_dict = {
                        "properties": {
                            "topic": n.get("name", ""),
                            "claim": content,
                            "confidence": n.get("confidence", 0.0),
                            "questions_answered": n.get("questions_answered", []),
                            "next_clues": n.get("next_clues", []),
                        },
                        "evidence_ids": n.get("evidence_ids", []),
                    }
                    formatted = format_cu_for_agent(cu_dict, zoom=zoom, cu_config=cu_config)
                    for fl in formatted.split("\n"):
                        lines.append(f"  {fl}")
                elif content and len(content) > 200:
                    # Passage-sized content: show full text
                    lines.append(f"  [{n['label']}] {n['name']}")
                    lines.append(f"    {content}")
                elif content:
                    lines.append(f"  [{n['label']}] {n['name']} — {content}")
                else:
                    lines.append(f"  [{n['label']}] {n['name']}")

        if connected:
            lines.append("")
            lines.append("CONNECTED CONTEXT:")
            for n in connected:
                edge_label = ""
                for e in subgraph.edges:
                    if e["target"] == n["id"] or e["source"] == n["id"]:
                        edge_label = f" -[{e['label']}]->"
                        break
                content = n.get("content", "")
                if content and len(content) > 200:
                    lines.append(f"  [{n['label']}] {n['name']}{edge_label}")
                    lines.append(f"    {content[:500]}")
                elif content:
                    lines.append(f"  [{n['label']}] {n['name']}{edge_label} — {content[:200]}")
                else:
                    lines.append(f"  [{n['label']}] {n['name']}{edge_label}")

        if cross_agent_signals:
            lines.append("")
            lines.append("CROSS-AGENT SIGNALS:")
            for sig in cross_agent_signals[:5]:
                lines.append(f"  [{sig.get('agent', '?')}] ({sig.get('label', '?')}) {sig.get('name', '')[:80]}")

        pct = f"{len(subgraph.nodes) / total_nodes * 100:.1f}%" if total_nodes > 0 else "?"
        lines.append("")
        lines.append(
            f"{len(subgraph.nodes)} nodes projected from {total_nodes} total ({pct}) "
            f"| Budget: {subgraph.token_estimate:,} tokens"
        )

        return "\n".join(lines)


# ── Top-level orchestrator ───────────────────────────────────────────

def project_context(ctx: Any, max_tokens: int = 2000,
                    max_time_ms: int = 1000, tier: str = "standard",
                    schema: Any = None) -> Optional[str]:
    """Top-level orchestrator: extract intent -> project subgraph -> assemble.

    Called by orient() when the agent has tasks. Returns formatted projection
    string, or None if no tasks / projection not applicable.
    Accepts an optional ExtractionSchema whose context_unit config drives
    domain-aware priority labels in the projection.
    """
    budget = ProjectionBudget(max_tokens=max_tokens, max_time_ms=max_time_ms, tier=tier)

    tasks = []
    try:
        if ctx.project:
            tasks = ctx.project.get_open_tasks(
                agent_id=getattr(ctx, "agent_id", ""),
                agent_name=getattr(ctx, "agent_name", ""),
            )
    except Exception:
        pass

    if not tasks:
        return None

    # Check cache first (60s TTL) — tier-aware
    agent_id = getattr(ctx, "agent_id", "")
    namespace = getattr(ctx.conn, "namespace", "") or getattr(ctx.conn, "_namespace", "")
    if budget.should_run_stage("cache"):
        cached = get_cached_projection(namespace, agent_id)
        if cached:
            return cached
        if budget.tier == "instant":
            return None

    # 1. Extract intent from runtime graph
    runtime_db = None
    if hasattr(ctx, "runtime_conn") and ctx.runtime_conn:
        runtime_db = getattr(ctx.runtime_conn, "db", None) or getattr(ctx.runtime_conn, "contextsynapse", None)

    intent = IntentExtractor.extract(
        tasks=tasks,
        runtime_db=runtime_db,
        agent_name=getattr(ctx, "agent_name", ""),
        agent_id=getattr(ctx, "agent_id", ""),
    )

    if intent.confidence == 0.0:
        return None

    # 2. Project subgraph from atomic graph
    atomic_db = getattr(ctx.conn, "db", None) or getattr(ctx.conn, "contextsynapse", None)
    namespace = getattr(ctx.conn, "namespace", "") or getattr(ctx.conn, "_namespace", "")

    # Extract priority labels from schema's context_unit config (if available)
    _priority_labels = None
    if schema is not None:
        cu_cfg = getattr(schema, "context_unit", None)
        if cu_cfg is not None:
            _priority_labels = getattr(cu_cfg, "priority_labels", None)

    subgraph = SubgraphProjector.project(
        intent=intent,
        atomic_db=atomic_db,
        namespace=namespace,
        budget=budget,
        priority_labels=_priority_labels,
    )

    # 3. Collect cross-agent signals — scored by relevance to THIS agent's task
    cross_signals = []
    if runtime_db and ("signals" in budget._stages or "hops" in budget._stages):
        my_name = (getattr(ctx, "agent_name", "") or "").lower()
        candidates = []
        for label in ("Finding", "Insight", "Decision"):
            try:
                for n in runtime_db.get_all_nodes(label=label):
                    props = n.properties if hasattr(n, "properties") else {}
                    author = (props.get("_agent_name", props.get("created_by", "")) or "").lower()
                    if author and author != my_name and author != "system":
                        name = (props.get("name", props.get("content", "")) or "")[:80]
                        if name:
                            # Score by keyword overlap with this agent's intent
                            sig_kws = _extract_keywords(name)
                            overlap = sum(1 for k in sig_kws if k in intent.keywords)
                            candidates.append({
                                "agent": author, "label": label,
                                "name": name, "_relevance": overlap,
                            })
            except Exception:
                pass
        # Take top 5 most relevant signals (not all 99 in a 100-agent session)
        candidates.sort(key=lambda x: x["_relevance"], reverse=True)
        cross_signals = candidates[:5]

    # 4. Count total nodes in atomic graph for stats
    total_nodes = 0
    try:
        adapter = getattr(atomic_db, "csr_adapter", None) or atomic_db
        if hasattr(adapter, "get_all_nodes"):
            total_nodes = len(list(adapter.get_all_nodes()))
    except Exception:
        pass

    # 5. Assemble
    # Extract cu_config for zoom-level rendering
    _cu_config = None
    if schema is not None:
        _cu_config = getattr(schema, "context_unit", None)

    projection = ProjectionAssembler.assemble(
        subgraph=subgraph,
        task_title=tasks[0].get("title", ""),
        total_nodes=total_nodes,
        cross_agent_signals=cross_signals[:5],
        tier=tier,
        cu_config=_cu_config,
    )

    # 6. Prepend task list
    task_lines = ["YOUR TASKS:"]
    for t in tasks[:5]:
        task_lines.append(f"  [{t['priority'].upper()}] {t['title']} (id: {t['id']})")

    # 7. Record what was projected (for feedback learning)
    try:
        _record_projection(
            namespace=namespace,
            agent_id=getattr(ctx, "agent_id", ""),
            task_keywords=list(intent.keywords.keys())[:5],
            projected_ids=[n["id"] for n in subgraph.nodes],
        )
    except Exception:
        pass

    # 8. Projection diff — show what changed since last orient
    diff_section = ""
    try:
        prev_log = _projection_log.get(agent_id)
        if prev_log and prev_log.get("projected_ids"):
            prev_ids = prev_log["projected_ids"]
            curr_ids = {n["id"] for n in subgraph.nodes}
            new_ids = curr_ids - prev_ids
            dropped_ids = prev_ids - curr_ids
            if new_ids or dropped_ids:
                diff_lines = []
                if new_ids:
                    new_names = [n["name"] for n in subgraph.nodes if n["id"] in new_ids]
                    diff_lines.append(f"  + {len(new_ids)} new: {', '.join(new_names[:3])}")
                if dropped_ids:
                    diff_lines.append(f"  - {len(dropped_ids)} no longer relevant")
                diff_section = "\nCHANGED SINCE LAST ORIENT:\n" + "\n".join(diff_lines)
    except Exception:
        pass

    # 9. Intent drift detection — are agent's actions diverging from task?
    drift_section = ""
    try:
        if "hops" in budget._stages and intent.confidence >= 0.6:  # only check when we have action signals
            task_kws = set(_extract_keywords(tasks[0].get("title", "")).keys())
            action_kws = set()
            if runtime_db:
                my_name = (getattr(ctx, "agent_name", "") or "").lower()
                for label in ("Finding", "AgentAction"):
                    try:
                        for n in runtime_db.get_all_nodes(label=label):
                            props = n.properties if hasattr(n, "properties") else {}
                            author = (props.get("_agent_name", props.get("agent", "")) or "").lower()
                            if author == my_name:
                                text = props.get("name", props.get("description", "")) or ""
                                action_kws.update(_extract_keywords(text).keys())
                    except Exception:
                        pass

            if action_kws and task_kws:
                overlap = task_kws & action_kws
                drift_kws = action_kws - task_kws - _STOP_WORDS
                if len(drift_kws) > len(overlap) * 2 and len(drift_kws) > 3:
                    drift_sample = list(drift_kws)[:5]
                    drift_section = (
                        f"\nDRIFT DETECTED: Your recent work focuses on "
                        f"[{', '.join(drift_sample)}] which diverges from your task. "
                        f"Consider updating your task or refocusing."
                    )
    except Exception:
        pass

    result = "\n".join(task_lines) + "\n\n" + projection
    if diff_section:
        result += diff_section
    if drift_section:
        result += drift_section

    # Cache for 60s
    try:
        cache_projection(namespace, agent_id, result)
    except Exception:
        pass

    return result


# ── Redis Helper ─────────────────────────────────────────────────────

def _get_redis():
    """Get Redis client, or None if unavailable."""
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


# ── Projection Cache ─────────────────────────────────────────────────

_CACHE_TTL = 60  # seconds

# In-memory fallback cache — used when Redis is unavailable (e.g. test environments)
_mem_projection_cache: Dict[str, tuple] = {}  # key -> (expiry_ts, result)

def _cache_key(namespace: str, agent_id: str) -> str:
    return f"cgp:cache:{namespace}:{agent_id}"

def cache_projection(namespace: str, agent_id: str, result: str) -> None:
    """Cache a projection result in Redis (60s TTL) and in-memory (always)."""
    import time as _t
    key = _cache_key(namespace, agent_id)
    # Always populate in-memory cache — ensures warm calls are fast even without Redis
    _mem_projection_cache[key] = (_t.monotonic() + _CACHE_TTL, result)
    r = _get_redis()
    if r:
        try:
            r.set(key, result, ex=_CACHE_TTL)
        except Exception:
            pass

def get_cached_projection(namespace: str, agent_id: str) -> Optional[str]:
    """Get cached projection, or None if expired/missing."""
    import time as _t
    key = _cache_key(namespace, agent_id)
    # Check in-memory first (zero-latency, always consistent within this process)
    entry = _mem_projection_cache.get(key)
    if entry is not None:
        expiry, result = entry
        if _t.monotonic() < expiry:
            return result
        del _mem_projection_cache[key]
    # Fall back to Redis (shared across workers)
    r = _get_redis()
    if r:
        try:
            val = r.get(key)
            if val is not None:
                return val
        except Exception:
            pass
    return None


def invalidate_projection_cache(namespace: str, agent_id: Optional[str] = None) -> int:
    """Invalidate projection cache for a namespace.

    If agent_id is given, clears only that agent's cache entry.
    If agent_id is None, clears all agents' entries for this namespace.

    Returns the number of entries cleared.
    """
    import logging as _log
    _logger = _log.getLogger(__name__)
    count = 0
    prefix = _cache_key(namespace, "")  # "cgp:cache:{namespace}:"

    # Clear in-memory cache entries
    to_remove = []
    for key in list(_mem_projection_cache.keys()):
        if agent_id is None:
            if key.startswith(prefix):
                to_remove.append(key)
        else:
            if key == _cache_key(namespace, agent_id):
                to_remove.append(key)
    for key in to_remove:
        _mem_projection_cache.pop(key, None)
        count += 1

    # Clear Redis cache entries
    r = _get_redis()
    if r:
        try:
            if agent_id is None:
                pattern = f"{prefix}*"
                cursor = 0
                while True:
                    cursor, keys = r.scan(cursor, match=pattern, count=100)
                    if keys:
                        r.delete(*keys)
                        count += len(keys)
                    if cursor == 0:
                        break
            else:
                key = _cache_key(namespace, agent_id)
                deleted = r.delete(key)
                count += deleted
        except Exception as _e:
            _logger.debug("Redis invalidation error: %s", _e)

    return count


# ── Projection Feedback Loop (Redis-backed) ──────────────────────────

# In-memory fallback when Redis is unavailable
_projection_log: Dict[str, Dict] = {}
_feedback_scores: Dict[str, Dict[str, float]] = {}


def _record_projection(namespace: str, agent_id: str, task_keywords: List[str],
                        projected_ids: List[str]) -> None:
    """Record what was projected to an agent (for learning from usage)."""
    import time, json as _json
    log_data = {
        "projected_ids": list(projected_ids),
        "task_keywords": task_keywords,
        "timestamp": time.time(),
        "namespace": namespace,
    }

    # Try Redis first
    r = _get_redis()
    if r:
        try:
            r.set(f"cgp:log:{agent_id}", _json.dumps(log_data), ex=3600)  # 1h TTL
        except Exception:
            pass

    # Always keep in-memory copy
    log_data["projected_ids"] = set(projected_ids)
    _projection_log[agent_id] = log_data


def record_usage(agent_id: str, referenced_node_id: str) -> None:
    """Called when an agent references a node in a Finding/Decision.

    Positive signal: boost this node for future projections.
    """
    import json as _json

    # Get projection log (Redis first, then memory)
    log = None
    r = _get_redis()
    if r:
        try:
            raw = r.get(f"cgp:log:{agent_id}")
            if raw:
                log = _json.loads(raw)
                log["projected_ids"] = set(log["projected_ids"])
        except Exception:
            pass
    if not log:
        log = _projection_log.get(agent_id)
    if not log:
        return

    ns = log["namespace"]
    projected = log["projected_ids"]

    if referenced_node_id in projected:
        # Boost in Redis (7-day TTL)
        if r:
            try:
                key = f"cgp:feedback:{ns}"
                current = float(r.hget(key, referenced_node_id) or 1.0)
                r.hset(key, referenced_node_id, str(current * 1.1))
                r.expire(key, 604800)  # 7 days
            except Exception:
                pass

        # Also update in-memory
        if ns not in _feedback_scores:
            _feedback_scores[ns] = {}
        _feedback_scores[ns][referenced_node_id] = (
            _feedback_scores[ns].get(referenced_node_id, 1.0) * 1.1
        )


def get_feedback_boost(namespace: str, node_id: str) -> float:
    """Get the learned relevance boost. 1.0 = neutral, >1.0 = useful."""
    # Try Redis first
    r = _get_redis()
    if r:
        try:
            val = r.hget(f"cgp:feedback:{namespace}", node_id)
            if val:
                return float(val)
        except Exception:
            pass
    return _feedback_scores.get(namespace, {}).get(node_id, 1.0)


def clear_feedback(namespace: str) -> None:
    """Clear feedback scores when a DIFFERENT atomic context is attached."""
    _feedback_scores.pop(namespace, None)
    r = _get_redis()
    if r:
        try:
            r.delete(f"cgp:feedback:{namespace}")
        except Exception:
            pass
    to_remove = [aid for aid, log in _projection_log.items() if log.get("namespace") == namespace]
    for aid in to_remove:
        _projection_log.pop(aid, None)


# ── Re-query Tracking (evidence of projection quality) ───────────────────────

_ORIENT_TIME_TTL = 300  # 5 minutes — window within which an ask() counts as a re-query
_REQUERY_TTL = 86400    # 24 hours — counter persists across sessions for daily stats

# In-memory fallbacks (used when Redis is unavailable)
_orient_times: Dict[str, float] = {}    # "{namespace}:{agent_id}" → unix timestamp
_requery_counts: Dict[str, int] = {}    # "{namespace}:{agent_id}" → count


def _rq_key(namespace: str, agent_id: str, suffix: str) -> str:
    return f"cgp:rq:{suffix}:{namespace}:{agent_id}"


def record_orient_time(namespace: str, agent_id: str) -> None:
    """Record that orient() was just called. Used to detect re-queries."""
    import time as _t
    ts = _t.time()
    mem_key = f"{namespace}:{agent_id}"
    _orient_times[mem_key] = ts
    r = _get_redis()
    if r:
        try:
            r.set(_rq_key(namespace, agent_id, "orient_ts"), str(ts), ex=_ORIENT_TIME_TTL)
        except Exception as _e:
            logger.debug("record_orient_time Redis error: %s", _e)


def get_orient_time(namespace: str, agent_id: str) -> Optional[float]:
    """Return unix timestamp of last orient() for this agent, or None."""
    import time as _t
    mem_key = f"{namespace}:{agent_id}"
    ts = _orient_times.get(mem_key)
    if ts is not None and (_t.time() - ts) < _ORIENT_TIME_TTL:
        return ts
    r = _get_redis()
    if r:
        try:
            val = r.get(_rq_key(namespace, agent_id, "orient_ts"))
            if val is not None:
                return float(val)
        except Exception as _e:
            logger.debug("get_orient_time Redis error: %s", _e)
    return None


def record_requery(namespace: str, agent_id: str) -> int:
    """Increment re-query counter if agent is asking within 5 min of orient().

    Returns the new counter value (or current value if orient was not recent).
    """
    import time as _t
    orient_ts = get_orient_time(namespace, agent_id)
    if orient_ts is None or (_t.time() - orient_ts) > _ORIENT_TIME_TTL:
        return get_requery_count(namespace, agent_id)

    mem_key = f"{namespace}:{agent_id}"
    _requery_counts[mem_key] = _requery_counts.get(mem_key, 0) + 1
    count = _requery_counts[mem_key]
    r = _get_redis()
    if r:
        try:
            rk = _rq_key(namespace, agent_id, "requery_count")
            count = r.incr(rk)
            r.expire(rk, _REQUERY_TTL)
        except Exception as _e:
            logger.debug("record_requery Redis error: %s", _e)
    return count


def get_requery_count(namespace: str, agent_id: str) -> int:
    """Return the current re-query count for this agent."""
    mem_key = f"{namespace}:{agent_id}"
    r = _get_redis()
    if r:
        try:
            val = r.get(_rq_key(namespace, agent_id, "requery_count"))
            if val is not None:
                return int(val)
        except Exception as _e:
            logger.debug("get_requery_count Redis error: %s", _e)
    return _requery_counts.get(mem_key, 0)


# ── Promotion Event Consumer ──────────────────────────────────────────────────

def get_recent_promotions(namespace: str, since_ts: float) -> list:
    """Return promotion events for a namespace newer than since_ts.

    Each event is: {"node_id": str, "label": str, "title": str, "promoted_at": float}
    Returns empty list if no events or on any error.
    """
    import json as _json
    results = []

    # Try Redis first (shared across workers)
    r = _get_redis()
    if r:
        try:
            rk = f"cgp:promotions:{namespace}"
            raw_events = r.lrange(rk, 0, 49)
            for raw in raw_events:
                try:
                    evt = _json.loads(raw)
                    if evt.get("promoted_at", 0) > since_ts:
                        results.append(evt)
                except Exception as _e:
                    logger.debug("get_recent_promotions JSON parse error: %s", _e)
            return results
        except Exception as _e:
            logger.debug("get_recent_promotions Redis error: %s", _e)

    # In-memory fallback
    try:
        from ..context.promotion import _promotion_events
        for evt in _promotion_events.get(namespace, []):
            if evt.get("promoted_at", 0) > since_ts:
                results.append(evt)
    except Exception as _e:
        logger.debug("get_recent_promotions in-memory fallback error: %s", _e)

    return results
