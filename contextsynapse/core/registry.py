"""
Dynamic graph creation, caching, and metadata management.
"""

import os
import json
import pickle
import logging
import threading
from typing import Dict, List, Any, Optional, Union
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
import hashlib

import re as _re

from .hybrid_graph_storage import AIContextDB

# Ensure .env is loaded before any graph creation (critical for Redis backend)
try:
    from ..utils.env_loader import initialize_env_loader
    initialize_env_loader()
except Exception:
    pass

logger = logging.getLogger(__name__)


def _safe_dir_name(name: str) -> str:
    """Sanitize a graph name for use as a filesystem directory name.

    Replaces all characters invalid on Windows (<>:"/\\|?*) with underscore.
    """
    return _re.sub(r'[<>:"/\\|?*]', '_', name)

@dataclass
class GraphMetadata:
    """Metadata for a graph instance."""
    name: str
    created_at: datetime
    updated_at: datetime
    num_nodes: int
    num_edges: int
    schema_file: Optional[str] = None
    data_files: List[str] = None
    embedding_model: Optional[str] = None
    last_accessed: Optional[datetime] = None
    access_count: int = 0
    size_mb: float = 0.0
    tags: List[str] = None
    storage_backend: str = "disk"  # "disk" or "redis"
    
    def __post_init__(self):
        if self.data_files is None:
            self.data_files = []
        if self.tags is None:
            self.tags = []

class GraphRegistry:
    """
    Registry for managing multiple graph instances with caching and metadata.
    """
    
    def __init__(self, storage_dir: str = None):
        # Use namespace-centric structure by default
        if storage_dir is None:
            storage_dir = "contextcore_data"
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(exist_ok=True)
        
        self.graphs: Dict[str, AIContextDB] = {}
        self.metadata: Dict[str, GraphMetadata] = {}
        self._lock = threading.RLock()
        self._metadata_mtime: float = 0.0  # track file mtime for multi-worker sync
        self._graph_file_mtimes: Dict[str, float] = {}  # track per-graph file mtime
        self.cache_config = {
            'max_graphs': int(os.environ.get('AICONTEXTDB_MAX_GRAPHS', '50')),
            'max_size_mb': int(os.environ.get('AICONTEXTDB_MAX_GRAPH_SIZE_MB', '1000')),
            'ttl_hours': 24
        }

        # Load existing metadata
        self._load_metadata()
    
    def create_graph(self, name: str, schema_file: Optional[str] = None,
                    config: Optional[Dict] = None,
                    schema_name: Optional[str] = None) -> AIContextDB:
        """
        Create a new graph instance.
        
        Args:
            name: Name of the graph
            schema_file: Optional schema file path
            config: Optional configuration
            
        Returns:
            AIContextDB instance
        """
        with self._lock:
            if name in self.graphs:
                return self.graphs[name]

            # Let AIContextDB pick its backend (Redis if available, else CSR)
            graph = AIContextDB(name, config)

            if schema_file:
                pass

            self.graphs[name] = graph

            # Create ContextMeta root node — metadata anchor for this graph
            from .graph_structures import GraphNode as _GN
            meta_props = {
                "name": name,
                "description": "",
                "purpose": "knowledge_base",
                "schema_summary": "",
                "node_count": 0,
                "edge_count": 0,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "owner": "",
                "tags": [],
            }
            # Include schema in ContextMeta if provided
            if config and config.get("schema"):
                meta_props["schema"] = config["schema"]
            if schema_name:
                meta_props["schema_name"] = schema_name
            graph.add_node(_GN(id="_context_meta", label="ContextMeta", properties=meta_props))

            # Bind namespace to schema if provided
            if schema_name:
                try:
                    from contextsynapse.project.schema_manager import get_schema_manager
                    get_schema_manager().bind(name, schema_name)
                except Exception as e:
                    logger.warning("Could not bind schema '%s' to namespace '%s': %s",
                                   schema_name, name, e)

            metadata = GraphMetadata(
                name=name,
                created_at=datetime.now(),
                updated_at=datetime.now(),
                num_nodes=0,
                num_edges=0,
                schema_file=schema_file,
                storage_backend="redis" if getattr(graph, '_using_redis_backend', False) else "disk",
            )
            self.metadata[name] = metadata
            self._save_metadata()

            # Auto-evict LRU if over limit
            self._auto_cleanup()

            return graph
    
    def drop_graph(self, name: str) -> bool:
        """
        Drop a graph instance and its associated files.

        Args:
            name: Name of the graph to drop

        Returns:
            True if successful, False if graph doesn't exist
        """
        with self._lock:
            if name not in self.graphs and name not in self.metadata:
                return False

            try:
                # Remove from memory
                if name in self.graphs:
                    del self.graphs[name]

                # Remove metadata
                if name in self.metadata:
                    del self.metadata[name]

                # Remove files from disk
                graph_dir = self.storage_dir / name
                if graph_dir.exists():
                    import shutil
                    shutil.rmtree(graph_dir)

                # Save updated metadata
                self._save_metadata()

                return True

            except Exception as e:
                print(f"Error dropping graph '{name}': {e}")
                return False
    
    def get_graph(self, name: str, load_if_missing: bool = True) -> Optional[AIContextDB]:
        """
        Get a graph instance by name.

        Args:
            name: Name of the graph
            load_if_missing: Whether to load from disk if not in memory

        Returns:
            AIContextDB instance or None
        """
        with self._lock:
            self._refresh_metadata_if_stale()

            if name in self.graphs:
                graph = self.graphs[name]
                # Skip disk reload for Redis-backed graphs — Redis is source of truth
                if not getattr(graph, '_using_redis_backend', False):
                    if self._graph_file_is_newer(name):
                        logger.debug("[REGISTRY] Graph '%s' changed on disk — reloading", name)
                        reloaded = self._load_graph_from_disk(name)
                        if reloaded:
                            self.graphs[name] = reloaded
                            graph = reloaded
                self._update_access_metadata(name)
                return graph

            if load_if_missing:
                # For Redis-backed graphs: just create instance (connects to Redis, no disk load)
                import os
                if os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL") and os.environ.get("CONTEXTSYNAPSE_GRAPH_BACKEND") or os.environ.get("AICONTEXTDB_GRAPH_BACKEND", "redis") == "redis":
                    graph = AIContextDB(name)
                    if getattr(graph, '_using_redis_backend', False):
                        self.graphs[name] = graph
                        self._auto_cleanup()
                        logger.debug("[REGISTRY] Graph '%s' created (Redis backend, no disk load)", name)
                        return graph

                # Fallback: load from disk (CSR/LMDB)
                if name in self.metadata:
                    loaded_graph = self._load_graph_from_disk(name)
                    if loaded_graph:
                        self.graphs[name] = loaded_graph
                        self._auto_cleanup()
                        return loaded_graph

            return None
    
    def list_graphs(self) -> List[Dict[str, Any]]:
        """List all available graphs with metadata."""
        with self._lock:
            self._refresh_metadata_if_stale()
            graphs_info = []
            for name, metadata in self.metadata.items():
                info = asdict(metadata)
                info['in_memory'] = name in self.graphs
                info['on_disk'] = True  # assume on disk if in metadata
                info['last_accessed'] = metadata.last_accessed.isoformat() if metadata.last_accessed else None
                info['created_at'] = metadata.created_at.isoformat()
                info['updated_at'] = metadata.updated_at.isoformat()
                graphs_info.append(info)
            return sorted(graphs_info, key=lambda x: x.get('last_accessed') or x.get('created_at', ''), reverse=True)
    
    def delete_graph(self, name: str, delete_files: bool = True) -> bool:
        """
        Delete a graph instance.

        Args:
            name: Name of the graph
            delete_files: Whether to delete associated files

        Returns:
            True if successful
        """
        with self._lock:
            try:
                # Close WAL/buffer handles before deleting
                if name in self.graphs:
                    graph = self.graphs[name]
                    if hasattr(graph, 'wal') and graph.wal:
                        try:
                            graph.wal.running = False
                            if graph.wal.wal_file:
                                graph.wal.wal_file.close()
                                graph.wal.wal_file = None
                        except Exception:
                            pass
                    del self.graphs[name]
                else:
                    # Graph not in memory — try to close any orphaned WAL file handles
                    # This handles the case where a graph was deleted but WAL is
                    # still locked by a stale file handle
                    safe_name = _safe_dir_name(name)
                    for wal_dir in [
                        self.storage_dir / "wal" / safe_name,
                        self.storage_dir / "wal" / name,
                    ]:
                        if wal_dir.exists():
                            for wal_file in wal_dir.glob("*.log"):
                                try:
                                    # Open and immediately close to release any stale handle
                                    with open(wal_file, "a"):
                                        pass
                                except Exception:
                                    pass

                # Remove metadata
                if name in self.metadata:
                    del self.metadata[name]

                # Delete files if requested
                if delete_files:
                    import shutil as _shutil
                    safe_name = _safe_dir_name(name)
                    # Clean namespace directories (relative to storage_dir)
                    for base in [
                        self.storage_dir / "namespaces" / safe_name,
                        self.storage_dir / "namespaces" / name,
                        self.storage_dir / safe_name,
                    ]:
                        if base.exists():
                            _shutil.rmtree(base, ignore_errors=True)
                    # Clean WAL directory
                    for wal in [
                        self.storage_dir / "wal" / safe_name,
                        self.storage_dir / "wal" / name,
                    ]:
                        if wal.exists():
                            try:
                                _shutil.rmtree(wal)
                            except PermissionError:
                                # WAL locked by another process — close handles and retry
                                for wf in wal.glob("*.log"):
                                    try:
                                        with open(wf, "a"):
                                            pass
                                    except Exception:
                                        pass
                                try:
                                    _shutil.rmtree(wal)
                                except Exception as e:
                                    logger.warning("WAL cleanup failed for %s: %s (will clear on restart)", name, e)
                            except Exception as e:
                                logger.warning("WAL cleanup failed for %s: %s", name, e)

                # Save updated metadata
                self._save_metadata()

                # Cascade: delete any context/session that uses this graph namespace
                # Use storage_dir-relative context.db so it works with custom paths
                context_db = str(self.storage_dir / "context.db")
                try:
                    import sqlite3 as _sqlite3
                    conn = _sqlite3.connect(context_db)
                    conn.row_factory = _sqlite3.Row
                    row = conn.execute(
                        "SELECT context_id FROM contexts WHERE graph_namespace = ?",
                        (name,),
                    ).fetchone()
                    if row:
                        conn.execute(
                            "DELETE FROM contexts WHERE graph_namespace = ?",
                            (name,),
                        )
                        conn.commit()
                        # Remove from default graph
                        default = self.graphs.get("default")
                        if default and row["context_id"]:
                            try:
                                default.remove_node(row["context_id"])
                            except Exception:
                                pass  # optional
                    conn.close()
                except Exception:
                    pass  # optional

                try:
                    import sqlite3 as _sqlite3
                    conn = _sqlite3.connect(context_db)
                    conn.execute(
                        "DELETE FROM context_sessions WHERE graph_namespace = ?",
                        (name,),
                    )
                    conn.commit()
                    conn.close()
                except Exception:
                    pass  # optional

                return True
            except Exception as e:
                print(f"Error deleting graph '{name}': {e}")
                return False
    
    def save_graph(self, name: str, create_checkpoint: bool = True) -> bool:
        """
        Save a graph to disk - uses namespace-centric structure for PERSISTENT namespaces.
        Optionally creates a checkpoint (Git-like versioning) for rollback capability.
        
        This method is used by BOTH direct graph operations and API operations.
        It ensures the same code path is used regardless of how the graph was created/modified.
        
        Args:
            name: Name of the graph/namespace
            create_checkpoint: Whether to create a checkpoint after saving (default: True)
            
        Returns:
            True if successful
        """
        if name not in self.graphs:
            logger.error(f"[REGISTRY SAVE] Graph '{name}' not found in registry!")
            return False
        
        try:
            graph = self.graphs[name]
            if graph is None:
                logger.error(f"[REGISTRY SAVE] Graph '{name}' is None in registry!")
                return False

            # Skip disk save for Redis-backed graphs — Redis is the persistence layer
            if getattr(graph, '_using_redis_backend', False):
                logger.debug("[REGISTRY SAVE] Skipping disk save for Redis-backed graph '%s'", name)
                if name in self.metadata:
                    self.metadata[name].storage_backend = "redis"
                self._save_metadata()
                return True
            
            # Single deterministic path — relative to storage_dir
            safe_name = _safe_dir_name(name)
            graph_file = self.storage_dir / "namespaces" / safe_name / "graph.json"
            
            # Ensure directory exists
            graph_file.parent.mkdir(parents=True, exist_ok=True)
            
            # NOTE: Aliases are now query-scoped (stored in executor, not in graph)
            # No need to save aliases to disk
            
            node_count = len(graph.node_index) if hasattr(graph, 'node_index') else 0
            logger.info(f"[REGISTRY SAVE] Saving graph '{name}': {node_count} nodes")
            
            # Save graph data
            success = graph.save(str(graph_file))
            
            if success:
                # Create metadata entry if it doesn't exist yet (e.g. graph
                # populated via ingestion without an explicit create_graph call)
                if name not in self.metadata:
                    self.metadata[name] = GraphMetadata(
                        name=name,
                        created_at=datetime.now(),
                        updated_at=datetime.now(),
                        num_nodes=0,
                        num_edges=0,
                    )

                # Update metadata
                metadata = self.metadata[name]
                metadata.updated_at = datetime.now()

                # Get node and edge counts — try multiple sources, take max
                node_count = 0
                edge_count = 0
                if hasattr(graph, 'csr_storage') and graph.csr_storage:
                    node_count = max(node_count, graph.csr_storage.get_node_count() if hasattr(graph.csr_storage, 'get_node_count') else 0)
                    edge_count = max(edge_count, graph.csr_storage.get_edge_count() if hasattr(graph.csr_storage, 'get_edge_count') else 0)
                if hasattr(graph, 'node_index') and graph.node_index:
                    node_count = max(node_count, len(graph.node_index))
                if hasattr(graph, 'edge_store') and graph.edge_store:
                    edge_count = max(edge_count, len(graph.edge_store))
                metadata.num_nodes = node_count
                metadata.num_edges = edge_count

                # Check actual file size (may be .h5 instead of .json)
                actual_file = graph_file if graph_file.exists() else graph_file.with_suffix('.h5')
                if actual_file.exists():
                    metadata.size_mb = actual_file.stat().st_size / (1024 * 1024)
                
                # Track file mtime so this worker knows it wrote this version
                # Check both .json and .h5 since save() may produce either format
                try:
                    actual_file = graph_file if graph_file.exists() else graph_file.with_suffix('.h5')
                    if not actual_file.exists():
                        # Also check the parent dir for graph.h5
                        h5_path = graph_file.parent / "graph.h5"
                        if h5_path.exists():
                            actual_file = h5_path
                    self._graph_file_mtimes[name] = actual_file.stat().st_mtime
                except Exception:
                    pass  # optional

                # Save metadata
                self._save_metadata()

                # Create checkpoint (Git-like versioning) if enabled
                if create_checkpoint:
                    try:
                        from .checkpoint import checkpoint_manager
                        checkpoint_manager.create_checkpoint(
                            namespace=safe_name,
                            graph_file=str(graph_file),
                            message="Auto-save checkpoint"
                        )
                    except Exception as e:
                        # Don't fail save if checkpoint fails
                        logger.warning(f"Failed to create checkpoint: {e}")
                
                return True
            
            return False
        except Exception as e:
            print(f"Error saving graph '{name}': {e}")
            return False
    
    def load_graph(self, name: str) -> bool:
        """
        Load a graph from disk.
        
        Args:
            name: Name of the graph
            
        Returns:
            True if successful
        """
        return self._load_graph_from_disk(name) is not None
    
    def _get_namespace_path(self, name: str) -> Optional[Path]:
        """Get the path to a namespace's graph file (if it exists) without loading.

        Single deterministic lookup — relative to storage_dir.
        Priority: namespace-centric JSON → namespace-centric H5 → old flat-structure fallback.
        """
        try:
            safe_name = _safe_dir_name(name)
            ns_dir = self.storage_dir / "namespaces" / safe_name
            candidates = [
                ns_dir / "graph.json",
                ns_dir / "graph.h5",
                # Old flat structure fallback
                self.storage_dir / safe_name / "graph.json",
                self.storage_dir / safe_name / "graph.h5",
            ]
            for c in candidates:
                if c.exists():
                    return c
            return None
        except Exception as e:
            logger.debug(f"Error getting namespace path for '{name}': {e}")
            return None
    
    def _namespace_exists_on_disk(self, name: str) -> bool:
        """Check if namespace exists on disk (fast check, no loading)."""
        graph_file = self._get_namespace_path(name)
        return graph_file is not None and graph_file.exists()
    
    def _load_graph_from_disk(self, name: str) -> Optional[AIContextDB]:
        """Load a graph from disk - supports both old and namespace-centric structures."""
        try:
            graph_file = self._get_namespace_path(name)
            
            if not graph_file or not graph_file.exists():
                return None
            
            # Create graph instance with CSR storage enabled by default
            graph = AIContextDB(name, storage_backend='csr')
            
            # Load graph data
            success = graph.load(str(graph_file))
            
            if success:
                node_count = len(graph.node_index) if hasattr(graph, 'node_index') else 0
                logger.debug(f"[LOAD] Loaded graph '{name}': {node_count} nodes")

                # Store in registry
                self.graphs[name] = graph

                # Track file mtime for multi-worker change detection
                try:
                    self._graph_file_mtimes[name] = graph_file.stat().st_mtime
                except Exception:
                    pass  # optional

                # Update access metadata
                self._update_access_metadata(name)

                return graph
            
            return None
        except Exception as e:
            logger.error(f"Error loading graph '{name}': {e}")
            return None
    
    def get_graph_for_request(self, name: str) -> AIContextDB:
        """Get graph instance for a request — delegates to get_graph().

        Creates and caches a transient instance if the graph isn't in metadata
        (e.g. Redis-backed graphs created at runtime). The cache ensures all
        callers within the same process share the same instance — critical for
        multi-agent experiments where agents must see each other's writes.
        """
        graph = self.get_graph(name, load_if_missing=True)
        if graph is None:
            # Graph doesn't exist in metadata or on disk — create a transient one.
            # Let AIContextDB pick its backend (Redis if available, else CSR).
            graph = AIContextDB(name)
            # Cache it so subsequent calls return the SAME instance
            with self._lock:
                self.graphs[name] = graph
        return graph
    
    def _update_access_metadata(self, name: str):
        """Update access metadata for a graph."""
        if name in self.metadata:
            metadata = self.metadata[name]
            metadata.last_accessed = datetime.now()
            metadata.access_count += 1
    
    def _load_metadata(self):
        """Load metadata from LMDB (fast) or disk JSON (fallback)."""
        # Try LMDB first — microsecond reads
        if self._load_metadata_lmdb():
            # Prune stale entries — skip Redis-backed graphs (they have no disk file by design)
            stale = [
                n for n in self.metadata
                if self.metadata[n].storage_backend != "redis"
                and not self._namespace_exists_on_disk(n)
            ]
            for n in stale:
                logger.debug(f"[REGISTRY] Pruning stale metadata entry '{n}' (no file on disk)")
                del self.metadata[n]
            if stale:
                logger.info(f"[REGISTRY] Pruned {len(stale)} stale metadata entries")
                # Write back to LMDB so they don't reload next startup
                self._save_metadata_lmdb()
            return

        # Fallback: JSON file
        metadata_file = self.storage_dir / "metadata.json"

        if metadata_file.exists():
            try:
                with open(metadata_file, 'r') as f:
                    data = json.load(f)

                for name, meta_data in data.items():
                    meta_data['created_at'] = datetime.fromisoformat(meta_data['created_at'])
                    meta_data['updated_at'] = datetime.fromisoformat(meta_data['updated_at'])
                    if meta_data.get('last_accessed'):
                        meta_data['last_accessed'] = datetime.fromisoformat(meta_data['last_accessed'])
                    self.metadata[name] = GraphMetadata(**meta_data)

                # Prune stale entries — if graph file doesn't exist on disk, remove
                # (skip Redis-backed graphs — they have no disk file by design)
                stale = [
                    n for n in self.metadata
                    if self.metadata[n].storage_backend != "redis"
                    and not self._namespace_exists_on_disk(n)
                ]
                for n in stale:
                    logger.debug(f"[REGISTRY] Pruning stale metadata entry '{n}' (no file on disk)")
                    del self.metadata[n]
                if stale:
                    logger.info(f"[REGISTRY] Pruned {len(stale)} stale metadata entries")
                    self._save_metadata()

                # Track file mtime so we can detect changes from other workers
                if metadata_file.exists():
                    self._metadata_mtime = metadata_file.stat().st_mtime
            except Exception as e:
                print(f"Error loading metadata: {e}")

    def _graph_file_is_newer(self, name: str) -> bool:
        """Check if another worker has saved a newer version of this graph to disk."""
        try:
            safe_name = _safe_dir_name(name)
            base = self.storage_dir / "namespaces" / safe_name
            # Check both .json and .h5 formats
            for ext in ("graph.h5", "graph.json"):
                graph_file = base / ext
                if graph_file.exists():
                    current_mtime = graph_file.stat().st_mtime
                    last_known = self._graph_file_mtimes.get(name, 0.0)
                    if current_mtime > last_known:
                        return True
            return False
        except Exception:
            return False

    def _refresh_metadata_if_stale(self):
        """Re-read metadata.json if another worker has written to it.

        Called before read operations (list_graphs, get_graph) so that
        multi-worker deployments stay consistent without Redis.
        """
        metadata_file = self.storage_dir / "metadata.json"
        try:
            if metadata_file.exists():
                current_mtime = metadata_file.stat().st_mtime
                if current_mtime > self._metadata_mtime:
                    logger.debug("[REGISTRY] metadata.json changed on disk — reloading")
                    old_names = set(self.metadata.keys())
                    self._load_metadata()
                    new_names = set(self.metadata.keys())
                    # Evict in-memory graphs that were deleted by another worker
                    for removed in old_names - new_names:
                        self.graphs.pop(removed, None)
        except Exception:
            pass  # optional

    def _save_metadata(self):
        """Save metadata to disk + LMDB."""
        metadata_file = self.storage_dir / "metadata.json"

        try:
            data = {}
            for name, metadata in self.metadata.items():
                meta_dict = asdict(metadata)
                meta_dict['created_at'] = metadata.created_at.isoformat()
                meta_dict['updated_at'] = metadata.updated_at.isoformat()
                if metadata.last_accessed:
                    meta_dict['last_accessed'] = metadata.last_accessed.isoformat()
                data[name] = meta_dict

            with open(metadata_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)

            # Update tracked mtime so we don't re-read our own write
            self._metadata_mtime = metadata_file.stat().st_mtime
        except Exception as e:
            print(f"Error saving metadata: {e}")

        # Also persist to LMDB for fast reads
        try:
            self._save_metadata_lmdb()
        except Exception:
            pass

    def _get_lmdb_meta_env(self):
        """Get or create LMDB environment for metadata."""
        if not hasattr(self, '_lmdb_meta_env') or self._lmdb_meta_env is None:
            try:
                import lmdb
                lmdb_path = str(self.storage_dir / "metadata.lmdb")
                Path(lmdb_path).mkdir(parents=True, exist_ok=True)
                self._lmdb_meta_env = lmdb.open(lmdb_path, map_size=64 * 1024 * 1024)
            except Exception:
                self._lmdb_meta_env = None
        return self._lmdb_meta_env

    def _save_metadata_lmdb(self):
        """Persist all metadata to LMDB, deleting entries for removed graphs."""
        env = self._get_lmdb_meta_env()
        if not env:
            return
        with env.begin(write=True) as txn:
            # Collect existing LMDB keys so we can delete orphaned ones
            existing_keys = {key.decode() for key, _ in txn.cursor()}
            for name, metadata in self.metadata.items():
                meta_dict = asdict(metadata)
                meta_dict['created_at'] = metadata.created_at.isoformat()
                meta_dict['updated_at'] = metadata.updated_at.isoformat()
                if metadata.last_accessed:
                    meta_dict['last_accessed'] = metadata.last_accessed.isoformat()
                txn.put(name.encode(), json.dumps(meta_dict).encode())
                existing_keys.discard(name)
            # Delete LMDB entries for graphs that no longer exist
            for orphaned_key in existing_keys:
                txn.delete(orphaned_key.encode())
                logger.debug("[REGISTRY] Deleted orphaned LMDB entry: %s", orphaned_key)

    def _load_metadata_lmdb(self) -> bool:
        """Try loading metadata from LMDB. Returns True if successful."""
        env = self._get_lmdb_meta_env()
        if not env:
            return False
        try:
            with env.begin() as txn:
                cursor = txn.cursor()
                count = 0
                for key, val in cursor:
                    name = key.decode()
                    meta_data = json.loads(val.decode())
                    meta_data['created_at'] = datetime.fromisoformat(meta_data['created_at'])
                    meta_data['updated_at'] = datetime.fromisoformat(meta_data['updated_at'])
                    if meta_data.get('last_accessed'):
                        meta_data['last_accessed'] = datetime.fromisoformat(meta_data['last_accessed'])
                    self.metadata[name] = GraphMetadata(**meta_data)
                    count += 1
            if count > 0:
                logger.info("[REGISTRY] Loaded %d graph metadata from LMDB", count)
                return True
        except Exception as e:
            logger.debug("[REGISTRY] LMDB metadata load failed: %s", e)
        return False
    
    def _auto_cleanup(self):
        """Evict LRU in-memory graphs when over max_graphs limit.

        Called internally after loading/creating graphs. Assumes the caller
        already holds ``self._lock``.
        """
        max_graphs = self.cache_config['max_graphs']
        if len(self.graphs) <= max_graphs:
            return

        # Sort loaded graphs by last_accessed (oldest first)
        sorted_graphs = sorted(
            [(name, self.metadata[name].last_accessed or self.metadata[name].created_at)
             for name in self.graphs
             if name in self.metadata],
            key=lambda x: x[1]
        )

        excess = len(self.graphs) - max_graphs
        for name, _ in sorted_graphs[:excess]:
            # Save to disk before evicting so in-memory changes aren't lost
            try:
                self.save_graph(name, create_checkpoint=False)
            except Exception:
                logger.warning(f"[REGISTRY] Failed to save graph '{name}' before eviction")
            logger.info(f"[REGISTRY] Auto-evicting LRU graph '{name}' from memory")
            del self.graphs[name]

    def cleanup_cache(self):
        """Clean up cache based on configuration (public, acquires lock)."""
        with self._lock:
            # Remove graphs that haven't been accessed recently
            cutoff_time = datetime.now() - timedelta(hours=self.cache_config['ttl_hours'])

            graphs_to_remove = []
            for name, metadata in self.metadata.items():
                if (name in self.graphs and
                    metadata.last_accessed and
                    metadata.last_accessed < cutoff_time):
                    graphs_to_remove.append(name)

            for name in graphs_to_remove:
                if name in self.graphs:
                    del self.graphs[name]

            # Delegate count/size eviction to _auto_cleanup
            self._auto_cleanup()

            # Check total size
            total_size = sum(metadata.size_mb for metadata in self.metadata.values())
            if total_size > self.cache_config['max_size_mb']:
                sorted_by_size = sorted(
                    [(name, metadata.size_mb) for name, metadata in self.metadata.items()],
                    key=lambda x: x[1],
                    reverse=True
                )

                current_size = total_size
                for name, size in sorted_by_size:
                    if current_size <= self.cache_config['max_size_mb']:
                        break
                    if name in self.graphs:
                        del self.graphs[name]
                        current_size -= size
    
    def get_stats(self) -> Dict[str, Any]:
        """Get registry statistics."""
        return {
            'total_graphs': len(self.metadata),
            'loaded_graphs': len(self.graphs),
            'total_nodes': sum(metadata.num_nodes for metadata in self.metadata.values()),
            'total_edges': sum(metadata.num_edges for metadata in self.metadata.values()),
            'total_size_mb': sum(metadata.size_mb for metadata in self.metadata.values()),
            'cache_config': self.cache_config
        }
    
    def search_graphs(self, query: str, tags: List[str] = None) -> List[Dict[str, Any]]:
        """
        Search graphs by name, tags, or metadata.
        
        Args:
            query: Search query
            tags: Optional tags to filter by
            
        Returns:
            List of matching graph metadata
        """
        results = []
        query_lower = query.lower()
        
        for name, metadata in self.metadata.items():
            # Check name match
            if query_lower in name.lower():
                results.append(name)
                continue
            
            # Check tag match
            if tags:
                if any(tag in metadata.tags for tag in tags):
                    results.append(name)
                    continue
            
            # Check metadata match
            if (query_lower in str(metadata.num_nodes) or
                query_lower in str(metadata.num_edges) or
                query_lower in metadata.embedding_model.lower() if metadata.embedding_model else False):
                results.append(name)
        
        # Return full metadata for matches
        return [asdict(self.metadata[name]) for name in results]

    # ── Property Indexes ─────────────────────────────────────────────────
    # Lightweight in-memory indexes built incrementally on add_node calls.
    # Structure: {graph_name: {field: {value: set(node_ids)}}}
    _INDEXED_FIELDS = ("node_type", "_sentiment", "stock_symbol", "portfolio_id", "source_type")

    def _ensure_property_index(self, graph_name: str) -> Dict[str, Dict[str, set]]:
        """Get or create the property index dict for a graph."""
        if not hasattr(self, '_property_indexes'):
            self._property_indexes: Dict[str, Dict[str, Dict[str, set]]] = {}
        if graph_name not in self._property_indexes:
            self._property_indexes[graph_name] = {f: {} for f in self._INDEXED_FIELDS}
        return self._property_indexes[graph_name]

    def index_node(self, graph_name: str, node_id: str, label: str = "", properties: Dict = None):
        """Index a node's key properties for O(1) lookup.

        Called incrementally when nodes are added — not a retroactive rebuild.
        """
        idx = self._ensure_property_index(graph_name)
        # Index by node_type / label
        if label:
            idx["node_type"].setdefault(label, set()).add(node_id)
        if properties:
            for field in self._INDEXED_FIELDS:
                if field == "node_type":
                    continue  # handled above via label
                val = properties.get(field)
                if val is not None:
                    idx[field].setdefault(str(val), set()).add(node_id)

    def query_nodes(self, graph_name: str, node_type: str = None,
                    properties: Dict = None, limit: int = 100) -> List:
        """Query nodes with indexed lookup instead of full scan.

        Uses indexes for: node_type, _sentiment, stock_symbol, portfolio_id, source_type.
        Falls back to scan if no index exists for the requested fields.
        """
        graph = self.get_graph(graph_name, load_if_missing=True)
        if not graph:
            return []

        # Try indexed path first
        idx = self._ensure_property_index(graph_name)
        candidate_ids = None

        # Narrow by node_type index
        if node_type and "node_type" in idx and node_type in idx["node_type"]:
            candidate_ids = set(idx["node_type"][node_type])

        # Narrow by property indexes
        if properties:
            for field, val in properties.items():
                if field in idx and str(val) in idx[field]:
                    matching = idx[field][str(val)]
                    if candidate_ids is None:
                        candidate_ids = set(matching)
                    else:
                        candidate_ids &= matching

        # If we have indexed candidates, resolve to nodes
        if candidate_ids is not None:
            results = []
            adapter = getattr(graph, 'csr_adapter', graph)
            csr = getattr(adapter, 'csr_storage', None)
            for nid in list(candidate_ids)[:limit]:
                node = None
                if csr and hasattr(csr, 'get_node'):
                    node = csr.get_node(nid)
                elif hasattr(graph, 'node_index') and nid in graph.node_index:
                    node = graph.node_index[nid]
                if node:
                    results.append(node)
            return results

        # Fallback: use get_nodes_by_type if only node_type is requested
        if node_type and not properties:
            return self.get_nodes_by_type(graph_name, node_type, limit=limit)

        # Final fallback: full scan with filter
        return self._scan_nodes(graph, node_type=node_type, properties=properties, limit=limit)

    def get_nodes_by_type(self, graph_name: str, node_type: str, limit: int = 100) -> List:
        """Get nodes by type using CSR adapter's type storage if available,
        falling back to filtered scan."""
        graph = self.get_graph(graph_name, load_if_missing=True)
        if not graph:
            return []

        # Try CSR adapter's typed storage
        adapter = getattr(graph, 'csr_adapter', graph)
        csr = getattr(adapter, 'csr_storage', None)
        if csr and hasattr(csr, 'get_all_nodes'):
            try:
                nodes = csr.get_all_nodes(node_type=node_type)
                return nodes[:limit]
            except TypeError:
                # get_all_nodes may not accept node_type kwarg
                pass

        # Fallback: property index
        idx = self._ensure_property_index(graph_name)
        if node_type in idx.get("node_type", {}):
            ids = list(idx["node_type"][node_type])[:limit]
            results = []
            for nid in ids:
                if hasattr(graph, 'node_index') and nid in graph.node_index:
                    results.append(graph.node_index[nid])
                elif csr and hasattr(csr, 'get_node'):
                    node = csr.get_node(nid)
                    if node:
                        results.append(node)
            return results

        # Final fallback: scan
        return self._scan_nodes(graph, node_type=node_type, limit=limit)

    def _scan_nodes(self, graph, node_type: str = None,
                    properties: Dict = None, limit: int = 100) -> List:
        """Full scan with optional filters (last resort)."""
        results = []
        all_nodes = []
        if hasattr(graph, 'node_index') and graph.node_index:
            all_nodes = list(graph.node_index.values())
        else:
            adapter = getattr(graph, 'csr_adapter', graph)
            if hasattr(adapter, 'get_all_nodes'):
                all_nodes = adapter.get_all_nodes()

        for node in all_nodes:
            if len(results) >= limit:
                break
            label = getattr(node, 'label', getattr(node, 'node_type', ''))
            if node_type and label != node_type:
                continue
            if properties:
                props = getattr(node, 'properties', {})
                if not all(props.get(k) == v for k, v in properties.items()):
                    continue
            results.append(node)
        return results

# Global singleton instance for backward compatibility
graph_registry = GraphRegistry()
