"""Retrieval Quality Improvements -- entity boost, reranking, diversity.

Three quality layers applied after initial retrieval:

  1. Entity Boost   — passages linked to query entities via graph get boosted
  2. Cross-Encoder  — precise reranking of top candidates (optional, needs model)
  3. MMR Diversity   — remove near-duplicate passages, balance relevance + novelty

Usage:
    from contextsynapse.search.retrieval_quality import improve_retrieval

    # After initial search returns raw passages
    improved = improve_retrieval(
        passages=raw_passages,
        query="TCS revenue growth",
        graph=db,
        top_k=5,
    )
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# ── 1. Entity Boost ──

def _extract_entity_names(query: str, graph=None) -> Set[str]:
    """Extract entity names from query text using graph lookup."""
    names = set()
    if not graph:
        return names

    # Get all entity names from graph
    query_lower = query.lower()
    for ntype in ("Company", "Person", "Organization", "Sector", "Product", "Location"):
        try:
            node_ids = graph.get_nodes_by_type(ntype)
            for nid in node_ids:
                node = graph.get_node(nid)
                if node:
                    name = node.properties.get("name", "").lower()
                    if name and len(name) >= 2 and name in query_lower:
                        names.add(nid)
        except Exception:
            pass

    return names


def entity_boost(
    passages: List[Dict[str, Any]],
    query: str,
    graph=None,
    boost_factor: float = 1.5,
) -> List[Dict[str, Any]]:
    """Boost passages that are linked to entities mentioned in the query.

    If query mentions "TCS" and a passage has a MENTIONS edge to ent_TCS,
    that passage's score gets multiplied by boost_factor.
    """
    if not graph or not passages:
        return passages

    # Find entity IDs mentioned in query
    query_entities = _extract_entity_names(query, graph)
    if not query_entities:
        return passages

    # For each passage, check if it mentions any query entity
    for passage in passages:
        pid = passage.get("id", "")
        if not pid:
            continue

        # Check passage entities (may already be populated by search)
        passage_entities = set()
        if passage.get("entities"):
            passage_entities = {e.get("id", e) if isinstance(e, dict) else e
                                for e in passage["entities"]}
        else:
            # Look up graph edges
            try:
                neighbors = graph.get_neighbors(pid)
                passage_entities = {nid for nid, edge in neighbors
                                     if edge.edge_type == "MENTIONS"}
            except Exception:
                pass

        # Boost if passage mentions any query entity
        overlap = query_entities & passage_entities
        if overlap:
            old_score = passage.get("score", 0)
            passage["score"] = old_score * boost_factor
            passage["_entity_boost"] = True
            passage["_boosted_by"] = list(overlap)

    # Re-sort by score
    passages.sort(key=lambda p: p.get("score", 0), reverse=True)
    return passages


# ── 2. Cross-Encoder Reranking ──

def cross_encoder_rerank(
    passages: List[Dict[str, Any]],
    query: str,
    top_k: int = 5,
    model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
) -> List[Dict[str, Any]]:
    """Rerank passages using a cross-encoder model for precise scoring.

    Cross-encoders score (query, passage) pairs together, giving much
    more accurate relevance scores than bi-encoder dot products.

    Falls back to keyword overlap scoring if no model is available.
    """
    if not passages or len(passages) <= 1:
        return passages

    # Try cross-encoder model
    try:
        from sentence_transformers import CrossEncoder
        model = CrossEncoder(model_name)
        pairs = [(query, p.get("text", "")[:512]) for p in passages]
        scores = model.predict(pairs)
        for i, score in enumerate(scores):
            passages[i]["_ce_score"] = float(score)
            passages[i]["score"] = float(score)  # override with precise score
        passages.sort(key=lambda p: p.get("score", 0), reverse=True)
        return passages[:top_k]
    except ImportError:
        pass
    except Exception as e:
        logger.debug("Cross-encoder failed: %s, falling back to keyword rerank", e)

    # Fallback: keyword overlap reranking
    return _keyword_rerank(passages, query, top_k)


def _keyword_rerank(
    passages: List[Dict[str, Any]],
    query: str,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """Simple keyword overlap reranking (no model needed).

    Scores based on:
      - Exact phrase match (high boost)
      - Individual keyword coverage (% of query terms found)
      - Position boost (terms early in passage score higher)
    """
    query_lower = query.lower()
    query_terms = [t for t in re.split(r'\W+', query_lower) if len(t) > 2]

    if not query_terms:
        return passages[:top_k]

    for passage in passages:
        text = passage.get("text", "").lower()
        if not text:
            continue

        score = passage.get("score", 0)

        # Exact phrase match bonus
        if query_lower in text:
            score += 2.0

        # Keyword coverage: what % of query terms appear in passage?
        found = sum(1 for t in query_terms if t in text)
        coverage = found / len(query_terms)
        score += coverage * 1.5

        # Position bonus: if keywords appear in first 100 chars
        first_100 = text[:100]
        early_found = sum(1 for t in query_terms if t in first_100)
        score += early_found * 0.3

        passage["score"] = score

    passages.sort(key=lambda p: p.get("score", 0), reverse=True)
    return passages[:top_k]


# ── 3. MMR Diversity ──

def _text_similarity(text_a: str, text_b: str) -> float:
    """Fast jaccard similarity between two texts."""
    if not text_a or not text_b:
        return 0.0
    words_a = set(text_a.lower().split())
    words_b = set(text_b.lower().split())
    intersection = len(words_a & words_b)
    union = len(words_a | words_b)
    return intersection / union if union > 0 else 0.0


def mmr_diversity(
    passages: List[Dict[str, Any]],
    top_k: int = 5,
    lambda_param: float = 0.7,
    similarity_threshold: float = 0.6,
) -> List[Dict[str, Any]]:
    """Maximal Marginal Relevance -- balance relevance and diversity.

    Instead of returning the top-5 most relevant (which may be redundant),
    iteratively select passages that are relevant AND different from
    already-selected passages.

    Args:
        passages: Pre-ranked by relevance score.
        top_k: Number to select.
        lambda_param: 0=pure diversity, 1=pure relevance. Default 0.7.
        similarity_threshold: Skip passages too similar to already selected.
    """
    if len(passages) <= top_k:
        return passages

    selected = []
    remaining = list(passages)

    while len(selected) < top_k and remaining:
        best_idx = 0
        best_score = -float("inf")

        for i, candidate in enumerate(remaining):
            relevance = candidate.get("score", 0)

            # Max similarity to any already-selected passage
            if selected:
                max_sim = max(
                    _text_similarity(candidate.get("text", ""), s.get("text", ""))
                    for s in selected
                )
            else:
                max_sim = 0

            # MMR score: balance relevance and novelty
            mmr_score = lambda_param * relevance - (1 - lambda_param) * max_sim

            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = i

        candidate = remaining.pop(best_idx)

        # Skip if too similar to existing selections
        if selected:
            max_sim = max(
                _text_similarity(candidate.get("text", ""), s.get("text", ""))
                for s in selected
            )
            if max_sim > similarity_threshold:
                candidate["_skipped_similar"] = True
                continue

        selected.append(candidate)

    return selected


# ── Combined improvement pipeline ──

def improve_retrieval(
    passages: List[Dict[str, Any]],
    query: str,
    graph=None,
    top_k: int = 5,
    enable_entity_boost: bool = True,
    enable_rerank: bool = True,
    enable_diversity: bool = True,
    boost_factor: float = 1.5,
    lambda_param: float = 0.7,
) -> List[Dict[str, Any]]:
    """Apply all retrieval quality improvements in sequence.

    Pipeline:
      1. Entity boost (graph-aware relevance)
      2. Keyword reranking (precise scoring)
      3. MMR diversity (remove redundancy)

    Args:
        passages: Raw search results with {id, text, score, ...}
        query: Original search query.
        graph: Graph for entity lookup (optional).
        top_k: Final number of results.

    Returns:
        Improved, deduplicated, diverse top-k passages.
    """
    if not passages:
        return []

    # Work on copies to avoid mutating originals
    results = [dict(p) for p in passages]

    # Step 1: Entity boost
    if enable_entity_boost and graph:
        results = entity_boost(results, query, graph, boost_factor)

    # Step 2: Rerank (keyword overlap — fast, no model needed)
    if enable_rerank:
        results = _keyword_rerank(results, query, top_k=top_k * 2)

    # Step 3: MMR diversity
    if enable_diversity:
        results = mmr_diversity(results, top_k=top_k, lambda_param=lambda_param)

    return results[:top_k]
