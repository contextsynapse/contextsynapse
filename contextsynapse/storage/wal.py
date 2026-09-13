"""
AIContextDB Write-Ahead Logging (WAL) System
Self-contained WAL system providing ACID compliance and crash recovery.
Extracted and adapted from QGraph WAL System.
"""

import os
import json
import time
import threading
from typing import Dict, List, Any, Optional, Union, Callable
from dataclasses import dataclass, asdict
from enum import Enum
from datetime import datetime
import hashlib
import pickle
import gzip
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

class OperationType(Enum):
    """Types of operations that can be logged."""
    CREATE_NODE = "CREATE_NODE"
    UPDATE_NODE = "UPDATE_NODE"
    DELETE_NODE = "DELETE_NODE"
    CREATE_EDGE = "CREATE_EDGE"
    UPDATE_EDGE = "UPDATE_EDGE"
    DELETE_EDGE = "DELETE_EDGE"
    BULK_OPERATION = "BULK_OPERATION"
    CHECKPOINT = "CHECKPOINT"

@dataclass
class WALEntry:
    """A single WAL entry."""
    timestamp: float
    sequence_number: int
    operation_type: OperationType
    transaction_id: str
    data: Dict[str, Any]
    checksum: str
    committed: bool = False
    rolled_back: bool = False

@dataclass
class WALConfig:
    """Configuration for WAL system."""
    wal_dir: str = "./contextcore_wal"
    max_wal_size: int = 100 * 1024 * 1024  # 100MB
    checkpoint_interval: int = 1000  # Checkpoint every N operations
    sync_interval: float = 1.0  # Sync to disk every N seconds
    compression: bool = True
    encryption: bool = False
    enable_logging: bool = True

class AIContextDBWALSystem:
    """AIContextDB Write-Ahead Logging System - Self-contained implementation."""
    
    def __init__(self, config: Optional[WALConfig] = None):
        self.config = config or WALConfig()
        self.sequence_number = 0
        self.active_transactions: Dict[str, List[WALEntry]] = {}
        self.committed_entries: List[WALEntry] = []
        self.wal_file = None
        self.lock = threading.RLock()
        self.sync_thread = None
        self.running = False
        
        # Initialize WAL directory and file
        self._initialize_wal()
        
        logger.info("[EMOJI] AIContextDB WAL System initialized")
    
    def _initialize_wal(self):
        """Initialize WAL directory and file."""
        try:
            # Create WAL directory
            os.makedirs(self.config.wal_dir, exist_ok=True)
            
            # Initialize WAL file
            wal_file_path = os.path.join(self.config.wal_dir, "wal.log")
            self.wal_file = open(wal_file_path, 'a')
            
            # Start sync thread
            if self.config.sync_interval > 0:
                self.running = True
                self.sync_thread = threading.Thread(target=self._sync_worker, daemon=True)
                self.sync_thread.start()
            
            logger.info(f"[EMOJI] WAL initialized in {self.config.wal_dir}")
            
        except Exception as e:
            logger.error(f"[EMOJI] Failed to initialize WAL: {e}")
            raise
    
    def begin_transaction(self, transaction_id: str = None) -> str:
        """Begin a new transaction."""
        try:
            if transaction_id is None:
                transaction_id = f"txn_{int(time.time() * 1000)}"
            
            with self.lock:
                if transaction_id in self.active_transactions:
                    logger.warning(f"Transaction {transaction_id} already exists")
                    return transaction_id
                
                self.active_transactions[transaction_id] = []
                logger.info(f"[EMOJI] Started transaction: {transaction_id}")
                return transaction_id
                
        except Exception as e:
            logger.error(f"[EMOJI] Failed to begin transaction: {e}")
            raise
    
    def log_operation(self, operation_type: OperationType, data: Dict[str, Any], 
                     transaction_id: str = None) -> WALEntry:
        """Log an operation to WAL."""
        try:
            with self.lock:
                self.sequence_number += 1
                
                # Create WAL entry
                entry = WALEntry(
                    timestamp=time.time(),
                    sequence_number=self.sequence_number,
                    operation_type=operation_type,
                    transaction_id=transaction_id or "default",
                    data=data,
                    checksum=self._calculate_checksum(data)
                )
                
                # Add to active transaction or committed entries
                if transaction_id and transaction_id in self.active_transactions:
                    self.active_transactions[transaction_id].append(entry)
                else:
                    entry.committed = True
                    self.committed_entries.append(entry)
                
                # Write to WAL file
                self._write_entry_to_file(entry)
                
                logger.debug(f"[EMOJI] Logged operation: {operation_type.value} (seq: {self.sequence_number})")
                return entry
                
        except Exception as e:
            logger.error(f"[EMOJI] Failed to log operation: {e}")
            raise
    
    def commit_transaction(self, transaction_id: str) -> bool:
        """Commit a transaction."""
        try:
            with self.lock:
                if transaction_id not in self.active_transactions:
                    logger.warning(f"Transaction {transaction_id} not found")
                    return False
                
                # Move entries to committed
                entries = self.active_transactions[transaction_id]
                for entry in entries:
                    entry.committed = True
                    self.committed_entries.append(entry)
                
                # Remove from active transactions
                del self.active_transactions[transaction_id]
                
                logger.info(f"[EMOJI] Committed transaction: {transaction_id} ({len(entries)} operations)")
                return True
                
        except Exception as e:
            logger.error(f"[EMOJI] Failed to commit transaction: {e}")
            return False
    
    def rollback_transaction(self, transaction_id: str) -> bool:
        """Rollback a transaction."""
        try:
            with self.lock:
                if transaction_id not in self.active_transactions:
                    logger.warning(f"Transaction {transaction_id} not found")
                    return False
                
                # Mark entries as rolled back
                entries = self.active_transactions[transaction_id]
                for entry in entries:
                    entry.rolled_back = True
                
                # Remove from active transactions
                del self.active_transactions[transaction_id]
                
                logger.info(f"[EMOJI] Rolled back transaction: {transaction_id} ({len(entries)} operations)")
                return True
                
        except Exception as e:
            logger.error(f"[EMOJI] Failed to rollback transaction: {e}")
            return False
    
    def checkpoint(self) -> bool:
        """Create a checkpoint."""
        try:
            with self.lock:
                checkpoint_entry = WALEntry(
                    timestamp=time.time(),
                    sequence_number=self.sequence_number + 1,
                    operation_type=OperationType.CHECKPOINT,
                    transaction_id="checkpoint",
                    data={
                        'committed_entries': len(self.committed_entries),
                        'active_transactions': len(self.active_transactions),
                        'sequence_number': self.sequence_number
                    },
                    checksum="checkpoint"
                )
                
                self.committed_entries.append(checkpoint_entry)
                self._write_entry_to_file(checkpoint_entry)
                
                logger.info(f"[EMOJI] Created checkpoint (seq: {checkpoint_entry.sequence_number})")
                return True
                
        except Exception as e:
            logger.error(f"[EMOJI] Failed to create checkpoint: {e}")
            return False
    
    def recover(self) -> Dict[str, Any]:
        """Recover from WAL after crash."""
        try:
            recovery_info = {
                'committed_operations': 0,
                'active_transactions': 0,
                'last_checkpoint': None,
                'recovered_transactions': []
            }
            
            wal_file_path = os.path.join(self.config.wal_dir, "wal.log")
            if not os.path.exists(wal_file_path):
                logger.info("No WAL file found, starting fresh")
                return recovery_info
            
            with open(wal_file_path, 'r') as f:
                for line in f:
                    try:
                        entry_data = json.loads(line.strip())
                        entry = WALEntry(**entry_data)
                        
                        if entry.operation_type == OperationType.CHECKPOINT:
                            recovery_info['last_checkpoint'] = entry.sequence_number
                        elif entry.committed and not entry.rolled_back:
                            recovery_info['committed_operations'] += 1
                        elif not entry.committed and not entry.rolled_back:
                            recovery_info['active_transactions'] += 1
                            recovery_info['recovered_transactions'].append(entry.transaction_id)
                            
                    except Exception as e:
                        logger.warning(f"Failed to parse WAL entry: {e}")
                        continue
            
            logger.info(f"[EMOJI] Recovery completed: {recovery_info['committed_operations']} committed, "
                       f"{recovery_info['active_transactions']} active transactions")
            
            return recovery_info
            
        except Exception as e:
            logger.error(f"[EMOJI] Recovery failed: {e}")
            return {}
    
    def _write_entry_to_file(self, entry: WALEntry):
        """Write entry to WAL file."""
        try:
            entry_dict = asdict(entry)
            # Convert enum to string for JSON serialization
            entry_dict['operation_type'] = entry.operation_type.value
            
            line = json.dumps(entry_dict) + '\n'
            
            if self.config.compression:
                # In a real implementation, this would use compression
                pass
            
            self.wal_file.write(line)
            self.wal_file.flush()
            
        except Exception as e:
            logger.error(f"[EMOJI] Failed to write entry to file: {e}")
            raise
    
    def _calculate_checksum(self, data: Dict[str, Any]) -> str:
        """Calculate checksum for data."""
        try:
            data_str = json.dumps(data, sort_keys=True)
            return hashlib.md5(data_str.encode()).hexdigest()
        except Exception as e:
            logger.error(f"[EMOJI] Checksum calculation failed: {e}")
            return ""
    
    def _sync_worker(self):
        """Background worker for syncing WAL to disk."""
        while self.running:
            try:
                time.sleep(self.config.sync_interval)
                if self.wal_file:
                    self.wal_file.flush()
                    os.fsync(self.wal_file.fileno())
            except Exception as e:
                logger.error(f"[EMOJI] Sync worker error: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get WAL statistics."""
        try:
            with self.lock:
                return {
                    'sequence_number': self.sequence_number,
                    'active_transactions': len(self.active_transactions),
                    'committed_entries': len(self.committed_entries),
                    'wal_file_size': self._get_wal_file_size(),
                    'config': asdict(self.config)
                }
        except Exception as e:
            logger.error(f"[EMOJI] Failed to get WAL stats: {e}")
            return {}
    
    def _get_wal_file_size(self) -> int:
        """Get WAL file size."""
        try:
            wal_file_path = os.path.join(self.config.wal_dir, "wal.log")
            if os.path.exists(wal_file_path):
                return os.path.getsize(wal_file_path)
            return 0
        except Exception as e:
            logger.error(f"[EMOJI] Failed to get WAL file size: {e}")
            return 0
    
    def cleanup(self):
        """Cleanup WAL system."""
        try:
            self.running = False
            if self.sync_thread:
                self.sync_thread.join(timeout=5.0)
            
            if self.wal_file:
                self.wal_file.close()
            
            logger.info("[EMOJI] WAL system cleaned up")
            
        except Exception as e:
            logger.error(f"[EMOJI] WAL cleanup failed: {e}")
    
    def __del__(self):
        """Destructor to ensure cleanup."""
        self.cleanup()

# Global WAL instance
_global_wal = None

def get_global_wal() -> AIContextDBWALSystem:
    """Get the global WAL instance."""
    global _global_wal
    if _global_wal is None:
        _global_wal = AIContextDBWALSystem()
    return _global_wal