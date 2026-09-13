"""
Vector Database Manager with Configuration Support
Allows easy switching between different vector DB backends.
"""

import logging
from typing import Optional, Dict, Any, List
from enum import Enum
from pathlib import Path
import os

logger = logging.getLogger(__name__)

class VectorDBBackend(Enum):
    """Supported vector database backends."""
    CUSTOM = "custom"  # NumPy-based (default, works everywhere)
    FAISS = "faiss"    # FAISS (fast, but Windows issues)
    CHROMA = "chroma"  # ChromaDB (cross-platform)
    QDRANT = "qdrant"  # Qdrant (cross-platform)
    PINECONE = "pinecone"  # Pinecone (cloud)
    WEAVIATE = "weaviate"  # Weaviate (cross-platform)

class VectorDBManager:
    """
    Manages vector database backends with easy switching via configuration.
    
    Configuration sources (in order of priority):
    1. Environment variable: AICONTEXTDB_VECTOR_DB_BACKEND
    2. Config file: .env or config.yaml
    3. Default: CUSTOM (NumPy-based)
    """
    
    def __init__(self, backend: Optional[str] = None, config: Optional[Dict[str, Any]] = None):
        """
        Initialize vector DB manager.
        
        Args:
            backend: Backend name ("custom", "faiss", "chroma", etc.)
            config: Optional configuration dict
        """
        self.config = config or {}
        self.backend_name = self._get_backend_name(backend)
        self.backend = self._create_backend()
        
        logger.info(f"Vector DB Manager initialized with backend: {self.backend_name}")
    
    def _get_backend_name(self, backend: Optional[str] = None) -> str:
        """Get backend name from various sources."""
        # Priority 1: Explicit parameter
        if backend:
            return backend.lower()
        
        # Priority 2: Environment variable
        env_backend = os.getenv("CONTEXTSYNAPSE_VECTOR_DB_BACKEND") or os.getenv("AICONTEXTDB_VECTOR_DB_BACKEND", "").lower().strip()
        if env_backend:
            return env_backend
        
        # Priority 3: Config file (check both vector_db.backend and vector_db_backend for compatibility)
        vector_db_config = self.config.get("vector_db", {})
        if isinstance(vector_db_config, dict):
            config_backend = vector_db_config.get("backend", "").lower().strip()
        else:
            config_backend = ""
        
        # Also check legacy format
        if not config_backend:
            config_backend = self.config.get("vector_db_backend", "").lower().strip()
        
        if config_backend:
            return config_backend
        
        # Priority 4: Qdrant if QDRANT_URL is set (running server)
        if os.getenv("QDRANT_URL"):
            return "qdrant"

        # Priority 5: Fallback to CUSTOM (NumPy-based, works everywhere)
        return "custom"
    
    def _create_backend(self):
        """Create the appropriate vector store backend."""
        try:
            if self.backend_name == "custom":
                from .custom_vector_store import CustomVectorStore
                return CustomVectorStore
            elif self.backend_name == "faiss":
                from .faiss_vector_store import FAISSVectorStore
                return FAISSVectorStore
            elif self.backend_name == "chroma":
                from .chroma_vector_store import ChromaVectorStore
                return ChromaVectorStore
            elif self.backend_name == "qdrant":
                from .qdrant_vector_store import QdrantVectorStore
                return QdrantVectorStore
            else:
                logger.warning(f"Unknown backend '{self.backend_name}', falling back to 'custom'")
                from .custom_vector_store import CustomVectorStore
                return CustomVectorStore
        except ImportError as e:
            logger.warning(f"Backend '{self.backend_name}' not available ({e}), falling back to 'custom'")
            from .custom_vector_store import CustomVectorStore
            return CustomVectorStore
    
    def create_store(self, dimension: int, metric: str = "cosine", **kwargs) -> Any:
        """
        Create a vector store instance.
        
        Args:
            dimension: Vector dimension
            metric: Similarity metric ("cosine", "euclidean", "dot")
            **kwargs: Additional backend-specific parameters
        
        Returns:
            Vector store instance
        """
        try:
            if self.backend_name == "custom":
                return self.backend(dimension=dimension, metric=metric)
            elif self.backend_name == "faiss":
                index_type = kwargs.get("index_type", "flat")
                return self.backend(dimension=dimension, metric=metric, index_type=index_type)
            elif self.backend_name == "chroma":
                collection_name = kwargs.get("collection_name", "default")
                persist_directory = kwargs.get("persist_directory", None)
                return self.backend(
                    dimension=dimension,
                    metric=metric,
                    collection_name=collection_name,
                    persist_directory=persist_directory
                )
            elif self.backend_name == "qdrant":
                collection_name = kwargs.get("collection_name", "default")
                url = kwargs.get("url") or os.environ.get("QDRANT_URL")
                path = kwargs.get("path") or (f"contextcore_data/qdrant" if not url else None)
                # Try remote first, fall back to embedded if remote is unreachable
                if url:
                    try:
                        store = self.backend(
                            dimension=dimension, metric=metric,
                            collection_name=collection_name, url=url,
                        )
                        # Quick health check
                        store.client.get_collections()
                        return store
                    except Exception as e:
                        logger.warning("Qdrant remote (%s) unreachable: %s — falling back to embedded mode", url, e)
                        path = f"contextcore_data/qdrant"
                return self.backend(
                    dimension=dimension, metric=metric,
                    collection_name=collection_name, path=path,
                )
            else:
                # Default: custom
                return self.backend(dimension=dimension, metric=metric)
        except Exception as e:
            logger.error(f"Failed to create vector store: {e}")
            # Fallback to custom
            from .custom_vector_store import CustomVectorStore
            return CustomVectorStore(dimension=dimension, metric=metric)
    
    def get_backend_info(self) -> Dict[str, Any]:
        """Get information about the current backend."""
        return {
            "backend": self.backend_name,
            "available": self.backend is not None,
            "description": self._get_backend_description()
        }
    
    def _get_backend_description(self) -> str:
        """Get description of current backend."""
        descriptions = {
            "custom": "NumPy-based custom vector store (cross-platform, no dependencies)",
            "faiss": "FAISS vector store (fast, but may have Windows issues)",
            "chroma": "ChromaDB vector store (cross-platform, feature-rich)",
            "qdrant": "Qdrant vector store (cross-platform, production-ready)",
            "pinecone": "Pinecone cloud vector store (managed service)",
            "weaviate": "Weaviate vector store (graph + vector database)"
        }
        return descriptions.get(self.backend_name, "Unknown backend")

# Global manager instance
_global_manager: Optional[VectorDBManager] = None

def get_vector_db_manager(backend: Optional[str] = None, config: Optional[Dict[str, Any]] = None) -> VectorDBManager:
    """Get or create global vector DB manager."""
    global _global_manager
    if _global_manager is None:
        _global_manager = VectorDBManager(backend=backend, config=config)
    return _global_manager

def set_vector_db_backend(backend: str):
    """Set the vector DB backend globally."""
    global _global_manager
    _global_manager = VectorDBManager(backend=backend)
    logger.info(f"Vector DB backend set to: {backend}")



