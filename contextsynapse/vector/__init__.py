"""
AIContextDB Vector Package
Self-contained universal vector database for AIContextDB.
"""

from .vector_db import (
    VectorDBProvider,
    VectorDBCapabilities,
    VectorDBConfig,
    VectorResult,
    AIContextDBUniversalVectorDBManager,
    VectorConfig,
    AIContextDBVectorDB,
    get_vector_db,
    create_vector_db,
    get_universal_vector_db
)

__all__ = [
    "VectorDBProvider",
    "VectorDBCapabilities",
    "VectorDBConfig",
    "VectorResult",
    "AIContextDBUniversalVectorDBManager",
    "VectorConfig",
    "AIContextDBVectorDB",
    "get_vector_db",
    "create_vector_db",
    "get_universal_vector_db"
]
