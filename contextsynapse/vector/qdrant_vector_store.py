"""
Qdrant Vector Store for AIContextDB
Cross-platform vector database using Qdrant (embedded local mode — no Docker needed).
"""

import logging
import hashlib
from typing import List, Dict, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams, PointStruct
    QDRANT_AVAILABLE = True
except ImportError:
    QDRANT_AVAILABLE = False
    logger.warning("Qdrant not available. Install with: pip install qdrant-client")


def _stable_int_id(node_id: str) -> int:
    """Convert string node_id to a stable positive integer for Qdrant."""
    return int(hashlib.sha256(node_id.encode()).hexdigest()[:15], 16)


class QdrantVectorStore:
    """
    Qdrant-based vector store.

    Default: embedded local mode (path on disk, no server/Docker needed).
    Optional: remote mode via url parameter.
    """

    def __init__(self, dimension: int = 1536, metric: str = "cosine",
                 collection_name: str = "default", url: Optional[str] = None,
                 path: Optional[str] = None):
        if not QDRANT_AVAILABLE:
            raise ImportError("Qdrant not available. Install with: pip install qdrant-client")

        self.dimension = dimension
        self.metric = metric
        self.collection_name = collection_name

        # Connect: remote URL > local path > default local path
        if url:
            self.client = QdrantClient(url=url)
        else:
            local_path = path or "contextcore_data/qdrant"
            Path(local_path).mkdir(parents=True, exist_ok=True)
            self.client = QdrantClient(path=local_path)

        # Create collection if missing — idempotent (handles race conditions + version mismatch)
        distance_map = {
            "cosine": Distance.COSINE,
            "euclidean": Distance.EUCLID,
            "dot": Distance.DOT,
        }
        try:
            exists = self.client.collection_exists(collection_name)
        except Exception:
            exists = False  # version mismatch or transient error — try creating anyway
        if not exists:
            try:
                self.client.create_collection(
                    collection_name=collection_name,
                    vectors_config=VectorParams(
                        size=dimension,
                        distance=distance_map.get(metric.lower(), Distance.COSINE),
                    ),
                )
            except Exception as e:
                # 409 = collection was just created by a concurrent worker — safe to continue
                if "409" not in str(e) and "already exists" not in str(e).lower():
                    raise
                logger.debug("Collection '%s' already exists (concurrent creation) — continuing", collection_name)

    def add_vectors(self, node_ids: List[str], vectors: List[List[float]],
                    metadata_list: Optional[List[Dict[str, Any]]] = None) -> bool:
        """Add vectors to Qdrant."""
        try:
            points = []
            for i, (node_id, vector) in enumerate(zip(node_ids, vectors)):
                payload = {"node_id": node_id}
                if metadata_list and i < len(metadata_list):
                    payload.update(metadata_list[i])

                points.append(PointStruct(
                    id=_stable_int_id(node_id),
                    vector=vector,
                    payload=payload,
                ))

            self.client.upsert(
                collection_name=self.collection_name,
                points=points,
            )
            return True

        except Exception as e:
            logger.error(f"Failed to add vectors to Qdrant: {e}")
            return False

    def search(self, query_vector: List[float], k: int = 10,
               filter_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Search for similar vectors."""
        try:
            query_filter = None
            if filter_ids:
                from qdrant_client.models import Filter, FieldCondition, MatchAny
                query_filter = Filter(
                    must=[FieldCondition(key="node_id", match=MatchAny(any=filter_ids))]
                )

            results = self.client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                limit=k,
                query_filter=query_filter,
            )

            formatted = []
            for point in results.points:
                node_id = point.payload.get("node_id", "")
                score = point.score
                formatted.append({
                    "node_id": node_id,
                    "score": score,
                    "distance": 1.0 - score if self.metric == "cosine" else score,
                    "metadata": point.payload,
                })
            return formatted

        except Exception as e:
            logger.error(f"Qdrant search failed: {e}")
            return []

    def save(self, filepath: str) -> bool:
        """Save is automatic with Qdrant local storage."""
        return True

    def load(self, filepath: str) -> bool:
        """Load is automatic with Qdrant local storage."""
        try:
            info = self.client.get_collection(self.collection_name)
            return True
        except Exception:
            return False

    def get_vector_count(self) -> int:
        """Get number of vectors."""
        try:
            info = self.client.get_collection(self.collection_name)
            return info.points_count
        except Exception:
            return 0

    def close(self):
        """Close the client connection."""
        try:
            self.client.close()
        except Exception:
            pass
