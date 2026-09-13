"""
LMDB Buffer Manager

LMDB (Lightning Memory-Mapped Database) buffer for high-performance scenarios.
Perfect for high-throughput, low-latency requirements.
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable, Awaitable
from .base import BufferManager, BufferConfig
import logging

logger = logging.getLogger(__name__)

try:
    import lmdb
    LMDB_AVAILABLE = True
except ImportError:
    LMDB_AVAILABLE = False
    logger.warning("lmdb package not available. Install with: pip install lmdb")


class LMDBBufferManager(BufferManager):
    """
    LMDB-based buffer manager.
    
    Perfect for:
    - High-throughput scenarios
    - Low-latency requirements
    - Memory-mapped performance
    - Single-writer, multiple-reader scenarios
    
    Requires:
    - LMDB Python package
    - LMDB C library
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._db_path: Optional[Path] = None
        self._env = None
        self._db = None
        self._next_id = 0
    
    async def initialize(self) -> None:
        """Initialize LMDB environment."""
        if not LMDB_AVAILABLE:
            raise RuntimeError("LMDB package not available. Install with: pip install lmdb")
        
        async with self._lock:
            # Determine database path
            if self.config.path:
                self._db_path = Path(self.config.path)
            else:
                buffer_dir = Path("contextcore_data") / "buffers"
                if self.config.namespace:
                    buffer_dir = buffer_dir / self.config.namespace
                buffer_dir.mkdir(parents=True, exist_ok=True)
                self._db_path = buffer_dir / "lmdb"
            
            # Ensure directory exists
            self._db_path.mkdir(parents=True, exist_ok=True)
            
            # Get LMDB config
            map_size = self.config.backend_config.get('map_size', 1024 * 1024 * 1024)  # 1GB default
            
            # Open LMDB environment (sync API, run in executor)
            loop = asyncio.get_event_loop()
            self._env = await loop.run_in_executor(
                None,
                lambda: lmdb.open(
                    str(self._db_path),
                    map_size=map_size,
                    max_dbs=1
                )
            )
            
            # Open database
            self._db = await loop.run_in_executor(
                None,
                lambda: self._env.open_db(b'buffer_records')
            )
            
            # Get next ID
            with self._env.begin(db=self._db, write=False) as txn:
                cursor = txn.cursor()
                if cursor.last():
                    last_key = cursor.key()
                    self._next_id = int.from_bytes(last_key, 'big') + 1
                else:
                    self._next_id = 1
            
            self._initialized = True
            logger.info(f"[EMOJI] LMDBBufferManager initialized: {self._db_path}")
    
    async def add_record(self, data: Dict[str, Any]) -> None:
        """Add record to LMDB buffer."""
        if not self._env:
            await self.initialize()
        
        # Serialize record
        record = {
            'timestamp': time.time(),
            'data': data
        }
        record_json = json.dumps(record, default=str).encode('utf-8')
        
        # Get ID and increment
        record_id = self._next_id
        self._next_id += 1
        
        # Write to LMDB (sync API, run in executor)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self._write_record(record_id, record_json)
        )
    
    def _write_record(self, record_id: int, record_json: bytes):
        """Sync write to LMDB."""
        key = record_id.to_bytes(8, 'big')
        with self._env.begin(db=self._db, write=True) as txn:
            txn.put(key, record_json)
    
    async def flush_to_db(
        self,
        batch_size: Optional[int] = None,
        writer_fn: Optional[Callable[[List[Dict[str, Any]]], Awaitable[None]]] = None
    ) -> int:
        """Flush records from LMDB to database."""
        if not writer_fn:
            logger.warning("No writer function provided, skipping flush")
            return 0
        
        if not self._env:
            return 0
        
        batch_size = batch_size or self.config.batch_size
        flushed_count = 0
        loop = asyncio.get_event_loop()
        
        while True:
            # Read batch (sync API, run in executor)
            batch_data = await loop.run_in_executor(
                None,
                lambda: self._read_batch(batch_size)
            )
            
            if not batch_data:
                break
            
            batch, keys = batch_data
            
            # Write batch
            try:
                await writer_fn(batch)
                flushed_count += len(batch)
                
                # Delete flushed records (sync API, run in executor)
                await loop.run_in_executor(
                    None,
                    lambda: self._delete_records(keys)
                )
                
                logger.debug(f"Flushed batch of {len(batch)} records from LMDB")
            except Exception as e:
                logger.error(f"Error writing batch: {e}")
                raise
        
        if flushed_count > 0:
            logger.info(f"[EMOJI] Flushed {flushed_count} records from LMDB buffer")
        
        return flushed_count
    
    def _read_batch(self, batch_size: int):
        """Sync read batch from LMDB."""
        batch = []
        keys = []
        
        with self._env.begin(db=self._db, write=False) as txn:
            cursor = txn.cursor()
            for key, value in cursor:
                if len(batch) >= batch_size:
                    break
                
                try:
                    record = json.loads(value.decode('utf-8'))
                    batch.append(record['data'])
                    keys.append(key)
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning(f"Invalid record: {e}")
                    keys.append(key)  # Mark for deletion
                    continue
        
        return (batch, keys) if batch else None
    
    def _delete_records(self, keys: List[bytes]):
        """Sync delete records from LMDB."""
        with self._env.begin(db=self._db, write=True) as txn:
            for key in keys:
                txn.delete(key)
    
    async def get_pending_count(self) -> int:
        """Get number of pending records."""
        if not self._env:
            return 0
        
        loop = asyncio.get_event_loop()
        count = await loop.run_in_executor(
            None,
            lambda: self._count_records()
        )
        return count
    
    def _count_records(self) -> int:
        """Sync count records in LMDB."""
        with self._env.begin(db=self._db, write=False) as txn:
            return txn.stat()['entries']
    
    async def cleanup(self, older_than_seconds: Optional[float] = None) -> int:
        """Cleanup old records."""
        if not self._env:
            return 0
        
        current_time = time.time()
        loop = asyncio.get_event_loop()
        removed_count = await loop.run_in_executor(
            None,
            lambda: self._cleanup_records(current_time, older_than_seconds)
        )
        
        if removed_count > 0:
            logger.info(f"[EMOJI] Cleaned up {removed_count} old records from LMDB buffer")
        
        return removed_count
    
    def _cleanup_records(self, current_time: float, older_than_seconds: Optional[float]) -> int:
        """Sync cleanup records."""
        removed_count = 0
        keys_to_delete = []
        
        with self._env.begin(db=self._db, write=False) as txn:
            cursor = txn.cursor()
            for key, value in cursor:
                try:
                    record = json.loads(value.decode('utf-8'))
                    timestamp = record.get('timestamp', 0)
                    age = current_time - timestamp
                    
                    if older_than_seconds is None or age > older_than_seconds:
                        keys_to_delete.append(key)
                except (json.JSONDecodeError, KeyError):
                    keys_to_delete.append(key)  # Delete invalid records
        
        # Delete records
        if keys_to_delete:
            with self._env.begin(db=self._db, write=True) as txn:
                for key in keys_to_delete:
                    txn.delete(key)
                    removed_count += 1
        
        return removed_count
    
    async def close(self) -> None:
        """Close LMDB environment."""
        async with self._lock:
            if self._env:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda: self._env.close()
                )
                self._env = None
                self._db = None
            self._initialized = False
            logger.debug("LMDBBufferManager closed")







