"""
CSR-Only Graph Storage Implementation
High-performance CSR storage backend - NetworkX fallback disabled
"""

from typing import Dict, List, Any, Optional, Union, Set, Tuple
from dataclasses import dataclass
import uuid
import time
import logging
import json
from collections import defaultdict

from ..storage.csr_graph_storage import CSRGraphStorage, CSRGraphStorageAdapter, CSRNode, CSREdge
from .graph_structures import GraphNode, GraphEdge

logger = logging.getLogger(__name__)

# Optional imports (removed modules)
TEMPORAL_AVAILABLE = True
HASHING_AVAILABLE = False

# Try to import sortedcontainers for B-tree indexes
try:
    from sortedcontainers import SortedDict
    SORTED_CONTAINERS_AVAILABLE = True
except ImportError:
    SORTED_CONTAINERS_AVAILABLE = False
    # Fallback to dict
    SortedDict = dict
    logger.warning("sortedcontainers not available, using dict for indexes (slower)")

# RAG-critical node types that need immediate write-through (bypass buffer)
RAG_CRITICAL_NODE_TYPES = {
    'Document', 'Chunk', 'TextChunk', 'Passage', 'Fact', 'Entity',
    'Table', 'Image', 'Embedding', 'Index', 'KnowledgeGraph',
    'ContextBoundary', 'CategoryBoundary',
    # Session graph conversation layer — need immediate visibility for
    # cross-layer REFERENCES edge linking and context building
    'Turn', 'Decision', 'Question', 'Action', 'Topic', 'AgentPresence',
}

# Metadata node types (use fast buffer with 0.5s flush)
METADATA_NODE_TYPES = {
    'Metadata', 'Config', 'Schema', 'IndexMetadata', 'Pipeline'
}

class VersionConflictError(Exception):
    """Raised when an optimistic-lock version check fails on node update."""

    def __init__(self, current_version: int, provided_version: int):
        self.current_version = current_version
        self.provided_version = provided_version
        super().__init__(
            f"Version conflict: stored version is {current_version}, "
            f"but update provided version {provided_version}. "
            f"Re-fetch the node and retry."
        )


class ContextSynapse:
    """ContextSynapse - CSR-only graph storage for high-performance operations (NetworkX disabled)."""

    def __init__(self, name: str = "default", config: Optional[Dict] = None, storage_backend: str = 'csr'):
        """
        Initialize Cognitive Database.
        
        Args:
            name: Name of the graph database
            config: Optional configuration dictionary
            storage_backend: 'csr' only (NetworkX fallback disabled)
        
        Raises:
            ValueError: If storage_backend is not 'csr'
        """
        self.name = name
        self.config = config or {}
        
        # CRITICAL: Force CSR-only mode - NetworkX fallback disabled
        if storage_backend != 'csr':
            logger.warning(f"NetworkX fallback disabled. Forcing CSR-only mode. Received: {storage_backend}")
            storage_backend = 'csr'
        
        self.storage_backend = 'csr'  # Always CSR
        self.use_csr = True  # Always True - no fallback
        
        # Enable single-file HDF5 storage by default (Oracle-type format)
        if 'graph' not in self.config:
            self.config['graph'] = {}
        # NEO4J-LEVEL PERFORMANCE: Default to HDF5 single-file storage (faster than JSON)
        if 'storage_strategy' not in self.config['graph']:
            self.config['graph']['storage_strategy'] = 'single_file'  # HDF5 single-file (faster)
        if 'storage_format' not in self.config['graph']:
            self.config['graph']['storage_format'] = 'single_file'  # Backward compatibility
        
        # CSR-only: No NetworkX storage
        # Thread-safe write lock for concurrent ingestion
        import threading
        self._write_lock = threading.RLock()

        # Maintain minimal compatibility structures for properties
        self.node_properties = {}  # Store node properties separately
        self.edge_properties = {}  # Store edge properties separately
        self.edge_store = {}  # Full edge metadata: edge_uuid -> {source, target, label, properties, weight}
        self.unique_constraints = defaultdict(set)
        # NOTE: alias_to_node removed - aliases are now query-scoped (stored in executor only)
        self.global_strategy = 'DFS'
        
        # Create node_index for compatibility
        self.node_index = {}
        
        # Create edge_index for compatibility
        self.edge_index = {}
        
        # NEW: Property indexes for fast filtering
        if SORTED_CONTAINERS_AVAILABLE:
            self._property_indexes: Dict[str, SortedDict] = {}
        else:
            self._property_indexes: Dict[str, Dict[Any, Set[str]]] = {}
        
        # Performance metrics
        self.operation_counts = {
            'add_node': 0,
            'add_edge': 0,
            'get_neighbors': 0,
            'traverse': 0
        }
        
        # Time travel / Temporal storage (always enabled)
        self.temporal_enabled = True
        self.temporal_storage = None
        try:
            from ..temporal.storage import TemporalStorage
            base_path = self.config.get('base_path', 'contextcore_data')
            self.temporal_storage = TemporalStorage(namespace=name, base_path=base_path)
            self.temporal_storage.load_versions()
            logger.debug(f"[TIME_TRAVEL] Temporal storage ready for namespace: {name}")
        except Exception as e:
            logger.warning(f"[TIME_TRAVEL] Failed to initialize temporal storage: {e}")
            self.temporal_enabled = False
            self.temporal_storage = None
        
        # Semantic hashing (optional, enabled via config)
        self.semantic_hashing_enabled = self.config.get('hashing', {}).get('enabled', False)
        self.hasher = None
        if self.semantic_hashing_enabled:
            try:
                from ..hashing.comprehensive_hashing import ComprehensiveHasher
                self.hasher = ComprehensiveHasher()
                logger.info(f"[SEMANTIC_HASH] Semantic hashing enabled for namespace: {name}")
            except Exception as e:
                logger.warning(f"[SEMANTIC_HASH] Failed to initialize semantic hasher: {e}")
                self.semantic_hashing_enabled = False
        
        # Initialize traceability layer (optional, for transparency and integrity)
        # Check config file first, then local config
        from ..config.database_config import DatabaseConfigManager
        try:
            config_manager = DatabaseConfigManager()
            db_config = config_manager.config
            traceability_enabled = db_config.traceability.enabled
        except:
            traceability_enabled = self.config.get('traceability', {}).get('enabled', False)
        
        self.traceability_enabled = traceability_enabled
        self.traceability_layer = None
        if self.traceability_enabled:
            try:
                from ..traceability.traceability_layer import TraceabilityLayer
                self.traceability_layer = TraceabilityLayer(name, base_path="contextcore_data")
                logger.info(f"Traceability layer enabled for graph '{name}'")
            except Exception as e:
                logger.warning(f"Failed to initialize traceability layer: {e}")

        # WAL (Write-Ahead Log) — lazy init on first write for speed
        self.wal = None
        self._wal_enabled = True
        self._wal_name = name
        try:
            self._wal_enabled = self.config.get('wal', {}).get('enabled', True)
        except Exception:
            pass
        
        # CRITICAL: Enable buffer manager by default with delayed block updates
        # Buffer writes to memory first, then flushes to CSR storage after delay
        buffer_config = self.config.get('buffer', {})
        if 'enabled' not in buffer_config:
            buffer_config['enabled'] = True  # Enable by default
        if 'config' not in buffer_config:
            buffer_config['config'] = {}
        buffer_config['config'].setdefault('max_size', 1000)  # Max records before flush
        buffer_config['config'].setdefault('flush_interval', 2.0)  # Flush every 2 seconds (delayed block update)
        buffer_config['config'].setdefault('batch_size', 100)  # Records per batch
        buffer_config['config'].setdefault('auto_flush', True)  # Enable auto-flush
        buffer_config['config'].setdefault('flush_threshold', 0.7)  # Flush when buffer reaches 70% capacity (0.0-1.0)
        buffer_config['config'].setdefault('threshold_flush_enabled', True)  # Enable threshold-based flushing
        
        self.buffer_enabled = buffer_config.get('enabled', True)
        self.buffer_manager = None
        # OPTIMIZATION: Pre-initialize buffer manager synchronously during __init__
        # This avoids async initialization overhead during first node creation
        if self.buffer_enabled:
            try:
                from ..buffer import create_buffer_manager
                buffer_type = buffer_config.get('type', 'local')
                buffer_params = buffer_config.get('config', {})
                buffer_params['namespace'] = name  # Add namespace for multi-tenant buffers
                self.buffer_manager = create_buffer_manager(buffer_type, buffer_params)
                # Store writer function reference for later use
                self._buffer_writer_fn_ref = self._buffer_writer_fn
                
                # Register cleanup on instance destruction
                import atexit
                atexit.register(self._cleanup_buffer_manager)
                
                # OPTIMIZATION: Initialize buffer manager synchronously if possible
                # This avoids async overhead during first node creation
                try:
                    import asyncio
                    # Try to get existing event loop
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            # Loop is running, initialize async (will be done lazily)
                            self._buffer_initialized = False
                        else:
                            # Loop exists but not running, initialize synchronously
                            loop.run_until_complete(self.buffer_manager.initialize())
                            if buffer_params.get('auto_flush', True):
                                loop.run_until_complete(self.buffer_manager.start_auto_flush(writer_fn=self._buffer_writer_fn_ref))
                            self._buffer_initialized = True
                            logger.debug(f"Buffer manager pre-initialized for graph '{name}'")
                    except RuntimeError:
                        # No event loop, create one and initialize
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        loop.run_until_complete(self.buffer_manager.initialize())
                        if buffer_params.get('auto_flush', True):
                            loop.run_until_complete(self.buffer_manager.start_auto_flush(writer_fn=self._buffer_writer_fn_ref))
                        self._buffer_initialized = True
                        logger.debug(f"Buffer manager pre-initialized for graph '{name}' (new loop)")
                except Exception as init_error:
                    # If sync initialization fails, fall back to lazy initialization
                    logger.debug(f"Buffer manager will be initialized lazily: {init_error}")
                    self._buffer_initialized = False
                
                logger.debug(f"Buffer manager enabled for graph '{name}' (type: {buffer_type}, flush_interval: {buffer_params.get('flush_interval', 2.0)}s)")
            except Exception as e:
                logger.warning(f"Failed to initialize buffer manager: {e}")
                self.buffer_enabled = False
                self._buffer_initialized = False
        else:
            self._buffer_initialized = False
        
        # Initialize graph storage backend: Redis (shared) or CSR (in-memory)
        import os
        graph_backend = os.environ.get("CONTEXTSYNAPSE_GRAPH_BACKEND") or os.environ.get("AICONTEXTDB_GRAPH_BACKEND", "csr")
        self._using_redis_backend = False

        if graph_backend == "redis" and os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL"):
            try:
                from ..storage.redis_graph_adapter import RedisGraphAdapter
                self.csr_adapter = RedisGraphAdapter(name)
                self.csr_storage = self.csr_adapter  # compatibility
                self._using_redis_backend = True
                # Disable buffer for Redis — Redis handles persistence directly
                self.buffer_enabled = False
                self.buffer_manager = None
                self._buffer_initialized = True
                logger.info("[REDIS] Graph '%s' using Redis backend (no in-memory loading)", name)
            except Exception as e:
                logger.warning("[REDIS] Redis graph backend failed, falling back to CSR: %s", e)
                graph_backend = "csr"

        # Check for LMDB backend
        self._using_lmdb_backend = False
        if not self._using_redis_backend:
            storage_backend_env = os.environ.get("CONTEXTSYNAPSE_STORAGE_BACKEND") or os.environ.get("AICONTEXTDB_STORAGE_BACKEND", "csr")
            if storage_backend_env == "lmdb":
                try:
                    from ..storage.lmdb_graph_storage import LMDBGraphStorage, LMDB_AVAILABLE
                    if LMDB_AVAILABLE:
                        lmdb_path = os.environ.get("CONTEXTSYNAPSE_LMDB_PATH") or os.environ.get("AICONTEXTDB_LMDB_PATH", "contextcore_data/lmdb")
                        lmdb_dir = os.path.join(lmdb_path, self.name)
                        self.csr_storage = LMDBGraphStorage(lmdb_dir, namespace=self.name)
                        self.csr_adapter = self.csr_storage
                        self._using_lmdb_backend = True
                        logger.info("[STORAGE] Using LMDB backend: %s", lmdb_dir)
                    else:
                        logger.warning("[STORAGE] LMDB requested but lmdb/msgpack not installed, falling back to CSR")
                except Exception as e:
                    logger.warning("[STORAGE] LMDB init failed (%s), falling back to CSR", e)

        if not self._using_redis_backend and not self._using_lmdb_backend:
            try:
                self.csr_storage = CSRGraphStorage()
                self.csr_adapter = CSRGraphStorageAdapter(self.csr_storage)
                logger.debug("[CSR] Graph '%s' using in-memory CSR storage", name)
            except Exception as e:
                logger.error("[CSR] Failed to initialize CSR storage: %s", e)
                raise RuntimeError(f"Graph storage initialization failed: {e}")
        
        # Initialize Time Travel (always enabled)
        self.temporal_enabled = True
        self.temporal_storage = None
        try:
            from ..temporal.storage import TemporalStorage as _TemporalStorage
            base_path = self.config.get('temporal', {}).get('base_path', self.config.get('base_path', 'contextcore_data'))
            self.temporal_storage = _TemporalStorage(namespace=name, base_path=base_path)
            self.temporal_storage.load_versions()
            logger.debug(f"[TIME_TRAVEL] Temporal storage ready for namespace: {name}")
        except Exception as e:
            logger.warning(f"[TIME_TRAVEL] Failed to initialize temporal storage: {e}")
            self.temporal_enabled = False
            self.temporal_storage = None

        # Initialize Semantic Hashing (if available and enabled)
        self.hashing_enabled = self.config.get('hashing', {}).get('enabled', False) and HASHING_AVAILABLE
        if self.hashing_enabled:
            try:
                self.hasher = get_global_hasher()
                logger.info(f"[EMOJI] Semantic hashing enabled for namespace: {name}")
            except Exception as e:
                logger.warning(f"[EMOJI] Failed to initialize semantic hashing: {e}")
                self.hashing_enabled = False
                self.hasher = None
        else:
            self.hasher = None
    
    def _clean_properties_for_indexing(self, properties: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively clean properties to remove unhashable types that could cause indexing errors.
        
        Converts nested dicts and lists to JSON strings to make them hashable.
        """
        import copy
        import json
        cleaned = {}
        for key, value in properties.items():
            if isinstance(value, dict):
                # Convert nested dict to JSON string
                try:
                    cleaned[key] = json.dumps(value, sort_keys=True)
                except (TypeError, ValueError):
                    # If can't serialize, skip it
                    continue
            elif isinstance(value, list):
                # Convert list to JSON string
                try:
                    cleaned[key] = json.dumps(value, sort_keys=True)
                except (TypeError, ValueError):
                    # If can't serialize, skip it
                    continue
            else:
                # Keep primitive types as-is
                cleaned[key] = value
        return cleaned
    
    def _cleanup_buffer_manager(self):
        """Cleanup buffer manager on instance destruction."""
        if self.buffer_manager:
            try:
                # Stop auto-flush if running
                if hasattr(self.buffer_manager, 'stop_auto_flush'):
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            # Schedule cleanup
                            asyncio.create_task(self.buffer_manager.stop_auto_flush())
                        else:
                            # Run cleanup synchronously
                            loop.run_until_complete(self.buffer_manager.stop_auto_flush())
                    except (RuntimeError, AttributeError):
                        # No event loop - task will be cleaned up by Python
                        pass
                
                # Close buffer manager
                if hasattr(self.buffer_manager, 'close'):
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            asyncio.create_task(self.buffer_manager.close())
                        else:
                            loop.run_until_complete(self.buffer_manager.close())
                    except (RuntimeError, AttributeError):
                        pass
            except Exception as e:
                logger.debug(f"Error cleaning up buffer manager for {self.name}: {e}")
    
    def load(self, file_path: str) -> bool:
        """
        Load graph data from a file.
        Tries to load CSR format first (for performance), falls back to JSON.
        
        Args:
            file_path: Path to the graph file
            
        Returns:
            True if successful, False otherwise
        """
        try:
            from pathlib import Path
            from scipy.sparse import load_npz
            
            file_path_obj = Path(file_path)
            base_dir = file_path_obj.parent
            base_name = file_path_obj.stem
            
            # Check storage strategy and load accordingly
            graph_config = self.config.get('graph', {})
            storage_strategy = graph_config.get('storage_strategy') or graph_config.get('storage_format', 'single_file')
            
            # Try single-file HDF5 first if strategy is single_file
            if storage_strategy == 'single_file':
                h5_path = file_path_obj.with_suffix('.h5')
                if h5_path.exists():
                    return self._load_single_file(str(h5_path))
            # Try multi-file optimized if strategy is multi_file_optimized
            elif storage_strategy == 'multi_file_optimized':
                if self._load_multi_file_optimized(str(file_path_obj)):
                    return True
            # Auto-detect: try single-file first, then multi-file
            else:
                h5_path = file_path_obj.with_suffix('.h5')
                if h5_path.exists():
                    return self._load_single_file(str(h5_path))
            
            # Try to load CSR format first (faster for large graphs)
            csr_file = base_dir / "csr" / f"{base_name}_csr.npz"
            csr_metadata_file = base_dir / "csr" / f"{base_name}_csr_metadata.json"
            
            if csr_file.exists() and csr_metadata_file.exists():
                try:
                    logger.info(f"Loading graph from CSR format: {csr_file}")
                    
                    # Load CSR matrix
                    csr_matrix_data = load_npz(str(csr_file))
                    
                    # Load CSR metadata
                    with open(csr_metadata_file, 'r') as f:
                        csr_metadata = json.load(f)
                    
                    node_list = csr_metadata.get('node_list', [])
                    node_to_idx = {node_id: idx for idx, node_id in enumerate(node_list)}
                    idx_to_node = {idx: node_id for node_id, idx in node_to_idx.items()}
                    edge_labels = csr_metadata.get('edge_labels', [])
                    edge_properties_list = csr_metadata.get('edge_properties', [])
                    
                    # Load nodes from metadata (we'll need to get properties from JSON)
                    # First, load JSON to get node properties
                    with open(file_path, 'r') as f:
                        json_data = json.load(f)
                    
                    # CRITICAL: Ensure CSR adapter is initialized
                    if self.use_csr and not self.csr_adapter:
                        from ..storage.csr_graph_storage import CSRGraphStorageAdapter
                        if self.csr_storage:
                            self.csr_adapter = CSRGraphStorageAdapter(self.csr_storage)
                            logger.info("Initialized CSR adapter during CSR format load")
                    
                    # Note: alias_to_node removed - aliases are query-scoped only (stored in executor)
                    
                    # Create node properties map
                    node_props_map = {}
                    if 'nodes' in json_data:
                        for node_data in json_data['nodes']:
                            node_id = node_data.get('id')
                            if node_id:
                                node_props_map[node_id] = node_data.get('properties', {})
                    
                    # Add nodes
                    for node_id in node_list:
                        label = 'Node'
                        properties = node_props_map.get(node_id, {})
                        # Get label from JSON data if available
                        if 'nodes' in json_data:
                            for node_data in json_data['nodes']:
                                if node_data.get('id') == node_id:
                                    label = node_data.get('label', 'Node')
                                    break
                        
                        node = GraphNode(id=node_id, label=label, properties=properties)
                        properties_cleaned = self._clean_properties_for_indexing(properties)
                        node.properties, original_props = properties_cleaned, node.properties
                        self.add_node(node)
                        self.node_properties[node_id] = original_props
                        if node_id in self.node_index:
                            self.node_index[node_id].properties = original_props
                    
                    # Add edges from CSR matrix
                    csr_matrix_data = csr_matrix_data.tocoo()  # Convert to COO for iteration
                    for i, (row_idx, col_idx) in enumerate(zip(csr_matrix_data.row, csr_matrix_data.col)):
                        if i < len(edge_labels):
                            src = idx_to_node.get(row_idx)
                            dst = idx_to_node.get(col_idx)
                            edge_label = edge_labels[i] if i < len(edge_labels) else 'Edge'
                            edge_props = edge_properties_list[i] if i < len(edge_properties_list) else {}
                            
                            if src and dst:
                                self.add_edge(GraphEdge(
                                    id=f"{src}_{dst}_{i}",
                                    source=src,
                                    target=dst,
                                    label=edge_label,
                                    properties=edge_props
                                ))
                    
                    logger.info(f"[EMOJI] Loaded graph from CSR format: {len(node_list)} nodes, {len(edge_labels)} edges")
                    return True
                    
                except Exception as e:
                    logger.warning(f"Failed to load CSR format: {e}, falling back to JSON")
            
            # Fallback to JSON format
            logger.info(f"Loading graph from JSON format: {file_path}")
            with open(file_path, 'r') as f:
                data = json.load(f)
            
            # Try to load node properties from Parquet (faster than JSON for large graphs)
            parquet_file = base_dir / "node_properties" / f"{base_name}_nodes.parquet"
            if parquet_file.exists():
                try:
                    import pandas as pd
                    nodes_df = pd.read_parquet(parquet_file, engine='pyarrow')
                    logger.info(f"Loading node properties from Parquet: {parquet_file}")
                    
                    # Update node properties from Parquet (if available)
                    parquet_nodes = {}
                    for _, row in nodes_df.iterrows():
                        node_id = row['node_id']
                        props = {}
                        for col in nodes_df.columns:
                            if col.startswith('prop_'):
                                prop_key = col.replace('prop_', '')
                                value = row[col]
                                # Try to parse JSON strings back to objects
                                if pd.notna(value) and isinstance(value, str) and (value.startswith('{') or value.startswith('[')):
                                    try:
                                        props[prop_key] = json.loads(value)
                                    except:
                                        props[prop_key] = value
                                elif pd.notna(value):
                                    props[prop_key] = value
                        if props:
                            parquet_nodes[node_id] = props
                    
                    # Merge Parquet properties into JSON data if they exist
                    if parquet_nodes:
                        logger.info(f"Loaded {len(parquet_nodes)} node properties from Parquet")
                        for node in data.get('nodes', []):
                            node_id = node.get('id')
                            if node_id in parquet_nodes:
                                # Merge Parquet properties (may be more up-to-date)
                                if 'properties' not in node:
                                    node['properties'] = {}
                                node['properties'].update(parquet_nodes[node_id])
                except ImportError:
                    logger.debug("PyArrow/pandas not available, using JSON node properties only")
                except Exception as e:
                    logger.debug(f"Failed to load node properties from Parquet: {e}, using JSON")
            
            # CRITICAL: Ensure CSR adapter is initialized
            # CSR adapter is created in __init__, but ensure it exists
            if self.use_csr and not self.csr_adapter:
                from ..storage.csr_graph_storage import CSRGraphStorageAdapter
                if self.csr_storage:
                    self.csr_adapter = CSRGraphStorageAdapter(self.csr_storage)
                    logger.info("Initialized CSR adapter during graph load")
            
            # Note: alias_to_node removed - aliases are query-scoped only (stored in executor)
            
            # Load nodes
            if 'nodes' in data:
                for node_data in data['nodes']:
                    try:
                        node_id = node_data['id']
                        label = node_data.get('label', 'Node')
                        properties = node_data.get('properties', {})
                        # Ensure properties is a dict (not nested dicts that could cause unhashable errors)
                        if isinstance(properties, dict):
                            # Deep copy to avoid reference issues
                            import copy
                            properties = copy.deepcopy(properties)
                            # Clean properties for indexing (convert nested dicts/lists to JSON strings)
                            properties_cleaned = self._clean_properties_for_indexing(properties)
                            # Keep original properties for node, but use cleaned for indexing
                            node = GraphNode(id=node_id, label=label, properties=properties)
                            # Temporarily replace properties with cleaned version for add_node (to avoid indexing errors)
                            # Then restore original properties after indexing
                            node.properties, original_props = properties_cleaned, node.properties
                            self.add_node(node)
                            # Restore original properties after indexing
                            self.node_properties[node_id] = original_props
                            if node_id in self.node_index:
                                self.node_index[node_id].properties = original_props
                        else:
                            properties = {}
                            self.add_node(GraphNode(id=node_id, label=label, properties=properties))
                    except Exception as e:
                        logger.warning(f"[EMOJI][EMOJI] Skipping node {node_data.get('id', 'unknown')} due to error: {e}")
                        continue
            
            # Load edges
            if 'edges' in data:
                for edge_data in data['edges']:
                    # Handle both 'src'/'dst' and 'source'/'target' formats
                    src = edge_data.get('src', edge_data.get('source', ''))
                    dest = edge_data.get('dst', edge_data.get('dest', edge_data.get('target', '')))
                    label = edge_data.get('label', edge_data.get('type', 'Edge'))
                    properties = edge_data.get('properties', {})
                    weight = edge_data.get('weight', 1.0)
                    
                    if src and dest:  # Only add edge if both source and target exist
                        self.add_edge(GraphEdge(
                            id=f"{src}_{dest}", 
                            source=src, 
                            target=dest, 
                            label=label, 
                            properties=properties
                        ))
            
            return True
        except Exception as e:
            logger.error(f"Error loading graph from {file_path}: {e}")
            return False
    
    def _load_single_file(self, h5_path: str) -> bool:
        """
        Load graph from single-file HDF5 format.
        
        Args:
            h5_path: Path to HDF5 file
            
        Returns:
            True if successful
        """
        try:
            from ..storage.single_file_storage import SingleFileStorage
            
            with SingleFileStorage(h5_path, mode='r') as storage:
                data = storage.load_graph()
                if not data:
                    return False
                
                # Note: alias_to_node removed - aliases are query-scoped only (stored in executor)
                
                # Load nodes (always write_through — bypass async buffer during load)
                for node_data in data.get('nodes', []):
                    node_id = node_data.get('id')
                    label = node_data.get('label', 'Node')
                    properties = node_data.get('properties', {})

                    node = GraphNode(id=node_id, label=label, properties=properties)
                    properties_cleaned = self._clean_properties_for_indexing(properties)
                    node.properties, original_props = properties_cleaned, node.properties
                    self.add_node(node, write_through=True)
                    self.node_properties[node_id] = original_props
                    if node_id in self.node_index:
                        self.node_index[node_id].properties = original_props
                
                # Load edges
                for edge_data in data.get('edges', []):
                    src = edge_data.get('src', edge_data.get('source', ''))
                    dst = edge_data.get('dst', edge_data.get('target', ''))
                    label = edge_data.get('label', 'Edge')
                    properties = edge_data.get('properties', {})
                    
                    if src and dst:
                        self.add_edge(GraphEdge(
                            id=f"{src}_{dst}",
                            source=src,
                            target=dst,
                            label=label,
                            properties=properties
                        ))
                
                # Try to load CSR matrix
                csr_matrix_data, csr_metadata = storage.load_csr_matrix()
                if csr_matrix_data is not None:
                    logger.info("Loaded CSR matrix from HDF5")
                    # CSR matrix can be used for fast operations
                    # Note: Full CSR integration would require CSR adapter setup
                
                logger.info(f"Loaded graph from single-file HDF5: {len(data.get('nodes', []))} nodes, {len(data.get('edges', []))} edges")
                return True
                
        except ImportError:
            logger.error("h5py not available. Install with: pip install h5py")
            return False
        except Exception as e:
            logger.error(f"Failed to load from single-file format: {e}")
            return False
    
    def flush(self):
        """Flush any buffered writes to CSR storage for immediate visibility."""
        if self.buffer_enabled and self.buffer_manager:
            try:
                import asyncio
                loop = asyncio.new_event_loop()
                loop.run_until_complete(self.buffer_manager.flush())
                loop.close()
            except Exception:
                pass

    def save(self, file_path: str) -> bool:
        """
        Save graph data to a file.
        Saves both JSON format (for compatibility) and CSR format (for performance).
        Optionally saves to single-file HDF5 format if configured.
        
        This method is used by BOTH direct graph operations and API operations.
        It ensures the same code path is used regardless of how the graph was created/modified.
        
        Args:
            file_path: Path to save the graph file
            
        Returns:
            True if successful, False otherwise
        """
        try:
            from pathlib import Path
            import numpy as np
            from scipy.sparse import save_npz
            
            file_path_obj = Path(file_path)
            base_dir = file_path_obj.parent
            base_name = file_path_obj.stem
            
            # Check storage strategy (prefer storage_strategy over storage_format for backward compatibility)
            graph_config = self.config.get('graph', {})
            storage_strategy = graph_config.get('storage_strategy') or graph_config.get('storage_format', 'single_file')
            
            if storage_strategy == 'single_file':
                return self._save_single_file(file_path)
            elif storage_strategy == 'multi_file_optimized':
                return self._save_multi_file_optimized(file_path)
            # Fallback to legacy multi-file format
            
            # 1. Save JSON format (for compatibility and human readability)
            # Note: Aliases are NOT saved (they are query-scoped only)
            
            # CSR-only: Get node/edge counts from CSR storage
            node_count = self.csr_storage.get_node_count() if self.csr_storage else 0
            edge_count = self.csr_storage.get_edge_count() if self.csr_storage else 0
            
            data = {
                'nodes': [],
                'edges': [],
                # 'alias_to_node': {},  # REMOVED: Aliases are query-scoped, not persisted
                'metadata': {
                    'name': self.name,
                    'node_count': node_count,
                    'edge_count': edge_count,
                    'storage_backend': self.storage_backend,
                    'saved_at': time.time(),
                    'has_csr': self.csr_storage is not None and node_count > 0
                }
            }
            
            # Save nodes from CSR storage
            nodes_saved = 0
            if self.csr_storage and hasattr(self.csr_storage, 'nodes'):
                nodes_dict = self.csr_storage.nodes if hasattr(self.csr_storage, 'nodes') else {}
                logger.debug(f"[SAVE] CSR storage has {len(nodes_dict)} nodes in nodes dict")
                
                for node_id, csr_node in nodes_dict.items():
                    # Handle both CSRNode objects and GraphNode objects
                    if hasattr(csr_node, 'node_type'):
                        node_type = csr_node.node_type
                    elif hasattr(csr_node, 'label'):
                        node_type = csr_node.label
                    else:
                        node_type = 'Node'
                    
                    # Get properties from node_properties dict (stored separately)
                    node_props = self.node_properties.get(node_id, {})
                    if not node_props and hasattr(csr_node, 'properties'):
                        node_props = csr_node.properties
                    
                    node_data = {
                        'id': node_id,
                        'label': node_type,
                        'properties': node_props
                    }
                    data['nodes'].append(node_data)
                    nodes_saved += 1
                
                logger.info(f"[SAVE] Saved {nodes_saved} nodes from CSR storage")
            
            # FALLBACK: If CSR storage has no nodes but node_index does, use node_index
            if not data['nodes'] and hasattr(self, 'node_index') and self.node_index:
                logger.warning(f"[SAVE] CSR storage has no nodes, using node_index fallback ({len(self.node_index)} nodes)")
                for node_id, node in self.node_index.items():
                    node_data = {
                        'id': node_id,
                        'label': node.label if hasattr(node, 'label') else 'Node',
                        'properties': self.node_properties.get(node_id, node.properties if hasattr(node, 'properties') else {})
                    }
                    data['nodes'].append(node_data)
                    nodes_saved += 1
                logger.info(f"[SAVE] Saved {nodes_saved} nodes from node_index fallback")
            
            # Update node count in metadata
            data['metadata']['node_count'] = len(data['nodes'])
            
            # Save edges from CSR storage
            saved_edge_pairs = set()  # Track (source, target, label) to avoid duplicates
            if self.csr_storage and hasattr(self.csr_storage, 'edge_data'):
                for edge in self.csr_storage.edge_data:
                    edge_id = f"{edge.source_id}_{edge.target_id}"
                    edge_info = {
                        'src': edge.source_id,
                        'dst': edge.target_id,
                        'label': edge.edge_type,
                        'properties': self.edge_properties.get(edge_id, edge.properties),
                        'weight': edge.weight
                    }
                    data['edges'].append(edge_info)
                    saved_edge_pairs.add((edge.source_id, edge.target_id, edge.edge_type))

            # Fallback: save edges from edge_store that weren't in CSR
            # This catches edges where CSR add_edge silently failed (e.g., node not in CSR)
            for edge_id, edge_meta in self.edge_store.items():
                pair = (edge_meta['source'], edge_meta['target'], edge_meta.get('label', 'EDGE'))
                if pair in saved_edge_pairs:
                    continue
                edge_info = {
                    'src': edge_meta['source'],
                    'dst': edge_meta['target'],
                    'label': edge_meta.get('label', 'EDGE'),
                    'properties': edge_meta.get('properties', {}),
                    'weight': edge_meta.get('weight', 1.0)
                }
                data['edges'].append(edge_info)
                saved_edge_pairs.add(pair)
            
            # Write JSON file (for compatibility)
            with open(file_path, 'w') as f:
                json.dump(data, f, indent=2)
            
            logger.info(f"Graph '{self.name}' saved to {file_path} (JSON format)")
            
            # 3. Save node properties to Parquet (for fast property queries)
            try:
                import pandas as pd
                parquet_dir = base_dir / "node_properties"
                parquet_dir.mkdir(parents=True, exist_ok=True)
                
                # Convert node properties to DataFrame
                if data['nodes']:
                    nodes_df_data = []
                    for node in data['nodes']:
                        row = {
                            'node_id': node['id'],
                            'label': node['label']
                        }
                        # Flatten properties
                        props = node.get('properties', {})
                        for key, value in props.items():
                            # Handle nested properties by converting to string
                            if isinstance(value, (dict, list)):
                                row[f'prop_{key}'] = json.dumps(value)
                            else:
                                row[f'prop_{key}'] = value
                        nodes_df_data.append(row)
                    
                    if nodes_df_data:
                        nodes_df = pd.DataFrame(nodes_df_data)
                        parquet_file = parquet_dir / f"{base_name}_nodes.parquet"
                        nodes_df.to_parquet(parquet_file, index=False, engine='pyarrow', compression='snappy')
                        logger.info(f"Node properties saved to Parquet: {parquet_file} ({len(nodes_df)} nodes)")
            except ImportError:
                logger.warning("PyArrow/pandas not available, skipping Parquet node properties storage")
            except Exception as e:
                logger.warning(f"Failed to save node properties to Parquet: {e}")
            
            # 2. Save CSR format (CRITICAL: Always save CSR when enabled, not just when _should_use_csr())
            # CSR is the primary storage format for performance
            if self.csr_storage and self.use_csr:
                try:
                    csr_dir = base_dir / "csr"
                    csr_dir.mkdir(parents=True, exist_ok=True)
                    
                    # Save CSR matrix and metadata
                    csr_file = csr_dir / f"{base_name}_csr.npz"
                    csr_metadata_file = csr_dir / f"{base_name}_csr_metadata.json"
                    
                    # Build CSR matrix from CSR storage edges
                    if not self.csr_storage or not hasattr(self.csr_storage, 'nodes'):
                        raise RuntimeError("CSR storage not available")
                    
                    node_list = list(self.csr_storage.nodes.keys())
                    node_to_idx = {node_id: idx for idx, node_id in enumerate(node_list)}
                    num_nodes = len(node_list)
                    
                    # Build edge lists for CSR
                    row_indices = []
                    col_indices = []
                    edge_labels = []
                    edge_properties_list = []
                    
                    if hasattr(self.csr_storage, 'edge_data'):
                        for edge in self.csr_storage.edge_data:
                            if edge.source_id in node_to_idx and edge.target_id in node_to_idx:
                                row_indices.append(node_to_idx[edge.source_id])
                                col_indices.append(node_to_idx[edge.target_id])
                                edge_labels.append(edge.edge_type)
                                edge_id = f"{edge.source_id}_{edge.target_id}"
                                edge_properties_list.append(self.edge_properties.get(edge_id, edge.properties))
                    
                    # CRITICAL: Always save CSR format, even if no edges (for node-only graphs)
                    # Create CSR matrix (use zeros if no edges, but still create the matrix)
                    from scipy.sparse import csr_matrix
                    if row_indices:
                        csr_matrix_data = csr_matrix(
                            (np.ones(len(row_indices)), (row_indices, col_indices)),
                            shape=(num_nodes, num_nodes)
                        )
                    else:
                        # Empty graph - create empty CSR matrix
                        csr_matrix_data = csr_matrix((num_nodes, num_nodes), dtype=np.float64)
                    
                    # Save CSR matrix
                    save_npz(str(csr_file), csr_matrix_data)
                    
                    # Save CSR metadata (without aliases - they are query-scoped)
                    csr_metadata = {
                        'node_list': node_list,
                        'node_to_idx': node_to_idx,
                        'edge_labels': edge_labels,
                        'edge_properties': edge_properties_list,
                        'row_indices': row_indices,
                        'col_indices': col_indices,
                        'num_nodes': num_nodes,
                        'num_edges': len(row_indices),
                        # 'alias_to_node': {},  # REMOVED: Aliases are query-scoped, not persisted
                        'saved_at': time.time()
                    }
                    
                    with open(csr_metadata_file, 'w') as f:
                        json.dump(csr_metadata, f, indent=2, default=str)
                    
                    logger.info(f"CSR format saved to {csr_file} ({num_nodes} nodes, {len(row_indices)} edges)")
                except Exception as e:
                    logger.error(f"[EMOJI] Failed to save CSR format: {e}")
                    import traceback
                    logger.debug(f"CSR save traceback: {traceback.format_exc()}")
                    # Don't fail completely - JSON is still saved as fallback
            
            return True
        except Exception as e:
            logger.error(f"Failed to save graph '{self.name}': {e}")
            return False
    
    def _save_single_file(self, file_path: str) -> bool:
        """
        Save graph to single-file HDF5 format (like Oracle's .dbf).
        
        Args:
            file_path: Path to save (will use .h5 extension)
            
        Returns:
            True if successful
        """
        try:
            from pathlib import Path
            import numpy as np
            from scipy.sparse import csr_matrix
            from ..storage.single_file_storage import SingleFileStorage
            
            # Use .h5 extension for single-file format
            h5_path = Path(file_path).with_suffix('.h5')
            
            # Prepare graph data (without aliases - they are query-scoped)
            # CSR-only: Get node/edge counts from CSR storage
            node_count = self.csr_storage.get_node_count() if self.csr_storage else 0
            edge_count = self.csr_storage.get_edge_count() if self.csr_storage else 0
            
            data = {
                'nodes': [],
                'edges': [],
                # 'alias_to_node': {},  # REMOVED: Aliases are query-scoped, not persisted
                'metadata': {
                    'name': self.name,
                    'node_count': node_count,
                    'edge_count': edge_count,
                    'storage_backend': self.storage_backend,
                    'saved_at': time.time(),
                    'has_csr': self.csr_storage is not None and node_count > 0
                }
            }
            
            # Collect nodes from CSR storage
            if self.csr_storage and hasattr(self.csr_storage, 'nodes'):
                for node_id, csr_node in self.csr_storage.nodes.items():
                    node_data = {
                        'id': node_id,
                        'label': csr_node.node_type,
                        'properties': self.node_properties.get(node_id, {})
                    }
                    data['nodes'].append(node_data)
            
            # Collect edges from CSR storage
            saved_edge_pairs = set()
            if self.csr_storage and hasattr(self.csr_storage, 'edge_data'):
                for edge in self.csr_storage.edge_data:
                    edge_id = f"{edge.source_id}_{edge.target_id}"
                    edge_info = {
                        'src': edge.source_id,
                        'dst': edge.target_id,
                        'label': edge.edge_type,
                        'properties': self.edge_properties.get(edge_id, edge.properties),
                        'weight': edge.weight
                    }
                    data['edges'].append(edge_info)
                    saved_edge_pairs.add((edge.source_id, edge.target_id, edge.edge_type))

            # Fallback: save edges from edge_store not in CSR
            for edge_id, edge_meta in self.edge_store.items():
                pair = (edge_meta['source'], edge_meta['target'], edge_meta.get('label', 'EDGE'))
                if pair in saved_edge_pairs:
                    continue
                edge_info = {
                    'src': edge_meta['source'],
                    'dst': edge_meta['target'],
                    'label': edge_meta.get('label', 'EDGE'),
                    'properties': edge_meta.get('properties', {}),
                    'weight': edge_meta.get('weight', 1.0)
                }
                data['edges'].append(edge_info)
                saved_edge_pairs.add(pair)
            
            # Save to HDF5
            # NEO4J-LEVEL PERFORMANCE: Use 'a' mode and delete/recreate groups to avoid file locking issues
            # This allows concurrent saves without "file already open" errors
            storage = None
            try:
                with SingleFileStorage(str(h5_path), mode='a') as storage:
                    storage.save_graph(data)
                    
                    # Save CSR matrix if available (CRITICAL: Inside try block, not except!)
                    if self.csr_storage and self.use_csr:
                        try:
                            # Build CSR matrix from CSR storage
                            if not hasattr(self.csr_storage, 'nodes'):
                                raise RuntimeError("CSR storage nodes not available")
                            
                            node_list = list(self.csr_storage.nodes.keys())
                            node_to_idx = {node_id: idx for idx, node_id in enumerate(node_list)}
                            num_nodes = len(node_list)
                            
                            row_indices = []
                            col_indices = []
                            edge_labels = []
                            edge_properties_list = []
                            
                            if hasattr(self.csr_storage, 'edge_data'):
                                for edge in self.csr_storage.edge_data:
                                    if edge.source_id in node_to_idx and edge.target_id in node_to_idx:
                                        row_indices.append(node_to_idx[edge.source_id])
                                        col_indices.append(node_to_idx[edge.target_id])
                                        edge_labels.append(edge.edge_type)
                                        edge_id = f"{edge.source_id}_{edge.target_id}"
                                        edge_properties_list.append(self.edge_properties.get(edge_id, edge.properties))
                            
                            if row_indices:
                                csr_matrix_data = csr_matrix(
                                    (np.ones(len(row_indices)), (row_indices, col_indices)),
                                    shape=(num_nodes, num_nodes)
                                )
                            else:
                                csr_matrix_data = csr_matrix((num_nodes, num_nodes), dtype=np.float64)
                            
                            csr_metadata = {
                                'node_list': node_list,
                                'node_to_idx': node_to_idx,
                                'edge_labels': edge_labels,
                                'edge_properties': edge_properties_list,
                                'num_nodes': num_nodes,
                                'num_edges': len(row_indices)
                                # 'alias_to_node': {}  # REMOVED: Aliases are query-scoped, not persisted
                            }
                            
                            storage.save_csr_matrix(csr_matrix_data, csr_metadata)
                            logger.info(f"CSR matrix saved to HDF5: {num_nodes} nodes, {len(row_indices)} edges")
                        except Exception as csr_error:
                            logger.warning(f"Failed to save CSR matrix to HDF5: {csr_error}")
            except Exception as e:
                # If append mode fails (file locked), try write mode with retry
                logger.warning(f"Append mode failed, trying write mode: {e}")
                max_retries = 3
                for retry in range(max_retries):
                    try:
                        with SingleFileStorage(str(h5_path), mode='w') as storage:
                            storage.save_graph(data)
                            
                            # Save CSR matrix if available (also in retry path)
                            if self.csr_storage and self.use_csr:
                                try:
                                    # Build CSR matrix from CSR storage
                                    if not hasattr(self.csr_storage, 'nodes'):
                                        raise RuntimeError("CSR storage nodes not available")
                                    
                                    node_list = list(self.csr_storage.nodes.keys())
                                    node_to_idx = {node_id: idx for idx, node_id in enumerate(node_list)}
                                    num_nodes = len(node_list)
                                    
                                    row_indices = []
                                    col_indices = []
                                    edge_labels = []
                                    edge_properties_list = []
                                    
                                    if hasattr(self.csr_storage, 'edge_data'):
                                        for edge in self.csr_storage.edge_data:
                                            if edge.source_id in node_to_idx and edge.target_id in node_to_idx:
                                                row_indices.append(node_to_idx[edge.source_id])
                                                col_indices.append(node_to_idx[edge.target_id])
                                                edge_labels.append(edge.edge_type)
                                                edge_id = f"{edge.source_id}_{edge.target_id}"
                                                edge_properties_list.append(self.edge_properties.get(edge_id, edge.properties))
                                    
                                    if row_indices:
                                        csr_matrix_data = csr_matrix(
                                            (np.ones(len(row_indices)), (row_indices, col_indices)),
                                            shape=(num_nodes, num_nodes)
                                        )
                                    else:
                                        csr_matrix_data = csr_matrix((num_nodes, num_nodes), dtype=np.float64)
                                    
                                    csr_metadata = {
                                        'node_list': node_list,
                                        'node_to_idx': node_to_idx,
                                        'edge_labels': edge_labels,
                                        'edge_properties': edge_properties_list,
                                        'num_nodes': num_nodes,
                                        'num_edges': len(row_indices)
                                    }
                                    
                                    storage.save_csr_matrix(csr_matrix_data, csr_metadata)
                                    logger.info(f"CSR matrix saved to HDF5 (retry): {num_nodes} nodes, {len(row_indices)} edges")
                                except Exception as csr_error:
                                    logger.warning(f"Failed to save CSR matrix to HDF5 (retry): {csr_error}")
                        break
                    except Exception as retry_error:
                        if retry < max_retries - 1:
                            time.sleep(0.1)  # Wait 100ms before retry
                        else:
                            raise retry_error
            
            logger.info(f"Graph '{self.name}' saved to single-file HDF5: {h5_path}")
            return True
            
        except ImportError:
            logger.error("h5py not available. Install with: pip install h5py")
            return False
        except Exception as e:
            logger.error(f"Failed to save to single-file format: {e}")
            return False
    
    def _save_multi_file_optimized(self, file_path: str) -> bool:
        """
        Save graph using multi-file optimized format (different formats for different data types).
        
        Uses:
        - JSON for graph structure (human-readable)
        - Parquet for node/edge properties (columnar, fast queries)
        - NPZ for CSR matrices (optimized for sparse graphs)
        - NPY for vectors (fast NumPy arrays)
        - HDF5 for images (efficient binary)
        - Parquet for tables (columnar analytics)
        
        Args:
            file_path: Base path for saving (will create multiple files)
            
        Returns:
            True if successful
        """
        try:
            from pathlib import Path
            import numpy as np
            from scipy.sparse import save_npz
            
            file_path_obj = Path(file_path)
            base_dir = file_path_obj.parent
            base_name = file_path_obj.stem
            
            # Get multi-file optimized config
            graph_config = self.config.get('graph', {})
            multi_config = graph_config.get('multi_file_optimized', {})
            graph_format = multi_config.get('graph_format', 'csr')  # Default: CSR for graph structure
            properties_format = multi_config.get('properties_format', 'parquet')
            csr_format = multi_config.get('csr_format', 'npz')
            
            # 1. Save graph structure (CSR by default for multi-file optimized)
            # Note: Aliases are NOT saved (they are query-scoped only)
            
            node_count = self.csr_storage.get_node_count() if self.csr_storage else 0
            edge_count = self.csr_storage.get_edge_count() if self.csr_storage else 0
            
            graph_data = {
                'nodes': [],
                'edges': [],
                # 'alias_to_node': {},  # REMOVED: Aliases are query-scoped, not persisted
                'metadata': {
                    'name': self.name,
                    'node_count': node_count,
                    'edge_count': edge_count,
                    'storage_backend': self.storage_backend,
                    'saved_at': time.time()
                }
            }
            
            # Collect nodes from CSR storage
            if self.csr_storage and hasattr(self.csr_storage, 'nodes'):
                for node_id, csr_node in self.csr_storage.nodes.items():
                    node_data = {
                        'id': node_id,
                        'label': csr_node.node_type,
                        'properties': self.node_properties.get(node_id, {})
                    }
                    graph_data['nodes'].append(node_data)
            
            # Collect edges from CSR storage
            if self.csr_storage and hasattr(self.csr_storage, 'edge_data'):
                for edge in self.csr_storage.edge_data:
                    edge_id = f"{edge.source_id}_{edge.target_id}"
                    edge_info = {
                        'src': edge.source_id,
                        'dst': edge.target_id,
                        'label': edge.edge_type,
                        'properties': self.edge_properties.get(edge_id, edge.properties),
                        'weight': edge.weight
                    }
                    graph_data['edges'].append(edge_info)
            
            # Save graph structure (CSR format for graph, JSON as fallback/metadata)
            if graph_format == 'csr':
                # Graph structure is saved as CSR (see step 3 below)
                # Also save minimal JSON for metadata
                graph_file = file_path_obj.with_suffix('.json')
                minimal_graph_data = {
                    # 'alias_to_node': {},  # REMOVED: Aliases are query-scoped, not persisted
                    'metadata': graph_data['metadata']
                }
                with open(graph_file, 'w') as f:
                    json.dump(minimal_graph_data, f, indent=2)
                logger.debug(f"Graph metadata saved to JSON: {graph_file}")
            elif graph_format == 'json':
                # Full JSON format (fallback)
                graph_file = file_path_obj.with_suffix('.json')
                with open(graph_file, 'w') as f:
                    json.dump(graph_data, f, indent=2)
                logger.debug(f"Graph structure saved to JSON: {graph_file}")
            
            # 2. Save node properties to Parquet (optimized for property queries)
            if properties_format == 'parquet':
                try:
                    import pandas as pd
                    parquet_dir = base_dir / "node_properties"
                    parquet_dir.mkdir(parents=True, exist_ok=True)
                    
                    if graph_data['nodes']:
                        nodes_df_data = []
                        for node in graph_data['nodes']:
                            row = {'node_id': node['id'], 'label': node['label']}
                            props = node.get('properties', {})
                            for key, value in props.items():
                                if isinstance(value, (dict, list)):
                                    row[f'prop_{key}'] = json.dumps(value)
                                else:
                                    row[f'prop_{key}'] = value
                            nodes_df_data.append(row)
                        
                        if nodes_df_data:
                            nodes_df = pd.DataFrame(nodes_df_data)
                            parquet_file = parquet_dir / f"{base_name}_nodes.parquet"
                            compression = multi_config.get('properties_compression', 'snappy')
                            nodes_df.to_parquet(parquet_file, index=False, engine='pyarrow', compression=compression)
                            logger.debug(f"Node properties saved to Parquet: {parquet_file}")
                except ImportError:
                    logger.warning("PyArrow/pandas not available, skipping Parquet storage")
                except Exception as e:
                    logger.warning(f"Failed to save node properties to Parquet: {e}")
            
            # 3. Save CSR matrix (NPZ format - optimized for sparse graphs)
            # This is the PRIMARY graph structure storage for multi-file optimized strategy
            if (graph_format == 'csr' or csr_format == 'npz') and self.csr_storage and self.use_csr:
                try:
                    csr_dir = base_dir / "csr"
                    csr_dir.mkdir(parents=True, exist_ok=True)
                    
                    node_list = list(self.csr_storage.nodes.keys())
                    node_to_idx = {node_id: idx for idx, node_id in enumerate(node_list)}
                    num_nodes = len(node_list)
                    
                    row_indices = []
                    col_indices = []
                    edge_labels = []
                    edge_properties_list = []
                    
                    if hasattr(self.csr_storage, 'edge_data'):
                        for edge in self.csr_storage.edge_data:
                            if edge.source_id in node_to_idx and edge.target_id in node_to_idx:
                                row_indices.append(node_to_idx[edge.source_id])
                                col_indices.append(node_to_idx[edge.target_id])
                                edge_labels.append(edge.edge_type)
                                edge_id = f"{edge.source_id}_{edge.target_id}"
                                edge_properties_list.append(self.edge_properties.get(edge_id, edge.properties))
                    
                    from scipy.sparse import csr_matrix
                    if row_indices:
                        csr_matrix_data = csr_matrix(
                            (np.ones(len(row_indices)), (row_indices, col_indices)),
                            shape=(num_nodes, num_nodes)
                        )
                    else:
                        csr_matrix_data = csr_matrix((num_nodes, num_nodes), dtype=np.float64)
                    
                    csr_file = csr_dir / f"{base_name}_csr.npz"
                    save_npz(str(csr_file), csr_matrix_data)
                    
                    csr_metadata = {
                        'node_list': node_list,
                        'node_to_idx': node_to_idx,
                        'edge_labels': edge_labels,
                        'edge_properties': edge_properties_list,
                        'num_nodes': num_nodes,
                        'num_edges': len(row_indices)
                        # 'alias_to_node': {}  # REMOVED: Aliases are query-scoped, not persisted
                    }
                    
                    csr_metadata_file = csr_dir / f"{base_name}_csr_metadata.json"
                    with open(csr_metadata_file, 'w') as f:
                        json.dump(csr_metadata, f, indent=2, default=str)
                    
                    logger.debug(f"CSR matrix saved to NPZ: {csr_file}")
                except Exception as e:
                    logger.warning(f"Failed to save CSR format: {e}")
            
            logger.info(f"Graph '{self.name}' saved using multi-file optimized format")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save multi-file optimized format: {e}")
            return False
    
    def _load_multi_file_optimized(self, file_path: str) -> bool:
        """
        Load graph from multi-file optimized format.
        
        Args:
            file_path: Base path for loading
            
        Returns:
            True if successful
        """
        try:
            from pathlib import Path
            from scipy.sparse import load_npz
            
            file_path_obj = Path(file_path)
            base_dir = file_path_obj.parent
            base_name = file_path_obj.stem
            
            # Get config
            graph_config = self.config.get('graph', {})
            multi_config = graph_config.get('multi_file_optimized', {})
            graph_format = multi_config.get('graph_format', 'csr')  # Default: CSR for graph structure
            csr_format = multi_config.get('csr_format', 'npz')
            
            # 1. Try loading CSR first (primary graph structure format for multi-file optimized)
            csr_file = base_dir / "csr" / f"{base_name}_csr.npz"
            csr_metadata_file = base_dir / "csr" / f"{base_name}_csr_metadata.json"
            
            if csr_file.exists() and csr_metadata_file.exists() and (graph_format == 'csr' or csr_format == 'npz'):
                try:
                    csr_matrix_data = load_npz(str(csr_file))
                    with open(csr_metadata_file, 'r') as f:
                        csr_metadata = json.load(f)
                    
                    node_list = csr_metadata.get('node_list', [])
                    edge_labels = csr_metadata.get('edge_labels', [])
                    edge_properties_list = csr_metadata.get('edge_properties', [])
                    # Note: aliases are NOT loaded (they are query-scoped only)
                    
                    # Restore nodes to CSR storage
                    for node_id in node_list:
                        self.csr_storage.add_node(node_id, 'Node', {})
                    
                    # Restore edges
                    row_indices, col_indices = csr_matrix_data.nonzero()
                    for i, (row_idx, col_idx) in enumerate(zip(row_indices, col_indices)):
                        source_id = node_list[row_idx]
                        target_id = node_list[col_idx]
                        edge_label = edge_labels[i] if i < len(edge_labels) else 'RELATED_TO'
                        edge_props = edge_properties_list[i] if i < len(edge_properties_list) else {}
                        self.csr_storage.add_edge(source_id, target_id, edge_label, edge_props)
                    
                    # Note: alias_to_node removed - aliases are query-scoped only (stored in executor)
                    
                    logger.debug(f"Loaded graph from multi-file optimized format (CSR)")
                    return True
                except Exception as e:
                    logger.warning(f"Failed to load CSR format: {e}")
            
            # 2. Fallback to JSON graph structure
            graph_file = file_path_obj.with_suffix('.json')
            if graph_file.exists() and graph_format == 'json':
                with open(graph_file, 'r') as f:
                    graph_data = json.load(f)
                
                # Restore nodes
                for node in graph_data.get('nodes', []):
                    node_id = node['id']
                    node_type = node.get('label', 'Node')
                    properties = node.get('properties', {})
                    self.csr_storage.add_node(node_id, node_type, properties)
                    self.node_properties[node_id] = properties
                
                # Restore edges
                for edge in graph_data.get('edges', []):
                    source_id = edge['src']
                    target_id = edge['dst']
                    edge_type = edge.get('label', 'RELATED_TO')
                    edge_props = edge.get('properties', {})
                    self.csr_storage.add_edge(source_id, target_id, edge_type, edge_props)
                    edge_id = f"{source_id}_{target_id}"
                    self.edge_properties[edge_id] = edge_props
                
                # Note: aliases are NOT loaded (they are query-scoped only)
                # No need to initialize alias_to_node - aliases are query-scoped only
                
                logger.debug(f"Loaded graph from multi-file optimized format (JSON)")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Failed to load multi-file optimized format: {e}")
            return False
    
    def _should_use_csr(self) -> bool:
        """Determine if CSR storage should be used. Always True - NetworkX disabled."""
        return True  # CSR-only mode - always use CSR
    
    def add_node(self, node: GraphNode, alias: Optional[str] = None, user_id: Optional[str] = None, write_through: Optional[bool] = None, _skip_temporal: bool = False):
        """
        Add a node to the graph with smart routing based on node type.
        
        RAG-critical nodes (Document, Chunk, Entity) are written immediately (write-through)
        to ensure they're visible for RAG queries without waiting for buffer flush.
        
        This method is used by BOTH direct graph operations and API operations.
        It ensures the same code path is used regardless of how the node was created.
        
        Args:
            node: GraphNode to add
            alias: Optional alias for the node
            user_id: Optional user ID for traceability
            write_through: Force write-through (bypass buffer). If None, auto-detect based on node type.
        """
        
        # STRATEGY: Default to write-through for immediate visibility.
        # Buffer is an optimization for bulk ingestion pipelines that explicitly
        # set write_through=False. Interactive AIQL queries and API operations
        # need nodes visible immediately for queries, saves, and multi-worker sync.
        if write_through is None:
            write_through = True
        
        
        # If write-through is enabled, write directly (bypass buffer)
        if write_through:
            logger.debug(f"Write-through enabled for node type '{node.label}' (RAG-critical)")
            self._add_node_direct(node, alias, user_id, _skip_temporal=_skip_temporal)
            return True
        
        # If buffer is enabled, add to buffer instead of directly to graph
        if self.buffer_enabled and self.buffer_manager:
            # OPTIMIZATION: Check if buffer was pre-initialized during __init__
            # If not, initialize lazily (should be rare now)
            if not getattr(self, '_buffer_initialized', False) and not self.buffer_manager._initialized:
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # If loop is running, schedule initialization
                        asyncio.create_task(self._init_buffer_async())
                    else:
                        loop.run_until_complete(self.buffer_manager.initialize())
                        if self.config.get('buffer', {}).get('config', {}).get('auto_flush', True):
                            loop.run_until_complete(self.buffer_manager.start_auto_flush(writer_fn=self._buffer_writer_fn_ref))
                        self._buffer_initialized = True
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(self.buffer_manager.initialize())
                    if self.config.get('buffer', {}).get('config', {}).get('auto_flush', True):
                        loop.run_until_complete(self.buffer_manager.start_auto_flush(writer_fn=self._buffer_writer_fn_ref))
                    self._buffer_initialized = True
            
            # Add to buffer (will be flushed later)
            record = {
                'operation': 'CREATE_NODE',
                'node_id': node.id,
                'label': node.label,
                'properties': node.properties,
                'alias': alias,
                'user_id': user_id
            }
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # If loop is running, schedule add_record
                    asyncio.create_task(self.buffer_manager.add_record(record))
                else:
                    loop.run_until_complete(self.buffer_manager.add_record(record))
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self.buffer_manager.add_record(record))
            
            # STRATEGY: Check if buffer reached threshold and trigger immediate flush
            if self.config.get('buffer', {}).get('config', {}).get('threshold_flush_enabled', True):
                try:
                    pending_count = loop.run_until_complete(self.buffer_manager.get_pending_count()) if not loop.is_running() else 0
                    max_size = self.config.get('buffer', {}).get('config', {}).get('max_size', 1000)
                    flush_threshold = self.config.get('buffer', {}).get('config', {}).get('flush_threshold', 0.7)
                    threshold = int(max_size * flush_threshold)
                    
                    if pending_count >= threshold:
                        logger.debug(f"Buffer threshold reached ({pending_count}/{max_size} = {pending_count/max_size*100:.1f}%) - triggering flush")
                        # Trigger immediate flush (non-blocking)
                        if loop.is_running():
                            asyncio.create_task(self.buffer_manager.flush_to_db(writer_fn=self._buffer_writer_fn_ref))
                        else:
                            loop.run_until_complete(self.buffer_manager.flush_to_db(writer_fn=self._buffer_writer_fn_ref))
                except Exception as e:
                    logger.debug(f"Error checking buffer threshold: {e}")
            
            logger.debug(f"Added node {node.id} to buffer")
            return True  # Return True to indicate success (node is buffered)
        
        # Direct addition (buffer disabled or called from buffer writer)
        self._add_node_direct(node, alias, user_id, _skip_temporal=_skip_temporal)
    
    async def _init_buffer_async(self):
        """Initialize buffer asynchronously."""
        await self.buffer_manager.initialize()
        if self.config.get('buffer', {}).get('config', {}).get('auto_flush', True):
            await self.buffer_manager.start_auto_flush(writer_fn=self._buffer_writer_fn_ref)
    
    async def _buffer_writer_fn(self, batch: List[Dict[str, Any]]) -> None:
        """
        Writer function for buffer manager.
        Writes buffered records to the graph (CSR storage in memory).
        
        NOTE: This writes to CSR storage in memory. To persist to disk, call save() separately.
        
        Args:
            batch: List of buffered records
        """
        if not batch:
            logger.debug("Buffer writer called with empty batch")
            return
        
        logger.info(f"Buffer writer: Processing {len(batch)} records for graph '{self.name}'")
        
        nodes_written = 0
        edges_written = 0
        errors = []
        
        for record in batch:
            try:
                operation = record.get('operation')
                if operation == 'CREATE_NODE':
                    # Create node from buffer record
                    node = GraphNode(
                        id=record['node_id'],
                        label=record['label'],
                        properties=record.get('properties', {})
                    )
                    alias = record.get('alias')
                    user_id = record.get('user_id')
                    # Call direct addition (bypassing buffer to avoid recursion)
                    self._add_node_direct(node, alias, user_id)
                    nodes_written += 1
                    logger.debug(f"Buffer writer: Wrote node {node.id[:8]}... (label: {node.label})")
                elif operation == 'CREATE_EDGE':
                    # Create edge from buffer record
                    edge = GraphEdge(
                        id=record.get('edge_id', f"{record['source']}_{record['target']}"),
                        source=record['source'],
                        target=record['target'],
                        label=record['label'],
                        properties=record.get('properties', {})
                    )
                    user_id = record.get('user_id')
                    # Call direct addition (bypassing buffer to avoid recursion)
                    self._add_edge_direct(edge, user_id)
                    edges_written += 1
                    logger.debug(f"Buffer writer: Wrote edge {edge.id[:8]}... ({edge.source} -> {edge.target})")
                else:
                    logger.warning(f"Buffer writer: Unknown operation '{operation}' in record")
                    errors.append(f"Unknown operation: {operation}")
            except Exception as e:
                error_msg = f"Error writing record {record.get('node_id') or record.get('edge_id', 'unknown')}: {e}"
                logger.error(error_msg)
                errors.append(error_msg)
                # Continue processing other records
        
        logger.info(f"Buffer writer: Completed - {nodes_written} nodes, {edges_written} edges written to CSR storage (graph: '{self.name}')")
        if errors:
            logger.warning(f"Buffer writer: {len(errors)} errors occurred during flush")
        
        # CRITICAL: Nodes are now in CSR storage (memory), but NOT persisted to disk yet
        # Trigger background save to persist flushed records to disk (non-blocking)
        # Only save if we wrote something and enough time has passed since last save
        if (nodes_written > 0 or edges_written > 0):
            try:
                # Import registry to trigger save
                from ..core.registry import graph_registry
                
                # Trigger background save (non-blocking)
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # Schedule background save
                        asyncio.create_task(self._background_save_async())
                    else:
                        # Run in executor if no event loop
                        loop.run_in_executor(None, self._save_to_disk)
                except RuntimeError:
                    # No event loop, run synchronously in thread
                    import threading
                    threading.Thread(target=self._save_to_disk, daemon=True).start()
                
                logger.debug(f"Buffer writer: Triggered background save for graph '{self.name}' ({nodes_written} nodes, {edges_written} edges)")
            except Exception as e:
                logger.warning(f"Buffer writer: Failed to trigger background save: {e}")
                # Don't fail the flush if save fails - nodes are still in memory
    
    def _save_to_disk(self):
        """Synchronous save to disk (runs in background thread)."""
        try:
            from ..core.registry import graph_registry
            # Re-register in registry if evicted (LRU cleanup can remove graphs
            # while their pipeline is still running in a background thread)
            if self.name not in graph_registry.graphs:
                graph_registry.graphs[self.name] = self
            success = graph_registry.save_graph(self.name)
            if success:
                logger.debug(f"Buffer writer: Successfully saved graph '{self.name}' to disk")
            else:
                logger.warning(f"Buffer writer: Failed to save graph '{self.name}' to disk")
        except Exception as e:
            logger.error(f"Buffer writer: Error saving graph '{self.name}' to disk: {e}")
    
    async def _background_save_async(self):
        """Async background save (runs in event loop)."""
        try:
            from ..core.registry import graph_registry
            import asyncio
            # Re-register in registry if evicted by LRU cleanup
            if self.name not in graph_registry.graphs:
                graph_registry.graphs[self.name] = self
            loop = asyncio.get_event_loop()
            # Run save in executor to avoid blocking
            success = await loop.run_in_executor(None, graph_registry.save_graph, self.name)
            if success:
                logger.debug(f"Buffer writer: Successfully saved graph '{self.name}' to disk (async)")
            else:
                logger.warning(f"Buffer writer: Failed to save graph '{self.name}' to disk (async)")
        except Exception as e:
            logger.error(f"Buffer writer: Error saving graph '{self.name}' to disk (async): {e}")
    
    def _ensure_wal(self):
        """Lazy-init WAL on first write."""
        if self.wal is None and self._wal_enabled:
            try:
                from ..storage.wal import AIContextDBWALSystem, WALConfig
                import re as _re
                safe = _re.sub(r'[<>:"/\\|?*]', '_', self._wal_name)
                self.wal = AIContextDBWALSystem(WALConfig(wal_dir=f"contextcore_data/wal/{safe}"))
            except Exception:
                self._wal_enabled = False

    def _add_node_direct(self, node: GraphNode, alias: Optional[str] = None, user_id: Optional[str] = None, _skip_temporal: bool = False):
        """Direct node addition (bypasses buffer). WAL-logged for durability. Thread-safe."""
        with self._write_lock:
            return self._add_node_direct_inner(node, alias, user_id, _skip_temporal=_skip_temporal)

    def _add_node_direct_inner(self, node: GraphNode, alias: Optional[str] = None, user_id: Optional[str] = None, _skip_temporal: bool = False):
        # WAL: log before writing to memory (write-ahead)
        self._ensure_wal()
        if self.wal:
            try:
                from ..storage.wal import OperationType
                self.wal.log_operation(OperationType.CREATE_NODE, {
                    'node_id': node.id, 'label': node.label,
                    'properties': node.properties,
                })
            except Exception:
                pass  # WAL failure should not block writes

        # Record operation in traceability layer
        if self.traceability_layer:
            self.traceability_layer.record_operation(
                operation_type='CREATE',
                entity_type='node',
                entity_id=node.id,
                data={'label': node.label, 'properties': node.properties, 'alias': alias},
                user_id=user_id,
                namespace=self.name
            )
        
        # Add domain metadata if not present (default to namespace as domain)
        if 'domain' not in node.properties:
            node.properties['domain'] = self.name

        # Provenance: inject write metadata
        from .write_context import get_write_context
        from datetime import datetime as _dt, timezone as _tz
        _wctx = get_write_context()
        if '_created_at' not in node.properties:
            node.properties['_created_at'] = _dt.now(_tz.utc).isoformat()
        if '_agent_id' not in node.properties:
            node.properties['_agent_id'] = user_id or (_wctx.agent_id if _wctx else "") or ""
        if '_origin' not in node.properties:
            node.properties['_origin'] = (_wctx.origin if _wctx else "unknown")
        if '_verified' not in node.properties:
            node.properties['_verified'] = (_wctx.verified if _wctx else False)
        if '_region' not in node.properties:
            node.properties['_region'] = (_wctx.region if _wctx else "local")

        # Provenance defaults (A4)
        if 'source_type' not in node.properties:
            node.properties['source_type'] = 'unknown'
        if 'confidence' not in node.properties:
            node.properties['confidence'] = 1.0
        if 'created_by' not in node.properties:
            node.properties['created_by'] = node.properties.get('_agent_id', '')

        # Temporal defaults (A2)
        if 'valid_from' not in node.properties:
            node.properties['valid_from'] = node.properties.get('_created_at', _dt.now(_tz.utc).isoformat())
        if 'valid_to' not in node.properties:
            node.properties['valid_to'] = None
        if 'version' not in node.properties:
            node.properties['version'] = 1
        if 'status' not in node.properties:
            node.properties['status'] = 'active'

        # Semantic hashing: Calculate content hash for deduplication
        if self.hashing_enabled and self.hasher:
            try:
                hash_result = self.hasher.hash_node(node.id, node.label, node.properties)
                # Store hash in properties for later deduplication
                if 'content_hash' not in node.properties:
                    node.properties['content_hash'] = hash_result.hash_value
                if 'hash_algorithm' not in node.properties:
                    node.properties['hash_algorithm'] = hash_result.algorithm
            except Exception as e:
                logger.warning(f"[SEMANTIC_HASH] Failed to hash node {node.id}: {e}")
        
        # Time travel: Create version for new node (skip when called internally)
        if self.temporal_enabled and self.temporal_storage and not _skip_temporal:
            try:
                from datetime import datetime
                self.temporal_storage.create_version(
                    entity_id=node.id,
                    entity_type='node',
                    properties=node.properties.copy(),
                    operation='CREATE',
                    timestamp=datetime.now()
                )
            except Exception as e:
                logger.warning(f"[TIME_TRAVEL] Failed to create version for node {node.id}: {e}")
        
        # Debug - log what we're adding
        logger.debug(f"add_node called: node_id={node.id[:8]}..., alias={alias} (query-scoped, not persisted), label={node.label}, graph='{self.name}'")
        
        # CSR-only: Store properties separately (no NetworkX)
        self.node_properties[node.id] = node.properties.copy()
        
        # Store in node_index for compatibility
        self.node_index[node.id] = node
        
        # NOTE: Aliases are query-scoped only (stored in executor.node_aliases, not here)
        # We accept the alias parameter for compatibility but don't store it persistently
        
        # NEW: Update property indexes (use cleaned properties to avoid unhashable errors)
        # Clean properties before indexing to handle nested dicts/lists
        properties_cleaned = self._clean_properties_for_indexing(node.properties)
        self._update_property_indexes(node.id, properties_cleaned)
        # Store original properties (with nested structures) separately
        self.node_properties[node.id] = node.properties
        
        # CRITICAL: Always add to CSR (mandatory - NetworkX disabled)
        if not self.csr_adapter:
            raise RuntimeError("CSR adapter not initialized. NetworkX fallback disabled.")
        # Add to CSR (pass None for alias since we don't persist aliases)
        success = self.csr_adapter.add_node(
            node.id, 
            node.label, 
            node.properties, 
            None  # Don't persist alias - query-scoped only
        )
        if not success:
            logger.warning(f"Failed to add node {node.id} to CSR storage")

        # Update ContextMeta counters (A1)
        if node.label != "ContextMeta" and success:
            try:
                meta_node = self.csr_adapter.get_node("_context_meta")
                if meta_node:
                    meta_node.properties["node_count"] = meta_node.properties.get("node_count", 0) + 1
                    meta_node.properties["updated_at"] = _dt.now(_tz.utc).isoformat()
                    existing = set(meta_node.properties.get("schema_summary", "").split(", ")) - {""}
                    existing.add(node.label)
                    meta_node.properties["schema_summary"] = ", ".join(sorted(existing))
                    self.csr_adapter.update_node_properties("_context_meta", meta_node.properties)
            except Exception:
                pass  # ContextMeta update failure should never block writes
        else:
            logger.debug(f"Successfully added node {node.id} to CSR storage")

        # Graph intelligence: auto-link, quality score, topic tag
        try:
            from .graph_intelligence import get_graph_intelligence
            gi = get_graph_intelligence(self)
            gi.on_node_added(node, namespace=self.name)
        except Exception as e:
            logger.debug(f"Graph intelligence hook failed: {e}")

        self.operation_counts['add_node'] += 1
        return True  # Return True to indicate success
    
    def remove_node(self, node_id: str, user_id: Optional[str] = None) -> bool:
        """Remove a node from the graph. Thread-safe."""
        with self._write_lock:
            return self._remove_node_inner(node_id, user_id)

    def _remove_node_inner(self, node_id: str, user_id: Optional[str] = None) -> bool:
        try:
            # Record operation in traceability layer
            if self.traceability_layer:
                node_data = {}
                # Get node from CSR storage
                csr_node = self.csr_adapter.get_node(node_id) if self.csr_adapter else None
                if csr_node:
                    node_data = {
                        'label': csr_node.node_type,
                        'properties': self.node_properties.get(node_id, {})
                    }
                self.traceability_layer.record_operation(
                    operation_type='DELETE',
                    entity_type='node',
                    entity_id=node_id,
                    data=node_data,
                    user_id=user_id,
                    namespace=self.name
                )
            # Check if node exists in CSR storage
            if not self.csr_adapter or not self.csr_adapter.get_node(node_id):
                return False
            
            # Remove from CSR storage (manual removal since CSR doesn't have remove_node method)
            if self.csr_storage and hasattr(self.csr_storage, 'nodes'):
                if node_id in self.csr_storage.nodes:
                    # Remove node from CSR storage
                    csr_node = self.csr_storage.nodes[node_id]
                    node_index = csr_node.index
                    del self.csr_storage.nodes[node_id]
                    del self.csr_storage.node_id_to_index[node_id]
                    if node_index in self.csr_storage.index_to_node_id:
                        del self.csr_storage.index_to_node_id[node_index]
                    
                    # Remove from node types
                    node_type = csr_node.node_type
                    if node_type in self.csr_storage.node_types:
                        self.csr_storage.node_types[node_type].discard(node_id)
                        if not self.csr_storage.node_types[node_type]:
                            del self.csr_storage.node_types[node_type]
                    
                    # Remove from property index
                    for prop_name, prop_index in self.csr_storage.property_index.items():
                        for prop_value, node_set in list(prop_index.items()):
                            node_set.discard(node_id)
                            if not node_set:
                                del prop_index[prop_value]
                    
                    # Note: Edges pointing to/from this node should also be removed
                    # This is a simplified removal - full implementation would require edge cleanup
                    if hasattr(self.csr_storage, 'edge_data'):
                        # Remove edges where this node is source or target
                        edges_to_remove = []
                        for i, edge in enumerate(self.csr_storage.edge_data):
                            if edge.source_id == node_id or edge.target_id == node_id:
                                edges_to_remove.append(i)
                        # Remove in reverse order to maintain indices
                        for i in reversed(edges_to_remove):
                            edge = self.csr_storage.edge_data.pop(i)
                            # Update edge types
                            if edge.edge_type in self.csr_storage.edge_types:
                                self.csr_storage.edge_types[edge.edge_type].discard(i)
                                if not self.csr_storage.edge_types[edge.edge_type]:
                                    del self.csr_storage.edge_types[edge.edge_type]
                        # Note: CSR row_ptr and col_indices would need to be rebuilt after edge removal
                        # This is a simplified implementation
            
            # Remove from properties
            if node_id in self.node_properties:
                del self.node_properties[node_id]
            
            # Remove from alias mapping
            # NOTE: Aliases are query-scoped only, so no need to remove from persistent storage
            # (alias_to_node is kept for backward compatibility but is not used)
            
            # Remove from CSR if enabled
            if self.csr_storage and hasattr(self.csr_storage, 'remove_node'):
                self.csr_storage.remove_node(node_id)
            
            logger.info(f"Node '{node_id}' removed successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to remove node '{node_id}': {e}")
            return False
    
    def get_node_by_alias(self, alias: str) -> Optional[str]:
        """
        Get node ID by alias.
        
        NOTE: Aliases are now query-scoped only (not persisted).
        This method returns None for query-scoped aliases.
        Use executor.node_aliases during query execution instead.
        """
        # Aliases are query-scoped only - not persisted in graph
        # This method is kept for backward compatibility but will return None
        return None
    
    def add_edge(self, edge: GraphEdge, user_id: Optional[str] = None):
        """Add an edge to the graph."""
        from datetime import datetime as _dt, timezone as _tz
        # Add domain metadata if not present (default to namespace as domain)
        if 'domain' not in edge.properties:
            edge.properties['domain'] = self.name
        # Edge metadata defaults (A5)
        if 'weight' not in edge.properties:
            edge.properties['weight'] = 1.0
        if 'confidence' not in edge.properties:
            edge.properties['confidence'] = 1.0
        if 'created_at' not in edge.properties:
            edge.properties['created_at'] = _dt.now(_tz.utc).isoformat()
        
        # If buffer is enabled, add to buffer instead of directly to graph
        if self.buffer_enabled and self.buffer_manager:
            # Initialize buffer if not already initialized
            if not self.buffer_manager._initialized:
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.create_task(self._init_buffer_async())
                    else:
                        loop.run_until_complete(self.buffer_manager.initialize())
                        if self.config.get('buffer', {}).get('config', {}).get('auto_flush', True):
                            loop.run_until_complete(self.buffer_manager.start_auto_flush(writer_fn=self._buffer_writer_fn_ref))
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(self.buffer_manager.initialize())
                    if self.config.get('buffer', {}).get('config', {}).get('auto_flush', True):
                        loop.run_until_complete(self.buffer_manager.start_auto_flush(writer_fn=self._buffer_writer_fn_ref))
            
            # Add to buffer (will be flushed later)
            record = {
                'operation': 'CREATE_EDGE',
                'edge_id': edge.id,
                'source': edge.source,
                'target': edge.target,
                'label': edge.label,
                'properties': edge.properties,
                'user_id': user_id
            }
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self.buffer_manager.add_record(record))
                else:
                    loop.run_until_complete(self.buffer_manager.add_record(record))
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self.buffer_manager.add_record(record))
            logger.debug(f"Added edge {edge.id} to buffer")
            # WRITE-THROUGH: Also add directly to CSR so edges are immediately queryable
            # (buffer reads fail in async context since loop.is_running() returns True)
            self._add_edge_direct(edge, user_id)
            return True  # Return True to indicate success
        
        # Direct addition (buffer disabled or called from buffer writer)
        return self._add_edge_direct(edge, user_id)
    
    def _add_edge_direct(self, edge: GraphEdge, user_id: Optional[str] = None):
        """Direct edge addition (bypasses buffer). WAL-logged for durability. Thread-safe."""
        with self._write_lock:
            return self._add_edge_direct_inner(edge, user_id)

    def _add_edge_direct_inner(self, edge: GraphEdge, user_id: Optional[str] = None):
        try:
            # WAL: log before writing to memory (write-ahead)
            self._ensure_wal()
            if self.wal:
                try:
                    from ..storage.wal import OperationType
                    self.wal.log_operation(OperationType.CREATE_EDGE, {
                        'edge_id': edge.id, 'source': edge.source,
                        'target': edge.target, 'label': edge.label,
                        'properties': edge.properties,
                    })
                except Exception:
                    pass  # WAL failure should not block writes

            # Add domain metadata if not present (default to namespace as domain)
            if 'domain' not in edge.properties:
                edge.properties['domain'] = self.name

            # Provenance: inject write metadata
            from .write_context import get_write_context
            from datetime import datetime as _dt, timezone as _tz
            _wctx = get_write_context()
            if '_created_at' not in edge.properties:
                edge.properties['_created_at'] = _dt.now(_tz.utc).isoformat()
            if '_agent_id' not in edge.properties:
                edge.properties['_agent_id'] = user_id or (_wctx.agent_id if _wctx else "") or ""
            if '_origin' not in edge.properties:
                edge.properties['_origin'] = (_wctx.origin if _wctx else "unknown")
            if '_verified' not in edge.properties:
                edge.properties['_verified'] = (_wctx.verified if _wctx else False)
            if '_region' not in edge.properties:
                edge.properties['_region'] = (_wctx.region if _wctx else "local")

            # Time travel: Create version for new edge
            if self.temporal_enabled and self.temporal_storage:
                try:
                    from datetime import datetime
                    self.temporal_storage.create_version(
                        entity_id=edge.id,
                        entity_type='edge',
                        properties=edge.properties.copy(),
                        operation='CREATE',
                        timestamp=datetime.now()
                    )
                except Exception as e:
                    logger.warning(f"[TIME_TRAVEL] Failed to create version for edge {edge.id}: {e}")
            
            # Semantic hashing: Calculate content hash for deduplication
            if self.hashing_enabled and self.hasher:
                try:
                    hash_result = self.hasher.hash_edge(edge.source, edge.target, edge.label, edge.properties)
                    if 'content_hash' not in edge.properties:
                        edge.properties['content_hash'] = hash_result.hash_value
                    if 'hash_algorithm' not in edge.properties:
                        edge.properties['hash_algorithm'] = hash_result.algorithm
                except Exception as e:
                    logger.warning(f"[SEMANTIC_HASH] Failed to hash edge {edge.id}: {e}")
            
            # Record operation in traceability layer
            if self.traceability_layer:
                self.traceability_layer.record_operation(
                    operation_type='CREATE',
                    entity_type='edge',
                    entity_id=edge.id,
                    data={
                        'source': edge.source,
                        'target': edge.target,
                        'label': edge.label,
                        'properties': edge.properties
                    },
                    user_id=user_id,
                    namespace=self.name
                )
            # CSR-only: Store edge properties separately (no NetworkX)
            # Include _source/_target so get_all_edges can match CSR entries back to this edge
            props_copy = edge.properties.copy()
            props_copy['_source'] = edge.source
            props_copy['_target'] = edge.target
            self.edge_properties[edge.id] = props_copy
            # Store full edge metadata for reliable save/load
            self.edge_store[edge.id] = {
                'source': edge.source,
                'target': edge.target,
                'label': edge.label,
                'properties': edge.properties.copy(),
                'weight': getattr(edge, 'weight', 1.0) if hasattr(edge, 'weight') else 1.0
            }
            
            # CRITICAL: Always add to CSR (mandatory - NetworkX disabled)
            if not self.csr_adapter:
                raise RuntimeError("CSR adapter not initialized. NetworkX fallback disabled.")
            self.csr_adapter.add_edge(
                edge.source,
                edge.target,
                edge.label,
                edge.properties
            )
            
            self.operation_counts['add_edge'] += 1

            # Update ContextMeta edge_count
            try:
                meta_node = self.csr_adapter.get_node("_context_meta")
                if meta_node:
                    meta_node.properties["edge_count"] = meta_node.properties.get("edge_count", 0) + 1
                    meta_node.properties["updated_at"] = _dt.now(_tz.utc).isoformat()
                    self.csr_adapter.update_node_properties("_context_meta", meta_node.properties)
            except Exception:
                pass
            # Only log edge additions in debug mode to reduce verbosity
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"Edge '{edge.source}' -> '{edge.target}' added successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to add edge '{edge.source}' -> '{edge.target}': {e}")
            return False
    
    def get_node(self, node_id: str) -> Optional[GraphNode]:
        """Get a node by ID. Checks both persistent storage and buffer (like Neo4j's page cache)."""
        # CSR-only: Get node from CSR storage first
        if not self.csr_adapter:
            # If no CSR adapter, check buffer only
            if self.buffer_enabled and self.buffer_manager:
                return self._get_buffered_node_by_id(node_id)
            return None
        
        csr_node = self.csr_adapter.get_node(node_id)
        if csr_node:
            properties = self.node_properties.get(node_id, {})
            # node_id is now the UUID - use it directly
            # Extract name and alias from properties if present
            node_uuid = node_id  # node_id is the UUID
            node_name = properties.get('name')
            node_alias = properties.get('alias')
            return GraphNode(
                id=node_id, 
                label=csr_node.node_type, 
                properties=properties,
                uuid=node_uuid,
                name=node_name,
                alias=node_alias
            )
        
        # OPTIMIZATION: Also check buffer memory for pending nodes (immediate consistency)
        # This ensures nodes created in buffer are immediately visible (like Neo4j's page cache)
        if self.buffer_enabled and self.buffer_manager:
            buffered_node = self._get_buffered_node_by_id(node_id)
            if buffered_node:
                return buffered_node
        
        return None

    def update_node(
        self,
        node_id: str,
        properties: dict,
        expected_version: Optional[int] = None,
        author: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> "GraphNode":
        """Update a node's properties with optional optimistic locking.

        Snapshots old content in TemporalStorage before overwriting.
        Propagates stale flag to directly downstream nodes.

        Args:
            node_id: ID of the node to update.
            properties: Property dict to merge into the existing node.
            expected_version: If set, raises VersionConflictError on mismatch.
            author: Agent/user making the change (stored as _updated_by).
            reason: Why this change is being made (stored as _change_reason).

        Raises:
            KeyError: If the node does not exist.
            VersionConflictError: If expected_version is provided and does not
                match the stored version.
        """
        from datetime import datetime as _dt, timezone as _tz

        existing = self.get_node(node_id)
        if existing is None:
            raise KeyError(f"Node {node_id!r} not found")

        stored_version = existing.properties.get("version", 1)
        if expected_version is not None and expected_version != stored_version:
            raise VersionConflictError(
                current_version=stored_version,
                provided_version=expected_version,
            )

        # Snapshot old content before overwriting
        if self.temporal_enabled and self.temporal_storage:
            try:
                self.temporal_storage.create_version(
                    entity_id=node_id,
                    entity_type="node",
                    properties=existing.properties.copy(),
                    operation="UPDATE",
                    timestamp=_dt.now(),
                    author=author or "",
                    reason=reason or "",
                )
            except Exception as e:
                logger.warning(f"[TIME_TRAVEL] Failed to snapshot node {node_id}: {e}")

        merged = dict(existing.properties)
        merged.update(properties)
        merged["version"] = stored_version + 1
        merged["_updated_at"] = _dt.now(_tz.utc).isoformat()
        if author:
            merged["_updated_by"] = author
        if reason:
            merged["_change_reason"] = reason

        updated = GraphNode(id=node_id, label=existing.label, properties=merged)
        self.add_node(updated, _skip_temporal=True)

        # Snapshot NEW content too (v2 in temporal storage)
        if self.temporal_enabled and self.temporal_storage:
            try:
                self.temporal_storage.create_version(
                    entity_id=node_id,
                    entity_type="node",
                    properties=updated.properties.copy(),
                    operation="UPDATE",
                    timestamp=_dt.now(),
                    author=author or "",
                    reason=reason or "",
                )
            except Exception as e:
                logger.warning(f"[TIME_TRAVEL] Failed to snapshot updated node {node_id}: {e}")

        # Propagate stale to directly downstream nodes
        self._propagate_stale(node_id, merged["version"])

        return updated

    def _propagate_stale(self, source_node_id: str, new_version: int) -> None:
        """Flag all directly-downstream nodes as stale (one hop only).

        Called automatically by update_node(). Never recursive — only
        direct outgoing neighbors are flagged.
        """
        from datetime import datetime as _dt, timezone as _tz

        source_node = self.get_node(source_node_id)
        if not source_node:
            return

        source_name = source_node.properties.get("name", source_node_id[:8])
        stale_reason = f"{source_name} updated to v{new_version}"
        stale_at = _dt.now(_tz.utc).isoformat()

        try:
            neighbors = self.get_neighbors(source_node_id, direction="OUTGOING")
        except Exception as e:
            logger.warning(f"[STALE] Failed to get neighbors of {source_node_id}: {e}")
            return

        for neighbor_id, _ in neighbors:
            neighbor = self.get_node(neighbor_id)
            if not neighbor:
                continue
            props = dict(neighbor.properties)
            props["_stale"] = True
            props["_stale_reason"] = stale_reason
            props["_stale_since"] = stale_at
            # Write directly via add_node to avoid triggering another update_node
            # cascade — stale writes are not versioned changes
            self.add_node(GraphNode(id=neighbor_id, label=neighbor.label, properties=props), _skip_temporal=True)

    def confirm_current(self, node_id: str, author: str = "") -> "GraphNode":
        """Clear the stale flag on a node — agent confirms it is still valid.

        Args:
            node_id: UUID of the node to confirm.
            author: Agent or user making the confirmation.

        Raises:
            KeyError: If node_id does not exist.
        """
        from datetime import datetime as _dt, timezone as _tz

        node = self.get_node(node_id)
        if node is None:
            raise KeyError(f"Node {node_id!r} not found")

        props = dict(node.properties)
        props["_stale"] = False
        props.pop("_stale_reason", None)
        props.pop("_stale_since", None)
        if author:
            props["_stale_confirmed_by"] = author
        props["_stale_confirmed_at"] = _dt.now(_tz.utc).isoformat()

        updated = GraphNode(id=node_id, label=node.label, properties=props)
        self.add_node(updated, _skip_temporal=True)
        return updated

    def get_neighbors(self, node_id: str, edge_label: Optional[str] = None, direction: str = "OUTGOING") -> List[Tuple[str, str]]:
        """Get neighbors of a node."""
        self.operation_counts['get_neighbors'] += 1
        
        # CSR-only: Use CSR adapter (NetworkX disabled)
        if not self.csr_adapter:
            return []
        
        if direction == "OUTGOING":
            csr_neighbors = self.csr_adapter.get_neighbors(node_id, edge_label)
            # Convert CSR format to expected format
            return [(neighbor_id, edge.source_id + "_" + edge.target_id) for neighbor_id, edge in csr_neighbors]
        else:  # INCOMING
            # For incoming edges, traverse all edges to find those pointing to this node
            neighbors = []
            # Get all edges from CSR storage
            if hasattr(self.csr_storage, 'edge_data'):
                for edge in self.csr_storage.edge_data:
                    if edge.target_id == node_id:
                        if edge_label is None or edge.edge_type == edge_label:
                            edge_id = f"{edge.source_id}_{edge.target_id}"
                            neighbors.append((edge.source_id, edge_id))
            return neighbors
    
    def check_unique_constraint(self, constraint_name: str, value: Any) -> bool:
        """Check if a unique constraint is satisfied."""
        return value not in self.unique_constraints[constraint_name]
    
    def add_unique_constraint(self, constraint_name: str, value: Any):
        """Add a value to a unique constraint."""
        self.unique_constraints[constraint_name].add(value)
    
    def get_all_nodes(self, label: Optional[str] = None, *, limit: Optional[int] = None, offset: int = 0) -> List[GraphNode]:
        """Get all nodes, optionally filtered by label. Includes buffered nodes.

        Args:
            label: Optional label filter.
            limit: Max number of nodes to return (None = unlimited).
            offset: Number of matching nodes to skip before collecting results.
        """
        seen_ids = set()
        nodes = []
        skipped = 0

        def _should_stop():
            return limit is not None and len(nodes) >= limit

        def _collect(node):
            nonlocal skipped
            if _should_stop():
                return
            if offset > 0 and skipped < offset:
                skipped += 1
                return
            nodes.append(node)

        # Primary source: storage adapter
        if self.csr_adapter:
            # Redis backend: use label index when available (O(k) vs O(n))
            if self._using_redis_backend:
                adapter_nodes = self.csr_adapter.get_all_nodes(node_type=label) if label else self.csr_adapter.get_all_nodes()
                for gn in adapter_nodes:
                    if gn.id not in seen_ids:
                        seen_ids.add(gn.id)
                        _collect(gn)
                return nodes

            # CSR backend: iterate node IDs
            if label:
                node_ids = self.csr_adapter.get_nodes_by_type(label)
            else:
                node_ids = list(self.csr_storage.nodes.keys()) if hasattr(self.csr_storage, 'nodes') else []

            for node_id in node_ids:
                if _should_stop():
                    break
                node = self.get_node(node_id)
                if node and node.id not in seen_ids:
                    seen_ids.add(node.id)
                    _collect(node)

        # Secondary source: node_index (catches nodes not yet in CSR)
        if not _should_stop() and hasattr(self, 'node_index'):
            for node_id, node in self.node_index.items():
                if _should_stop():
                    break
                if node_id in seen_ids:
                    continue
                if label is None or node.label == label:
                    seen_ids.add(node_id)
                    _collect(node)

        # Tertiary source: write buffer (unflushed nodes)
        if not _should_stop():
            buffered_nodes = self._get_buffered_nodes(label)
            for buffered_node in buffered_nodes:
                if _should_stop():
                    break
                if buffered_node.id not in seen_ids:
                    seen_ids.add(buffered_node.id)
                    _collect(buffered_node)

        return nodes
    
    def _get_buffered_nodes(self, label: Optional[str] = None) -> List[GraphNode]:
        """Get nodes from buffer memory that haven't been flushed yet."""
        buffered_nodes = []
        
        if not self.buffer_enabled or not self.buffer_manager:
            return buffered_nodes
        
        try:
            # Get pending records from buffer
            import asyncio
            
            # Try to get records synchronously
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Can't run sync in async context, return empty
                    return buffered_nodes
                else:
                    # Run async method synchronously
                    records = loop.run_until_complete(self._get_buffer_records_async())
            except RuntimeError:
                # No event loop, create one
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                records = loop.run_until_complete(self._get_buffer_records_async())
                loop.close()
            
            # Convert buffer records to GraphNode objects
            for record in records:
                operation = record.get('operation', '')
                
                if operation == 'CREATE_NODE':
                    node_label = record.get('label', '')
                    node_id = record.get('node_id', '')
                    properties = record.get('properties', {})
                    
                    # Filter by label if specified
                    if label is None or node_label == label:
                        node = GraphNode(
                            id=node_id,
                            label=node_label,
                            properties=properties
                        )
                        buffered_nodes.append(node)
        
        except Exception as e:
            logger.debug(f"Error getting buffered nodes: {e}")
        
        return buffered_nodes
    
    def _get_buffered_node_by_id(self, node_id: str) -> Optional[GraphNode]:
        """Get a specific node from buffer by ID (for immediate consistency)."""
        if not self.buffer_enabled or not self.buffer_manager:
            return None
        
        try:
            # Get pending records from buffer synchronously
            records = self._get_buffer_records_sync()
            
            for record in records:
                if record.get('operation') == 'CREATE_NODE':
                    if record.get('node_id') == node_id:
                        return GraphNode(
                            id=record.get('node_id'),
                            label=record.get('label', ''),
                            properties=record.get('properties', {})
                        )
        except Exception as e:
            logger.debug(f"Error getting buffered node by ID: {e}")
        
        return None
    
    def _get_buffer_records_sync(self) -> List[Dict[str, Any]]:
        """Get buffer records synchronously (helper for sync contexts)."""
        import asyncio
        
        if not self.buffer_manager:
            return []
        
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Can't run sync in async context, return empty (will be available after flush)
                return []
            else:
                return loop.run_until_complete(self._get_buffer_records_async())
        except RuntimeError:
            # No event loop, create one
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(self._get_buffer_records_async())
            finally:
                loop.close()
    
    async def _get_buffer_records_async(self) -> List[Dict[str, Any]]:
        """Get all pending records from buffer asynchronously."""
        if not self.buffer_manager:
            return []
        
        try:
            # Check buffer type and get records accordingly
            if hasattr(self.buffer_manager, '_records'):
                # Local buffer - direct access
                async with self.buffer_manager._lock:
                    return list(self.buffer_manager._records)
            elif hasattr(self.buffer_manager, 'get_pending_records'):
                # Buffer manager with get_pending_records method
                return await self.buffer_manager.get_pending_records()
            else:
                # For other buffer types, we'd need to implement get_pending_records
                # For now, return empty list
                return []
        except Exception as e:
            logger.debug(f"Error getting buffer records: {e}")
            return []
    
    def get_all_edges(self, label: Optional[str] = None, *, limit: Optional[int] = None, offset: int = 0) -> List[GraphEdge]:
        """Get all edges, optionally filtered by label. Includes buffered edges.

        Args:
            label: Optional label filter.
            limit: Max number of edges to return (None = unlimited).
            offset: Number of matching edges to skip before collecting results.
        """
        edges = []
        _skipped = 0

        def _at_limit():
            return limit is not None and len(edges) >= limit
        # Redis backend: use adapter directly
        if self._using_redis_backend and self.csr_adapter:
            for edge in self.csr_adapter.get_all_edges():
                if _at_limit():
                    break
                edge_label = getattr(edge, 'edge_type', getattr(edge, 'label', ''))
                if label and edge_label != label:
                    continue
                if offset > 0 and _skipped < offset:
                    _skipped += 1
                    continue
                edges.append(GraphEdge(
                    id=getattr(edge, 'id', ''),
                    source=getattr(edge, 'source_id', getattr(edge, 'source', '')),
                    target=getattr(edge, 'target_id', getattr(edge, 'target', '')),
                    label=edge_label,
                    properties=getattr(edge, 'properties', {}),
                ))
            return edges

        # CSR backend: Get edges from CSR storage
        if not self.csr_storage or not hasattr(self.csr_storage, 'edge_data'):
            pass  # Continue to check buffer
        else:
            for edge in self.csr_storage.edge_data:
                if _at_limit():
                    break
                edge_label = edge.edge_type
                if label is None or edge_label == label:
                    import uuid as uuid_module
                    
                    # Try to find edge in edge_properties by matching source/target
                    # Since edge_properties now uses UUID as key, we need to search
                    edge_id = None
                    properties = edge.properties.copy() if edge.properties else {}
                    
                    # Search edge_properties for matching source/target
                    # This handles both new (UUID key) and old (source_target key) formats
                    old_format_id = f"{edge.source_id}_{edge.target_id}"
                    if old_format_id in self.edge_properties:
                        # Old format - migrate to UUID
                        edge_id = str(uuid_module.uuid4())
                        properties = self.edge_properties[old_format_id].copy()
                        # Migrate to new format
                        self.edge_properties[edge_id] = properties
                        self.edge_properties.pop(old_format_id, None)
                    else:
                        # Search for edge with matching source/target in edge_properties
                        for stored_id, stored_props in self.edge_properties.items():
                            # Check if properties indicate this is our edge
                            if (stored_props.get('_source') == edge.source_id or 
                                stored_props.get('source') == edge.source_id):
                                if (stored_props.get('_target') == edge.target_id or
                                    stored_props.get('target') == edge.target_id):
                                    edge_id = stored_id
                                    properties = stored_props.copy()
                                    break
                        
                        # If not found, generate new UUID (new edge or migration)
                        if edge_id is None:
                            edge_id = str(uuid_module.uuid4())
                            # Store properties with new UUID key
                            self.edge_properties[edge_id] = properties
                    
                    # Remove uuid from properties if present (to avoid duplication)
                    properties.pop('uuid', None)
                    
                    # edge_id is now the UUID
                    edge_uuid = edge_id
                    edge_name = properties.get('name')
                    edge_alias = properties.get('alias')
                    
                    graph_edge = GraphEdge(
                        id=edge_id,  # This is the UUID
                        source=edge.source_id,  # Source node UUID
                        target=edge.target_id,  # Target node UUID
                        label=edge_label,
                        properties=properties,
                        uuid=edge_uuid,
                        name=edge_name,
                        alias=edge_alias
                    )
                    if offset > 0 and _skipped < offset:
                        _skipped += 1
                        continue
                    edges.append(graph_edge)

        # OPTIMIZATION: Also check buffer memory for pending edges
        if not _at_limit():
            buffered_edges = self._get_buffered_edges(label)
            if buffered_edges:
                existing_ids = {edge.id for edge in edges}
                # Also deduplicate by (source, target, label) to handle write-through duplicates
                existing_triples = {(edge.source, edge.target, edge.label) for edge in edges}
                for buffered_edge in buffered_edges:
                    if _at_limit():
                        break
                    triple = (buffered_edge.source, buffered_edge.target, buffered_edge.label)
                    if buffered_edge.id not in existing_ids and triple not in existing_triples:
                        if offset > 0 and _skipped < offset:
                            _skipped += 1
                            continue
                        edges.append(buffered_edge)

        return edges

    def get_statistics(self) -> Dict[str, Any]:
        """Return graph statistics for query optimization.

        Returns node/edge counts by type using the label index (O(1) per label
        on Redis, O(k) on CSR) — avoids full scan.
        """
        node_counts: Dict[str, int] = {}
        edge_counts: Dict[str, int] = {}
        total_nodes = 0
        total_edges = 0

        if self._using_redis_backend and self.csr_adapter:
            # Use Redis SETs for O(1) count per label
            r = getattr(self.csr_adapter, '_r', None)
            prefix = getattr(self.csr_adapter, '_prefix', '')
            if r:
                total_nodes = r.scard(f"{prefix}nodes")
                total_edges = r.scard(f"{prefix}edges")
                # Scan ntype:* keys for label counts
                for key in r.scan_iter(f"{prefix}ntype:*", count=100):
                    label_name = key.split("ntype:")[-1] if "ntype:" in key else ""
                    if label_name:
                        node_counts[label_name] = r.scard(key)
                for key in r.scan_iter(f"{prefix}etype:*", count=100):
                    etype = key.split("etype:")[-1] if "etype:" in key else ""
                    if etype:
                        edge_counts[etype] = r.scard(key)
        elif self.csr_adapter:
            # CSR: use in-memory node_types dict
            nt = getattr(self.csr_adapter, 'node_types', {})
            for lbl, ids in nt.items():
                node_counts[lbl] = len(ids)
            total_nodes = sum(node_counts.values())
            total_edges = len(self.get_all_edges())

        return {
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "node_count_by_label": node_counts,
            "edge_count_by_type": edge_counts,
            "label_index_available": True,
        }

    def _get_buffered_edges(self, label: Optional[str] = None) -> List[GraphEdge]:
        """Get edges from buffer memory that haven't been flushed yet."""
        buffered_edges = []
        
        if not self.buffer_enabled or not self.buffer_manager:
            return buffered_edges
        
        try:
            import asyncio
            
            # Try to get records synchronously
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    return buffered_edges  # Can't run sync in async context
                else:
                    records = loop.run_until_complete(self._get_buffer_records_async())
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                records = loop.run_until_complete(self._get_buffer_records_async())
                loop.close()
            
            # Convert buffer records to GraphEdge objects
            for record in records:
                operation = record.get('operation', '')
                
                if operation == 'CREATE_EDGE':
                    edge_label = record.get('label', '')
                    edge_id = record.get('edge_id', f"{record.get('source')}_{record.get('target')}")
                    source = record.get('source', '')
                    target = record.get('target', '')
                    properties = record.get('properties', {})
                    
                    # Filter by label if specified
                    if label is None or edge_label == label:
                        edge = GraphEdge(
                            id=edge_id,
                            source=source,
                            target=target,
                            label=edge_label,
                            properties=properties
                        )
                        buffered_edges.append(edge)
        
        except Exception as e:
            logger.debug(f"Error getting buffered edges: {e}")
        
        return buffered_edges
    
    def get_node_types(self) -> Set[str]:
        """Get all unique node types (labels) in the graph."""
        # CSR-only: Get node types from CSR storage
        if not self.csr_storage:
            return set()
        return self.csr_storage.get_node_types()
    
    def get_edge_types(self) -> Set[str]:
        """Get all unique edge types (labels) in the graph."""
        # CSR-only: Get edge types from CSR storage
        if not self.csr_storage:
            return set()
        return self.csr_storage.get_edge_types()
    
    def traverse_graph(self, start_node_id: str, edge_label: Optional[str] = None, 
                      max_depth: int = 1, direction: str = "OUTGOING") -> List[str]:
        """Traverse the graph from a starting node."""
        self.operation_counts['traverse'] += 1
        
        # CSR-only: Use CSR adapter (NetworkX disabled)
        if not self.csr_adapter:
            return []
        return self.csr_adapter.traverse(start_node_id, edge_label, max_depth, direction)
    
    def get_nodes_by_property(self, property_name: str, property_value: Any) -> List[str]:
        """Get nodes by property value."""
        # CSR-only: Use CSR adapter (NetworkX disabled)
        if not self.csr_adapter:
            return []
        return self.csr_adapter.get_nodes_by_property(property_name, property_value)
    
    def _update_property_indexes(self, node_id: str, properties: Dict[str, Any]):
        """Update property indexes when a node is added."""
        for prop_name, prop_value in properties.items():
            # Skip complex types (dict, list) that can't be indexed
            if isinstance(prop_value, (dict, list)):
                continue
            
            # Skip None values
            if prop_value is None:
                continue
            
            # Test hashability to prevent "unhashable type" errors
            try:
                # Try to use value as dict key (tests hashability)
                _ = {prop_value: True}
            except TypeError:
                # Value is not hashable, skip indexing
                continue
            
            if prop_name not in self._property_indexes:
                if SORTED_CONTAINERS_AVAILABLE:
                    self._property_indexes[prop_name] = SortedDict()
                else:
                    self._property_indexes[prop_name] = {}
            
            if SORTED_CONTAINERS_AVAILABLE:
                # Use SortedDict — coerce key to str to avoid mixed-type comparison errors
                _idx_key = str(prop_value) if not isinstance(prop_value, str) else prop_value
                if _idx_key not in self._property_indexes[prop_name]:
                    self._property_indexes[prop_name][_idx_key] = set()
                self._property_indexes[prop_name][_idx_key].add(node_id)
            else:
                # Use regular dict
                if prop_value not in self._property_indexes[prop_name]:
                    self._property_indexes[prop_name][prop_value] = set()
                self._property_indexes[prop_name][prop_value].add(node_id)
    
    def filter_by_property(self, prop_name: str, prop_value: Any) -> Set[str]:
        """Fast property filtering using index."""
        if prop_name not in self._property_indexes:
            return set()
        
        index = self._property_indexes[prop_name]
        return index.get(prop_value, set())
    
    def filter_by_property_range(self, prop_name: str, op: str, value: Any) -> Set[str]:
        """Filter by property range using index."""
        if prop_name not in self._property_indexes:
            return set()
        
        index = self._property_indexes[prop_name]
        filtered = set()
        
        if op == '=':
            return index.get(value, set())
        elif op == '>':
            # For now, use dict fallback for range queries
            for k, node_ids in index.items():
                if k > value:
                    filtered.update(node_ids)
        elif op == '<':
            for k, node_ids in index.items():
                if k < value:
                    filtered.update(node_ids)
        elif op == '>=':
            for k, node_ids in index.items():
                if k >= value:
                    filtered.update(node_ids)
        elif op == '<=':
            for k, node_ids in index.items():
                if k <= value:
                    filtered.update(node_ids)
        
        return filtered
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get comprehensive graph statistics."""
        # CSR-only: Get statistics from CSR storage
        if not self.csr_storage:
            return {
                'node_count': 0,
                'edge_count': 0,
                'node_types': 0,
                'edge_types': 0,
                'storage_backend': 'CSR',
                'operation_counts': self.operation_counts
            }
        
        csr_stats = self.csr_storage.get_statistics()
        csr_stats['storage_backend'] = 'CSR'
        csr_stats['operation_counts'] = self.operation_counts
        return csr_stats
    
    def optimize(self):
        """Optimize the graph storage."""
        # CSR-only: Optimize CSR storage
        if self.csr_storage:
            self.csr_storage.optimize()
            logger.info("CSR storage optimized")
    
    def migrate_to_csr(self):
        """Migrate existing data to CSR storage. No-op in CSR-only mode."""
        logger.info("CSR-only mode: No migration needed (NetworkX disabled)")
        return True
    
    def clear(self):
        """Clear all data."""
        # CSR-only: Clear CSR storage and properties
        self.node_properties.clear()
        self.edge_properties.clear()
        self.unique_constraints.clear()
        # NOTE: alias_to_node removed - aliases are query-scoped only
        
        if self.csr_storage:
            self.csr_storage.clear()
        
        # NOTE: alias_mapping in csr_adapter is not used (aliases are query-scoped only)
        
        self.operation_counts = {k: 0 for k in self.operation_counts}
        logger.info("Graph storage cleared (CSR-only mode)")

# Performance comparison utility
def compare_storage_performance():
    """Compare performance between NetworkX and CSR storage."""
    import time
    import random
    
    print("Storage Performance Comparison")
    print("=" * 50)
    
    # Test parameters
    num_nodes = 1000
    num_edges = 2000
    
    # Generate test data
    nodes = []
    edges = []
    
    for i in range(num_nodes):
        nodes.append(GraphNode(
            id=f"node_{i}",
            label="Person",
            properties={"id": i, "name": f"Person_{i}", "age": random.randint(18, 80)}
        ))
    
    for i in range(num_edges):
        source = f"node_{random.randint(0, num_nodes-1)}"
        target = f"node_{random.randint(0, num_nodes-1)}"
        edges.append(GraphEdge(
            id=f"edge_{i}",
            source=source,
            target=target,
            label="KNOWS",
            properties={"weight": random.random()}
        ))
    
    # Test NetworkX-only storage
    print("\nTesting NetworkX-only storage:")
    nx_storage = HybridGraphStorage('networkx')
    
    start_time = time.time()
    for node in nodes:
        nx_storage.add_node(node)
    nx_node_time = time.time() - start_time
    
    start_time = time.time()
    for edge in edges:
        nx_storage.add_edge(edge)
    nx_edge_time = time.time() - start_time
    
    start_time = time.time()
    for i in range(100):
        start_node = f"node_{random.randint(0, num_nodes-1)}"
        nx_storage.traverse_graph(start_node, max_depth=2)
    nx_traverse_time = time.time() - start_time
    
    # Test CSR storage
    print("\nTesting CSR storage:")
    csr_storage = HybridGraphStorage('csr')
    
    start_time = time.time()
    for node in nodes:
        csr_storage.add_node(node)
    csr_node_time = time.time() - start_time
    
    start_time = time.time()
    for edge in edges:
        csr_storage.add_edge(edge)
    csr_edge_time = time.time() - start_time
    
    start_time = time.time()
    for i in range(100):
        start_node = f"node_{random.randint(0, num_nodes-1)}"
        csr_storage.traverse_graph(start_node, max_depth=2)
    csr_traverse_time = time.time() - start_time
    
    # Results
    print(f"\nResults ({num_nodes} nodes, {num_edges} edges):")
    print(f"Add Nodes:")
    print(f"  NetworkX: {nx_node_time:.4f}s")
    print(f"  CSR: {csr_node_time:.4f}s")
    print(f"  Speedup: {nx_node_time/csr_node_time:.2f}x")
    
    print(f"\nAdd Edges:")
    print(f"  NetworkX: {nx_edge_time:.4f}s")
    print(f"  CSR: {csr_edge_time:.4f}s")
    print(f"  Speedup: {nx_edge_time/csr_edge_time:.2f}x")
    
    print(f"\nTraverse (100 iterations):")
    print(f"  NetworkX: {nx_traverse_time:.4f}s")
    print(f"  CSR: {csr_traverse_time:.4f}s")
    print(f"  Speedup: {nx_traverse_time/csr_traverse_time:.2f}x")
    
    print(f"\nStorage Statistics:")
    print("NetworkX:", nx_storage.get_statistics())
    print("CSR:", csr_storage.get_statistics())

# Backward compatibility alias
AIContextDB = ContextSynapse

if __name__ == "__main__":
    compare_storage_performance()
