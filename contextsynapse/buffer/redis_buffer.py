"""
Redis Buffer Manager

Redis-based buffer for distributed/clustered deployments.
Perfect for high-availability and multi-node setups.
"""

import asyncio
import json
import time
from typing import Dict, Any, Optional, List, Callable, Awaitable
from .base import BufferManager, BufferConfig
import logging

logger = logging.getLogger(__name__)

try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("redis package not available. Install with: pip install redis")


class RedisBufferManager(BufferManager):
    """
    Redis-based buffer manager.
    
    Perfect for:
    - Distributed/clustered deployments
    - High-availability requirements
    - Multi-node setups
    - Shared buffers across processes
    
    Requires:
    - Redis server running
    - redis Python package
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._redis_client: Optional[redis.Redis] = None
        self._key_prefix = "contextcore:buffer"
        if self.config.namespace:
            self._key_prefix = f"{self._key_prefix}:{self.config.namespace}"
    
    async def initialize(self) -> None:
        """Initialize Redis connection."""
        if not REDIS_AVAILABLE:
            raise RuntimeError("Redis package not available. Install with: pip install redis")
        
        async with self._lock:
            # Get Redis config
            host = self.config.backend_config.get('host', 'localhost')
            port = self.config.backend_config.get('port', 6379)
            db = self.config.backend_config.get('db', 0)
            password = self.config.backend_config.get('password')
            decode_responses = self.config.backend_config.get('decode_responses', True)
            
            # Create Redis client
            self._redis_client = redis.Redis(
                host=host,
                port=port,
                db=db,
                password=password,
                decode_responses=decode_responses
            )
            
            # Test connection
            await self._redis_client.ping()
            
            self._initialized = True
            logger.info(f"[EMOJI] RedisBufferManager initialized: {host}:{port}/{db}")
    
    async def add_record(self, data: Dict[str, Any]) -> None:
        """Add record to Redis list."""
        if not self._redis_client:
            await self.initialize()
        
        # Serialize record
        record = {
            'timestamp': time.time(),
            'data': data
        }
        record_json = json.dumps(record, default=str)
        
        # Add to Redis list
        key = f"{self._key_prefix}:records"
        await self._redis_client.lpush(key, record_json)
    
    async def flush_to_db(
        self,
        batch_size: Optional[int] = None,
        writer_fn: Optional[Callable[[List[Dict[str, Any]]], Awaitable[None]]] = None
    ) -> int:
        """Flush records from Redis to database."""
        if not writer_fn:
            logger.warning("No writer function provided, skipping flush")
            return 0
        
        if not self._redis_client:
            return 0
        
        batch_size = batch_size or self.config.batch_size
        flushed_count = 0
        key = f"{self._key_prefix}:records"
        
        while True:
            # Get batch of records (right-pop for FIFO)
            records_json = []
            for _ in range(batch_size):
                record_json = await self._redis_client.rpop(key)
                if not record_json:
                    break
                records_json.append(record_json)
            
            if not records_json:
                break
            
            # Deserialize and extract data
            batch = []
            for record_json in records_json:
                try:
                    record = json.loads(record_json)
                    batch.append(record['data'])
                except json.JSONDecodeError as e:
                    logger.warning(f"Invalid JSON record: {e}")
                    continue
            
            if not batch:
                continue
            
            # Write batch
            try:
                await writer_fn(batch)
                flushed_count += len(batch)
                logger.debug(f"Flushed batch of {len(batch)} records from Redis")
            except Exception as e:
                logger.error(f"Error writing batch: {e}")
                # Put records back (left-push to maintain order)
                for record_json in reversed(records_json):
                    await self._redis_client.rpush(key, record_json)
                raise
        
        if flushed_count > 0:
            logger.info(f"[EMOJI] Flushed {flushed_count} records from Redis buffer")
        
        return flushed_count
    
    async def get_pending_count(self) -> int:
        """Get number of pending records in Redis."""
        if not self._redis_client:
            return 0
        
        key = f"{self._key_prefix}:records"
        return await self._redis_client.llen(key)
    
    async def cleanup(self, older_than_seconds: Optional[float] = None) -> int:
        """Cleanup old records from Redis."""
        if not self._redis_client:
            return 0
        
        key = f"{self._key_prefix}:records"
        current_time = time.time()
        removed_count = 0
        
        # Get all records
        records_json = await self._redis_client.lrange(key, 0, -1)
        
        kept_records = []
        for record_json in records_json:
            try:
                record = json.loads(record_json)
                timestamp = record.get('timestamp', 0)
                age = current_time - timestamp
                
                if older_than_seconds is None or age <= older_than_seconds:
                    kept_records.append(record_json)
                else:
                    removed_count += 1
            except json.JSONDecodeError:
                removed_count += 1  # Remove invalid records
        
        # Replace list with kept records
        if kept_records != records_json:
            # Delete old list
            await self._redis_client.delete(key)
            # Add kept records back
            if kept_records:
                await self._redis_client.lpush(key, *kept_records)
        
        if removed_count > 0:
            logger.info(f"[EMOJI] Cleaned up {removed_count} old records from Redis buffer")
        
        return removed_count
    
    async def close(self) -> None:
        """Close Redis connection."""
        async with self._lock:
            if self._redis_client:
                await self._redis_client.close()
                self._redis_client = None
            self._initialized = False
            logger.debug("RedisBufferManager closed")







