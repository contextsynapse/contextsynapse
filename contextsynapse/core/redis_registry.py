"""
Redis-backed Context Registry — scales to 100K+ contexts across multiple workers.

Drop-in replacement for the file-based GraphRegistry. Stores metadata in
Redis hashes, uses sorted sets for LRU tracking, and keeps contexts on disk
with lazy loading into memory.

Architecture:
  - Redis hash per context: `context:{name}` → metadata fields
  - Redis sorted set: `context:lru` → name → last_access_timestamp
  - Redis set: `context:names` → all context names (for fast listing)
  - Local disk: context data files (unchanged)
  - In-memory: LRU cache of loaded contexts (configurable max)

Falls back to file-based metadata.json if Redis is unavailable.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .hybrid_graph_storage import AIContextDB
from .registry import GraphMetadata, _safe_dir_name
from ..security.sanitize import validate_namespace

logger = logging.getLogger(__name__)


class RedisGraphRegistry:
    """
    Redis-backed graph registry for multi-worker, high-scale deployments.

    Same public API as GraphRegistry so it's a drop-in replacement.
    """

    KEY_PREFIX = "context:"
    LRU_KEY = "context:lru"
    NAMES_KEY = "context:names"

    def __init__(self, redis_url: str = None, storage_dir: str = None, max_graphs_in_memory: int = None):
        import redis
        self._redis_url = redis_url or os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
        if not self._redis_url:
            raise ValueError("Redis URL required — set CONTEXTSYNAPSE_REDIS_URL or pass redis_url")
        self._r = redis.from_url(self._redis_url, decode_responses=True, socket_connect_timeout=2)

        self.storage_dir = Path(storage_dir or "contextcore_data")
        self.storage_dir.mkdir(exist_ok=True)

        # In-memory graph cache (LRU, bounded)
        self.graphs: Dict[str, AIContextDB] = {}
        self._lock = threading.RLock()
        self._max_in_memory = max_graphs_in_memory or int(os.environ.get("CONTEXTSYNAPSE_MAX_GRAPHS") or os.environ.get("AICONTEXTDB_MAX_GRAPHS", "50"))

        # Migrate: if metadata.json exists but Redis is empty, seed Redis
        self._migrate_from_file()

        # Migrate: rename old graph:* keys to context:* (one-time)
        self._migrate_graph_to_context_keys()

        # Migrate: rename flat context:{name} keys to context:market:{name} (one-time)
        self._migrate_flat_to_hierarchical()

    # ------------------------------------------------------------------
    # Public API (matches GraphRegistry)
    # ------------------------------------------------------------------

    def create_graph(self, name: str, schema_file: Optional[str] = None,
                     config: Optional[Dict] = None) -> AIContextDB:
        validate_namespace(name)
        now = datetime.now(timezone.utc)
        graph = AIContextDB(name)  # uses default backend from env

        with self._lock:
            self.graphs[name] = graph

        meta = {
            "name": name,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "num_nodes": 0,
            "num_edges": 0,
            "schema_file": schema_file or "",
            "embedding_model": (config or {}).get("embedding_model", ""),
            "access_count": 0,
            "size_mb": 0,
            "tags": json.dumps((config or {}).get("tags", [])),
        }
        pipe = self._r.pipeline()
        pipe.hset(f"{self.KEY_PREFIX}{name}", mapping=meta)
        pipe.sadd(self.NAMES_KEY, name)
        pipe.zadd(self.LRU_KEY, {name: time.time()})
        pipe.execute()

        # Create disk directory
        ns_dir = self.storage_dir / _safe_dir_name(name)
        ns_dir.mkdir(exist_ok=True)

        self._auto_evict()
        logger.info("[REDIS-REG] Created graph '%s'", name)
        return graph

    def get_graph(self, name: str, load_if_missing: bool = True) -> Optional[AIContextDB]:
        with self._lock:
            if name in self.graphs:
                self._touch_lru(name)
                return self.graphs[name]

        if not load_if_missing:
            return None

        # Check if graph exists in Redis
        if not self._r.sismember(self.NAMES_KEY, name):
            return None

        # With Redis graph backend: just create an AIContextDB that connects to Redis
        # No need to load from disk — data lives in Redis
        import os
        graph_backend = os.environ.get("CONTEXTSYNAPSE_GRAPH_BACKEND") or os.environ.get("AICONTEXTDB_GRAPH_BACKEND", "redis")
        if graph_backend == "redis":
            graph = AIContextDB(name)
            with self._lock:
                self.graphs[name] = graph
            self._touch_lru(name)
            return graph

        # CSR backend: load from disk
        graph = self._load_from_disk(name)
        if graph:
            with self._lock:
                self.graphs[name] = graph
            self._touch_lru(name)
            self._auto_evict()
        return graph

    def get_graph_for_request(self, name: str) -> AIContextDB:
        validate_namespace(name)
        graph = self.get_graph(name, load_if_missing=True)
        if graph is None:
            # Create new context — uses default backend (redis or csr from env)
            graph = AIContextDB(name)
            with self._lock:
                self.graphs[name] = graph
        return graph

    def save_graph(self, name: str, create_checkpoint: bool = False):
        """Save graph — updates Redis metadata and exports to disk for backup."""
        import os
        graph_backend = os.environ.get("CONTEXTSYNAPSE_GRAPH_BACKEND") or os.environ.get("AICONTEXTDB_GRAPH_BACKEND", "redis")

        # Count nodes/edges from Redis (source of truth when using Redis backend)
        node_count = 0
        edge_count = 0

        if graph_backend == "redis":
            try:
                from ..storage.redis_graph_storage import RedisGraphStorage
                rgs = RedisGraphStorage(name, self._redis_url)
                node_count = rgs.get_node_count()
                edge_count = rgs.get_edge_count()

                # Export to disk for backup (async-safe, non-blocking)
                ns_dir = self.storage_dir / "namespaces" / _safe_dir_name(name)
                ns_dir.mkdir(parents=True, exist_ok=True)
                backup_file = ns_dir / "graph.json"
                try:
                    data = rgs.export_json()
                    with open(backup_file, "w") as f:
                        json.dump(data, f)
                except Exception:
                    pass  # backup is best-effort
            except Exception as e:
                logger.debug("[REDIS-REG] Redis count failed for '%s': %s", name, e)
        else:
            # CSR backend: save from in-memory graph
            with self._lock:
                graph = self.graphs.get(name)
            if not graph:
                return
            ns_dir = self.storage_dir / "namespaces" / _safe_dir_name(name)
            ns_dir.mkdir(parents=True, exist_ok=True)
            graph_file = ns_dir / "graph.json"
            try:
                graph.save(str(graph_file))
            except Exception:
                pass
            if hasattr(graph, "csr_storage") and graph.csr_storage:
                node_count = graph.csr_storage.get_node_count() if hasattr(graph.csr_storage, "get_node_count") else 0
                edge_count = graph.csr_storage.get_edge_count() if hasattr(graph.csr_storage, "get_edge_count") else 0

        # Update metadata in Redis
        try:
            self._r.hset(f"{self.KEY_PREFIX}{name}", mapping={
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "num_nodes": node_count,
                "num_edges": edge_count,
            })
            self._r.sadd(self.NAMES_KEY, name)
        except Exception as e:
            logger.error("[REDIS-REG] Failed to update metadata for '%s': %s", name, e)

    def delete_graph(self, name: str) -> bool:
        with self._lock:
            self.graphs.pop(name, None)

        pipe = self._r.pipeline()
        pipe.delete(f"{self.KEY_PREFIX}{name}")
        pipe.srem(self.NAMES_KEY, name)
        pipe.zrem(self.LRU_KEY, name)
        pipe.sadd(self.DELETED_KEY, name)  # track as deleted — prevents resurrection
        pipe.execute()

        # Remove from disk
        ns_dir = self.storage_dir / _safe_dir_name(name)
        if ns_dir.exists():
            import shutil
            try:
                shutil.rmtree(ns_dir)
            except Exception:
                pass

        logger.info("[REDIS-REG] Deleted graph '%s'", name)
        return True

    def list_graphs(self) -> List[Dict[str, Any]]:
        names = self._r.smembers(self.NAMES_KEY)
        result = []
        pipe = self._r.pipeline()
        for name in sorted(names):
            pipe.hgetall(f"{self.KEY_PREFIX}{name}")
        metas = pipe.execute()

        for meta in metas:
            if not meta:
                continue
            # Coerce numeric fields from Redis strings to proper types
            for int_field in ("num_nodes", "num_edges", "access_count"):
                try:
                    meta[int_field] = int(meta.get(int_field, 0))
                except (ValueError, TypeError):
                    meta[int_field] = 0
            try:
                meta["size_mb"] = float(meta.get("size_mb", 0))
            except (ValueError, TypeError):
                meta["size_mb"] = 0.0
            # Check if loaded in memory
            meta["in_memory"] = meta.get("name", "") in self.graphs
            meta["on_disk"] = self._get_namespace_path(meta.get("name", "")) is not None
            try:
                meta["tags"] = json.loads(meta.get("tags", "[]"))
            except Exception:
                meta["tags"] = []
            result.append(meta)
        return result

    @property
    def metadata(self) -> Dict[str, GraphMetadata]:
        """Compatibility property — builds metadata dict from Redis."""
        names = self._r.smembers(self.NAMES_KEY)
        result = {}
        for name in names:
            meta = self._r.hgetall(f"{self.KEY_PREFIX}{name}")
            if meta:
                result[name] = GraphMetadata(
                    name=name,
                    created_at=meta.get("created_at", ""),
                    updated_at=meta.get("updated_at", ""),
                    num_nodes=int(meta.get("num_nodes", 0)),
                    num_edges=int(meta.get("num_edges", 0)),
                    schema_file=meta.get("schema_file"),
                    embedding_model=meta.get("embedding_model"),
                    access_count=int(meta.get("access_count", 0)),
                    size_mb=float(meta.get("size_mb", 0)),
                )
        return result

    def graph_count(self) -> int:
        return self._r.scard(self.NAMES_KEY)

    # ------------------------------------------------------------------
    # LRU management
    # ------------------------------------------------------------------

    def _touch_lru(self, name: str):
        pipe = self._r.pipeline()
        pipe.zadd(self.LRU_KEY, {name: time.time()})
        pipe.hincrby(f"{self.KEY_PREFIX}{name}", "access_count", 1)
        pipe.execute()

    def _auto_evict(self):
        """Evict least-recently-used graphs from memory if over limit."""
        with self._lock:
            if len(self.graphs) <= self._max_in_memory:
                return

            # Get LRU order from Redis
            lru_names = self._r.zrange(self.LRU_KEY, 0, -1)
            evict_count = len(self.graphs) - self._max_in_memory

            evicted = 0
            for name in lru_names:
                if evicted >= evict_count:
                    break
                if name in self.graphs:
                    # Save before evicting
                    try:
                        self.save_graph(name)
                    except Exception:
                        pass
                    del self.graphs[name]
                    evicted += 1
                    logger.debug("[REDIS-REG] Evicted graph '%s' from memory (LRU)", name)

    # ------------------------------------------------------------------
    # Disk I/O
    # ------------------------------------------------------------------

    def _load_from_disk(self, name: str) -> Optional[AIContextDB]:
        # Try namespace-centric path first, then flat path
        candidates = [
            self.storage_dir / "namespaces" / _safe_dir_name(name) / "graph.json",
            self.storage_dir / "namespaces" / _safe_dir_name(name) / "graph.h5",
            self.storage_dir / _safe_dir_name(name) / "graph.json",
            self.storage_dir / _safe_dir_name(name) / "graph.h5",
            self.storage_dir / _safe_dir_name(name) / "graph.pkl",
        ]
        graph_file = None
        for c in candidates:
            if c.exists():
                graph_file = c
                break

        if not graph_file:
            return None

        try:
            graph = AIContextDB(name)  # uses default backend from env
            success = graph.load(str(graph_file))
            if success:
                node_count = len(graph.node_index) if hasattr(graph, "node_index") else 0
                logger.debug("[REDIS-REG] Loaded graph '%s' from %s (%d nodes)", name, graph_file.name, node_count)
                return graph
            return None
        except Exception as e:
            logger.error("[REDIS-REG] Failed to load graph '%s': %s", name, e)
            return None

    def _get_namespace_path(self, name: str) -> Optional[Path]:
        for subdir in ["namespaces", ""]:
            base = self.storage_dir / subdir / _safe_dir_name(name) if subdir else self.storage_dir / _safe_dir_name(name)
            for ext in ["graph.json", "graph.h5", "graph.pkl"]:
                p = base / ext
                if p.exists():
                    return p
        return None

    # ------------------------------------------------------------------
    # Migration from file-based metadata.json
    # ------------------------------------------------------------------

    DELETED_KEY = "context:deleted"  # tracks intentionally deleted context names

    # Legacy prefixes (pre-rename)
    _OLD_PREFIX = "graph:"
    _OLD_NAMES_KEY = "graph:names"
    _OLD_LRU_KEY = "graph:lru"
    _OLD_DELETED_KEY = "graph:deleted"

    def _migrate_graph_to_context_keys(self):
        """One-time migration: rename graph:* Redis keys to context:*.

        Runs on startup. If old keys exist and new keys don't, copies data over
        and removes old keys. Safe to run multiple times (idempotent).
        """
        try:
            old_names = self._r.smembers(self._OLD_NAMES_KEY)
            if not old_names:
                return  # nothing to migrate

            new_names = self._r.smembers(self.NAMES_KEY)
            if new_names:
                # New keys already exist — just clean up old keys
                self._r.delete(self._OLD_NAMES_KEY, self._OLD_LRU_KEY, self._OLD_DELETED_KEY)
                for name in old_names:
                    self._r.delete(f"{self._OLD_PREFIX}{name}")
                logger.info("[REDIS-REG] Cleaned up %d old graph:* keys", len(old_names))
                return

            # Copy: graph:{name} → context:{name} for each graph
            pipe = self._r.pipeline()
            count = 0
            for name in old_names:
                meta = self._r.hgetall(f"{self._OLD_PREFIX}{name}")
                if meta:
                    pipe.hset(f"{self.KEY_PREFIX}{name}", mapping=meta)
                pipe.sadd(self.NAMES_KEY, name)
                count += 1

            # Copy LRU scores
            lru_data = self._r.zrange(self._OLD_LRU_KEY, 0, -1, withscores=True)
            for name, score in lru_data:
                pipe.zadd(self.LRU_KEY, {name: score})

            # Copy deleted set
            deleted = self._r.smembers(self._OLD_DELETED_KEY)
            for name in deleted:
                pipe.sadd(self.DELETED_KEY, name)

            pipe.execute()

            # Remove old keys
            pipe2 = self._r.pipeline()
            pipe2.delete(self._OLD_NAMES_KEY, self._OLD_LRU_KEY, self._OLD_DELETED_KEY)
            for name in old_names:
                pipe2.delete(f"{self._OLD_PREFIX}{name}")
            pipe2.execute()

            logger.info("[REDIS-REG] Migrated %d contexts from graph:* to context:* keys", count)
        except Exception as e:
            logger.warning("[REDIS-REG] graph→context key migration failed: %s", e)

    def _migrate_flat_to_hierarchical(self):
        """One-time migration: rename flat context:{name} keys to context:market:{name}.

        Only runs if hierarchical keys don't exist yet. Safe to run multiple times.
        """
        try:
            names = self._r.smembers(self.NAMES_KEY)
            if not names:
                return

            # Check if any names are already hierarchical (contain ':')
            has_hierarchical = any(":" in n for n in names)
            if has_hierarchical:
                return  # already migrated

            pipe = self._r.pipeline()
            count = 0
            for name in names:
                old_key = f"{self.KEY_PREFIX}{name}"
                new_name = f"market:{name}"
                new_key = f"{self.KEY_PREFIX}{new_name}"

                meta = self._r.hgetall(old_key)
                if meta:
                    meta["name"] = new_name
                    pipe.hset(new_key, mapping=meta)
                    pipe.delete(old_key)

                pipe.srem(self.NAMES_KEY, name)
                pipe.sadd(self.NAMES_KEY, new_name)

                # Update LRU
                score = self._r.zscore(self.LRU_KEY, name)
                if score is not None:
                    pipe.zrem(self.LRU_KEY, name)
                    pipe.zadd(self.LRU_KEY, {new_name: score})

                count += 1

            pipe.execute()
            if count > 0:
                logger.info("[REDIS-REG] Migrated %d contexts to hierarchical (market:*) keys", count)
        except Exception as e:
            logger.warning("[REDIS-REG] Flat→hierarchical migration failed: %s", e)

    def _migrate_from_file(self):
        """One-time migration from metadata.json to Redis. Renames file after migration."""
        metadata_file = self.storage_dir / "metadata.json"
        if not metadata_file.exists():
            return

        try:
            with open(metadata_file, "r") as f:
                file_metadata = json.load(f)

            if not file_metadata:
                return

            existing_names = self._r.smembers(self.NAMES_KEY)
            deleted_names = self._r.smembers(self.DELETED_KEY)
            pipe = self._r.pipeline()
            count = 0
            for name, meta in file_metadata.items():
                if name in existing_names:
                    continue  # already in Redis
                if name in deleted_names:
                    continue  # intentionally deleted — don't resurrect
                redis_meta = {
                    "name": name,
                    "created_at": str(meta.get("created_at") or ""),
                    "updated_at": str(meta.get("updated_at") or ""),
                    "num_nodes": int(meta.get("num_nodes") or 0),
                    "num_edges": int(meta.get("num_edges") or 0),
                    "schema_file": str(meta.get("schema_file") or ""),
                    "embedding_model": str(meta.get("embedding_model") or ""),
                    "access_count": int(meta.get("access_count") or 0),
                    "size_mb": float(meta.get("size_mb") or 0),
                    "tags": json.dumps(meta.get("tags") or []),
                }
                pipe.hset(f"{self.KEY_PREFIX}{name}", mapping=redis_meta)
                pipe.sadd(self.NAMES_KEY, name)
                pipe.zadd(self.LRU_KEY, {name: time.time()})
                count += 1

            pipe.execute()
            if count > 0:
                logger.info("[REDIS-REG] Migrated %d graphs from metadata.json to Redis", count)
            # Rename the file so migration never runs again
            migrated_file = self.storage_dir / "metadata.json.migrated"
            try:
                metadata_file.rename(migrated_file)
                logger.info("[REDIS-REG] Renamed metadata.json → metadata.json.migrated")
            except Exception:
                pass
        except Exception as e:
            logger.warning("[REDIS-REG] Migration from metadata.json failed: %s", e)
