"""
Scalable Multi-Source Ingestion System

This module provides connectors and orchestration for ingesting data from
various external sources including URLs, files, text, and conversations.
"""

from .source_connector import SourceConnector, IngestionEvent
from .orchestrator import IngestionOrchestrator

__all__ = [
    'SourceConnector',
    'IngestionEvent',
    'IngestionOrchestrator',
]
