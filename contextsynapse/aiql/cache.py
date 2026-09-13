"""
AIQL Caching System
==================

A comprehensive caching system for AIQL query results with multiple cache strategies:
- Query result caching
- AST caching
- Graph state caching
- Configurable TTL and eviction policies
"""

import hashlib
import json
import time
import threading
from typing import Any, Dict, Optional, Union
from dataclasses import dataclass, asdict
from enum import Enum
import logging

logger = logging.getLogger(__name__)

class CacheStrategy(Enum):
    """Cache eviction strategies."""
    LRU = "lru"  # Least Recently Used
    LFU = "lfu"  # Least Frequently Used
    TTL = "ttl"  # Time To Live
    SIZE = "size"  # Size-based eviction

@dataclass
class CacheEntry:
    """A cache entry with metadata."""
    key: str
    value: Any
    created_at: float
    last_accessed: float
    access_count: int
    ttl: Optional[float] = None
    size_bytes: int = 0
    
    def is_expired(self) -> bool:
        """Check if the cache entry is expired."""
        if self.ttl is None:
            return False
        return time.time() - self.created_at > self.ttl
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return asdict(self)

class QueryCache:
    """Main query cache implementation."""
    
    def __init__(self, 
                 max_size: int = 5000,  # Increased cache size
                 max_memory_mb: int = 500,  # Increased memory limit
                 default_ttl: float = 1800,  # 30 minutes (longer TTL)
                 strategy: CacheStrategy = CacheStrategy.LRU):
        self.max_size = max_size
        self.max_memory_bytes = max_memory_mb * 1024 * 1024
        self.default_ttl = default_ttl
        self.strategy = strategy
        
        self._cache: Dict[str, CacheEntry] = {}
        self._lock = threading.RLock()
        self._memory_usage = 0
        
        logger.info(f"QueryCache initialized: max_size={max_size}, max_memory={max_memory_mb}MB, strategy={strategy.value}")
    
    def _generate_key(self, query: str, graph_name: str = "default", **kwargs) -> str:
        """Generate a cache key from query and parameters."""
        # Create a deterministic key from query and parameters
        key_data = {
            "query": query.strip(),
            "graph": graph_name,
            **kwargs
        }
        key_string = json.dumps(key_data, sort_keys=True)
        return hashlib.sha256(key_string.encode()).hexdigest()[:16]
    
    def _calculate_size(self, value: Any) -> int:
        """Calculate approximate size of a value in bytes."""
        try:
            if isinstance(value, (str, int, float, bool)):
                return len(str(value))
            elif isinstance(value, (list, dict)):
                return len(json.dumps(value))
            else:
                return len(str(value))
        except:
            return 1024  # Default size if calculation fails
    
    def _evict_entries(self) -> None:
        """Evict entries based on the configured strategy."""
        if len(self._cache) < self.max_size and self._memory_usage < self.max_memory_bytes:
            return
        
        current_time = time.time()
        
        # Remove expired entries first
        expired_keys = [k for k, v in self._cache.items() if v.is_expired()]
        for key in expired_keys:
            self._remove_entry(key)
        
        # If still over limits, evict based on strategy
        if len(self._cache) >= self.max_size or self._memory_usage >= self.max_memory_bytes:
            if self.strategy == CacheStrategy.LRU:
                self._evict_lru()
            elif self.strategy == CacheStrategy.LFU:
                self._evict_lfu()
            elif self.strategy == CacheStrategy.TTL:
                self._evict_oldest()
            elif self.strategy == CacheStrategy.SIZE:
                self._evict_largest()
    
    def _evict_lru(self) -> None:
        """Evict least recently used entries."""
        sorted_entries = sorted(self._cache.items(), key=lambda x: x[1].last_accessed)
        # Remove oldest 10% of entries
        to_remove = max(1, len(sorted_entries) // 10)
        for key, _ in sorted_entries[:to_remove]:
            self._remove_entry(key)
    
    def _evict_lfu(self) -> None:
        """Evict least frequently used entries."""
        sorted_entries = sorted(self._cache.items(), key=lambda x: x[1].access_count)
        # Remove least used 10% of entries
        to_remove = max(1, len(sorted_entries) // 10)
        for key, _ in sorted_entries[:to_remove]:
            self._remove_entry(key)
    
    def _evict_oldest(self) -> None:
        """Evict oldest entries."""
        sorted_entries = sorted(self._cache.items(), key=lambda x: x[1].created_at)
        # Remove oldest 10% of entries
        to_remove = max(1, len(sorted_entries) // 10)
        for key, _ in sorted_entries[:to_remove]:
            self._remove_entry(key)
    
    def _evict_largest(self) -> None:
        """Evict largest entries."""
        sorted_entries = sorted(self._cache.items(), key=lambda x: x[1].size_bytes, reverse=True)
        # Remove largest 10% of entries
        to_remove = max(1, len(sorted_entries) // 10)
        for key, _ in sorted_entries[:to_remove]:
            self._remove_entry(key)
    
    def _remove_entry(self, key: str) -> None:
        """Remove an entry from the cache."""
        if key in self._cache:
            entry = self._cache[key]
            self._memory_usage -= entry.size_bytes
            del self._cache[key]
    
    def get(self, query: str, graph_name: str = "default", **kwargs) -> Optional[Any]:
        """Get a cached result."""
        with self._lock:
            key = self._generate_key(query, graph_name, **kwargs)
            
            if key not in self._cache:
                return None
            
            entry = self._cache[key]
            
            # Check if expired
            if entry.is_expired():
                self._remove_entry(key)
                return None
            
            # Update access metadata
            entry.last_accessed = time.time()
            entry.access_count += 1
            
            logger.debug(f"Cache hit for key: {key}")
            return entry.value
    
    def put(self, query: str, result: Any, graph_name: str = "default", ttl: Optional[float] = None, **kwargs) -> None:
        """Store a result in the cache."""
        with self._lock:
            key = self._generate_key(query, graph_name, **kwargs)
            current_time = time.time()
            
            # Calculate size
            size_bytes = self._calculate_size(result)
            
            # Create cache entry
            entry = CacheEntry(
                key=key,
                value=result,
                created_at=current_time,
                last_accessed=current_time,
                access_count=1,
                ttl=ttl or self.default_ttl,
                size_bytes=size_bytes
            )
            
            # Remove existing entry if it exists
            if key in self._cache:
                self._remove_entry(key)
            
            # Add new entry
            self._cache[key] = entry
            self._memory_usage += size_bytes
            
            # Evict if necessary
            self._evict_entries()
            
            logger.debug(f"Cached result for key: {key}, size: {size_bytes} bytes")
    
    def invalidate(self, query: str = None, graph_name: str = None, pattern: str = None) -> int:
        """Invalidate cache entries."""
        with self._lock:
            if query:
                # Invalidate specific query
                key = self._generate_key(query, graph_name or "default")
                if key in self._cache:
                    self._remove_entry(key)
                    return 1
                return 0
            
            elif pattern:
                # Invalidate entries matching query pattern
                removed_count = 0
                keys_to_remove = []
                
                # Convert pattern to regex for better matching
                import re
                pattern_regex = pattern.replace('*', '.*').replace('?', '.')
                
                for key, entry in self._cache.items():
                    # Extract query from cache key
                    query_part = key.split('|')[0] if '|' in key else key
                    
                    # Check if query matches pattern
                    if re.match(pattern_regex, query_part, re.IGNORECASE):
                        keys_to_remove.append(key)
                
                for key in keys_to_remove:
                    self._remove_entry(key)
                    removed_count += 1
                
                logger.info(f"Pattern '{pattern}' matched {removed_count} cache entries")
                return removed_count
            
            elif graph_name:
                # Invalidate all entries for a graph
                removed_count = 0
                keys_to_remove = []
                
                for key, entry in self._cache.items():
                    # This is a simplified check - in practice, you'd need to store graph info
                    if graph_name in str(entry.value):
                        keys_to_remove.append(key)
                
                for key in keys_to_remove:
                    self._remove_entry(key)
                    removed_count += 1
                
                return removed_count
            
            else:
                # Clear all cache
                count = len(self._cache)
                self._cache.clear()
                self._memory_usage = 0
                return count
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            current_time = time.time()
            total_accesses = sum(entry.access_count for entry in self._cache.values())
            avg_access_count = total_accesses / len(self._cache) if self._cache else 0
            
            return {
                "size": len(self._cache),
                "max_size": self.max_size,
                "memory_usage_bytes": self._memory_usage,
                "max_memory_bytes": self.max_memory_bytes,
                "memory_usage_percent": (self._memory_usage / self.max_memory_bytes) * 100,
                "total_accesses": total_accesses,
                "avg_access_count": avg_access_count,
                "strategy": self.strategy.value,
                "default_ttl": self.default_ttl
            }
    
    def clear(self) -> None:
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()
            self._memory_usage = 0
            logger.info("Cache cleared")

# Global cache instance
_global_cache: Optional[QueryCache] = None

def get_cache() -> QueryCache:
    """Get the global cache instance."""
    global _global_cache
    if _global_cache is None:
        _global_cache = QueryCache(max_size=5000, max_memory_mb=500, default_ttl=1800)
    return _global_cache

def configure_cache(max_size: int = 5000, 
                   max_memory_mb: int = 500, 
                   default_ttl: float = 1800,
                   strategy: CacheStrategy = CacheStrategy.LRU) -> None:
    """Configure the global cache."""
    global _global_cache
    _global_cache = QueryCache(
        max_size=max_size,
        max_memory_mb=max_memory_mb,
        default_ttl=default_ttl,
        strategy=strategy
    )
    logger.info(f"Cache configured: {max_size} entries, {max_memory_mb}MB, {default_ttl}s TTL, {strategy.value}")

def cache_query_result(query: str, result: Any, graph_name: str = "default", ttl: Optional[float] = None, **kwargs) -> None:
    """Cache a query result."""
    cache = get_cache()
    cache.put(query, result, graph_name, ttl, **kwargs)

def get_cached_result(query: str, graph_name: str = "default", **kwargs) -> Optional[Any]:
    """Get a cached query result."""
    cache = get_cache()
    return cache.get(query, graph_name, **kwargs)

def invalidate_cache(query: str = None, graph_name: str = None, pattern: str = None) -> int:
    """Invalidate cache entries."""
    cache = get_cache()
    return cache.invalidate(query, graph_name, pattern)

def get_cache_stats() -> Dict[str, Any]:
    """Get cache statistics."""
    cache = get_cache()
    return cache.get_stats()

def clear_cache() -> None:
    """Clear all cache entries."""
    cache = get_cache()
    cache.clear()
