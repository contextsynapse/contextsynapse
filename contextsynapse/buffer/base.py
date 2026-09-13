"""
Base Buffer Manager Interface

Defines the contract that all buffer managers must implement.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Callable, Awaitable, List
from dataclasses import dataclass, field
import asyncio
import logging
import atexit
import weakref
import signal
import sys

logger = logging.getLogger(__name__)

# Global registry for all buffer managers to ensure cleanup on exit
_buffer_managers: weakref.WeakSet = weakref.WeakSet()
_cleanup_registered = False

def _cleanup_all_buffers():
    """Cleanup all buffer managers on program exit."""
    logger.debug("Cleaning up all buffer managers...")
    for buffer_mgr in list(_buffer_managers):
        try:
            if hasattr(buffer_mgr, '_flush_task') and buffer_mgr._flush_task and not buffer_mgr._flush_task.done():
                buffer_mgr._flush_task.cancel()
                # Try to await cancellation if event loop is running
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # Schedule cleanup task
                        asyncio.create_task(_await_task_cancellation(buffer_mgr._flush_task))
                    else:
                        # Run cleanup synchronously
                        loop.run_until_complete(_await_task_cancellation(buffer_mgr._flush_task))
                except (RuntimeError, AttributeError):
                    # No event loop or can't access it - task will be cleaned up by Python
                    pass
        except Exception as e:
            logger.debug(f"Error cleaning up buffer manager: {e}")

async def _await_task_cancellation(task: asyncio.Task):
    """Await task cancellation gracefully."""
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.debug(f"Error awaiting task cancellation: {e}")

def _signal_handler(signum, frame):
    """Handle signals (SIGINT, SIGTERM) for graceful shutdown."""
    logger.info(f"Received signal {signum}, cleaning up buffer managers...")
    _cleanup_all_buffers()
    sys.exit(0)

def _register_cleanup_handlers():
    """Register cleanup handlers (atexit and signals)."""
    global _cleanup_registered
    if _cleanup_registered:
        return
    _cleanup_registered = True
    
    # Register atexit handler
    atexit.register(_cleanup_all_buffers)
    
    # Register signal handlers for graceful shutdown
    try:
        signal.signal(signal.SIGINT, _signal_handler)  # Ctrl+C
    except (ValueError, OSError):
        pass  # Not available on all platforms
    
    try:
        signal.signal(signal.SIGTERM, _signal_handler)  # Termination signal
    except (ValueError, OSError):
        pass  # Not available on all platforms

# Register cleanup handlers on module import
_register_cleanup_handlers()


@dataclass
class BufferConfig:
    """Configuration for buffer managers."""
    max_size: int = 1000  # Maximum records before auto-flush
    flush_interval: float = 5.0  # Seconds between auto-flushes
    batch_size: int = 100  # Records per batch when flushing
    auto_flush: bool = True  # Enable automatic flushing
    flush_threshold: float = 0.7  # Flush when buffer reaches 70% capacity (0.0-1.0)
    threshold_flush_enabled: bool = True  # Enable threshold-based flushing
    path: Optional[str] = None  # Storage path (for file-based buffers)
    namespace: Optional[str] = None  # Namespace for multi-tenant buffers
    # Backend-specific config
    backend_config: Dict[str, Any] = field(default_factory=dict)


class BufferManager(ABC):
    """
    Base class for all buffer managers.
    
    Buffer managers provide:
    - Temporary storage for records before writing to database
    - Batching for efficient writes
    - Automatic flushing based on size/time
    - Cleanup of old records
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize buffer manager.
        
        Args:
            config: Configuration dictionary
        """
        if config is None:
            config = {}
        
        # Register this instance for cleanup on exit
        _buffer_managers.add(self)
        
        self.config = BufferConfig(**config)
        self._records: List[Dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None
        self._initialized = False
    
    @abstractmethod
    async def initialize(self) -> None:
        """
        Initialize the buffer manager (connect to backend, create tables, etc.).
        Must be called before use.
        """
        pass
    
    @abstractmethod
    async def add_record(self, data: Dict[str, Any]) -> None:
        """
        Add a record to the buffer.
        
        Args:
            data: Record data dictionary
        """
        pass
    
    @abstractmethod
    async def flush_to_db(
        self,
        batch_size: Optional[int] = None,
        writer_fn: Optional[Callable[[List[Dict[str, Any]]], Awaitable[None]]] = None
    ) -> int:
        """
        Flush buffered records to database.
        
        Args:
            batch_size: Number of records per batch (defaults to config.batch_size)
            writer_fn: Async function to write batches: async def writer(batch: List[Dict]) -> None
            
        Returns:
            Number of records flushed
        """
        pass
    
    @abstractmethod
    async def get_pending_count(self) -> int:
        """
        Get number of pending records in buffer.
        
        Returns:
            Number of pending records
        """
        pass
    
    @abstractmethod
    async def cleanup(self, older_than_seconds: Optional[float] = None) -> int:
        """
        Cleanup old records from buffer.
        
        Args:
            older_than_seconds: Remove records older than this (None = remove all)
            
        Returns:
            Number of records cleaned up
        """
        pass
    
    @abstractmethod
    async def close(self) -> None:
        """
        Close the buffer manager and cleanup resources.
        """
        # Stop auto-flush tasks before closing
        await self.stop_auto_flush()
    
    async def start_auto_flush(
        self,
        writer_fn: Optional[Callable[[List[Dict[str, Any]]], Awaitable[None]]] = None
    ) -> None:
        """
        Start automatic flushing based on config.
        
        Args:
            writer_fn: Async function to write batches
        """
        if not self.config.auto_flush:
            return
        
        if self._flush_task and not self._flush_task.done():
            return  # Already running
        
        async def _auto_flush_loop():
            while True:
                try:
                    await asyncio.sleep(self.config.flush_interval)
                    pending = await self.get_pending_count()
                    threshold = int(self.config.max_size * self.config.flush_threshold)
                    
                    # STRATEGY: Flush if buffer exceeds threshold (70% by default) OR max_size
                    if self.config.threshold_flush_enabled and pending >= threshold:
                        logger.debug(f"Buffer threshold reached: {pending}/{self.config.max_size} ({pending/self.config.max_size*100:.1f}%) - flushing")
                        await self.flush_to_db(writer_fn=writer_fn)
                    elif pending >= self.config.max_size:
                        logger.debug(f"Buffer max_size reached: {pending}/{self.config.max_size} - flushing")
                        await self.flush_to_db(writer_fn=writer_fn)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Error in auto-flush loop: {e}")
        
        self._flush_task = asyncio.create_task(_auto_flush_loop())
    
    async def stop_auto_flush(self) -> None:
        """Stop automatic flushing."""
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass  # Expected when cancelling
            except Exception as e:
                logger.warning(f"Error while stopping auto-flush: {e}")
    
    async def __aenter__(self):
        """Async context manager entry."""
        await self.initialize()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.stop_auto_flush()
        await self.flush_to_db()  # Flush remaining records
        await self.close()



