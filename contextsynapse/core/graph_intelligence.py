"""
Graph Intelligence — auto-enrichment on node creation.

Runs inline on every node add (<10ms target). Three capabilities:
1. Auto-link: match entity names in text → create MENTIONS edges
2. Quality scoring: assign _quality_score based on node type + metadata
3. Topic tagging: assign _topics from cached keyword index

Usage:
    gi = GraphIntelligence(db)
    gi.on_node_added(node)  # called after node persists to storage

    # Or register as callback:
    db.register_node_callback(gi.on_node_added)
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ── Quality scores by node type ────────────────────────────────────

QUALITY_SCORES = {
    "Document": 1.0,
    "WebPage": 1.0,
    "Passage": 0.85,
    "TextChunk": 0.85,
    "Fact": 0.8,
    "Statistic": 0.8,
    "Claim": 0.75,
    "Article": 0.75,
    "Entity": 0.7,
    "Organization": 0.7,
    "Author": 0.7,
    "Date": 0.6,
    "Finding": 0.5,
    "Decision": 0.5,
    "Insight": 0.4,
    "Task": 0.3,
    "Action": 0.2,
}

# Labels that should NOT be processed (infrastructure/noise)
SKIP_LABELS = {
    "AgentThought", "AgentAction", "AgentMessage", "AgentPresence",
    "ExperimentRun", "ExperimentScore", "PipelineRun",
    "VectorIndex", "BM25Index", "Session", "ContextRef",
    "Context", "ContextIntelligence", "Project", "KnowledgeBase", "CodeBase",
    "SystemStore", "UserStore", "WebStore", "GeneratedStore",
    "MemoryStore", "ArtifactStore", "ToolStore",
}

# Minimum entity name length to match (avoids false positives)
MIN_ENTITY_NAME_LEN = 3


class GraphIntelligence:
    """Inline graph enrichment — runs on every node add.

    Designed for speed: no LLM calls, no network I/O.
    Uses cached entity names and keyword index.
    """

    def __init__(self, db):
        self._db = db
        self._entity_cache: Optional[Dict[str, str]] = None  # name_lower → node_id
        self._entity_cache_ts: float = 0
        self._cache_ttl: float = 120.0  # rebuild entity cache every 2 min

    def on_node_added(self, node, namespace: str = "") -> None:
        """Called after a node is persisted. Enriches it inline.

        Must complete in <10ms for normal nodes.
        """
        label = getattr(node, "label", "")
        if label in SKIP_LABELS:
            return

        props = getattr(node, "properties", {}) or {}
        node_id = getattr(node, "id", "")
        if not node_id:
            return

        t0 = time.time()

        # 1. Quality scoring
        self._score_quality(node_id, label, props)

        # 2. Topic tagging
        self._tag_topics(node_id, label, props, namespace)

        # 3. Auto-link entities (only for content nodes with text)
        if label in ("Fact", "Passage", "TextChunk", "Claim", "Finding",
                      "Insight", "Document", "Article", "Statistic"):
            self._auto_link_entities(node_id, props)

        # 4. Auto-embed to vector store (for content-rich nodes)
        self._auto_embed(node_id, label, props, namespace)

        # 5. Schedule debounced index rebuild (batches multiple node adds)
        self._schedule_index_rebuild(namespace)

        # 6. Update context state (incremental — Findings/Insights only)
        if label in ("Finding", "Insight"):
            try:
                from .context_state import get_context_state
                state = get_context_state(self._db, namespace)
                state.on_node_added(node)
            except Exception:
                pass

        elapsed_ms = (time.time() - t0) * 1000
        if elapsed_ms > 50:
            logger.debug("[INTELLIGENCE] on_node_added took %.1fms for %s:%s", elapsed_ms, label, node_id[:12])

    # ── Quality Scoring ─────────────────────────────────────────

    def _score_quality(self, node_id: str, label: str, props: dict):
        """Assign _quality_score based on node type + metadata."""
        score = QUALITY_SCORES.get(label, 0.3)

        # Boost if node has source attribution
        if props.get("source") or props.get("source_url") or props.get("url"):
            score = min(1.0, score + 0.1)

        # Boost if node was verified
        if props.get("_verified") or props.get("verified"):
            score = min(1.0, score + 0.1)

        # Penalize if agent-generated without source
        if props.get("_agent_id") and not props.get("source"):
            score = max(0.1, score - 0.1)

        props["_quality_score"] = round(score, 2)

    # ── Topic Tagging ───────────────────────────────────────────

    def _tag_topics(self, node_id: str, label: str, props: dict, namespace: str):
        """Assign _topics from keyword frequency in cached index."""
        # Get text content
        text = self._get_text(props).lower()
        if not text or len(text) < 10:
            return

        # Use topic_scan's cached index if available
        try:
            from ..search.rag import topic_scan
            result = topic_scan(self._db, namespace, max_topics=10, samples_per_topic=0)
            topics = result.get("topics", [])
            if not topics:
                return

            # Match node text against topic keywords
            matched = []
            for t in topics:
                topic_name = t.get("name", "")
                if topic_name and topic_name in text:
                    matched.append(topic_name)

            if matched:
                props["_topics"] = ",".join(matched[:3])
        except Exception:
            pass

    # ── Auto-Link Entities ──────────────────────────────────────

    def _auto_link_entities(self, node_id: str, props: dict):
        """Find entity names mentioned in node text, create MENTIONS edges."""
        text = self._get_text(props)
        if not text or len(text) < 20:
            return

        text_lower = text.lower()
        entities = self._get_entity_cache()
        if not entities:
            return

        linked = 0
        from .graph_structures import GraphEdge
        import uuid as _uuid

        for entity_name_lower, entity_id in entities.items():
            if entity_id == node_id:
                continue  # don't self-link
            if entity_name_lower in text_lower:
                try:
                    # Check word boundary — avoid matching "India" inside "Indiana"
                    pattern = r'\b' + re.escape(entity_name_lower) + r'\b'
                    if re.search(pattern, text_lower):
                        edge = GraphEdge(
                            id=str(_uuid.uuid4()),
                            source=node_id,
                            target=entity_id,
                            label="MENTIONS",
                            properties={"auto_linked": True},
                        )
                        self._db.add_edge(edge)
                        linked += 1
                        if linked >= 5:
                            break  # cap at 5 links per node
                except Exception:
                    pass

    def _get_entity_cache(self) -> Dict[str, str]:
        """Get or rebuild the entity name → id cache."""
        now = time.time()
        if self._entity_cache is not None and (now - self._entity_cache_ts) < self._cache_ttl:
            return self._entity_cache

        # Rebuild cache
        cache: Dict[str, str] = {}
        try:
            all_nodes = self._db.get_all_nodes()
            for n in all_nodes:
                label = getattr(n, "label", "")
                if label in ("Entity", "Organization", "Author"):
                    name = (getattr(n, "properties", {}) or {}).get("name", "")
                    if name and len(name) >= MIN_ENTITY_NAME_LEN:
                        cache[name.lower()] = getattr(n, "id", "")
        except Exception:
            pass

        self._entity_cache = cache
        self._entity_cache_ts = now
        logger.debug("[INTELLIGENCE] Entity cache rebuilt: %d entities", len(cache))
        return cache

    # ── Auto-Embed ──────────────────────────────────────────────

    # Labels worth embedding (have meaningful text)
    _EMBED_LABELS = {"Fact", "Passage", "TextChunk", "Claim", "Finding",
                     "Insight", "Article", "Document", "Statistic"}
    _MIN_EMBED_LENGTH = 50

    # Embed queue — limits concurrent Ollama calls during bulk ingest
    _embed_semaphore = None

    def _auto_embed(self, node_id: str, label: str, props: dict, namespace: str):
        """Embed content-rich nodes to vector store asynchronously (fire-and-forget).

        Uses a semaphore to limit concurrent embed calls (prevents overloading Ollama
        during bulk ingest of 500+ nodes).
        """
        if label not in self._EMBED_LABELS:
            return
        if not namespace:
            return

        # Skip if already embedded (pipeline STORE_VECTORS stage may have done it)
        if props.get("vector_ref"):
            return

        text = props.get("content") or props.get("statement") or props.get("name") or ""
        text = str(text)
        if len(text) < self._MIN_EMBED_LENGTH:
            return

        # Set vector_ref optimistically
        props["vector_ref"] = f"{namespace}_passages:{node_id}"

        # Init semaphore once (max 3 concurrent embeds)
        import threading
        if GraphIntelligence._embed_semaphore is None:
            GraphIntelligence._embed_semaphore = threading.Semaphore(3)

        def _do_embed():
            GraphIntelligence._embed_semaphore.acquire()
            try:
                from ..context.vector_integration import SessionVectorStore, get_session_vector_store
                svs = get_session_vector_store()
                if svs.available:
                    svs.add_text(
                        namespace, text[:500], node_id=node_id,
                        metadata={"label": label, "name": props.get("name", "")[:100]},
                    )
            except Exception:
                pass
            finally:
                GraphIntelligence._embed_semaphore.release()

        t = threading.Thread(target=_do_embed, daemon=True)
        t.start()

    # ── Index Rebuild (debounced) ─────────────────────────────

    _rebuild_timers: Dict[str, Any] = {}  # namespace → Timer

    def _schedule_index_rebuild(self, namespace: str):
        """Schedule a debounced index rebuild. Waits 2s after last node add before rebuilding.

        This batches rapid node additions (e.g. pipeline ingest of 500 nodes)
        into a single rebuild at the end.
        """
        import threading
        ns = namespace or getattr(self._db, "name", "")
        if not ns:
            return

        # Cancel previous timer for this namespace
        old_timer = GraphIntelligence._rebuild_timers.get(ns)
        if old_timer:
            old_timer.cancel()

        # Schedule new rebuild in 2 seconds
        def _do_rebuild():
            try:
                build_indexes(self._db, ns)
            except Exception:
                pass
            GraphIntelligence._rebuild_timers.pop(ns, None)

        timer = threading.Timer(2.0, _do_rebuild)
        timer.daemon = True
        timer.start()
        GraphIntelligence._rebuild_timers[ns] = timer

    # ── Helpers ──────────────────────────────────────────────────

    def _get_text(self, props: dict) -> str:
        """Extract searchable text from node properties."""
        parts = []
        for field in ("name", "content", "statement", "description", "title"):
            val = props.get(field, "")
            if val:
                parts.append(str(val))
        return " ".join(parts)


# ── Index Builder (called from ingest, background, on-demand) ──

def build_indexes(db, namespace: str = "") -> Dict[str, Any]:
    """Build all search indexes for a graph. Called after ingest or on demand.

    Builds: keyword index, BM25 index, context state.
    Fast: ~2-3s for 800 nodes. Indexes are cached after build.
    """
    ns = namespace or getattr(db, "name", "")
    results = {}
    t0 = time.time()

    # 1. Keyword index
    try:
        from ..search.rag import _build_keyword_index
        node_map, inv = _build_keyword_index(db, ns)
        results["keyword"] = {"nodes": len(node_map), "terms": len(inv)}
    except Exception as e:
        results["keyword"] = {"error": str(e)}

    # 2. BM25 index
    try:
        from ..search.rag import _get_bm25_engine
        engine = _get_bm25_engine(db, ns)
        if engine:
            results["bm25"] = {"documents": len(engine._documents)}
    except Exception as e:
        results["bm25"] = {"error": str(e)}

    # 3. Context state (rebuild + save as graph node)
    try:
        from .context_state import get_context_state
        state = get_context_state(db, ns)
        state.rebuild()  # this also saves to ContextIntelligence node
        results["context_state"] = {"status": state.status, "topics": len(state.coverage)}
    except Exception as e:
        results["context_state"] = {"error": str(e)}

    elapsed = int((time.time() - t0) * 1000)
    results["total_ms"] = elapsed
    logger.info("[INDEX] Built all indexes for '%s' in %dms: %s", ns, elapsed, results)
    return results


# ── Singleton per graph ─────────────────────────────────────────

_instances: Dict[str, GraphIntelligence] = {}


def get_graph_intelligence(db) -> GraphIntelligence:
    """Get or create a GraphIntelligence instance for a graph."""
    name = getattr(db, "name", id(db))
    if name not in _instances:
        _instances[name] = GraphIntelligence(db)
    return _instances[name]
