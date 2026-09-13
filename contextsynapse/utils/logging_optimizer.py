"""
Logging Optimizer for Performance-Critical Code

Provides optimized logging utilities that minimize I/O overhead.
"""

import logging
from typing import Callable, Any, Optional


class LazyLogMessage:
    """Lazy evaluation wrapper for expensive log message construction."""
    
    def __init__(self, func: Callable[[], str]):
        self.func = func
    
    def __str__(self):
        return self.func()


class PerformanceLogger:
    """
    Performance-optimized logger wrapper.
    
    Features:
    - Lazy evaluation of log messages
    - Level checking before message construction
    - Conditional logging based on configuration
    """
    
    def __init__(self, logger: logging.Logger, 
                 min_level: int = logging.WARNING,
                 enable_perf_logging: bool = False):
        """
        Initialize performance logger.
        
        Args:
            logger: Base logger instance
            min_level: Minimum log level for performance-critical paths (default: WARNING)
            enable_perf_logging: Enable performance logging (default: False)
        """
        self.logger = logger
        self.min_level = min_level
        self.enable_perf_logging = enable_perf_logging
    
    def _should_log(self, level: int) -> bool:
        """Check if logging should occur at this level."""
        if level < self.min_level:
            return False
        return self.logger.isEnabledFor(level)
    
    def debug(self, msg: str, *args, **kwargs):
        """Debug logging - only if enabled."""
        if self._should_log(logging.DEBUG):
            self.logger.debug(msg, *args, **kwargs)
    
    def info(self, msg: str, *args, **kwargs):
        """Info logging - only if enabled."""
        if self._should_log(logging.INFO):
            self.logger.info(msg, *args, **kwargs)
    
    def info_lazy(self, msg_func: Callable[[], str]):
        """Lazy info logging - only constructs message if logging is enabled."""
        if self._should_log(logging.INFO):
            self.logger.info(msg_func())
    
    def warning(self, msg: str, *args, **kwargs):
        """Warning logging."""
        if self._should_log(logging.WARNING):
            self.logger.warning(msg, *args, **kwargs)
    
    def error(self, msg: str, *args, **kwargs):
        """Error logging."""
        if self._should_log(logging.ERROR):
            self.logger.error(msg, *args, **kwargs)
    
    def critical(self, msg: str, *args, **kwargs):
        """Critical logging."""
        if self._should_log(logging.CRITICAL):
            self.logger.critical(msg, *args, **kwargs)
    
    def perf(self, msg: str, *args, **kwargs):
        """Performance logging - only if enabled."""
        if self.enable_perf_logging and self._should_log(logging.INFO):
            self.logger.info(f"[PERF] {msg}", *args, **kwargs)


def get_perf_logger(name: str, 
                   min_level: int = logging.WARNING,
                   enable_perf_logging: bool = False) -> PerformanceLogger:
    """
    Get a performance-optimized logger.
    
    Args:
        name: Logger name
        min_level: Minimum log level (default: WARNING for production)
        enable_perf_logging: Enable performance logging
    
    Returns:
        PerformanceLogger instance
    """
    base_logger = logging.getLogger(name)
    return PerformanceLogger(base_logger, min_level, enable_perf_logging)


# Global flag to control verbose logging in hot paths
_VERBOSE_LOGGING_ENABLED = False


def set_verbose_logging(enabled: bool):
    """Enable/disable verbose logging in performance-critical paths."""
    global _VERBOSE_LOGGING_ENABLED
    _VERBOSE_LOGGING_ENABLED = enabled


def is_verbose_logging_enabled() -> bool:
    """Check if verbose logging is enabled."""
    return _VERBOSE_LOGGING_ENABLED

