"""
Vector Integration for Context Sessions
========================================
Bridges the CAS session layer with the vector DB stack.

Each session gets its own vector collection. Text ingested into a session
is auto-embedded and stored, enabling semantic search across context.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default embedding dimension (OpenAI ada-002 / fallback)
DEFAULT_DIMENSION = 1536

# Singleton — one SVS instance shared across all callers
_svs_singleton: Optional["SessionVectorStore"] = None


def get_session_vector_store() -> "SessionVectorStore":
    """Get the singleton SessionVectorStore. Creates and warms up on first call."""
    global _svs_singleton
    if _svs_singleton is None:
        _svs_singleton = SessionVectorStore()
        # Warm up Ollama embedding model — first call loads model into memory (5s),
        # subsequent calls are <100ms. keep_alive=60m prevents unloading.
        if _svs_singleton._embedding_service:
            try:
                import time
                t0 = time.time()
                result = _svs_singleton._embedding_service.embed_text("warmup embedding model")
                emb = result.get("embedding") if isinstance(result, dict) else None
                elapsed = int((time.time() - t0) * 1000)
                if emb:
                    logger.info("Embedding model warmed up: dim=%d (%dms)", len(emb), elapsed)
                else:
                    logger.info("Embedding warmup completed (%dms)", elapsed)
            except Exception as e:
                logger.debug("Embedding warmup failed: %s", e)
    return _svs_singleton


class SessionVectorStore:
    """
    Manages per-session vector collections.

    Wraps ``VectorDBManager`` so the rest of CAS never touches vector
    internals directly.
    """

    def __init__(self, dimension: int = DEFAULT_DIMENSION, backend: Optional[str] = None):
        self._dimension = dimension
        self._stores: Dict[str, Any] = {}  # session_id -> vector store instance
        self._embedding_service = None
        self._manager = None

        # Lazy-init embedding service first (to detect dimension)
        try:
            from ..models.embedding_service import get_embedding_service
            self._embedding_service = get_embedding_service()
            # Auto-detect dimension from embedding service
            try:
                test = self._embedding_service.embed_text("test")
                emb = test.get("embedding")
                if emb is not None:
                    detected = len(emb) if isinstance(emb, list) else (emb.shape[0] if hasattr(emb, 'shape') else len(list(emb)))
                    if detected > 0:
                        self._dimension = detected
                        logger.info(f"Auto-detected embedding dimension: {detected}")
            except Exception:
                pass
            logger.info("Embedding service available for auto-embed")
        except Exception as e:
            logger.warning(f"Embedding service unavailable: {e}")

        # Lazy-init vector backend
        try:
            from ..vector.vector_db_manager import get_vector_db_manager
            self._manager = get_vector_db_manager(backend=backend)
            logger.info(f"SessionVectorStore using backend: {self._manager.backend_name}")
        except Exception as e:
            logger.warning(f"Vector DB manager unavailable: {e}")

    @property
    def available(self) -> bool:
        """True if both vector store and embeddings are functional."""
        return self._manager is not None and self._embedding_service is not None

    def _get_store(self, session_id: str):
        """Get or create the vector store for a session/graph namespace.

        Collection naming convention matches the ingestion pipeline:
        ``{namespace}_passages`` — so vectors written during ingestion
        are findable by search_nodes, and vice versa.
        """
        if session_id not in self._stores:
            if self._manager is None:
                return None
            # Use {namespace}_passages to match ingestion STORE_VECTORS stage
            safe_ns = session_id.replace(":", "_")
            collection = f"{safe_ns}_passages"
            store = self._manager.create_store(
                dimension=self._dimension,
                metric="cosine",
                collection_name=collection,
            )
            self._stores[session_id] = store
            logger.info(f"Created vector store for session {session_id} (collection: {collection})")
        return self._stores[session_id]

    def embed_text(self, text: str) -> Optional[List[float]]:
        """Generate an embedding for a single text string. Uses LMDB cache."""
        if self._embedding_service is None:
            return None

        # Check cache first (microseconds vs 3-5s Ollama call)
        try:
            from ..search.embedding_cache import get_embedding_cache
            cache = get_embedding_cache()
            cached = cache.get(text)
            if cached is not None:
                return cached
        except Exception:
            cache = None

        try:
            result = self._embedding_service.embed_text(text)
            # Reject fallback/mock embeddings — they are not semantically meaningful
            if result.get("provider") == "fallback":
                logger.warning("Embedding returned fallback (mock) — treating as unavailable")
                return None
            embedding = result.get("embedding")
            if embedding is not None:
                vec = list(embedding) if not isinstance(embedding, list) else embedding
                # Cache for next time
                if cache:
                    try:
                        cache.put(text, vec)
                    except Exception:
                        pass
                return vec
        except Exception as e:
            logger.warning(f"Embedding failed: {e}")
        return None

    def embed_batch(self, texts: List[str]) -> Optional[List[List[float]]]:
        """Generate embeddings for multiple texts."""
        if self._embedding_service is None:
            return None
        try:
            result = self._embedding_service.embed_batch(texts)
            embeddings = result.get("embeddings", [])
            return [list(e) if not isinstance(e, list) else e for e in embeddings]
        except Exception as e:
            logger.warning(f"Batch embedding failed: {e}")
        return None

    def add_text(
        self,
        session_id: str,
        text: str,
        node_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Embed and store a single text for a session."""
        store = self._get_store(session_id)
        if store is None:
            return False

        embedding = self.embed_text(text)
        if embedding is None:
            return False

        try:
            store.add_vectors(
                node_ids=[node_id],
                vectors=[embedding],
                metadata_list=[{
                    "text": text[:500],  # store truncated text for retrieval
                    **(metadata or {}),
                }],
            )
            return True
        except Exception as e:
            logger.warning(f"Vector add failed: {e}")
            return False

    def add_texts(
        self,
        session_id: str,
        texts: List[str],
        node_ids: List[str],
        metadata_list: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """Embed and store multiple texts for a session."""
        store = self._get_store(session_id)
        if store is None:
            return False

        embeddings = self.embed_batch(texts)
        if embeddings is None or len(embeddings) != len(texts):
            return False

        meta = metadata_list or [{}] * len(texts)
        # Inject truncated text into each metadata entry
        enriched_meta = [
            {"text": t[:500], **m}
            for t, m in zip(texts, meta)
        ]

        try:
            store.add_vectors(
                node_ids=node_ids,
                vectors=embeddings,
                metadata_list=enriched_meta,
            )
            return True
        except Exception as e:
            logger.warning(f"Vector batch add failed: {e}")
            return False

    def search(
        self,
        session_id: str,
        query: str,
        k: int = 10,
        filter_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Semantic search across a session's vectors.

        Returns list of ``{node_id, score, metadata}`` dicts sorted by relevance.
        """
        store = self._get_store(session_id)
        if store is None:
            return []

        query_embedding = self.embed_text(query)
        if query_embedding is None:
            return []

        try:
            results = store.search(
                query_vector=query_embedding,
                k=k,
                filter_ids=filter_ids,
            )
            return results
        except Exception as e:
            logger.warning(f"Vector search failed: {e}")
            return []

    def has_vectors(self, session_id: str) -> bool:
        """Return True if this namespace has any indexed vectors.

        Cheap check — avoids embedding the query when the collection is empty
        (e.g. test graphs that were never passed through the ingestion pipeline).
        """
        if self._manager is None:
            return False
        safe_ns = session_id.replace(":", "_")
        collection = f"{safe_ns}_passages"
        try:
            # Ask the backend whether the collection exists and is non-empty
            client = getattr(self._manager, "_client", None)
            if client is not None and hasattr(client, "collection_exists"):
                if not client.collection_exists(collection):
                    return False
                info = client.get_collection(collection)
                count = getattr(info, "vectors_count", None)
                if count is not None:
                    return count > 0
        except Exception:
            pass
        # If we can't check, assume vectors may exist (safe default)
        return True

    def delete_session(self, session_id: str):
        """Remove a session's vector store from memory."""
        self._stores.pop(session_id, None)

    def get_stats(self, session_id: str) -> Dict[str, Any]:
        """Get stats for a session's vector store."""
        store = self._get_store(session_id)
        if store is None:
            return {"available": False}
        return {
            "available": True,
            "backend": self._manager.backend_name if self._manager else "none",
            "vector_count": getattr(store, "vector_count", 0),
            "dimension": self._dimension,
            "embedding_service": self._embedding_service is not None,
        }
