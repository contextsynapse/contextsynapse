"""
Buffer Manager System

Provides pluggable buffer managers for different storage backends.
Allows AIContextDB to run everywhere - from laptop to cluster - with no code changes.

Supported backends:
- redis: Redis-based buffer (for clusters)
- local: In-memory buffer (for development)
- sqlite: SQLite-based buffer (for single-node)
- duckdb: DuckDB-based buffer (for analytics)
- lmdb: LMDB-based buffer (for high-performance)
- file: File-based buffer (for simple deployments)
"""

from typing import Optional, Dict, Any, Callable, Awaitable
import logging

logger = logging.getLogger(__name__)

# Import buffer managers
from .base import BufferManager, BufferConfig
from .local_buffer import LocalBufferManager
from .file_buffer import FileBufferManager

# Optional imports (will fail gracefully if not available)
try:
    from .redis_buffer import RedisBufferManager
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.debug("Redis buffer manager not available (redis package not installed)")

try:
    from .sqlite_buffer import SQLiteBufferManager
    SQLITE_AVAILABLE = True
except ImportError:
    SQLITE_AVAILABLE = False
    logger.debug("SQLite buffer manager not available")

try:
    from .duckdb_buffer import DuckDBBufferManager
    DUCKDB_AVAILABLE = True
except ImportError:
    DUCKDB_AVAILABLE = False
    logger.debug("DuckDB buffer manager not available (duckdb package not installed)")

try:
    from .lmdb_buffer import LMDBBufferManager
    LMDB_AVAILABLE = True
except ImportError:
    LMDB_AVAILABLE = False
    logger.debug("LMDB buffer manager not available (lmdb package not installed)")


def create_buffer_manager(
    buffer_type: str = "local",
    config: Optional[Dict[str, Any]] = None
) -> BufferManager:
    """
    Factory function to create buffer managers.
    
    Args:
        buffer_type: Type of buffer manager ("redis", "local", "sqlite", "duckdb", "lmdb", "file")
        config: Configuration dictionary for the buffer manager
        
    Returns:
        BufferManager instance
        
    Raises:
        ValueError: If buffer_type is not supported or required dependencies are missing
        
    Example:
        >>> # Local development
        >>> buffer = create_buffer_manager("local")
        >>> 
        >>> # Production cluster
        >>> buffer = create_buffer_manager("redis", {"host": "redis-cluster", "port": 6379})
        >>> 
        >>> # Single-node production
        >>> buffer = create_buffer_manager("sqlite", {"path": "/data/buffer.db"})
    """
    config = config or {}
    buffer_type = buffer_type.lower()
    
    if buffer_type == "local":
        return LocalBufferManager(config)
    
    elif buffer_type == "file":
        return FileBufferManager(config)
    
    elif buffer_type == "redis":
        if not REDIS_AVAILABLE:
            raise ValueError(
                "Redis buffer manager requires 'redis' package. "
                "Install with: pip install redis"
            )
        return RedisBufferManager(config)
    
    elif buffer_type == "sqlite":
        if not SQLITE_AVAILABLE:
            raise ValueError(
                "SQLite buffer manager requires 'sqlite3' (built-in) or 'aiosqlite'. "
                "Install with: pip install aiosqlite"
            )
        return SQLiteBufferManager(config)
    
    elif buffer_type == "duckdb":
        if not DUCKDB_AVAILABLE:
            raise ValueError(
                "DuckDB buffer manager requires 'duckdb' package. "
                "Install with: pip install duckdb"
            )
        return DuckDBBufferManager(config)
    
    elif buffer_type == "lmdb":
        if not LMDB_AVAILABLE:
            raise ValueError(
                "LMDB buffer manager requires 'lmdb' package. "
                "Install with: pip install lmdb"
            )
        return LMDBBufferManager(config)
    
    else:
        raise ValueError(
            f"Unknown buffer type: {buffer_type}. "
            f"Supported types: local, file, redis, sqlite, duckdb, lmdb"
        )


__all__ = [
    'BufferManager',
    'BufferConfig',
    'create_buffer_manager',
    'LocalBufferManager',
    'FileBufferManager',
]

# Conditionally export optional managers
if REDIS_AVAILABLE:
    __all__.append('RedisBufferManager')
if SQLITE_AVAILABLE:
    __all__.append('SQLiteBufferManager')
if DUCKDB_AVAILABLE:
    __all__.append('DuckDBBufferManager')
if LMDB_AVAILABLE:
    __all__.append('LMDBBufferManager')







