"""Content store factory -- picks the right backend from config/env.

Usage:
    store = create_content_store()  # reads CONTEXTCORE_CONTENT_STORE env var
    store = create_content_store("postgres")  # explicit backend

Backends:
    duckdb   -- embedded columnar DB (default, zero config)
    postgres -- PostgreSQL with connection pooling (production)

Configure via:
    CONTEXTCORE_CONTENT_STORE=duckdb     (default)
    CONTEXTCORE_CONTENT_STORE=postgres   (requires DATABASE_URL)
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

from contextsynapse.storage.router.interface import ContentStoreInterface

logger = logging.getLogger(__name__)

_global_store: Optional[ContentStoreInterface] = None
_factory_lock = threading.Lock()


def create_content_store(backend: str = None, **kwargs) -> ContentStoreInterface:
    """Create a content store instance.

    Args:
        backend: "duckdb" or "postgres". If None, reads from env.
        **kwargs: Backend-specific config (db_path, database_url, etc.)

    Returns:
        ContentStoreInterface implementation.
    """
    if backend is None:
        backend = os.environ.get("CONTEXTCORE_CONTENT_STORE", "duckdb").lower()

    if backend == "postgres" or backend == "postgresql":
        from contextsynapse.storage.router.postgres_store import PostgresContentStore
        url = kwargs.get("database_url") or os.environ.get("DATABASE_URL")
        if not url:
            logger.warning("CONTEXTCORE_CONTENT_STORE=postgres but DATABASE_URL not set, falling back to DuckDB")
            backend = "duckdb"
        else:
            logger.info("Content store: PostgreSQL")
            return PostgresContentStore(database_url=url)

    if backend == "duckdb" or backend == "duck":
        from contextsynapse.storage.router.duckdb_store import DuckDBStore
        path = kwargs.get("db_path") or os.environ.get(
            "CONTEXTCORE_DUCKDB_PATH", "contextcore_data/content.duckdb"
        )
        logger.info("Content store: DuckDB (%s)", path)
        return DuckDBStore(db_path=path)

    raise ValueError(
        f"Unknown content store backend: '{backend}'. "
        f"Use 'duckdb' or 'postgres'."
    )


def get_content_store(**kwargs) -> ContentStoreInterface:
    """Get or create the global content store singleton.

    Thread-safe. Reads CONTEXTCORE_CONTENT_STORE env var on first call.
    """
    global _global_store
    if _global_store is None:
        with _factory_lock:
            if _global_store is None:
                _global_store = create_content_store(**kwargs)
    return _global_store


def set_content_store(store: ContentStoreInterface) -> None:
    """Override the global content store (for testing)."""
    global _global_store
    _global_store = store
