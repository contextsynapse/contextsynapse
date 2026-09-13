"""
RAG Retrieval + Generation
==========================
Multiple retrieval strategies with LLM answer generation.
Used by both the dashboard RAG endpoint and agent tools.

Strategies:
- hybrid_retrieve: vector + keyword, merged by RRF
- graph_retrieve: BM25 + vector + entity extraction + dedup boosting
- plain_search: simple keyword matching, no LLM
"""

from __future__ import annotations

import logging
import re
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Set, Tuple

import math
import threading
from collections import deque
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from ..context.quality import QualityFilter, get_manifest

# ---------------------------------------------------------------------------
# Access tracking — background thread updates _access_count on retrieved nodes
# ---------------------------------------------------------------------------

_access_queue = deque()
_access_lock = threading.Lock()
_access_thread_started = False


def _track_access(db, node_ids):
    """Queue node access updates for background processing."""
    global _access_thread_started
    if not node_ids or not db:
        return
    _access_queue.append((db, list(node_ids)))
    if not _access_thread_started:
        with _access_lock:
            if not _access_thread_started:
                _access_thread_started = True
                t = threading.Thread(target=_access_worker, daemon=True)
                t.start()


def _access_worker():
    """Background thread that processes access tracking updates."""
    import time as _time
    while True:
        try:
            db, node_ids = _access_queue.popleft()
        except IndexError:
            _time.sleep(0.5)
            continue
        now_iso = datetime.now(timezone.utc).isoformat()
        for nid in node_ids:
            try:
                node = db.csr_adapter.get_node(nid) if hasattr(db, 'csr_adapter') else None
                if node:
                    props = node.properties or {}
                    db.csr_adapter.update_node_properties(nid, {
                        "_access_count": props.get("_access_count", 0) + 1,
                        "_last_accessed": now_iso,
                    })
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Keyword index cache — avoids re-scanning all nodes on every query
# ---------------------------------------------------------------------------

_CLEARANCE_LEVELS = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}

_keyword_index_cache: Dict[str, Tuple[float, Dict, Dict]] = {}  # graph_name → (timestamp, node_map, inverted_index)
_bm25_cache: Dict[str, Tuple[float, Any]] = {}  # graph_name → (timestamp, WhooshSearchEngine)
_KEYWORD_CACHE_TTL = 60.0  # seconds


def _get_bm25_engine(db, graph_name: str):
    """Get or build cached BM25 engine for a graph.

    Tries LMDB first (persistent, microsecond reads), falls back to Whoosh.
    """
    # LMDB fast-path — persistent BM25, no rebuild
    try:
        from .lmdb_index import get_lmdb_index
        lmdb_idx = get_lmdb_index(graph_name)
        stats = lmdb_idx.stats()
        if stats.get("nodes", 0) > 0:
            return lmdb_idx  # has search_bm25() method
    except Exception:
        pass

    # Whoosh fallback
    cache_key = graph_name or id(db)
    now = time.time()

    if cache_key in _bm25_cache:
        ts, engine = _bm25_cache[cache_key]
        if now - ts < _KEYWORD_CACHE_TTL:
            return engine

    try:
        from .whoosh_search import WhooshSearchEngine, WhooshConfig
        index_dir = f"contextcore_data/whoosh_index/{graph_name}" if graph_name else "contextcore_data/whoosh_index/default"
        engine = WhooshSearchEngine(WhooshConfig(index_dir=index_dir))
        engine.index_nodes_from_graph(db)
        _bm25_cache[cache_key] = (now, engine)
        logger.debug("[RAG] BM25 index built for '%s': %d documents", graph_name, len(engine._documents))
        return engine
    except Exception as e:
        logger.debug("[RAG] BM25 engine creation failed: %s", e)
        return None


def _build_keyword_index(db, graph_name: str):
    """Build or return cached keyword index for a graph."""
    cache_key = graph_name or id(db)
    now = time.time()

    if cache_key in _keyword_index_cache:
        ts, node_map, inv_idx = _keyword_index_cache[cache_key]
        if now - ts < _KEYWORD_CACHE_TTL:
            return node_map, inv_idx

    field_weights = {"name": 2.0, "title": 2.0, "content": 1.0, "statement": 1.5, "description": 1.0}
    node_map: Dict[str, Dict[str, Any]] = {}
    keyword_scored: Dict[str, Dict[str, float]] = {}  # term → {node_id: weight}

    try:
        all_nodes = db.get_all_nodes()
    except Exception:
        all_nodes = []

    # Agent-generated noise — exclude from keyword index entirely
    _NOISE = {"AgentThought", "AgentAction", "AgentMessage", "AgentPresence",
              "ExperimentRun", "PipelineRun", "VectorIndex", "BM25Index",
              "Session", "ContextRef", "Context", "Project"}

    for node in all_nodes:
        if isinstance(node, dict):
            props = node.get("properties", {})
            node_id = str(node.get("id", ""))
            node_label = node.get("label", "")
        else:
            props = getattr(node, "properties", {}) or {}
            node_id = str(getattr(node, "id", ""))
            node_label = getattr(node, "label", "")

        if node_label in _NOISE:
            continue  # never index agent noise

        if props.get("_playground_generated"):
            continue  # exclude playground/experiment-generated nodes from search

        node_map[node_id] = {"props": props, "label": node_label}

        # Resolve text for vector-ref nodes (no content on node)
        resolved_content = props.get("content") or ""
        if not resolved_content and props.get("vector_ref"):
            try:
                from .text_resolver import resolve_text
                resolved_content = resolve_text(node_id, props, graph_name)
            except Exception:
                pass

        # Build inverted index: term → {node_id: total_weight}
        for field, weight in field_weights.items():
            if field == "content" and resolved_content and not props.get("content"):
                val = resolved_content.lower()
            else:
                val = str(props.get(field, "")).lower()
            if not val:
                continue
            terms = set(val.split())
            for term in terms:
                if len(term) < 2:
                    continue
                if term not in keyword_scored:
                    keyword_scored[term] = {}
                keyword_scored[term][node_id] = keyword_scored[term].get(node_id, 0) + weight

    _keyword_index_cache[cache_key] = (now, node_map, keyword_scored)
    logger.debug("[RAG] Built keyword index for '%s': %d nodes, %d terms", graph_name, len(node_map), len(keyword_scored))
    return node_map, keyword_scored


# ---------------------------------------------------------------------------
# Stop words — shared by hybrid_retrieve, topic_scan, and plain_search
# ---------------------------------------------------------------------------

_STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "what", "which", "who", "whom", "this", "that", "these", "those",
    "and", "but", "or", "nor", "not", "so", "yet", "for", "with",
    "about", "between", "through", "during", "before", "after",
    "above", "below", "from", "into", "out", "off", "over", "under",
    "again", "further", "then", "once", "here", "there", "when",
    "where", "how", "all", "both", "each", "few", "more", "most",
    "other", "some", "such", "than", "too", "very", "just", "only",
    "own", "same", "also", "latest", "tell", "give", "show",
    "find", "get", "list", "on", "in", "at", "to", "of", "by", "up",
    # URL / data noise
    "https", "http", "www", "com", "org", "net", "html", "php",
    # Common filler
    "said", "new", "one", "two", "also", "would", "like", "many",
    "according", "made", "first", "last", "since", "still", "even",
    "says", "say", "told", "added", "noted", "reported",
}


# ---------------------------------------------------------------------------
# Topic Scan — cluster graph content into topics (no LLM needed)
# ---------------------------------------------------------------------------

def topic_scan(
    db,
    graph_name: str = "",
    max_topics: int = 10,
    samples_per_topic: int = 5,
) -> Dict[str, Any]:
    """Scan graph content and cluster into topics by keyword frequency.

    Pure Python — no LLM calls. Reuses the cached keyword index.
    Returns structured dict with topic names, counts, and sample node names.
    """
    t0 = time.time()
    node_map, inv_index = _build_keyword_index(db, graph_name)

    total_nodes = len(node_map)

    # Early exit: very small graph — just list all node names
    if total_nodes < 10:
        samples = []
        for nid, info in node_map.items():
            name = info["props"].get("name") or info["props"].get("title") or info["label"]
            samples.append({"name": str(name)[:60], "label": info["label"]})
        return {
            "topics": [{"name": "all", "count": total_nodes, "samples": [s["name"] for s in samples]}],
            "total_content_nodes": total_nodes,
            "scan_ms": int((time.time() - t0) * 1000),
        }

    # Compute document frequency per term (normalize: strip punctuation)
    doc_freq: Dict[str, int] = {}
    term_to_nodes: Dict[str, Dict[str, float]] = {}
    for raw_term, node_weights in inv_index.items():
        term = raw_term.strip(".,;:!?()[]{}\"'").lower()
        if term in _STOP_WORDS or len(term) < 3:
            continue
        if term.isdigit() or not term.isalpha():
            continue
        # Merge variants (e.g. "india" and "india.")
        if term not in doc_freq:
            doc_freq[term] = 0
            term_to_nodes[term] = {}
        doc_freq[term] += len(node_weights)
        term_to_nodes[term].update(node_weights)
    # Recount with merged node sets
    for term in doc_freq:
        doc_freq[term] = len(term_to_nodes[term])

    # Sort by frequency, take top 50 candidates
    candidates = sorted(doc_freq.items(), key=lambda x: -x[1])[:50]

    # Deduplicate: skip terms whose node sets overlap >70% with an accepted topic
    accepted = []
    accepted_sets: List[Set[str]] = []

    for term, freq in candidates:
        if len(accepted) >= max_topics:
            break
        term_node_weights = term_to_nodes.get(term, {})
        term_nodes = set(term_node_weights.keys())

        # Check overlap with existing topics
        is_dup = False
        for existing_set in accepted_sets:
            overlap = len(term_nodes & existing_set)
            smaller = min(len(term_nodes), len(existing_set))
            if smaller > 0 and overlap / smaller > 0.7:
                is_dup = True
                break

        if not is_dup:
            # Pick best sample nodes (highest weight for this term)
            sorted_nodes = sorted(term_node_weights.items(), key=lambda x: -x[1])
            samples = []
            for nid, weight in sorted_nodes[:samples_per_topic]:
                info = node_map.get(nid)
                if info:
                    name = info["props"].get("name") or info["props"].get("title") or ""
                    if name:
                        samples.append(str(name)[:60])
            if samples or samples_per_topic == 0:  # accept if samples found or not requested
                accepted.append({"name": term, "count": freq, "samples": samples})
                accepted_sets.append(term_nodes)

    return {
        "topics": accepted,
        "total_content_nodes": total_nodes,
        "scan_ms": int((time.time() - t0) * 1000),
    }


def topic_scan_text(
    db,
    graph_name: str = "",
    max_topics: int = 10,
    samples_per_topic: int = 3,
) -> str:
    """Return a formatted text summary of graph topics for prompt injection.

    Capped at ~1500 tokens. Ready to paste into an LLM prompt.
    """
    result = topic_scan(db, graph_name, max_topics, samples_per_topic)
    topics = result["topics"]
    total = result["total_content_nodes"]

    if not topics:
        return f"DATA TOPICS: No content nodes found in graph."

    lines = [f"DATA TOPICS ({total} content nodes across {len(topics)} topics):\n"]
    for t in topics:
        samples_str = ", ".join(f'"{s}"' for s in t["samples"][:samples_per_topic])
        lines.append(f"- {t['name']} ({t['count']} nodes): {samples_str}")

    text = "\n".join(lines)
    # Hard cap at ~6000 chars (~1500 tokens)
    if len(text) > 6000:
        text = text[:5950] + "\n... (truncated)"
    return text


def hybrid_retrieve(
    db,
    question: str,
    graph_name: Optional[str] = None,
    k: int = 8,
    skip_vector: bool = False,
    agent_clearance: str = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Hybrid retrieval: keyword + BM25 + optional vector, merged by RRF, with graph expansion.

    Args:
        skip_vector: If True, skip the vector search step (fast mode, ~500ms).
                     If False, include vector search (~6-12s with Ollama embedding).

    Returns:
        (top_results, sources) where each result has {node_id, label, props, score, signals}
    """
    # Filter stop words and short terms for better precision
    _stop = {"the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
             "have", "has", "had", "do", "does", "did", "will", "would", "could",
             "should", "may", "might", "shall", "can", "need", "dare", "ought",
             "what", "which", "who", "whom", "this", "that", "these", "those",
             "and", "but", "or", "nor", "not", "so", "yet", "for", "with",
             "about", "between", "through", "during", "before", "after",
             "above", "below", "from", "into", "out", "off", "over", "under",
             "again", "further", "then", "once", "here", "there", "when",
             "where", "how", "all", "both", "each", "few", "more", "most",
             "other", "some", "such", "than", "too", "very", "just", "only",
             "own", "same", "also", "latest", "latest", "tell", "give", "show",
             "find", "get", "list", "on", "in", "at", "to", "of", "by", "up"}
    query_terms = [w for w in question.lower().split() if w not in _stop and len(w) > 2]
    if not query_terms:
        # If all terms were stop words, use the original but longer terms only
        query_terms = [w for w in question.lower().split() if len(w) > 3]
    if not query_terms:
        return [], []

    # -- Vector retrieval (skip if fast mode) --
    vector_results: Dict[str, int] = {}
    vector_metadata: Dict[str, Dict] = {}
    if not skip_vector:
        try:
            from contextsynapse.context.vector_integration import SessionVectorStore
            svs = get_session_vector_store()
            ns = graph_name or getattr(db, "name", "")
            if ns and svs.available:
                hits = svs.search(ns, question, k=k)
                for rank, hit in enumerate(hits):
                    nid = hit.get("node_id", "")
                    vector_results[nid] = rank + 1
                    meta = hit.get("metadata", {})
                    if meta:
                        vector_metadata[nid] = meta
        except Exception as e:
            logger.debug("Vector retrieval skipped: %s", e)

    # -- Keyword retrieval (cached inverted index) --
    ns = graph_name or getattr(db, "name", "")
    node_map, inv_index = _build_keyword_index(db, ns)

    # Score nodes by query terms using the inverted index
    # Track both total weight AND number of distinct terms matched per node
    node_scores: Dict[str, float] = {}
    node_term_hits: Dict[str, int] = {}  # how many distinct query terms matched
    for term in query_terms:
        if term in inv_index:
            for nid, weight in inv_index[term].items():
                node_scores[nid] = node_scores.get(nid, 0) + weight
                node_term_hits[nid] = node_term_hits.get(nid, 0) + 1

    # Boost nodes that match multiple query terms (precision boost)
    num_terms = len(query_terms)
    if num_terms > 1:
        for nid in node_scores:
            hits = node_term_hits.get(nid, 1)
            # Boost: matching 2/3 terms gets 2x, matching 3/3 gets 3x
            node_scores[nid] *= (hits / num_terms) * hits

    # Temporal recency boost — newer nodes rank higher (no timeframe = default decay)
    import math
    from datetime import datetime, timezone
    _now = datetime.now(timezone.utc)
    _HALF_LIFE = 72  # hours — score halves every 3 days
    for nid in node_scores:
        info = node_map.get(nid)
        if info:
            created = info["props"].get("_created_at", "")
            if created:
                try:
                    ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    age_h = max(0.01, (_now - ts).total_seconds() / 3600)
                    boost = 1.0 + math.exp(-0.693 * age_h / _HALF_LIFE)
                    node_scores[nid] *= boost
                except Exception:
                    pass

    keyword_scored = sorted(node_scores.items(), key=lambda x: x[1], reverse=True)

    # Normalize keyword scores
    if keyword_scored:
        max_s = keyword_scored[0][1]
        if max_s > 0:
            keyword_scored = [(nid, s / max_s) for nid, s in keyword_scored]
    keyword_ranks = {nid: rank + 1 for rank, (nid, _) in enumerate(keyword_scored)}

    # -- BM25 retrieval (Whoosh) --
    bm25_ranks: Dict[str, int] = {}
    try:
        bm25_engine = _get_bm25_engine(db, graph_name or getattr(db, "name", ""))
        if bm25_engine:
            # LMDB index returns list directly; Whoosh returns {"results": [...]}
            from .lmdb_index import LMDBIndex
            if isinstance(bm25_engine, LMDBIndex):
                bm25_hits = bm25_engine.search_bm25(question, limit=k * 2)
                for rank, hit in enumerate(bm25_hits):
                    nid = hit.get("node_id", "")
                    if nid:
                        bm25_ranks[nid] = rank + 1
            else:
                bm25_results = bm25_engine.search(question, limit=k * 2)
                for rank, hit in enumerate(bm25_results.get("results", [])):
                    nid = hit.get("id", "")
                    if nid:
                        bm25_ranks[nid] = rank + 1
    except Exception as e:
        logger.debug("BM25 retrieval skipped: %s", e)

    # -- Reciprocal Rank Fusion (RRF) — merge 3 signals --
    rrf_k = 60
    all_candidates = set(vector_results.keys()) | set(keyword_ranks.keys()) | set(bm25_ranks.keys())
    fused = []
    for nid in all_candidates:
        rrf_score = 0.0
        signals = 0
        if nid in vector_results:
            rrf_score += 1.0 / (rrf_k + vector_results[nid])
            signals += 1
        if nid in keyword_ranks:
            rrf_score += 1.0 / (rrf_k + keyword_ranks[nid])
            signals += 1
        if nid in bm25_ranks:
            rrf_score += 1.0 / (rrf_k + bm25_ranks[nid])
            signals += 1
        fused.append((nid, rrf_score, signals))

    fused.sort(key=lambda x: x[1], reverse=True)
    fused_dict = {nid: score for nid, score, _ in fused}
    fused_signals = {nid: signals for nid, _, signals in fused}

    # Build result — use keyword index data, fall back to vector metadata
    top = []
    for nid, _, _ in fused[:k]:
        info = node_map.get(nid)
        if info:
            top.append({
                "node_id": nid,
                "label": info.get("label", ""),
                "props": info.get("props", {}),
                "score": round(fused_dict.get(nid, 0), 4),
                "signals": fused_signals.get(nid, 0),
            })
        elif nid in vector_metadata:
            meta = vector_metadata[nid]
            top.append({
                "node_id": nid,
                "label": meta.get("label", ""),
                "props": {"name": meta.get("name", ""), "content": meta.get("text", "")},
                "score": round(fused_dict.get(nid, 0), 4),
                "signals": fused_signals.get(nid, 0),
            })

    # Usage boost: logarithmic, applied post-RRF
    for r in top:
        access_count = r.get("props", {}).get("_access_count", 0)
        if access_count > 0:
            usage_boost = 1.0 + math.log(1 + access_count) * 0.1
            r["score"] = round(r["score"] * usage_boost, 4)
    top.sort(key=lambda r: r["score"], reverse=True)

    # Clearance filtering — exclude nodes above agent's clearance level
    if agent_clearance:
        max_level = _CLEARANCE_LEVELS.get(agent_clearance, 0)
        top = [r for r in top if _CLEARANCE_LEVELS.get(
            r.get("props", {}).get("_sensitivity", "public"), 0
        ) <= max_level]

    # Normalize scores for display
    if top:
        max_rrf = max(r["score"] for r in top)
        if max_rrf > 0:
            for r in top:
                r["score"] = round(r["score"] / max_rrf, 3)

    sources = [
        {
            "node_id": r["node_id"],
            "label": r["props"].get("name") or r["props"].get("title") or r["label"],
            "score": r["score"],
            "node_type": r["label"],
        }
        for r in top
    ]

    if top:
        _track_access(db, [r["node_id"] for r in top])

    # Quality gate — filter, dedup, rank
    try:
        graph_name = getattr(db, '_namespace', '') or 'default'
        redis_client = getattr(db, '_redis', None)
        manifest = get_manifest(graph_name, redis_client)
        ctx_quality = manifest.get("context_quality", 50) if manifest else 50
        content_type = manifest.get("content_type", "mixed") if manifest else "mixed"
        adapter = getattr(db, 'csr_adapter', None) or db
        for r in top:
            if "_quality" not in r:
                try:
                    node = adapter.get_node(r["node_id"])
                    r["_quality"] = (node.properties or {}).get("_quality", 50) if node else 50
                except Exception:
                    r["_quality"] = 50
        top = QualityFilter.apply(top, context_quality=ctx_quality,
                                   content_type=content_type, max_per_label=5)
    except Exception as e:
        logger.debug("Quality filter skipped: %s", e)

    return top, sources


def rerank(
    question: str,
    candidates: List[Dict[str, Any]],
    k: int = 5,
) -> List[Dict[str, Any]]:
    """Rerank retrieval candidates using cross-encoder or LLM scoring.

    Strategy (fast → slow):
    1. Cross-encoder model (local, ~50ms for 15 candidates) — if available
    2. Embedding cosine similarity (local, ~100ms) — fast fallback
    3. LLM scoring (remote, ~2s) — last resort
    4. Original RRF order — if everything fails
    """
    if not candidates or len(candidates) <= 2:
        return candidates[:k]

    # Build text pairs for scoring
    def _get_text(c):
        props = c.get("props", c.get("properties", {}))
        title = props.get("name") or props.get("title") or c.get("label", "")
        content = (props.get("content") or props.get("description")
                   or props.get("statement") or props.get("snippet") or "")[:300]
        return f"{title} {content}".strip()

    texts = [_get_text(c) for c in candidates[:20]]

    # Strategy 1: Cross-encoder (fastest, most accurate)
    scored = _rerank_cross_encoder(question, texts, candidates[:20])
    if scored:
        return scored[:k]

    # Strategy 2: Embedding cosine similarity
    scored = _rerank_embedding(question, texts, candidates[:20])
    if scored:
        return scored[:k]

    # Strategy 3: LLM scoring (slow but available)
    scored = _rerank_llm(question, texts, candidates[:15])
    if scored:
        return scored[:k]

    return candidates[:k]


def _rerank_cross_encoder(question: str, texts: List[str], candidates: List[Dict]) -> Optional[List[Dict]]:
    """Rerank using a cross-encoder model (e.g., sentence-transformers)."""
    try:
        from sentence_transformers import CrossEncoder
        # Cache the model
        if not hasattr(_rerank_cross_encoder, '_model'):
            _rerank_cross_encoder._model = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')
            logger.info("[RERANK] Cross-encoder loaded: ms-marco-MiniLM-L-6-v2")

        model = _rerank_cross_encoder._model
        pairs = [(question, text) for text in texts]
        scores = model.predict(pairs)

        scored = sorted(zip(scores, candidates), key=lambda x: x[0], reverse=True)
        return [c for _, c in scored]
    except ImportError:
        return None  # sentence-transformers not installed
    except Exception as e:
        logger.debug("[RERANK] Cross-encoder failed: %s", e)
        return None


def _rerank_embedding(question: str, texts: List[str], candidates: List[Dict]) -> Optional[List[Dict]]:
    """Rerank using embedding cosine similarity (uses cached embeddings)."""
    try:
        from ..search.embedding_cache import get_embedding_cache
        from ..context.vector_integration import get_session_vector_store

        svs = get_session_vector_store()
        if not svs or not svs.available:
            return None

        # Embed the query
        query_vec = svs.embed_text(question)
        if not query_vec:
            return None

        import numpy as np
        q = np.array(query_vec, dtype=np.float32)
        q_norm = q / (np.linalg.norm(q) + 1e-12)

        scores = []
        cache = get_embedding_cache()
        for text in texts:
            cached = cache.get(text) if cache else None
            if cached:
                t = np.array(cached, dtype=np.float32)
                t_norm = t / (np.linalg.norm(t) + 1e-12)
                score = float(np.dot(q_norm, t_norm))
            else:
                score = 0.0  # No embedding — neutral score
            scores.append(score)

        scored = sorted(zip(scores, candidates), key=lambda x: x[0], reverse=True)
        return [c for _, c in scored]
    except Exception as e:
        logger.debug("[RERANK] Embedding rerank failed: %s", e)
        return None


def _rerank_llm(question: str, texts: List[str], candidates: List[Dict]) -> Optional[List[Dict]]:
    """Rerank using LLM scoring (slowest, most expensive)."""
    try:
        from contextsynapse.llm.client import LLMClient
        llm = LLMClient()

        items = [f"[{i}] {text[:200]}" for i, text in enumerate(texts)]
        prompt = (
            f"Rate each document's relevance to the question on a scale of 0-10.\n"
            f"Return ONLY a comma-separated list of scores.\n\n"
            f"Question: {question}\n\n"
            f"Documents:\n" + "\n".join(items)
        )

        result = llm.generate(prompt, system="Output only comma-separated numbers (0-10).", max_tokens=100)
        if result and result.strip():
            scores = [float(s) for s in re.findall(r"[\d.]+", result)]
            if len(scores) >= len(candidates):
                scored = sorted(zip(scores, candidates), key=lambda x: x[0], reverse=True)
                return [c for _, c in scored]
    except Exception as e:
        logger.debug("[RERANK] LLM rerank failed: %s", e)
    return None


def generate_answer(
    question: str,
    top: List[Dict[str, Any]],
    sources: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Generate answer from retrieved context using LLM.

    Skips the expensive reranking LLM call — RRF fusion is good enough.
    Caps context per source to 500 chars to keep the prompt small and fast.
    """
    # Resolve text for all top results (handles vector-ref nodes)
    from .text_resolver import resolve_texts
    graph_name = ""  # try to infer from sources
    for r in top[:1]:
        graph_name = r.get("graph_name", "")
    text_map = resolve_texts(
        [(r.get("node_id", ""), r.get("props", {})) for r in top[:5]],
        graph_name,
    )

    context_parts = []
    for r in top[:5]:
        nid = r.get("node_id", "")
        content = text_map.get(nid, r["props"].get("name", ""))
        source_label = r["props"].get("name") or r["props"].get("title") or r["label"]
        if content:
            part = f"[Source: {source_label}]\n{content[:500]}"
            # Add connected entities as enrichment
            entities = r.get("entities", [])
            if entities:
                entity_names = [f"{e['name']} ({e['label']})" for e in entities[:5] if e.get("name")]
                if entity_names:
                    part += f"\nRelated: {', '.join(entity_names)}"
            context_parts.append(part)

    context_block = "\n\n---\n\n".join(context_parts)

    prompt = (
        f"Answer the following question based ONLY on the provided context. "
        f"If the context doesn't contain enough information, say so. "
        f"Be concise and cite source names.\n\n"
        f"## Context\n\n{context_block}\n\n"
        f"## Question\n\n{question}\n\n## Answer\n\n"
    )
    system = "You are a helpful assistant. Answer using ONLY the provided context. Be concise."

    # Try LLM with rate limiting
    llm_errors = []
    try:
        from contextsynapse.llm.rate_limiter import get_limiter
        limiter = get_limiter()
    except Exception:
        limiter = None

    _backends = [
        ("get_llm_client", lambda: __import__("contextsynapse.llm", fromlist=["get_llm_client"]).get_llm_client()),
        ("LLMClient", lambda: __import__("contextsynapse.llm.client", fromlist=["LLMClient"]).LLMClient()),
    ]
    for name, factory in _backends:
        try:
            llm = factory()
            provider = getattr(llm, "provider", name)
            # Acquire rate limit slot
            if limiter and not limiter.acquire(provider, timeout=15):
                llm_errors.append(f"{name}: rate limited")
                continue
            try:
                if name == "get_llm_client":
                    answer = llm.generate(prompt=prompt, max_tokens=500)
                else:
                    answer = llm.generate(prompt, system=system)
                if answer and answer.strip():
                    return {"answer": answer.strip(), "sources": sources}
            finally:
                if limiter:
                    limiter.release(provider)
        except Exception as e:
            llm_errors.append(f"{name}: {e}")
            continue

    logger.warning("[RAG] All LLM backends failed: %s", "; ".join(llm_errors))

    # Fallback: return structured context without LLM synthesis
    context_lines = []
    for r in top[:5]:
        content = r["props"].get("statement") or r["props"].get("content") or r["props"].get("name", "")
        if content:
            context_lines.append(f"• {content[:200]}")

    return {
        "answer": f"Found {len(top)} relevant items (LLM unavailable for synthesis):\n\n" + "\n".join(context_lines),
        "sources": sources,
        "llm_errors": llm_errors,
    }


# ======================================================================
# Label classification for search
# ======================================================================

# Content nodes — entry points for vector + BM25 search
CONTENT_LABELS = {"Passage", "TextChunk", "Fact", "Document", "Knowledge",
                  "Feature", "Requirement", "Decision", "Finding", "Insight"}

# Agent activity — excluded from search results
AGENT_LABELS = {"AgentAction", "ExperimentRun", "Connector", "SyncRun",
                "Memory", "ProjectContext"}

# Entity nodes — found via graph expansion, not direct search
ENTITY_LABELS = {"Person", "Organization", "Technology", "Concept",
                 "Event", "Location", "Topic"}


# ======================================================================
# graph_retrieve — Vector-First + BM25-First + Graph Expansion
# ======================================================================

def graph_retrieve(
    db,
    question: str,
    graph_name: Optional[str] = None,
    k: int = 10,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Graph-native retrieval delegating to :func:`graph_search`.

    Wraps the new ``graph_search`` pipeline and converts its
    :class:`GraphSearchResult` back to the legacy ``(top, sources)``
    tuple format expected by callers.

    Returns:
        (top_results, sources)
    """
    from .graph_search import graph_search

    ns = graph_name or getattr(db, "name", "")

    # ── Delegate to graph_search ──
    result = graph_search(db, question, graph_name=ns, k=k)

    # ── Convert GraphSearchResult → (top, sources) ──
    top: List[Dict[str, Any]] = []
    for sn in result.nodes:
        top.append({
            "node_id": sn.node_id,
            "label": sn.label,
            "props": sn.props,
            "score": round(sn.score, 4),
            "signals": len(sn.props.get("_signals", {})) if isinstance(sn.props.get("_signals"), dict) else 1,
            "signals_used": list(sn.props.get("_signals", {}).keys()) if isinstance(sn.props.get("_signals"), dict) else [],
            "graph_name": ns,
            "distance": sn.distance,
            "via_edge": sn.via_edge,
            "entities": [],
        })

    # Attach expanded entities (distance > 0 nodes linked to seed nodes)
    seed_ids = set(result.seeds)
    entity_nodes = [sn for sn in result.nodes if sn.distance > 0 and sn.label in ENTITY_LABELS]
    if entity_nodes:
        # Build edge lookup: target → (source, edge_type)
        edge_map: Dict[str, List[Tuple[str, str]]] = {}
        for e in result.edges:
            edge_map.setdefault(e.target, []).append((e.source, e.edge_type))
            edge_map.setdefault(e.source, []).append((e.target, e.edge_type))
        for en in entity_nodes:
            parents = edge_map.get(en.node_id, [])
            for parent_id, edge_type in parents:
                if parent_id in seed_ids:
                    for r in top:
                        if r["node_id"] == parent_id:
                            r["entities"].append({
                                "node_id": en.node_id,
                                "label": en.label,
                                "name": en.props.get("name", ""),
                                "edge": edge_type,
                                "from_node": parent_id,
                            })
                            break

    # Normalize scores
    if top:
        max_s = max(r["score"] for r in top)
        if max_s > 0:
            for r in top:
                r["score"] = round(r["score"] / max_s, 3)

    sources = [
        {
            "node_id": r["node_id"],
            "label": r["props"].get("name") or r["props"].get("title") or r["label"],
            "score": r["score"],
            "node_type": r["label"],
            "signals": r.get("signals", 1),
        }
        for r in top
    ]

    # Track access
    if top:
        _track_access(db, [r["node_id"] for r in top])

    # Quality gate — filter, dedup, rank
    try:
        graph_name_q = getattr(db, '_namespace', '') or 'default'
        redis_client_q = getattr(db, '_redis', None)
        manifest_q = get_manifest(graph_name_q, redis_client_q)
        ctx_quality_q = manifest_q.get("context_quality", 50) if manifest_q else 50
        content_type_q = manifest_q.get("content_type", "mixed") if manifest_q else "mixed"
        adapter_q = getattr(db, 'csr_adapter', None) or db
        for r in top:
            if "_quality" not in r:
                try:
                    node = adapter_q.get_node(r["node_id"])
                    r["_quality"] = (node.properties or {}).get("_quality", 50) if node else 50
                except Exception:
                    r["_quality"] = 50
        top = QualityFilter.apply(top, context_quality=ctx_quality_q,
                                   content_type=content_type_q, max_per_label=5)
    except Exception as e:
        logger.debug("Quality filter skipped: %s", e)

    return top, sources


def _extract_query_entities(question: str) -> List[str]:
    """Extract entity-like terms from the question for graph matching.

    Uses fast regex extraction — no LLM call needed for entity detection.
    """
    entities = []
    # Quoted terms
    for match in re.findall(r'"([^"]+)"', question):
        entities.append(match.lower())
    # Capitalized words (likely proper nouns)
    for word in question.split():
        if word[0].isupper() and len(word) > 2 and word.lower() not in (
            "what", "which", "where", "when", "who", "how", "the", "are", "was",
            "show", "find", "get", "list", "tell", "give", "can", "does", "did",
        ):
            entities.append(word.lower())
    return entities


# ======================================================================
# plain_search — Simple keyword matching, no LLM
# ======================================================================

def plain_search(
    db,
    query: str,
    k: int = 20,
    agent_clearance: str = None,
) -> List[Dict[str, Any]]:
    """Simple keyword search across graph nodes. No LLM, no vectors.

    Returns list of {node_id, label, name, snippet, score} sorted by relevance.
    """
    query_terms = [w for w in query.lower().split() if len(w) > 2]
    if not query_terms:
        return []

    # LMDB fast-path — persistent index, microsecond reads
    ns = getattr(db, "name", "")
    try:
        from .lmdb_index import get_lmdb_index
        lmdb_idx = get_lmdb_index(ns)
        stats = lmdb_idx.stats()
        if stats.get("nodes", 0) > 0:
            lmdb_results = lmdb_idx.search(query, limit=k)
            if lmdb_results:
                # Convert to expected format
                return [{
                    "node_id": r["node_id"], "label": r["label"],
                    "name": r["name"], "snippet": r.get("snippet", ""),
                    "content": r.get("snippet", ""), "score": r["score"],
                } for r in lmdb_results]
    except Exception:
        pass  # fall through to in-memory index

    # Fallback: in-memory keyword index
    node_map, inv_index = _build_keyword_index(db, ns)

    # Score via inverted index
    node_scores: Dict[str, float] = {}
    for term in query_terms:
        if term in inv_index:
            for nid, weight in inv_index[term].items():
                node_scores[nid] = node_scores.get(nid, 0) + weight

    # Compute temporal recency boost — newer nodes rank higher
    import math
    from datetime import datetime, timezone
    _now = datetime.now(timezone.utc)
    _RECENCY_HALF_LIFE_HOURS = 72  # score halves every 3 days

    results = []
    for nid, score in sorted(node_scores.items(), key=lambda x: x[1], reverse=True):
        info = node_map.get(nid)
        if not info:
            continue
        props = info["props"]
        if props.get("_playground_generated"):
            continue  # exclude playground/experiment-generated nodes
        name = props.get("name") or props.get("title") or info["label"]

        # Temporal recency boost: newer = higher multiplier
        recency_boost = 1.0
        created_at = props.get("_created_at", "")
        if created_at:
            try:
                ts = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                age_hours = max(0.01, (_now - ts).total_seconds() / 3600)
                # Exponential decay: score halves every HALF_LIFE hours
                recency_boost = 1.0 + math.exp(-0.693 * age_hours / _RECENCY_HALF_LIFE_HOURS)
                # Range: ~2.0 for brand new, ~1.0 for very old
            except Exception:
                pass

        # Quality boost based on _quality_score (set by graph_intelligence on ingest)
        # Source data (Fact=0.8, Passage=0.85, Document=1.0) naturally ranks higher
        # Agent data (Finding=0.5, Insight=0.4) ranks lower unless it has good content
        quality = props.get("_quality_score", 0.5)

        # Boost if node has verifiable source attribution
        if props.get("source") or props.get("source_url"):
            quality = min(1.0, quality + 0.15)

        # Penalize if content is generic (short, no numbers/names)
        content_text = str(props.get("content", "")) or str(props.get("statement", ""))
        if content_text:
            has_numbers = any(c.isdigit() for c in content_text)
            is_short = len(content_text) < 80
            if has_numbers:
                quality = min(1.0, quality + 0.1)  # specific data = better
            if is_short and not has_numbers:
                quality = max(0.1, quality - 0.1)  # short + no data = worse

        quality_boost = 0.5 + quality  # range 0.6–1.5

        # Content richness boost: nodes with real content rank higher than name-only
        content_len = len(str(props.get("content", ""))) + len(str(props.get("statement", "")))
        content_boost = 1.0
        if content_len > 200:
            content_boost = 1.3  # long content = more useful
        elif content_len > 50:
            content_boost = 1.15

        # Usage boost: logarithmic, caps ~1.3x for heavily used nodes
        access_count = props.get("_access_count", 0)
        usage_boost = 1.0 + math.log(1 + access_count) * 0.1 if access_count > 0 else 1.0

        boosted_score = score * recency_boost * quality_boost * content_boost * usage_boost

        # Build snippet from content
        snippet = ""
        for field in ("content", "statement", "description", "name"):
            val = str(props.get(field, ""))
            for term in query_terms:
                idx = val.lower().find(term)
                if idx >= 0:
                    start = max(0, idx - 40)
                    end = min(len(val), idx + len(term) + 60)
                    snippet = ("..." if start > 0 else "") + val[start:end] + ("..." if end < len(val) else "")
                    break
            if snippet:
                break

        # For content-rich nodes, include more of the content as snippet
        if not snippet and content_len > 0:
            content_text = str(props.get("content", "")) or str(props.get("statement", ""))
            snippet = content_text[:150]

        results.append({
            "node_id": nid,
            "label": info["label"],
            "name": name,
            "snippet": snippet[:250],
            "score": round(boosted_score, 2),
        })

    # Sort: source data first, then agent data, each group sorted by score
    _SOURCE = {"Fact", "Passage", "Document", "TextChunk", "WebPage", "Entity",
               "Organization", "Claim", "Statistic", "Article", "Author", "Date"}
    source_results = [r for r in results if r["label"] in _SOURCE]
    agent_results = [r for r in results if r["label"] not in _SOURCE]
    source_results.sort(key=lambda x: x["score"], reverse=True)
    agent_results.sort(key=lambda x: x["score"], reverse=True)
    results = source_results + agent_results

    # Clearance filtering — exclude nodes above agent's clearance level
    if agent_clearance:
        max_level = _CLEARANCE_LEVELS.get(agent_clearance, 0)
        results = [r for r in results if _CLEARANCE_LEVELS.get(
            node_map.get(r["node_id"], {}).get("props", {}).get("_sensitivity", "public"), 0
        ) <= max_level]

    # Normalize
    if results:
        max_s = results[0]["score"]
        if max_s > 0:
            for r in results:
                r["score"] = round(r["score"] / max_s, 3)

    if results:
        _track_access(db, [r["node_id"] for r in results[:k]])

    # Quality gate — filter, dedup, rank
    try:
        graph_name_ps = getattr(db, '_namespace', '') or 'default'
        redis_client_ps = getattr(db, '_redis', None)
        manifest_ps = get_manifest(graph_name_ps, redis_client_ps)
        ctx_quality_ps = manifest_ps.get("context_quality", 50) if manifest_ps else 50
        content_type_ps = manifest_ps.get("content_type", "mixed") if manifest_ps else "mixed"
        adapter_ps = getattr(db, 'csr_adapter', None) or db
        for r in results:
            if "_quality" not in r:
                try:
                    node = adapter_ps.get_node(r["node_id"])
                    r["_quality"] = (node.properties or {}).get("_quality", 50) if node else 50
                except Exception:
                    r["_quality"] = 50
        results = QualityFilter.apply(results, context_quality=ctx_quality_ps,
                                       content_type=content_type_ps, max_per_label=5)
    except Exception as e:
        logger.debug("Quality filter skipped: %s", e)

    return results[:k]


# ======================================================================
# parallel_retrieve — 4-signal retrieval (keyword + BM25 + vector + graph)
# ======================================================================

def parallel_retrieve(
    db,
    query: str,
    graph_name: str = "",
    k: int = 15,
) -> List[Dict[str, Any]]:
    """4-signal parallel retrieval: keyword + BM25 + vector + entity graph traversal.

    All signals run in parallel threads. Results merged via RRF.
    Nodes found by multiple signals rank highest.

    Returns list of {node_id, label, name, content, score, signals, signal_names}
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import re as _re

    ns = graph_name or getattr(db, "name", "")

    # Node info cache (filled by keyword signal, shared)
    node_info: Dict[str, Dict] = {}  # node_id → {label, props}

    # ── Signal 1: Keyword ──
    def _keyword():
        results = plain_search(db, query, k=k * 2)
        ranked = {}
        for rank, r in enumerate(results):
            nid = r.get("node_id", "")
            ranked[nid] = rank + 1
            node_info[nid] = {"label": r.get("label", ""), "props": {
                "name": r.get("name", ""), "content": r.get("content", ""),
                "snippet": r.get("snippet", ""),
            }}
        return ranked

    # ── Signal 2: BM25 ──
    def _bm25():
        try:
            engine = _get_bm25_engine(db, ns)
            if not engine:
                return {}
            result = engine.search(query, limit=k * 2)
            ranked = {}
            for rank, r in enumerate(result.get("results", [])):
                nid = r.get("id", "")
                if nid:
                    ranked[nid] = rank + 1
                    if nid not in node_info:
                        node_info[nid] = {"label": r.get("label", ""), "props": {
                            "name": r.get("text", "")[:100],
                            "content": r.get("text", ""),
                        }}
            return ranked
        except Exception:
            return {}

    # ── Signal 3: Vector ──
    def _vector():
        try:
            from contextsynapse.context.vector_integration import get_session_vector_store
            svs = get_session_vector_store()
            if not svs.available or not ns:
                return {}
            hits = svs.search(ns, query, k=k)
            ranked = {}
            for rank, hit in enumerate(hits):
                nid = hit.get("node_id", "")
                if nid:
                    ranked[nid] = rank + 1
                    meta = hit.get("metadata", {})
                    if nid not in node_info and meta:
                        node_info[nid] = {"label": meta.get("label", ""), "props": {
                            "name": meta.get("name", ""),
                            "content": meta.get("text", ""),
                        }}
            return ranked
        except Exception:
            return {}

    # ── Signal 4: Entity + Graph Traversal ──
    def _graph():
        try:
            if not db or not hasattr(db, "get_all_nodes"):
                return {}
            # Extract entity-like terms from query (capitalized words, quoted phrases)
            entities_to_find = []
            for word in query.split():
                clean = word.strip(".,;:!?()[]{}\"'").lower()
                if len(clean) >= 3:
                    entities_to_find.append(clean)

            # Find matching Entity/Organization nodes
            entity_ids = []
            all_nodes = db.get_all_nodes()
            for n in all_nodes:
                label = getattr(n, "label", "")
                if label in ("Entity", "Organization"):
                    name = (getattr(n, "properties", {}) or {}).get("name", "").lower()
                    if any(e in name for e in entities_to_find):
                        entity_ids.append(getattr(n, "id", ""))

            if not entity_ids:
                return {}

            # Follow edges from entity nodes to find connected facts/passages
            ranked = {}
            rank = 0
            for eid in entity_ids[:5]:  # cap at 5 entities
                if hasattr(db, "get_neighbors"):
                    try:
                        neighbors = db.get_neighbors(eid)
                        for nid, edge in (neighbors or []):
                            if nid not in ranked:
                                rank += 1
                                ranked[nid] = rank
                                if nid not in node_info:
                                    node = db.get_node(nid)
                                    if node:
                                        node_info[nid] = {
                                            "label": getattr(node, "label", ""),
                                            "props": getattr(node, "properties", {}) or {},
                                        }
                                if rank >= k * 2:
                                    break
                    except Exception:
                        pass
            return ranked
        except Exception:
            return {}

    # ── Run all 4 signals in parallel ──
    signal_results = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(_keyword): "keyword",
            pool.submit(_bm25): "bm25",
            pool.submit(_vector): "vector",
            pool.submit(_graph): "graph",
        }
        try:
            for future in as_completed(futures, timeout=10.0):
                name = futures[future]
                try:
                    signal_results[name] = future.result()
                except Exception:
                    signal_results[name] = {}
        except TimeoutError:
            pass
        # Collect any remaining that didn't complete
        for future, name in futures.items():
            if name not in signal_results:
                signal_results[name] = {}
                logger.debug("[PARALLEL] Signal '%s' timed out", name)

    # ── RRF Fusion ──
    rrf_k = 60
    all_candidates = set()
    for ranked in signal_results.values():
        all_candidates |= set(ranked.keys())

    # Source vs agent label sets for ordering
    _SOURCE_LABELS = {"Fact", "Passage", "Document", "TextChunk", "WebPage",
                      "Entity", "Organization", "Claim", "Statistic", "Article"}

    fused = []
    for nid in all_candidates:
        rrf_score = 0.0
        signals = 0
        signal_names = []
        for sig_name, ranked in signal_results.items():
            if nid in ranked:
                rrf_score += 1.0 / (rrf_k + ranked[nid])
                signals += 1
                signal_names.append(sig_name)
        info = node_info.get(nid, {})
        label = info.get("label", "")
        is_source = label in _SOURCE_LABELS
        fused.append((nid, rrf_score, signals, signal_names, is_source))

    # Sort: source data first within each signal count, then by score
    fused.sort(key=lambda x: (-x[2], -int(x[4]), -x[1]))

    # Build results with full content
    results = []
    for nid, score, signals, signal_names, is_source in fused[:k]:
        info = node_info.get(nid, {})
        props = info.get("props", {})

        # Get full content from node if not already in props
        content = props.get("content") or props.get("statement") or props.get("description") or ""
        if not content and db:
            try:
                node = db.get_node(nid)
                if node:
                    np = getattr(node, "properties", {}) or {}
                    content = np.get("content") or np.get("statement") or np.get("description") or ""
                    props.update(np)
            except Exception:
                pass

        name = props.get("name", "")[:120]
        results.append({
            "node_id": nid,
            "label": info.get("label", ""),
            "name": name,
            "content": str(content)[:400],
            "score": round(score, 4),
            "signals": signals,
            "signal_names": signal_names,
        })

    return results
