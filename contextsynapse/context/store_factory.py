"""
Store Factory
==============
Single point where the backend decision is made: Redis if available,
SQLite fallback.  Everything else imports from here.

Usage:
    from contextsynapse.context.store_factory import create_agent_registry

    registry = create_agent_registry()   # auto-selects backend
    agent, key = registry.register(name="my-agent")
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_cached_registry = None


def create_agent_registry(redis_url: Optional[str] = None, force_new: bool = False):
    """Create an agent registry — Redis if available, SQLite fallback.

    Args:
        redis_url: Redis connection URL. Falls back to AICONTEXTDB_REDIS_URL env var.
        force_new: If True, create a new instance (don't use cached).

    Returns:
        RedisAgentRegistry or AgentRegistry (SQLite), same API.
    """
    global _cached_registry
    if _cached_registry is not None and not force_new:
        return _cached_registry

    url = redis_url or os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
    if url:
        try:
            from .agents_redis import RedisAgentRegistry
            registry = RedisAgentRegistry(redis_url=url)
            registry._r.ping()
            logger.info("Agent registry: Redis (%s)", url.split("@")[-1])
            _cached_registry = registry
            return registry
        except Exception as e:
            logger.warning("Redis unavailable (%s), falling back to SQLite", e)

    from .agents import AgentRegistry
    registry = AgentRegistry()
    logger.info("Agent registry: SQLite")
    _cached_registry = registry
    return registry
