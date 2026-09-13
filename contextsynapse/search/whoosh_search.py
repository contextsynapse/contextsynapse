"""
AIContextDB Whoosh Search Engine - BM25 Sparse Search
Provides keyword-based search using Whoosh's BM25F scoring.
Falls back to TF-IDF via scikit-learn if Whoosh is unavailable.
"""

import logging
import time
from typing import Dict, List, Any, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Try Whoosh first, fall back to sklearn TF-IDF
WHOOSH_AVAILABLE = False
SKLEARN_AVAILABLE = False

try:
    from whoosh.index import create_in, open_dir, exists_in
    from whoosh.fields import Schema, TEXT, ID, STORED, NUMERIC
    from whoosh.qparser import MultifieldParser, OrGroup
    from whoosh import scoring
    WHOOSH_AVAILABLE = True
except ImportError:
    pass

if not WHOOSH_AVAILABLE:
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
        import numpy as np
        SKLEARN_AVAILABLE = True
    except ImportError:
        pass


@dataclass
class WhooshConfig:
    """Configuration for the Whoosh search engine."""
    index_dir: str = "contextcore_data/whoosh_index"
    analyzer: str = "standard"
    bm25_b: float = 0.75
    bm25_k1: float = 1.2
    max_results: int = 100


class WhooshSearchEngine:
    """
    BM25 sparse search engine.

    Uses Whoosh for BM25F scoring when available.
    Falls back to scikit-learn TF-IDF when Whoosh is not installed.
    """

    def __init__(self, config: Optional[WhooshConfig] = None):
        self.config = config or WhooshConfig()
        self._documents: Dict[str, Dict[str, Any]] = {}  # id -> {text, label, properties}
        self._whoosh_index = None
        self._tfidf_vectorizer = None
        self._tfidf_matrix = None
        self._tfidf_ids: List[str] = []
        self._tfidf_dirty = True
        self._backend = "none"

        if WHOOSH_AVAILABLE:
            self._backend = "whoosh"
            self._init_whoosh()
        elif SKLEARN_AVAILABLE:
            self._backend = "tfidf"
            self._tfidf_vectorizer = TfidfVectorizer(
                stop_words="english",
                max_features=50000,
                ngram_range=(1, 2),
            )
        else:
            self._backend = "keyword"

        logger.info(f"WhooshSearchEngine initialized (backend={self._backend})")

    # ------------------------------------------------------------------
    # Whoosh backend
    # ------------------------------------------------------------------

    def _init_whoosh(self):
        """Initialize or open Whoosh index."""
        import os
        os.makedirs(self.config.index_dir, exist_ok=True)
        schema = Schema(
            doc_id=ID(stored=True, unique=True),
            label=ID(stored=True),
            content=TEXT(stored=True),
            properties_text=TEXT(stored=True),
        )
        if exists_in(self.config.index_dir):
            self._whoosh_index = open_dir(self.config.index_dir)
        else:
            self._whoosh_index = create_in(self.config.index_dir, schema)

    def _whoosh_add(self, doc_id: str, label: str, content: str, properties_text: str):
        """Add a document to the Whoosh index."""
        writer = self._whoosh_index.writer()
        writer.update_document(
            doc_id=doc_id,
            label=label,
            content=content,
            properties_text=properties_text,
        )
        writer.commit()

    def _whoosh_search(self, query_text: str, limit: int = 10,
                       label_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """Search the Whoosh index using BM25F."""
        with self._whoosh_index.searcher(
            weighting=scoring.BM25F(B=self.config.bm25_b, K1=self.config.bm25_k1)
        ) as searcher:
            parser = MultifieldParser(
                ["content", "properties_text"],
                schema=self._whoosh_index.schema,
                group=OrGroup,
            )
            parsed = parser.parse(query_text)
            raw_results = searcher.search(parsed, limit=limit * 2)

            results = []
            for hit in raw_results:
                doc_id = hit["doc_id"]
                if label_filter and hit.get("label", "") != label_filter:
                    continue
                doc_meta = self._documents.get(doc_id, {})
                results.append({
                    "id": doc_id,
                    "label": hit.get("label", ""),
                    "text": hit.get("content", ""),
                    "score": float(hit.score),
                    "properties": doc_meta.get("properties", {}),
                    "source": "whoosh_bm25",
                })
                if len(results) >= limit:
                    break
            return results

    # ------------------------------------------------------------------
    # TF-IDF fallback backend
    # ------------------------------------------------------------------

    def _rebuild_tfidf(self):
        """Rebuild the TF-IDF matrix from current documents."""
        if not self._documents:
            self._tfidf_matrix = None
            self._tfidf_ids = []
            self._tfidf_dirty = False
            return

        self._tfidf_ids = list(self._documents.keys())
        corpus = []
        for doc_id in self._tfidf_ids:
            doc = self._documents[doc_id]
            text = doc.get("text", "")
            props_text = doc.get("properties_text", "")
            corpus.append(f"{text} {props_text}")

        self._tfidf_vectorizer = TfidfVectorizer(
            stop_words="english",
            max_features=50000,
            ngram_range=(1, 2),
        )
        self._tfidf_matrix = self._tfidf_vectorizer.fit_transform(corpus)
        self._tfidf_dirty = False

    def _tfidf_search(self, query_text: str, limit: int = 10,
                      label_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """Search using TF-IDF cosine similarity."""
        if self._tfidf_dirty or self._tfidf_matrix is None:
            self._rebuild_tfidf()

        if self._tfidf_matrix is None or self._tfidf_matrix.shape[0] == 0:
            return []

        query_vec = self._tfidf_vectorizer.transform([query_text])
        scores = cosine_similarity(query_vec, self._tfidf_matrix).flatten()
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)

        results = []
        for idx, score in ranked:
            if score <= 0:
                break
            doc_id = self._tfidf_ids[idx]
            doc = self._documents[doc_id]
            if label_filter and doc.get("label", "") != label_filter:
                continue
            results.append({
                "id": doc_id,
                "label": doc.get("label", ""),
                "text": doc.get("text", ""),
                "score": float(score),
                "properties": doc.get("properties", {}),
                "source": "tfidf",
            })
            if len(results) >= limit:
                break
        return results

    # ------------------------------------------------------------------
    # Keyword fallback (no dependencies)
    # ------------------------------------------------------------------

    def _keyword_search(self, query_text: str, limit: int = 10,
                        label_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """Simple keyword matching fallback (no external deps)."""
        terms = query_text.lower().split()
        scored: List[tuple] = []

        for doc_id, doc in self._documents.items():
            if label_filter and doc.get("label", "") != label_filter:
                continue
            haystack = (doc.get("text", "") + " " + doc.get("properties_text", "")).lower()
            hits = sum(1 for t in terms if t in haystack)
            if hits > 0:
                score = hits / max(len(terms), 1)
                scored.append((doc_id, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        results = []
        for doc_id, score in scored[:limit]:
            doc = self._documents[doc_id]
            results.append({
                "id": doc_id,
                "label": doc.get("label", ""),
                "text": doc.get("text", ""),
                "score": float(score),
                "properties": doc.get("properties", {}),
                "source": "keyword",
            })
        return results

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def index_node(self, node_id: str, label: str, properties: Dict[str, Any]):
        """
        Index a graph node for sparse search.

        Args:
            node_id: Node UUID
            label: Node type label (e.g., "Person")
            properties: Node properties dict
        """
        # Build searchable text from properties
        text_parts = []
        for key, value in properties.items():
            if key in ("domain", "content_hash", "hash_algorithm", "uuid", "id"):
                continue
            text_parts.append(f"{key}: {value}")
        properties_text = " ".join(text_parts)
        content = properties.get("name", "") or properties.get("title", "") or properties.get("text", "")

        self._documents[node_id] = {
            "text": str(content),
            "label": label,
            "properties": properties,
            "properties_text": properties_text,
        }

        if self._backend == "whoosh":
            self._whoosh_add(node_id, label, str(content), properties_text)
        elif self._backend == "tfidf":
            self._tfidf_dirty = True

    def index_nodes_from_graph(self, graph_db):
        """
        Bulk-index all nodes from an AIContextDB graph instance.
        Uses single commit for Whoosh (much faster than per-node commits).
        """
        nodes = graph_db.get_all_nodes()

        # Noise labels to skip
        _SKIP = {
            "AgentThought", "AgentAction", "AgentMessage", "AgentPresence",
            "ExperimentRun", "ExperimentScore", "PipelineRun",
            "VectorIndex", "BM25Index", "Session", "ContextRef",
            "Context", "Project", "KnowledgeBase", "CodeBase",
            "SystemStore", "UserStore", "WebStore", "GeneratedStore",
            "MemoryStore", "ArtifactStore", "ToolStore",
        }

        # Batch: collect all docs, then commit once
        if self._backend == "whoosh" and self._whoosh_index:
            writer = self._whoosh_index.writer()
            count = 0
            for node in nodes:
                props = node.properties if hasattr(node, "properties") else {}
                label = node.label if hasattr(node, "label") else "Node"
                if label in _SKIP:
                    continue
                nid = node.id if hasattr(node, "id") else str(count)
                # Build text
                text_parts = []
                for key, value in props.items():
                    if key in ("domain", "content_hash", "hash_algorithm", "uuid", "id"):
                        continue
                    text_parts.append(f"{key}: {value}")
                properties_text = " ".join(text_parts)
                content = props.get("content") or props.get("statement") or props.get("name") or props.get("title") or ""
                self._documents[nid] = {
                    "text": str(content), "label": label,
                    "properties": props, "properties_text": properties_text,
                }
                writer.update_document(
                    doc_id=nid, label=label,
                    content=str(content), properties_text=properties_text,
                )
                count += 1
            writer.commit()  # single commit for all nodes
            logger.info(f"BM25 indexed {count} nodes (whoosh, single commit)")
        else:
            # Fallback: per-node indexing for non-whoosh backends
            count = 0
            for node in nodes:
                props = node.properties if hasattr(node, "properties") else {}
                label = node.label if hasattr(node, "label") else "Node"
                if label in _SKIP:
                    continue
                nid = node.id if hasattr(node, "id") else str(count)
                self.index_node(nid, label, props)
                count += 1
            logger.info(f"Indexed {count} nodes for sparse search")

    def search(self, query: str, limit: int = 10,
               label_filter: Optional[str] = None) -> Dict[str, Any]:
        """
        Perform BM25 / sparse search.

        Args:
            query: Search query string
            limit: Max results to return
            label_filter: Optional node type to restrict results

        Returns:
            Dict with success, results list, and metadata
        """
        start = time.time()
        try:
            if self._backend == "whoosh":
                results = self._whoosh_search(query, limit, label_filter)
            elif self._backend == "tfidf":
                results = self._tfidf_search(query, limit, label_filter)
            else:
                results = self._keyword_search(query, limit, label_filter)

            return {
                "success": True,
                "results": results,
                "query": query,
                "count": len(results),
                "backend": self._backend,
                "execution_time": time.time() - start,
            }
        except Exception as e:
            logger.error(f"Sparse search failed: {e}")
            return {
                "success": False,
                "results": [],
                "query": query,
                "error": str(e),
                "backend": self._backend,
                "execution_time": time.time() - start,
            }

    @property
    def document_count(self) -> int:
        """Number of indexed documents."""
        return len(self._documents)
