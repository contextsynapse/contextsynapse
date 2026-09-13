"""
Local In-Memory Buffer Manager

Simple in-memory buffer for development and testing.
Not suitable for production (data lost on restart).
"""

import asyncio
import time
from typing import Dict, Any, Optional, List, Callable, Awaitable
from .base import BufferManager, BufferConfig
import logging

logger = logging.getLogger(__name__)


class LocalBufferManager(BufferManager):
    """
    In-memory buffer manager.
    
    Perfect for:
    - Development and testing
    - Single-process applications
    - Low-volume workloads
    
    Not suitable for:
    - Production clusters
    - High-availability requirements
    - Data persistence across restarts
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._records: List[Dict[str, Any]] = []
        self._timestamps: List[float] = []
    
    async def initialize(self) -> None:
        """Initialize local buffer (no-op for in-memory)."""
        async with self._lock:
            self._initialized = True
            logger.info("[EMOJI] LocalBufferManager initialized (in-memory)")
    
    async def add_record(self, data: Dict[str, Any]) -> None:
        """Add record to in-memory buffer with threshold check."""
        async with self._lock:
            self._records.append(data)
            self._timestamps.append(time.time())
            
            current_size = len(self._records)
            max_size = self.config.max_size
            threshold = int(max_size * self.config.flush_threshold) if self.config.threshold_flush_enabled else max_size
            
            # STRATEGY: Check threshold (70% by default) and log if reached
            if self.config.threshold_flush_enabled and current_size >= threshold:
                logger.debug(f"Buffer threshold reached: {current_size}/{max_size} ({current_size/max_size*100:.1f}%) - will flush on next cycle")
                # Note: Don't flush here directly in async context, let auto-flush handle it
                # But the threshold check in add_node will trigger flush
            
            # Auto-flush if buffer is full (100%)
            if current_size >= max_size:
                logger.debug(f"Buffer size ({current_size}) >= max_size ({max_size}), triggering flush")
                # Note: Don't flush here directly, let auto-flush handle it
    
    async def flush_to_db(
        self,
        batch_size: Optional[int] = None,
        writer_fn: Optional[Callable[[List[Dict[str, Any]]], Awaitable[None]]] = None
    ) -> int:
        """Flush records to database."""
        async with self._lock:
            if not self._records:
                return 0
            
            batch_size = batch_size or self.config.batch_size
            flushed_count = 0
            
            # Process in batches
            while self._records:
                batch = self._records[:batch_size]
                batch_timestamps = self._timestamps[:batch_size]
                
                # Remove from buffer
                self._records = self._records[batch_size:]
                self._timestamps = self._timestamps[batch_size:]
                
                # Write batch
                if writer_fn:
                    try:
                        await writer_fn(batch)
                        flushed_count += len(batch)
                        logger.debug(f"Flushed batch of {len(batch)} records")
                    except Exception as e:
                        logger.error(f"Error writing batch: {e}")
                        # Put records back on error
                        self._records = batch + self._records
                        self._timestamps = batch_timestamps + self._timestamps
                        raise
                else:
                    # No writer function, just remove records
                    flushed_count += len(batch)
                    logger.debug(f"Removed {len(batch)} records (no writer function)")
            
            if flushed_count > 0:
                logger.info(f"[EMOJI] Flushed {flushed_count} records from local buffer")
            
            return flushed_count
    
    async def get_pending_count(self) -> int:
        """Get number of pending records."""
        async with self._lock:
            return len(self._records)
    
    async def cleanup(self, older_than_seconds: Optional[float] = None) -> int:
        """Cleanup old records."""
        async with self._lock:
            if older_than_seconds is None:
                # Remove all
                count = len(self._records)
                self._records.clear()
                self._timestamps.clear()
                return count
            
            # Remove records older than threshold
            current_time = time.time()
            kept_records = []
            kept_timestamps = []
            removed_count = 0
            
            for record, timestamp in zip(self._records, self._timestamps):
                age = current_time - timestamp
                if age <= older_than_seconds:
                    kept_records.append(record)
                    kept_timestamps.append(timestamp)
                else:
                    removed_count += 1
            
            self._records = kept_records
            self._timestamps = kept_timestamps
            
            if removed_count > 0:
                logger.info(f"[EMOJI] Cleaned up {removed_count} old records from local buffer")
            
            return removed_count
    
    async def close(self) -> None:
        """Close buffer manager."""
        async with self._lock:
            self._records.clear()
            self._timestamps.clear()
            self._initialized = False
            logger.debug("LocalBufferManager closed")



