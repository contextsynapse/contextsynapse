"""
Graph Coordinator
==================
Redis-coordinated graph access for multi-worker deployments.

Each API worker holds its own in-memory CSR graph. Redis provides:
- Version counter per namespace (detect stale cache)
- Distributed write lock (prevent concurrent saves)
- Pub/Sub invalidation (notify workers of changes)

The graph engine stays in-memory per-worker (fast reads). Redis is
NOT the graph store — it's the coordination layer.

Read path (hot — must be fast):
    1. Check local version vs Redis version (1 GET, ~0.1ms)
    2. If match → serve from memory (zero I/O)
    3. If mismatch → reload from disk

Write path:
    1. Acquire lock (SETNX with 30s TTL)
    2. Write to CSR + save to disk
    3. INCR version counter
    4. PUBLISH invalidation
    5. Release lock

Usage:
    coordinator = GraphCoordinator(registry)
    graph = coordinator.get_graph("my-namespace")   # version-checked
    coordinator.save_graph("my-namespace")           # locked + versioned
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

_WORKER_ID = str(uuid.uuid4())[:8]


class GraphCoordinator:
    """Redis-coordinated graph access for multi-worker scaling.

    Wraps a GraphRegistry with version checking and distributed locking.
    Falls back to direct registry access when Redis is not available.
    """

    def __init__(self, registry, redis_url: Optional[str] = None, prefix: str = "contextsynapse"):
        self._registry = registry
        self._prefix = prefix
        self._redis = None
        self._local_versions: dict[str, int] = {}

        url = redis_url or os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
        if url:
            try:
                import redis
                self._redis = redis.Redis.from_url(url, decode_responses=True)
                self._redis.ping()
                logger.info("GraphCoordinator: Redis connected (%s)", url.split("@")[-1])
            except Exception as e:
                logger.warning("GraphCoordinator: Redis unavailable (%s), using single-worker mode", e)
                self._redis = None

    def _k(self, *parts: str) -> str:
        return ":".join([self._prefix, *parts])

    # ==================================================================
    # Read path (hot — must be fast)
    # ==================================================================

    def get_graph(self, namespace: str, load_if_missing: bool = True):
        """Get a graph, reloading from disk if the version is stale.

        This is the main read path — called on every API request.
        """
        if not self._redis:
            # No Redis → direct registry access (single-worker mode)
            return self._registry.get_graph(namespace, load_if_missing=load_if_missing)

        # Check version
        remote_version = self._get_remote_version(namespace)
        local_version = self._local_versions.get(namespace, -1)

        if local_version == remote_version:
            # Cache hit — serve from memory
            cached = self._registry.get_graph(namespace, load_if_missing=False)
            if cached:
                return cached

        # Cache miss or stale — reload from disk
        graph = self._registry.get_graph(namespace, load_if_missing=load_if_missing)
        if graph:
            self._local_versions[namespace] = remote_version
            logger.debug("GraphCoordinator: reloaded %s (version %s)", namespace, remote_version)
        return graph

    # ==================================================================
    # Write path (locked + versioned)
    # ==================================================================

    def save_graph(self, namespace: str, create_checkpoint: bool = False) -> bool:
        """Save a graph with distributed locking and version increment.

        Only one worker can save a namespace at a time.
        """
        if not self._redis:
            return self._registry.save_graph(namespace, create_checkpoint=create_checkpoint)

        lock_key = self._k("graph", "lock", namespace)
        lock_acquired = False

        try:
            # Acquire distributed lock (SETNX with 30s TTL)
            lock_acquired = self._redis.set(lock_key, _WORKER_ID, nx=True, ex=30)
            if not lock_acquired:
                holder = self._redis.get(lock_key)
                logger.warning("GraphCoordinator: namespace %s locked by worker %s", namespace, holder)
                # Wait briefly and retry once
                time.sleep(0.5)
                lock_acquired = self._redis.set(lock_key, _WORKER_ID, nx=True, ex=30)
                if not lock_acquired:
                    return False

            # Save to disk
            result = self._registry.save_graph(namespace, create_checkpoint=create_checkpoint)

            if result:
                # Increment version counter
                new_version = self._redis.incr(self._k("graph", "version", namespace))
                self._local_versions[namespace] = new_version

                # Publish invalidation to other workers
                self._redis.publish(
                    self._k("graph", "invalidate"),
                    f"{namespace}:{new_version}",
                )
                logger.debug("GraphCoordinator: saved %s (version %s)", namespace, new_version)

            return result

        finally:
            # Release lock (only if we hold it)
            if lock_acquired:
                current_holder = self._redis.get(lock_key)
                if current_holder == _WORKER_ID:
                    self._redis.delete(lock_key)

    def create_graph(self, namespace: str, **kwargs):
        """Create a new graph with version tracking."""
        graph = self._registry.create_graph(namespace, **kwargs)
        if self._redis:
            self._redis.setnx(self._k("graph", "version", namespace), 0)
        return graph

    # ==================================================================
    # Invalidation (handle messages from other workers)
    # ==================================================================

    def invalidate(self, namespace: str):
        """Force reload of a namespace on next access."""
        if namespace in self._local_versions:
            del self._local_versions[namespace]

    def handle_invalidation(self, message: str):
        """Handle a pub/sub invalidation message from another worker."""
        try:
            ns, version = message.rsplit(":", 1)
            self.invalidate(ns)
            logger.debug("GraphCoordinator: invalidated %s (remote version %s)", ns, version)
        except Exception:
            pass

    # ==================================================================
    # Internals
    # ==================================================================

    def _get_remote_version(self, namespace: str) -> int:
        try:
            val = self._redis.get(self._k("graph", "version", namespace))
            return int(val) if val else 0
        except Exception:
            return 0

    @property
    def is_coordinated(self) -> bool:
        """True if Redis coordination is active."""
        return self._redis is not None

    def info(self) -> dict:
        """Get coordinator status."""
        return {
            "coordinated": self.is_coordinated,
            "worker_id": _WORKER_ID,
            "cached_namespaces": list(self._local_versions.keys()),
            "local_versions": dict(self._local_versions),
        }
