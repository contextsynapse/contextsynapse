"""
AIQL Engine Module
Execution engine for AIQL queries and pipelines.
"""

from .executor import AIQLExecutor

# ExecutionResult is a simple dict type alias for now
ExecutionResult = dict

__all__ = ['AIQLExecutor', 'ExecutionResult']
