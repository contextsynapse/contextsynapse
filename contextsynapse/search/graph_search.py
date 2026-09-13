"""Graph-enhanced search: query expansion, hop traversal, and reranking.

Builds on the LMDB keyword index and graph structure to deliver richer
search results by expanding queries with graph context, traversing
neighbours, and reranking by graph centrality.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from .lmdb_index import get_lmdb_index, _STOP_WORDS, _tokenize

logger = logging.getLogger(__name__)

# Stop words imported from lmdb_index — single source of truth.
# graph_search previously maintained a diverged copy; unified here.
_STOP = _STOP_WORDS

# Default labels whose snippet text is useful for query expansion
_DEFAULT_EXPANSION_LABELS = frozenset({"Topic", "Entity", "Concept", "Technology"})


def _get_expansion_labels(graph_name: str) -> frozenset:
    """Return expansion labels from schema, or defaults."""
    try:
        from contextsynapse.project.schema_manager import get_schema_manager
        labels = get_schema_manager().get_consumer_hint(
            graph_name, "search_expansion_labels", None
        )
        if labels is not None:
            return frozenset(labels)
    except Exception:
        pass
    return _DEFAULT_EXPANSION_LABELS

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ExpandedQuery:
    """Result of query expansion — carries original + enriched terms."""

    raw_query: str
    original_terms: List[str]
    entity_terms: List[str]
    expanded_terms: List[str]

    @property
    def all_terms(self) -> List[str]:
        """Union of original, entity, and expanded terms (deduplicated, ordered)."""
        seen: set[str] = set()
        out: list[str] = []
        for t in self.original_terms + self.entity_terms + self.expanded_terms:
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out


@dataclass
class SearchNode:
    """A node returned by graph-enhanced search."""

    node_id: str
    label: str
    props: Dict[str, Any]
    score: float = 0.0
    distance: int = 0        # 0 = seed (direct match), 1 = 1-hop neighbour, etc.
    via_edge: str = ""       # edge type that led here
    explain: Optional[Dict[str, float]] = None  # per-signal breakdown when explain=True


@dataclass
class SearchEdge:
    """An edge connecting two SearchNodes."""

    source: str
    target: str
    edge_type: str
    weight: float = 1.0


@dataclass
class GraphSearchResult:
    """Full result of a graph-enhanced search."""

    nodes: List[SearchNode] = field(default_factory=list)
    edges: List[SearchEdge] = field(default_factory=list)
    seeds: List[str] = field(default_factory=list)
    expanded_terms: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_QUOTED_RE = re.compile(r'"([^"]+)"')
_CAP_WORD_RE = re.compile(r'\b([A-Z][a-z]{2,})\b')


def _extract_query_entities(query: str) -> List[str]:
    """Extract entity-like terms: quoted strings and capitalized words."""
    entities: list[str] = []
    seen: set[str] = set()

    # Quoted strings first
    for match in _QUOTED_RE.finditer(query):
        term = match.group(1).strip().lower()
        if term and term not in seen:
            seen.add(term)
            entities.append(term)

    # Capitalized words (not at sentence start — heuristic: skip first word)
    words = query.split()
    for i, w in enumerate(words):
        m = _CAP_WORD_RE.match(w.strip('"'))
        if m:
            term = m.group(1).lower()
            if term not in seen and term not in _STOP:
                seen.add(term)
                entities.append(term)

    return entities


def _tokenize_query(query: str) -> List[str]:
    """Tokenize query using the shared lmdb_index tokenizer (camelCase, punctuation, stemming).

    Quoted strings are stripped first so they are handled by entity extraction
    rather than plain tokenization.
    """
    plain = _QUOTED_RE.sub(" ", query)
    return _tokenize(plain)


# ---------------------------------------------------------------------------
# Main expand function
# ---------------------------------------------------------------------------

def expand_query(
    query: str,
    db: Any = None,
    graph_name: str = "",
) -> ExpandedQuery:
    """Expand a search query using graph context.

    1. Tokenize into original terms (3+ chars, no stop words).
    2. Extract entity-like terms (quoted strings, capitalized words).
    3. If *graph_name* is provided, look up matching Topic/Entity/Concept/
       Technology nodes in the LMDB index and pull keywords from their
       snippet text as expanded terms.

    Returns an ``ExpandedQuery`` with all term sets.
    """
    original_terms = _tokenize_query(query)
    entity_terms = _extract_query_entities(query)
    expanded_terms: list[str] = []

    # Graph-based expansion via LMDB index
    if graph_name and db is not None:
        try:
            idx = get_lmdb_index(graph_name)
            stats = idx.stats()
            if stats.get("nodes", 0) > 0:
                # Search for nodes matching the query
                hits = idx.search_bm25(query, limit=10)
                seen: set[str] = set(original_terms + entity_terms)

                for hit in hits:
                    label = hit.get("label", "")
                    if label not in _get_expansion_labels(graph_name):
                        continue

                    # Pull keywords from snippet using the shared tokenizer
                    snippet = hit.get("snippet", "")
                    if snippet:
                        for token in _tokenize(snippet):
                            if token not in seen:
                                seen.add(token)
                                expanded_terms.append(token)
        except Exception:
            logger.debug("LMDB expansion unavailable for graph %s", graph_name, exc_info=True)

    return ExpandedQuery(
        raw_query=query,
        original_terms=original_terms,
        entity_terms=entity_terms,
        expanded_terms=expanded_terms,
    )


# ---------------------------------------------------------------------------
# Edge weights — semantic importance of each edge type
# ---------------------------------------------------------------------------
_DEFAULT_EDGE_WEIGHTS: Dict[str, float] = {
    "SOLVED_BY": 1.0,
    "SOLVES": 1.0,
    "MENTIONS": 0.8,
    "INVOLVES": 0.7,
    "DISCUSSES": 0.7,
    "RAISES": 0.6,
    "STATES": 0.6,
    "HAS_PASSAGE": 0.4,
    "CONTAINS": 0.5,
    "NEXT": 0.3,
    "RELATED_TO": 0.5,
}
EDGE_WEIGHTS = _DEFAULT_EDGE_WEIGHTS  # backward compat alias


def _get_edge_weights(graph_name: str) -> Dict[str, float]:
    """Return edge weights, merged with schema overrides if available."""
    try:
        from contextsynapse.project.schema_manager import get_schema_manager
        config = get_schema_manager().get_processing_config(graph_name, "search", {})
        overrides = config.get("graph", {}).get("edge_weights", {}) if config else {}
        if overrides:
            merged = dict(_DEFAULT_EDGE_WEIGHTS)
            merged.update(overrides)
            return merged
    except Exception:
        pass
    return _DEFAULT_EDGE_WEIGHTS

# Labels to skip during hop traversal (infrastructure / meta nodes)
_SKIP_LABELS = frozenset({
    "VectorIndex", "BM25Index", "Session", "ContextRef", "ExperimentRun",
    "AgentAction", "AgentThought", "SyncRun", "ContextMeta",
    "ContextIntelligence",
})


# ---------------------------------------------------------------------------
# Hop traversal helpers
# ---------------------------------------------------------------------------

def _estimate_tokens(props: Dict[str, Any]) -> int:
    """Rough token count from name + statement/description text."""
    text = str(props.get("name", ""))
    for key in ("statement", "description"):
        val = props.get(key, "")
        if val:
            text += " " + str(val)
    return max(len(text) // 4, 1)


def hop_traverse(
    db: Any,
    seeds: List[SearchNode],
    *,
    max_hops: int = 2,
    token_budget: int = 4000,
) -> Tuple[List[SearchNode], List[SearchEdge]]:
    """Walk typed edges 1..max_hops from *seeds*, respecting a token budget.

    Returns ``(all_nodes, traversed_edges)`` where *all_nodes* includes the
    original seeds.  Each expanded node carries ``distance`` (hop number) and
    ``via_edge`` (the edge type that led to it).
    """
    visited: Set[str] = {s.node_id for s in seeds}
    all_nodes: List[SearchNode] = list(seeds)
    all_edges: List[SearchEdge] = []

    # Token accounting — start with seed tokens
    tokens_used = sum(_estimate_tokens(s.props) for s in seeds)

    # BFS frontier: list of (node_id, current_hop)
    frontier: List[Tuple[str, int]] = [(s.node_id, 0) for s in seeds]

    for hop in range(1, max_hops + 1):
        next_frontier: List[Tuple[str, int]] = []
        for src_id, _ in frontier:
            try:
                neighbors = db.get_neighbors(src_id)
            except Exception:
                logger.debug("get_neighbors failed for %s", src_id, exc_info=True)
                continue

            for neighbor_id, edge_obj in neighbors:
                if neighbor_id in visited:
                    continue

                # Resolve node
                try:
                    node = db.get_node(neighbor_id)
                except Exception:
                    logger.debug("get_node failed for %s", neighbor_id, exc_info=True)
                    continue

                if node is None:
                    continue

                label = getattr(node, "label", "")
                if label in _SKIP_LABELS:
                    continue

                props = getattr(node, "properties", {}) or {}
                cost = _estimate_tokens(props)
                if tokens_used + cost > token_budget:
                    continue

                edge_type = getattr(edge_obj, "edge_type", "RELATED_TO")
                weight = EDGE_WEIGHTS.get(edge_type, 0.5)

                visited.add(neighbor_id)
                tokens_used += cost

                all_nodes.append(SearchNode(
                    node_id=neighbor_id,
                    label=label,
                    props=props,
                    score=weight,
                    distance=hop,
                    via_edge=edge_type,
                ))

                all_edges.append(SearchEdge(
                    source=src_id,
                    target=neighbor_id,
                    edge_type=edge_type,
                    weight=weight,
                ))

                next_frontier.append((neighbor_id, hop))

        frontier = next_frontier

    return all_nodes, all_edges


# ---------------------------------------------------------------------------
# Reranking weights
# ---------------------------------------------------------------------------
_W_RELEVANCE = 0.40   # original BM25/vector score
_W_DISTANCE = 0.30    # graph distance (seed=1.0, 1-hop=0.7, 2-hop=0.4)
_W_QUALITY = 0.15     # _quality property (0-100 → 0-1)
_W_EDGE = 0.10        # average edge type weight for edges pointing to node
_W_FEEDBACK = 0.15    # feedback boost — human signals should meaningfully affect ranking

_DISTANCE_SCORES = {0: 1.0, 1: 0.7, 2: 0.4}

_DEFAULT_RERANKING_WEIGHTS = {
    "relevance": _W_RELEVANCE, "distance": _W_DISTANCE,
    "quality": _W_QUALITY, "edge": _W_EDGE, "feedback": _W_FEEDBACK,
}


def _get_reranking_weights(graph_name: str) -> dict:
    """Return reranking weights, with schema overrides if available."""
    try:
        from contextsynapse.project.schema_manager import get_schema_manager
        config = get_schema_manager().get_processing_config(graph_name, "search", {})
        overrides = config.get("reranking", {}).get("weights", {}) if config else {}
        if overrides:
            merged = dict(_DEFAULT_RERANKING_WEIGHTS)
            merged.update(overrides)
            return merged
    except Exception:
        pass
    return _DEFAULT_RERANKING_WEIGHTS


def rerank(
    nodes: List[SearchNode],
    edges: List[SearchEdge],
    original_scores: Optional[Dict[str, float]] = None,
    feedback_boosts: Optional[Dict[str, float]] = None,
    explain: bool = False,
) -> List[SearchNode]:
    """Multi-signal reranking of search nodes.

    Combines relevance, graph distance, quality, edge weights, and feedback
    into a single score per node.  Returns nodes sorted descending with
    scores normalized to 0-1.

    Parameters
    ----------
    explain : bool
        When True, attach a per-signal breakdown dict to each node's
        ``explain`` field: ``{relevance, distance, quality, edge, feedback,
        raw_score}`` (values are the weighted contributions, pre-normalization).
    """
    original_scores = original_scores or {}
    feedback_boosts = feedback_boosts or {}

    # Pre-compute incoming edge weights per target node
    edge_weights_by_target: Dict[str, List[float]] = {}
    for e in edges:
        edge_weights_by_target.setdefault(e.target, []).append(e.weight)

    # (node_index, raw_score, signal_breakdown)
    raw_scores: List[Tuple[int, float, Dict[str, float]]] = []

    for i, node in enumerate(nodes):
        relevance = original_scores.get(node.node_id, 0.0)
        distance = _DISTANCE_SCORES.get(node.distance, 0.2)
        quality_raw = node.props.get("_quality", 50)
        try:
            quality = float(quality_raw) / 100.0
        except (TypeError, ValueError):
            quality = 0.5
        incoming = edge_weights_by_target.get(node.node_id, [])
        edge_signal = sum(incoming) / len(incoming) if incoming else 0.0
        # Feedback: use explicit boosts if provided, otherwise read _usefulness_score from node
        feedback = feedback_boosts.get(node.node_id, 0.0)
        if not feedback:
            usefulness = node.props.get("_usefulness_score")
            if usefulness is not None:
                try:
                    # _usefulness_score is 0.0-1.0, center at 0.5 so neutral = no boost
                    feedback = (float(usefulness) - 0.5) * 2.0  # range: -1.0 to +1.0
                except (TypeError, ValueError):
                    pass

        w_rel = _W_RELEVANCE * relevance
        w_dist = _W_DISTANCE * distance
        w_qual = _W_QUALITY * quality
        w_edge = _W_EDGE * edge_signal
        w_feed = _W_FEEDBACK * feedback
        score = w_rel + w_dist + w_qual + w_edge + w_feed

        breakdown = {
            "relevance": round(w_rel, 4),
            "distance": round(w_dist, 4),
            "quality": round(w_qual, 4),
            "edge": round(w_edge, 4),
            "feedback": round(w_feed, 4),
            "raw_score": round(score, 4),
        }
        raw_scores.append((i, score, breakdown))

    # Sort descending by score
    raw_scores.sort(key=lambda x: x[1], reverse=True)

    # Normalize to 0-1
    max_score = raw_scores[0][1] if raw_scores else 1.0
    if max_score <= 0:
        max_score = 1.0

    result: List[SearchNode] = []
    for idx, raw, breakdown in raw_scores:
        node = nodes[idx]
        node.score = raw / max_score
        if explain:
            node.explain = breakdown
        result.append(node)

    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def graph_search(
    db: Any,
    query: str,
    graph_name: str = "",
    k: int = 10,
    max_hops: int = 2,
    token_budget: int = 4000,
) -> GraphSearchResult:
    """Graph-enhanced search: expand → seed → traverse → rerank.

    1. Expand query using graph context.
    2. Seed retrieval via LMDB BM25 + optional vector search.
    3. Hop traversal from seed nodes.
    4. Rerank all nodes with multi-signal scoring.
    5. Return top results as a ``GraphSearchResult``.
    """
    t0 = time.time()

    # Resolve graph name
    gname = graph_name or getattr(db, "name", "") or ""

    # 1. Expand query
    eq = expand_query(query, db=db, graph_name=gname)

    # 2. Seed retrieval with Reciprocal Rank Fusion (RRF)
    # RRF: score(d) = Σ 1/(k + rank(d)) across retrieval sources.
    # Nodes that rank highly in *multiple* sources get a higher combined score
    # than nodes that score well in only one — the cross-signal agreement bonus.
    _RRF_K = 60  # standard constant; higher = smoother, lower = more top-heavy

    # Per-source ranked lists: bm25_ranks[nid] = rank position (0-based)
    bm25_ranks: Dict[str, int] = {}
    vec_ranks: Dict[str, int] = {}
    seed_hits: Dict[str, Dict[str, Any]] = {}

    try:
        idx = get_lmdb_index(gname)

        # Primary BM25 search
        bm25_hits = idx.search_bm25(query, limit=k * 3)
        for rank, hit in enumerate(bm25_hits):
            nid = hit.get("node_id", "")
            if nid:
                bm25_ranks[nid] = rank
                seed_hits[nid] = hit

        # Search expanded terms — add to bm25_ranks at degraded rank
        for term in eq.expanded_terms:
            try:
                exp_hits = idx.search_bm25(term, limit=k)
                base_rank = len(bm25_hits)  # expanded hits start after primary
                for rank, hit in enumerate(exp_hits):
                    nid = hit.get("node_id", "")
                    if nid and nid not in bm25_ranks:
                        bm25_ranks[nid] = base_rank + rank
                        seed_hits[nid] = hit
            except Exception:
                pass
    except Exception:
        logger.debug("LMDB seed retrieval failed for graph %s", gname, exc_info=True)

    # Optional vector search — RRF across BM25 + vector
    try:
        from contextsynapse.context.vector_integration import get_session_vector_store
        svs = get_session_vector_store()
        if svs and getattr(svs, 'available', False):
            vec_hits = svs.search(gname, query, k=k * 3)
            for rank, hit in enumerate(vec_hits or []):
                nid = hit.get("node_id", "")
                if nid:
                    vec_ranks[nid] = rank
                    if nid not in seed_hits:
                        seed_hits[nid] = hit
    except Exception:
        logger.warning("[search] Vector search unavailable — running BM25-only mode", exc_info=False)

    # Compute RRF scores across all retrieval sources
    all_nids = set(bm25_ranks) | set(vec_ranks)
    seed_scores: Dict[str, float] = {}
    for nid in all_nids:
        rrf = 0.0
        if nid in bm25_ranks:
            rrf += 1.0 / (_RRF_K + bm25_ranks[nid])
        if nid in vec_ranks:
            rrf += 1.0 / (_RRF_K + vec_ranks[nid])
        seed_scores[nid] = rrf

    # No seeds found — return empty result
    if not seed_scores:
        elapsed = (time.time() - t0) * 1000
        return GraphSearchResult(
            stats={"ms": f"{elapsed:.1f}", "seeds": 0, "total_nodes": 0},
        )

    # Build seed SearchNode list (top k seeds by score)
    sorted_seeds = sorted(seed_scores.items(), key=lambda x: x[1], reverse=True)[:k]
    seeds: List[SearchNode] = []
    for nid, score in sorted_seeds:
        hit = seed_hits.get(nid, {})
        label = hit.get("label", "")
        props: Dict[str, Any] = {}

        # Try to get full node props from db
        try:
            node_obj = db.get_node(nid)
            if node_obj:
                label = getattr(node_obj, "label", label)
                props = getattr(node_obj, "properties", {}) or {}
        except Exception:
            pass

        if not props:
            props = {"name": hit.get("name", nid)}

        seeds.append(SearchNode(
            node_id=nid,
            label=label,
            props=props,
            score=score,
            distance=0,
        ))

    # 3. Hop traversal
    all_nodes, all_edges = hop_traverse(db, seeds, max_hops=max_hops, token_budget=token_budget)

    # 4. Feedback boosts (optional)
    feedback_boosts: Dict[str, float] = {}
    try:
        from contextsynapse.context.projection import get_feedback_boost
        for node in all_nodes:
            boost = get_feedback_boost(graph_name, node.node_id)
            if boost and boost != 1.0:
                feedback_boosts[node.node_id] = boost
    except Exception:
        logger.warning("[search] Feedback boost unavailable for graph %s", graph_name, exc_info=False)

    # 5. Normalize RRF scores to [0, 1] so relevance competes fairly with distance/quality
    # Raw RRF scores are in ~[0.003, 0.033] while distance scores are in [0.2, 1.0].
    # Without normalization, _W_RELEVANCE * rrf ≈ 0.013 vs _W_DISTANCE * distance ≈ 0.30,
    # making the relevance signal contribute ~2% of the composite score.
    if seed_scores:
        max_rrf = max(seed_scores.values())
        if max_rrf > 0:
            normalized_scores = {nid: s / max_rrf for nid, s in seed_scores.items()}
        else:
            normalized_scores = seed_scores
    else:
        normalized_scores = seed_scores

    # 6. Rerank with normalized relevance scores
    ranked = rerank(all_nodes, all_edges, original_scores=normalized_scores, feedback_boosts=feedback_boosts)

    # Return top k*2 nodes
    top_nodes = ranked[:k * 2]

    # Filter edges to only include those connecting returned nodes
    node_ids = {n.node_id for n in top_nodes}
    top_edges = [e for e in all_edges if e.source in node_ids and e.target in node_ids]

    elapsed = (time.time() - t0) * 1000
    return GraphSearchResult(
        nodes=top_nodes,
        edges=top_edges,
        seeds=[s.node_id for s in seeds],
        expanded_terms=eq.expanded_terms,
        stats={
            "ms": f"{elapsed:.1f}",
            "seeds": len(seeds),
            "total_nodes": len(all_nodes),
            "returned_nodes": len(top_nodes),
            "hops": max_hops,
            "retrieval_sources": ("bm25+vector" if vec_ranks else "bm25") if seed_scores else "none",
        },
    )
