"""
Database Configuration Manager

Centralized configuration system similar to PostgreSQL (postgresql.conf), 
MySQL (my.cnf), MongoDB (mongod.conf), etc.

Supports:
- Performance tuning
- Storage configuration
- Connection settings
- Logging configuration
- Feature flags
"""

import json
import yaml
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Union
from dataclasses import dataclass, asdict
from enum import Enum

logger = logging.getLogger(__name__)


class ConfigFormat(Enum):
    """Supported configuration formats."""
    YAML = "yaml"
    JSON = "json"
    INI = "ini"


@dataclass
class PerformanceConfig:
    """Performance tuning parameters."""
    # Graph Database Settings (moved from config/config.yaml)
    default_engine: str = "csr"  # Graph engine: csr (NetworkX disabled - CSR-only mode)
    auto_index: bool = True  # Enable automatic indexing
    cache_size: int = 1000  # Cache size (number of items)
    
    # Graph operations
    max_nodes_in_memory: int = 1000000  # Max nodes before spilling to disk
    max_edges_in_memory: int = 5000000  # Max edges before spilling to disk
    cache_size_mb: int = 512  # Cache size in MB
    enable_query_cache: bool = True  # Enable query result caching
    
    # CSR settings
    csr_auto_switch_threshold: int = 1000  # Switch to CSR at this node count
    csr_enabled: bool = True  # Enable CSR storage
    
    # Buffer settings (for delayed block updates)
    buffer_enabled: bool = True  # Enable buffer manager for batch writes
    buffer_type: str = "local"  # Buffer backend: local, file, redis, sqlite, duckdb, lmdb
    buffer_max_size: int = 1000  # Max records before auto-flush
    buffer_flush_interval: float = 2.0  # Seconds between auto-flushes (delayed block update)
    buffer_batch_size: int = 100  # Records per batch when flushing
    buffer_auto_flush: bool = True  # Enable automatic flushing
    buffer_flush_threshold: float = 0.7  # Flush when buffer reaches 70% capacity (0.0-1.0)
    buffer_threshold_flush_enabled: bool = True  # Enable threshold-based flushing
    
    # Vector operations
    vector_batch_size: int = 1000  # Batch size for vector operations
    vector_index_type: str = "flat"  # FAISS index type: flat, ivf, hnsw
    
    # Parallel processing
    max_workers: int = 4  # Max parallel workers
    enable_parallel_queries: bool = True  # Enable parallel query execution
    
    # Memory management
    gc_threshold: int = 10000  # Garbage collection threshold
    enable_auto_gc: bool = True  # Automatic garbage collection
    
    # WAL (Write-Ahead Logging) Configuration - Neo4j-level performance
    wal_enabled: bool = True  # Enable WAL for fast writes (Neo4j-style)
    wal_dir: str = "./contextcore_wal"  # WAL directory
    wal_max_size_mb: int = 100  # Max WAL size before rotation (MB)
    wal_checkpoint_interval: int = 1000  # Checkpoint every N operations
    wal_sync_interval: float = 1.0  # Sync to disk every N seconds
    wal_compression: bool = True  # Enable WAL compression
    wal_background_flush: bool = True  # Background flush to main graph file
    
    # Persistence Performance Optimization
    persistence_mode: str = "wal"  # Options: "wal" (fast, recommended), "direct" (fallback)
    background_save_threshold: int = 10000  # Use background save for graphs > N nodes
    immediate_persistence: bool = True  # Neo4j-style immediate persistence


@dataclass
class StorageConfig:
    """Storage configuration."""
    # Storage strategy
    storage_strategy: str = "single_file"  # Options: "single_file" (HDF5), "multi_file_optimized" (format-specific)
    storage_format: str = "single_file"  # Deprecated: use storage_strategy instead
    storage_type: str = "hdf5"  # json, hdf5, parquet (default: hdf5 for single-file)
    
    # Compression
    compression_enabled: bool = True
    compression_level: int = 6  # 0-9, higher = more compression
    
    # Persistence
    auto_save: bool = True  # Auto-save on changes
    auto_save_interval: int = 300  # Auto-save interval in seconds
    save_on_shutdown: bool = True  # Save on graceful shutdown
    
    # Backup
    backup_enabled: bool = True
    backup_retention_days: int = 30  # Keep backups for N days
    backup_location: str = "contextcore_data/backups"
    
    # Node properties
    node_properties_format: str = "parquet"  # json, parquet
    node_properties_compression: str = "snappy"  # snappy, gzip, lz4
    
    # CSR storage
    csr_save_enabled: bool = True
    csr_load_priority: bool = True  # Try CSR first on load


@dataclass
class ConnectionConfig:
    """Connection and API settings."""
    # API server
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_workers: int = 1  # Single process — agents connect from their own platforms
    
    # Timeouts
    request_timeout: int = 300  # Request timeout in seconds
    session_timeout: int = 3600  # Session timeout in seconds
    
    # Rate limiting
    rate_limit_enabled: bool = False
    rate_limit_per_minute: int = 1000
    
    # CORS
    cors_enabled: bool = True
    cors_origins: list = None  # None = allow all
    
    def __post_init__(self):
        if self.cors_origins is None:
            self.cors_origins = ["*"]


@dataclass
class LoggingConfig:
    """Logging configuration."""
    # Log levels
    log_level: str = "INFO"  # DEBUG, INFO, WARNING, ERROR, CRITICAL
    log_file: Optional[str] = None  # None = console only
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    log_date_format: str = "%Y-%m-%d %H:%M:%S"
    
    # Log rotation
    log_rotation_enabled: bool = True
    log_max_bytes: int = 10485760  # 10 MB
    log_backup_count: int = 5
    
    # Component-specific logging
    log_queries: bool = True  # Log all queries
    log_performance: bool = False  # Log performance metrics
    log_storage_ops: bool = False  # Log storage operations


@dataclass
class SecurityConfig:
    """Security settings."""
    # Authentication
    auth_enabled: bool = False
    auth_method: str = "token"  # token, oauth, basic
    
    # Encryption
    encryption_enabled: bool = False
    encryption_key: Optional[str] = None
    
    # Access control
    access_control_enabled: bool = False
    default_role: str = "user"  # admin, user, readonly


@dataclass
class TraceabilityConfig:
    """Traceability/Blockchain configuration."""
    enabled: bool = True  # Enable blockchain/traceability layer
    batch_size: int = 100  # Commit Merkle root every N operations
    auto_commit: bool = True  # Auto-commit batches
    verify_on_read: bool = False  # Verify integrity on read (performance impact)
    persistence_enabled: bool = True  # Persist blockchain to disk


@dataclass
class FeatureFlags:
    """Feature flags."""
    # Experimental features
    enable_temporal_queries: bool = True  # Enable time-travel queries
    enable_distributed_mode: bool = False
    enable_replication: bool = False
    
    # Advanced features
    enable_full_text_search: bool = True
    enable_vector_search: bool = True
    enable_graph_algorithms: bool = True
    
    # Performance features
    enable_query_optimizer: bool = True
    enable_index_hints: bool = True


@dataclass
class DatabaseConfig:
    """Complete database configuration."""
    # Database identity
    database_name: str = "contextsynapse"
    database_version: str = "1.0.0"
    
    # Configuration sections
    performance: PerformanceConfig = None
    storage: StorageConfig = None
    connection: ConnectionConfig = None
    logging: LoggingConfig = None
    security: SecurityConfig = None
    features: FeatureFlags = None
    traceability: TraceabilityConfig = None
    
    def __post_init__(self):
        if self.performance is None:
            self.performance = PerformanceConfig()
        if self.storage is None:
            self.storage = StorageConfig()
        if self.connection is None:
            self.connection = ConnectionConfig()
        if self.logging is None:
            self.logging = LoggingConfig()
        if self.security is None:
            self.security = SecurityConfig()
        if self.features is None:
            self.features = FeatureFlags()
        if self.traceability is None:
            self.traceability = TraceabilityConfig()


class DatabaseConfigManager:
    """Manages database configuration files."""
    
    DEFAULT_CONFIG_FILE = "contextsynapse.conf"
    DEFAULT_CONFIG_DIR = Path("contextcore_data/config")
    
    def __init__(self, config_file: Optional[Union[str, Path]] = None):
        """
        Initialize configuration manager.
        
        Args:
            config_file: Path to configuration file (default: config/core/database.yaml or contextsynapse.conf)
        """
        if config_file is None:
            # Try multiple locations (new structure first, then legacy fallbacks)
            possible_locations = [
                Path("config/core/database.yaml"),  # New structure (primary)
                Path.home() / ".contextcore" / "contextsynapse.conf",  # User home (legacy)
                self.DEFAULT_CONFIG_DIR / "contextsynapse.conf",  # Data directory (legacy)
                Path(self.DEFAULT_CONFIG_FILE),  # Legacy: Current directory (last resort)
            ]
            
            for loc in possible_locations:
                if loc.exists():
                    config_file = loc
                    break
            else:
                # Use default location
                config_file = Path(self.DEFAULT_CONFIG_FILE)
        else:
            config_file = Path(config_file)
        
        self.config_file = config_file
        self.config = self._load_config()
    
    def _load_config(self) -> DatabaseConfig:
        """Load configuration from file or return defaults."""
        if not self.config_file.exists():
            logger.info(f"Config file not found: {self.config_file}, using defaults")
            return DatabaseConfig()
        
        try:
            with open(self.config_file, 'r') as f:
                if self.config_file.suffix in ['.yaml', '.yml']:
                    data = yaml.safe_load(f) or {}
                elif self.config_file.suffix == '.json':
                    data = json.load(f)
                else:
                    # Try YAML first, then JSON
                    try:
                        f.seek(0)
                        data = yaml.safe_load(f) or {}
                    except:
                        f.seek(0)
                        data = json.load(f)
            
            return self._dict_to_config(data)
            
        except Exception as e:
            logger.error(f"Failed to load config from {self.config_file}: {e}")
            logger.info("Using default configuration")
            return DatabaseConfig()
    
    def _dict_to_config(self, data: Dict[str, Any]) -> DatabaseConfig:
        """Convert dictionary to DatabaseConfig."""
        config = DatabaseConfig()
        
        # Database identity
        config.database_name = data.get('database_name', config.database_name)
        config.database_version = data.get('database_version', config.database_version)
        
        # Performance
        if 'performance' in data:
            perf_data = data['performance']
            config.performance = PerformanceConfig(**{
                k: v for k, v in perf_data.items() 
                if k in PerformanceConfig.__annotations__
            })
        
        # Storage
        if 'storage' in data:
            storage_data = data['storage']
            config.storage = StorageConfig(**{
                k: v for k, v in storage_data.items()
                if k in StorageConfig.__annotations__
            })
        
        # Connection
        if 'connection' in data:
            conn_data = data['connection']
            config.connection = ConnectionConfig(**{
                k: v for k, v in conn_data.items()
                if k in ConnectionConfig.__annotations__
            })
        
        # Logging
        if 'logging' in data:
            log_data = data['logging']
            config.logging = LoggingConfig(**{
                k: v for k, v in log_data.items()
                if k in LoggingConfig.__annotations__
            })
        
        # Security
        if 'security' in data:
            sec_data = data['security']
            config.security = SecurityConfig(**{
                k: v for k, v in sec_data.items()
                if k in SecurityConfig.__annotations__
            })
        
        # Features
        if 'features' in data:
            feat_data = data['features']
            config.features = FeatureFlags(**{
                k: v for k, v in feat_data.items()
                if k in FeatureFlags.__annotations__
            })
        
        # Traceability (Blockchain)
        if 'traceability' in data:
            trace_data = data['traceability']
            config.traceability = TraceabilityConfig(**{
                k: v for k, v in trace_data.items()
                if k in TraceabilityConfig.__annotations__
            })
        
        return config
    
    def save_config(self, config: Optional[DatabaseConfig] = None, format: ConfigFormat = ConfigFormat.YAML):
        """Save configuration to file."""
        if config is None:
            config = self.config
        
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            config_dict = self._config_to_dict(config)
            
            with open(self.config_file, 'w') as f:
                if format == ConfigFormat.YAML:
                    yaml.dump(config_dict, f, default_flow_style=False, sort_keys=False)
                elif format == ConfigFormat.JSON:
                    json.dump(config_dict, f, indent=2, sort_keys=False)
            
            logger.info(f"Configuration saved to {self.config_file}")
            
        except Exception as e:
            logger.error(f"Failed to save config to {self.config_file}: {e}")
            raise
    
    def _config_to_dict(self, config: DatabaseConfig) -> Dict[str, Any]:
        """Convert DatabaseConfig to dictionary."""
        return {
            'database_name': config.database_name,
            'database_version': config.database_version,
            'performance': asdict(config.performance),
            'storage': asdict(config.storage),
            'connection': asdict(config.connection),
            'logging': asdict(config.logging),
            'security': asdict(config.security),
            'features': asdict(config.features),
            'traceability': asdict(config.traceability),
        }
    
    def get_config(self) -> DatabaseConfig:
        """Get current configuration."""
        return self.config
    
    def update_config(self, section: str, **kwargs):
        """Update configuration section."""
        if not hasattr(self.config, section):
            raise ValueError(f"Unknown config section: {section}")
        
        section_obj = getattr(self.config, section)
        for key, value in kwargs.items():
            if hasattr(section_obj, key):
                setattr(section_obj, key, value)
            else:
                logger.warning(f"Unknown config key: {section}.{key}")
    
    def create_default_config(self, output_file: Optional[Union[str, Path]] = None):
        """Create a default configuration file with all options."""
        if output_file is None:
            output_file = self.config_file
        
        output_file = Path(output_file)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        default_config = DatabaseConfig()
        self.save_config(default_config, format=ConfigFormat.YAML)
        
        logger.info(f"Default configuration created at {output_file}")


# Global config manager instance
_global_config_manager: Optional[DatabaseConfigManager] = None


def get_config_manager(config_file: Optional[Union[str, Path]] = None) -> DatabaseConfigManager:
    """Get or create global configuration manager."""
    global _global_config_manager
    if _global_config_manager is None:
        _global_config_manager = DatabaseConfigManager(config_file)
    return _global_config_manager


def get_config() -> DatabaseConfig:
    """Get current database configuration."""
    return get_config_manager().get_config()



