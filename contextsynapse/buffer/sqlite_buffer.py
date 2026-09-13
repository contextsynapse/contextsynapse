"""
SQLite Buffer Manager

SQLite-based buffer for single-node deployments with persistence.
Good balance between simplicity and reliability.
"""

import asyncio
import json
import time
import sqlite3
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable, Awaitable
from .base import BufferManager, BufferConfig
import logging

logger = logging.getLogger(__name__)

try:
    import aiosqlite
    AIOSQLITE_AVAILABLE = True
except ImportError:
    AIOSQLITE_AVAILABLE = False
    # Fallback to sync sqlite3 (not ideal but works)
    logger.warning("aiosqlite not available. Using sync sqlite3 (not recommended for async)")


class SQLiteBufferManager(BufferManager):
    """
    SQLite-based buffer manager.
    
    Perfect for:
    - Single-node deployments
    - Need for persistence
    - Simple setup (no external dependencies)
    - ACID guarantees
    
    Not suitable for:
    - High-concurrency writes (SQLite limitations)
    - Distributed setups
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._db_path: Optional[Path] = None
        self._db_connection = None
    
    async def initialize(self) -> None:
        """Initialize SQLite database."""
        async with self._lock:
            # Determine database path
            if self.config.path:
                self._db_path = Path(self.config.path)
            else:
                buffer_dir = Path("contextcore_data") / "buffers"
                if self.config.namespace:
                    buffer_dir = buffer_dir / self.config.namespace
                buffer_dir.mkdir(parents=True, exist_ok=True)
                self._db_path = buffer_dir / "buffer.db"
            
            # Ensure directory exists
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Connect to database
            if AIOSQLITE_AVAILABLE:
                self._db_connection = await aiosqlite.connect(str(self._db_path))
            else:
                # Fallback to sync (not ideal)
                self._db_connection = sqlite3.connect(str(self._db_path))
            
            # Create table
            create_table_sql = """
                CREATE TABLE IF NOT EXISTS buffer_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    data TEXT NOT NULL,
                    created_at REAL DEFAULT (julianday('now'))
                )
            """
            
            if AIOSQLITE_AVAILABLE:
                await self._db_connection.execute(create_table_sql)
                await self._db_connection.commit()
            else:
                self._db_connection.execute(create_table_sql)
                self._db_connection.commit()
            
            # Create index on timestamp for cleanup
            create_index_sql = "CREATE INDEX IF NOT EXISTS idx_timestamp ON buffer_records(timestamp)"
            if AIOSQLITE_AVAILABLE:
                await self._db_connection.execute(create_index_sql)
                await self._db_connection.commit()
            else:
                self._db_connection.execute(create_index_sql)
                self._db_connection.commit()
            
            self._initialized = True
            logger.info(f"[EMOJI] SQLiteBufferManager initialized: {self._db_path}")
    
    async def add_record(self, data: Dict[str, Any]) -> None:
        """Add record to SQLite buffer."""
        if not self._db_connection:
            await self.initialize()
        
        data_json = json.dumps(data, default=str)
        timestamp = time.time()
        
        insert_sql = "INSERT INTO buffer_records (timestamp, data) VALUES (?, ?)"
        
        if AIOSQLITE_AVAILABLE:
            await self._db_connection.execute(insert_sql, (timestamp, data_json))
            await self._db_connection.commit()
        else:
            # Sync fallback
            self._db_connection.execute(insert_sql, (timestamp, data_json))
            self._db_connection.commit()
    
    async def flush_to_db(
        self,
        batch_size: Optional[int] = None,
        writer_fn: Optional[Callable[[List[Dict[str, Any]]], Awaitable[None]]] = None
    ) -> int:
        """Flush records from SQLite to database."""
        if not writer_fn:
            logger.warning("No writer function provided, skipping flush")
            return 0
        
        if not self._db_connection:
            return 0
        
        batch_size = batch_size or self.config.batch_size
        flushed_count = 0
        
        while True:
            # Fetch batch
            select_sql = "SELECT id, data FROM buffer_records ORDER BY id LIMIT ?"
            
            if AIOSQLITE_AVAILABLE:
                cursor = await self._db_connection.execute(select_sql, (batch_size,))
                rows = await cursor.fetchall()
            else:
                cursor = self._db_connection.execute(select_sql, (batch_size,))
                rows = cursor.fetchall()
            
            if not rows:
                break
            
            # Deserialize data
            batch = []
            record_ids = []
            for row_id, data_json in rows:
                try:
                    data = json.loads(data_json)
                    batch.append(data)
                    record_ids.append(row_id)
                except json.JSONDecodeError as e:
                    logger.warning(f"Invalid JSON record {row_id}: {e}")
                    # Delete invalid record
                    delete_sql = "DELETE FROM buffer_records WHERE id = ?"
                    if AIOSQLITE_AVAILABLE:
                        await self._db_connection.execute(delete_sql, (row_id,))
                        await self._db_connection.commit()
                    else:
                        self._db_connection.execute(delete_sql, (row_id,))
                        self._db_connection.commit()
                    continue
            
            if not batch:
                continue
            
            # Write batch
            try:
                await writer_fn(batch)
                flushed_count += len(batch)
                
                # Delete flushed records
                delete_sql = "DELETE FROM buffer_records WHERE id IN ({})".format(
                    ','.join('?' * len(record_ids))
                )
                if AIOSQLITE_AVAILABLE:
                    await self._db_connection.execute(delete_sql, record_ids)
                    await self._db_connection.commit()
                else:
                    self._db_connection.execute(delete_sql, record_ids)
                    self._db_connection.commit()
                
                logger.debug(f"Flushed batch of {len(batch)} records from SQLite")
            except Exception as e:
                logger.error(f"Error writing batch: {e}")
                raise
        
        if flushed_count > 0:
            logger.info(f"[EMOJI] Flushed {flushed_count} records from SQLite buffer")
        
        return flushed_count
    
    async def get_pending_count(self) -> int:
        """Get number of pending records."""
        if not self._db_connection:
            return 0
        
        count_sql = "SELECT COUNT(*) FROM buffer_records"
        
        if AIOSQLITE_AVAILABLE:
            cursor = await self._db_connection.execute(count_sql)
            row = await cursor.fetchone()
            return row[0] if row else 0
        else:
            cursor = self._db_connection.execute(count_sql)
            row = cursor.fetchone()
            return row[0] if row else 0
    
    async def cleanup(self, older_than_seconds: Optional[float] = None) -> int:
        """Cleanup old records."""
        if not self._db_connection:
            return 0
        
        current_time = time.time()
        
        if older_than_seconds is None:
            # Delete all
            delete_sql = "DELETE FROM buffer_records"
            if AIOSQLITE_AVAILABLE:
                cursor = await self._db_connection.execute(delete_sql)
                await self._db_connection.commit()
                removed_count = cursor.rowcount
            else:
                cursor = self._db_connection.execute(delete_sql)
                self._db_connection.commit()
                removed_count = cursor.rowcount
        else:
            # Delete older than threshold
            threshold = current_time - older_than_seconds
            delete_sql = "DELETE FROM buffer_records WHERE timestamp < ?"
            if AIOSQLITE_AVAILABLE:
                cursor = await self._db_connection.execute(delete_sql, (threshold,))
                await self._db_connection.commit()
                removed_count = cursor.rowcount
            else:
                cursor = self._db_connection.execute(delete_sql, (threshold,))
                self._db_connection.commit()
                removed_count = cursor.rowcount
        
        if removed_count > 0:
            logger.info(f"[EMOJI] Cleaned up {removed_count} old records from SQLite buffer")
        
        return removed_count
    
    async def close(self) -> None:
        """Close database connection."""
        async with self._lock:
            if self._db_connection:
                if AIOSQLITE_AVAILABLE:
                    await self._db_connection.close()
                else:
                    self._db_connection.close()
                self._db_connection = None
            self._initialized = False
            logger.debug("SQLiteBufferManager closed")







