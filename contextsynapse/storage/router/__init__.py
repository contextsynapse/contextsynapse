"""Storage Router -- routes data to the right store based on type.

Graph stays thin (entities + edges). Content goes to the configured
content store (DuckDB default, PostgreSQL for production).
Vectors go to Qdrant. Blobs go to filesystem.

Configure backend via env:
    CONTEXTCORE_CONTENT_STORE=duckdb     (default, embedded)
    CONTEXTCORE_CONTENT_STORE=postgres   (requires DATABASE_URL)
"""
from contextsynapse.storage.router.interface import ContentStoreInterface
from contextsynapse.storage.router.router import StorageRouter, get_storage_router
from contextsynapse.storage.router.resolver import ContentResolver, get_content_resolver
from contextsynapse.storage.router.factory import (
    create_content_store,
    get_content_store,
    set_content_store,
)

__all__ = [
    "ContentStoreInterface",
    "StorageRouter",
    "get_storage_router",
    "ContentResolver",
    "get_content_resolver",
    "create_content_store",
    "get_content_store",
    "set_content_store",
]
