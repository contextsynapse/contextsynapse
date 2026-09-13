"""
Registry Factory — auto-select Redis or file-based graph registry.

Usage:
    from contextsynapse.core.registry_factory import create_graph_registry

    registry = create_graph_registry()  # Redis if available, file fallback
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_cached_registry = None


def create_graph_registry(redis_url: Optional[str] = None, storage_dir: Optional[str] = None, force_new: bool = False):
    """Create a graph registry — Redis if available, file-based fallback.

    Args:
        redis_url: Redis URL. Falls back to AICONTEXTDB_REDIS_URL env var.
        storage_dir: Disk storage directory. Default: contextcore_data
        force_new: Create new instance even if cached.

    Returns:
        RedisGraphRegistry or GraphRegistry, same public API.
    """
    global _cached_registry
    if _cached_registry is not None and not force_new:
        return _cached_registry

    url = redis_url or os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
    if url:
        try:
            from .redis_registry import RedisGraphRegistry
            registry = RedisGraphRegistry(redis_url=url, storage_dir=storage_dir)
            registry._r.ping()
            count = registry.graph_count()
            logger.info("Graph registry: Redis (%s) — %d graphs", url.split("@")[-1], count)
            _cached_registry = registry
            return registry
        except Exception as e:
            logger.warning("Redis graph registry unavailable (%s), falling back to file-based", e)

    from .registry import GraphRegistry
    registry = GraphRegistry(storage_dir=storage_dir)
    logger.info("Graph registry: file-based (%s) — %d graphs", registry.storage_dir, len(registry.metadata))
    _cached_registry = registry
    return registry
