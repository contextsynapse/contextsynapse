"""
File-Based Buffer Manager

Simple file-based buffer for single-node deployments.
Uses JSON Lines format for easy debugging.
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable, Awaitable
from .base import BufferManager, BufferConfig
import logging

logger = logging.getLogger(__name__)


class FileBufferManager(BufferManager):
    """
    File-based buffer manager.
    
    Stores records in a JSON Lines file (one JSON object per line).
    Perfect for:
    - Single-node deployments
    - Simple persistence
    - Easy debugging (human-readable format)
    
    Not suitable for:
    - High-concurrency (file locking overhead)
    - Very high throughput
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._file_path: Optional[Path] = None
        self._file_handle = None
    
    async def initialize(self) -> None:
        """Initialize file buffer."""
        async with self._lock:
            # Determine file path
            if self.config.path:
                self._file_path = Path(self.config.path)
            else:
                # Default path
                buffer_dir = Path("contextcore_data") / "buffers"
                if self.config.namespace:
                    buffer_dir = buffer_dir / self.config.namespace
                buffer_dir.mkdir(parents=True, exist_ok=True)
                self._file_path = buffer_dir / "buffer.jsonl"
            
            # Ensure directory exists
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Open file in append mode
            self._file_handle = open(self._file_path, 'a', encoding='utf-8')
            
            self._initialized = True
            logger.info(f"[EMOJI] FileBufferManager initialized: {self._file_path}")
    
    async def add_record(self, data: Dict[str, Any]) -> None:
        """Add record to file buffer."""
        async with self._lock:
            if not self._file_handle:
                await self.initialize()
            
            # Add timestamp
            record = {
                'timestamp': time.time(),
                'data': data
            }
            
            # Write as JSON line
            json_line = json.dumps(record, default=str) + '\n'
            self._file_handle.write(json_line)
            self._file_handle.flush()  # Ensure written to disk
    
    async def flush_to_db(
        self,
        batch_size: Optional[int] = None,
        writer_fn: Optional[Callable[[List[Dict[str, Any]]], Awaitable[None]]] = None
    ) -> int:
        """Flush records from file to database."""
        if not writer_fn:
            logger.warning("No writer function provided, skipping flush")
            return 0
        
        async with self._lock:
            if not self._file_path or not self._file_path.exists():
                return 0
            
            # Close current file handle
            if self._file_handle:
                self._file_handle.close()
                self._file_handle = None
            
            # Read all records
            records = []
            with open(self._file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        try:
                            record = json.loads(line)
                            records.append(record['data'])
                        except json.JSONDecodeError as e:
                            logger.warning(f"Invalid JSON line: {e}")
            
            if not records:
                # Reopen file for appending
                self._file_handle = open(self._file_path, 'a', encoding='utf-8')
                return 0
            
            # Process in batches
            batch_size = batch_size or self.config.batch_size
            flushed_count = 0
            failed_records = []
            
            for i in range(0, len(records), batch_size):
                batch = records[i:i + batch_size]
                try:
                    await writer_fn(batch)
                    flushed_count += len(batch)
                    logger.debug(f"Flushed batch of {len(batch)} records")
                except Exception as e:
                    logger.error(f"Error writing batch: {e}")
                    failed_records.extend(batch)
            
            # Write failed records back to file
            if failed_records:
                with open(self._file_path, 'w', encoding='utf-8') as f:
                    for record in failed_records:
                        json_line = json.dumps({'timestamp': time.time(), 'data': record}, default=str) + '\n'
                        f.write(json_line)
            else:
                # Clear file if all records flushed successfully
                self._file_path.write_text('')
            
            # Reopen file for appending
            self._file_handle = open(self._file_path, 'a', encoding='utf-8')
            
            if flushed_count > 0:
                logger.info(f"[EMOJI] Flushed {flushed_count} records from file buffer")
            
            return flushed_count
    
    async def get_pending_count(self) -> int:
        """Get number of pending records in file."""
        async with self._lock:
            if not self._file_path or not self._file_path.exists():
                return 0
            
            # Count lines in file
            try:
                with open(self._file_path, 'r', encoding='utf-8') as f:
                    return sum(1 for line in f if line.strip())
            except Exception as e:
                logger.error(f"Error counting records: {e}")
                return 0
    
    async def cleanup(self, older_than_seconds: Optional[float] = None) -> int:
        """Cleanup old records from file."""
        async with self._lock:
            if not self._file_path or not self._file_path.exists():
                return 0
            
            # Close current handle
            if self._file_handle:
                self._file_handle.close()
                self._file_handle = None
            
            # Read all records
            records = []
            with open(self._file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        try:
                            record = json.loads(line)
                            records.append(record)
                        except json.JSONDecodeError:
                            continue
            
            if not records:
                self._file_handle = open(self._file_path, 'a', encoding='utf-8')
                return 0
            
            # Filter records
            current_time = time.time()
            kept_records = []
            removed_count = 0
            
            for record in records:
                timestamp = record.get('timestamp', 0)
                age = current_time - timestamp
                
                if older_than_seconds is None or age <= older_than_seconds:
                    kept_records.append(record)
                else:
                    removed_count += 1
            
            # Write kept records back
            with open(self._file_path, 'w', encoding='utf-8') as f:
                for record in kept_records:
                    json_line = json.dumps(record, default=str) + '\n'
                    f.write(json_line)
            
            # Reopen for appending
            self._file_handle = open(self._file_path, 'a', encoding='utf-8')
            
            if removed_count > 0:
                logger.info(f"[EMOJI] Cleaned up {removed_count} old records from file buffer")
            
            return removed_count
    
    async def close(self) -> None:
        """Close buffer manager."""
        async with self._lock:
            if self._file_handle:
                self._file_handle.close()
                self._file_handle = None
            self._initialized = False
            logger.debug("FileBufferManager closed")







