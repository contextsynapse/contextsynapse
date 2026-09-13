"""
DuckDB Buffer Manager

DuckDB-based buffer for analytics workloads.
Perfect for analytical queries and aggregations.
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
    import duckdb
    DUCKDB_AVAILABLE = True
except ImportError:
    DUCKDB_AVAILABLE = False
    logger.warning("duckdb package not available. Install with: pip install duckdb")


class DuckDBBufferManager(BufferManager):
    """
    DuckDB-based buffer manager.
    
    Perfect for:
    - Analytics workloads
    - Complex aggregations
    - Columnar operations
    - Fast analytical queries
    
    Requires:
    - DuckDB Python package
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._db_path: Optional[Path] = None
        self._connection = None
    
    async def initialize(self) -> None:
        """Initialize DuckDB connection."""
        if not DUCKDB_AVAILABLE:
            raise RuntimeError("DuckDB package not available. Install with: pip install duckdb")
        
        async with self._lock:
            # Determine database path
            if self.config.path:
                self._db_path = Path(self.config.path)
            else:
                buffer_dir = Path("contextcore_data") / "buffers"
                if self.config.namespace:
                    buffer_dir = buffer_dir / self.config.namespace
                buffer_dir.mkdir(parents=True, exist_ok=True)
                self._db_path = buffer_dir / "buffer.duckdb"
            
            # Ensure directory exists
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Connect to DuckDB (sync API, run in executor)
            loop = asyncio.get_event_loop()
            self._connection = await loop.run_in_executor(
                None,
                lambda: duckdb.connect(str(self._db_path))
            )
            
            # Create table
            create_table_sql = """
                CREATE TABLE IF NOT EXISTS buffer_records (
                    id BIGINT PRIMARY KEY,
                    timestamp DOUBLE NOT NULL,
                    data VARCHAR NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """
            
            await loop.run_in_executor(
                None,
                lambda: self._connection.execute(create_table_sql)
            )
            
            # Create index
            create_index_sql = "CREATE INDEX IF NOT EXISTS idx_timestamp ON buffer_records(timestamp)"
            await loop.run_in_executor(
                None,
                lambda: self._connection.execute(create_index_sql)
            )
            
            self._initialized = True
            logger.info(f"[EMOJI] DuckDBBufferManager initialized: {self._db_path}")
    
    async def add_record(self, data: Dict[str, Any]) -> None:
        """Add record to DuckDB buffer."""
        if not self._connection:
            await self.initialize()
        
        data_json = json.dumps(data, default=str)
        timestamp = time.time()
        
        # Get next ID
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self._connection.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM buffer_records").fetchone()
        )
        record_id = result[0] if result else 1
        
        # Insert record
        insert_sql = "INSERT INTO buffer_records (id, timestamp, data) VALUES (?, ?, ?)"
        await loop.run_in_executor(
            None,
            lambda: self._connection.execute(insert_sql, (record_id, timestamp, data_json))
        )
    
    async def flush_to_db(
        self,
        batch_size: Optional[int] = None,
        writer_fn: Optional[Callable[[List[Dict[str, Any]]], Awaitable[None]]] = None
    ) -> int:
        """Flush records from DuckDB to database."""
        if not writer_fn:
            logger.warning("No writer function provided, skipping flush")
            return 0
        
        if not self._connection:
            return 0
        
        batch_size = batch_size or self.config.batch_size
        flushed_count = 0
        loop = asyncio.get_event_loop()
        
        while True:
            # Fetch batch
            select_sql = f"SELECT id, data FROM buffer_records ORDER BY id LIMIT {batch_size}"
            rows = await loop.run_in_executor(
                None,
                lambda: self._connection.execute(select_sql).fetchall()
            )
            
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
                    continue
            
            if not batch:
                break
            
            # Write batch
            try:
                await writer_fn(batch)
                flushed_count += len(batch)
                
                # Delete flushed records
                ids_str = ','.join(str(id) for id in record_ids)
                delete_sql = f"DELETE FROM buffer_records WHERE id IN ({ids_str})"
                await loop.run_in_executor(
                    None,
                    lambda: self._connection.execute(delete_sql)
                )
                
                logger.debug(f"Flushed batch of {len(batch)} records from DuckDB")
            except Exception as e:
                logger.error(f"Error writing batch: {e}")
                raise
        
        if flushed_count > 0:
            logger.info(f"[EMOJI] Flushed {flushed_count} records from DuckDB buffer")
        
        return flushed_count
    
    async def get_pending_count(self) -> int:
        """Get number of pending records."""
        if not self._connection:
            return 0
        
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self._connection.execute("SELECT COUNT(*) FROM buffer_records").fetchone()
        )
        return result[0] if result else 0
    
    async def cleanup(self, older_than_seconds: Optional[float] = None) -> int:
        """Cleanup old records."""
        if not self._connection:
            return 0
        
        current_time = time.time()
        loop = asyncio.get_event_loop()
        
        if older_than_seconds is None:
            # Delete all
            delete_sql = "DELETE FROM buffer_records"
            result = await loop.run_in_executor(
                None,
                lambda: self._connection.execute(delete_sql)
            )
            removed_count = result.rowcount if hasattr(result, 'rowcount') else 0
        else:
            # Delete older than threshold
            threshold = current_time - older_than_seconds
            delete_sql = "DELETE FROM buffer_records WHERE timestamp < ?"
            result = await loop.run_in_executor(
                None,
                lambda: self._connection.execute(delete_sql, (threshold,))
            )
            removed_count = result.rowcount if hasattr(result, 'rowcount') else 0
        
        if removed_count > 0:
            logger.info(f"[EMOJI] Cleaned up {removed_count} old records from DuckDB buffer")
        
        return removed_count
    
    async def close(self) -> None:
        """Close DuckDB connection."""
        async with self._lock:
            if self._connection:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda: self._connection.close()
                )
                self._connection = None
            self._initialized = False
            logger.debug("DuckDBBufferManager closed")







