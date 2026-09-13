"""Configuration for storage modes and namespace settings."""

from enum import Enum
from pathlib import Path
from typing import Dict, Any, Optional
import json
import yaml
import logging

logger = logging.getLogger(__name__)


class StorageMode(Enum):
    """Storage mode options."""
    PURE_GRAPH = "pure_graph"
    COLLECTION_BASED = "collection_based"
    HYBRID = "hybrid"


class NamespaceConfig:
    """Namespace configuration manager."""
    
    def __init__(self, namespace: str, config_path: Optional[Path] = None):
        self.namespace = namespace
        self.config_path = config_path or self._get_default_config_path()
        self.config = self._load_config()
        
    def _get_default_config_path(self) -> Path:
        """Get default config path for namespace."""
        return Path(f"contextcore_data/namespaces/{self.namespace}/config.yaml")
    
    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from file or return defaults."""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r') as f:
                    if self.config_path.suffix == '.yaml' or self.config_path.suffix == '.yml':
                        return yaml.safe_load(f) or {}
                    else:
                        return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load config from {self.config_path}: {e}")
                return self._get_default_config()
        else:
            # Create default config
            default_config = self._get_default_config()
            self._save_config(default_config)
            return default_config
    
    @property
    def embedding_config(self) -> Dict[str, Any]:
        """Get embedding configuration for HYBRID SEARCH."""
        return self.config.get('embedding', {
            'enabled': False,  # Don't generate embeddings by default
            'model': 'text-embedding-3-small',
            'api_key': None,  # Will use OPENAI_API_KEY from env if not set
            'dimension': 1536,
            'provider': 'openai'
        })
    
    def _get_default_config(self) -> Dict[str, Any]:
        """Get default configuration."""
        return {
            'namespace': self.namespace,
            'mode': 'collection_based',  # Default to collection-based for backward compatibility
            'storage_mode': 'ai',  # Default: AI mode (optimized for RAG/Agentic/Multimodal)
            'metadata': {
                'database': 'sqlite',
                'path': f'contextcore_data/namespaces/{self.namespace}/metadata/metadata.db'
            },
            'graph': {
                'storage_strategy': 'single_file',  # Options: 'single_file' (HDF5), 'multi_file_optimized' (format-specific)
                'storage_type': 'hdf5',  # Deprecated: use storage_strategy
                'storage_format': 'single_file',  # Deprecated: use storage_strategy
                'location': 'graph/',
                'enable_csr': True,
                # Single-file HDF5 config
                'single_file': {
                    'enabled': True,
                    'compression': 'gzip',
                    'compression_level': 6,
                    'enable_metadata': True,
                    'enable_csr': True
                },
                # Multi-file optimized config
                'multi_file_optimized': {
                    'enabled': True,
                    'graph_format': 'csr',  # CSR for graph structure (nodes, edges)
                    'properties_format': 'parquet',  # Parquet for node/edge properties
                    'csr_format': 'npz',  # NPZ for CSR matrices
                    'vectors_format': 'npy',  # NPY for vector embeddings
                    'tables_format': 'parquet'  # Parquet for tables
                }
            },
            'collections': {
                'enabled': True,
                'type_based': True,
                'schema': 'config/core/schema.yaml'
            },
            'document_store': {
                'backend': 'tinydb',  # Options: 'tinydb', 'sqlite', 'couchdb'
                'enabled': True,
                'config': {
                    # Backend-specific configuration
                    # For TinyDB/SQLite: optional 'storage_path'
                    # For CouchDB: 'url', 'username', 'password', 'database'
                }
            },
            'vector_db': {
                'backend': 'faiss',  # Options: 'custom' (NumPy), 'faiss' (recommended), 'chroma', 'qdrant'
                'enabled': True,
                'config': {
                    # FAISS: 'index_type' ('flat', 'ivf', 'hnsw')
                    # Chroma/Qdrant: 'persist_directory', 'collection_name'
                }
            },
            'buffer': {
                'type': 'local',  # Options: 'local', 'file', 'redis', 'sqlite', 'duckdb', 'lmdb'
                'enabled': True,  # Enabled by default for delayed block updates
                'config': {
                    'max_size': 1000,  # Max records before auto-flush
                    'flush_interval': 2.0,  # Seconds between auto-flushes (delayed block update)
                    'batch_size': 100,  # Records per batch when flushing
                    'auto_flush': True,  # Enable automatic flushing
                    'flush_threshold': 0.7,  # Flush when buffer reaches 70% capacity (0.0-1.0)
                    'threshold_flush_enabled': True,  # Enable threshold-based flushing
                    # Backend-specific config:
                    # Redis: 'host', 'port', 'db', 'password'
                    # SQLite/DuckDB/LMDB: 'path'
                    # File: 'path'
                }
            },
            'storage_mode': 'ai'  # Options: 'ai' (default), 'pure_graph', 'hybrid'
        }
    
    def _save_config(self, config: Dict[str, Any]):
        """Save configuration to file."""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, 'w') as f:
                if self.config_path.suffix == '.yaml' or self.config_path.suffix == '.yml':
                    yaml.dump(config, f, default_flow_style=False)
                else:
                    json.dump(config, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save config to {self.config_path}: {e}")
    
    @property
    def mode(self) -> StorageMode:
        """Get storage mode."""
        mode_str = self.config.get('mode', 'collection_based')
        try:
            return StorageMode(mode_str)
        except ValueError:
            logger.warning(f"Invalid mode '{mode_str}', defaulting to 'collection_based'")
            return StorageMode.COLLECTION_BASED
    
    @property
    def metadata_config(self) -> Dict[str, Any]:
        """Get metadata database configuration."""
        return self.config.get('metadata', {
            'database': 'sqlite',
            'path': f'contextcore_data/namespaces/{self.namespace}/metadata/metadata.db'
        })
    
    @property
    def graph_config(self) -> Dict[str, Any]:
        """Get graph storage configuration."""
        return self.config.get('graph', {
            'storage_type': 'hdf5',  # Default: HDF5 for single-file
            'storage_format': 'single_file',  # Default: single-file HDF5 (Oracle-type)
            'location': 'graph/',
            'enable_csr': True
        })
    
    @property
    def collections_config(self) -> Dict[str, Any]:
        """Get collections configuration."""
        return self.config.get('collections', {
            'enabled': True,
            'type_based': True,
            'schema': 'knowledge_graph.yaml'
        })
    
    @property
    def document_store_config(self) -> Dict[str, Any]:
        """Get document store configuration."""
        return self.config.get('document_store', {
            'backend': 'tinydb',
            'enabled': True,
            'config': {}
        })
    
    @property
    def storage_mode(self) -> str:
        """Get storage mode ('ai', 'pure_graph', or 'hybrid')."""
        return self.config.get('storage_mode', 'ai')  # Default: AI mode
    
    @property
    def buffer_config(self) -> Dict[str, Any]:
        """Get buffer manager configuration."""
        return self.config.get('buffer', {
            'type': 'local',
            'enabled': True,  # Enabled by default for delayed block updates
            'config': {
                'max_size': 1000,
                'flush_interval': 2.0,  # Delayed block update: flush every 2 seconds
                'batch_size': 100,
                'auto_flush': True
            }
        })
    
    def set_mode(self, mode: StorageMode):
        """Set storage mode."""
        self.config['mode'] = mode.value
        self._save_config(self.config)
    
    def get_config(self) -> Dict[str, Any]:
        """Get full configuration."""
        return self.config.copy()



