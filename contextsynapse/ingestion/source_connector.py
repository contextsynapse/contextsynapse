"""
Source Connector Infrastructure

Base classes and interfaces for connecting to external data sources.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, Callable, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class IngestionEvent:
    """
    Standardized ingestion event format.
    
    All source connectors normalize their data into this format
    before routing to ingestion pipelines.
    """
    event_id: str
    source_type: str  # 'kafka', 'websocket', 'api', 'folder', 'url'
    source_id: str    # Identifier for the source
    timestamp: datetime
    data: Dict[str, Any]  # Raw data payload (file_path, payload, api_data, etc.)
    metadata: Dict[str, Any] = field(default_factory=dict)  # Source-specific metadata
    pipeline_name: Optional[str] = None  # Target pipeline
    namespace: str = "default"
    priority: int = 0  # Processing priority
    retry_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert event to dictionary for serialization."""
        return {
            'event_id': self.event_id,
            'source_type': self.source_type,
            'source_id': self.source_id,
            'timestamp': self.timestamp.isoformat(),
            'data': self.data,
            'metadata': self.metadata,
            'pipeline_name': self.pipeline_name,
            'namespace': self.namespace,
            'priority': self.priority,
            'retry_count': self.retry_count
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'IngestionEvent':
        """Create event from dictionary."""
        return cls(
            event_id=data['event_id'],
            source_type=data['source_type'],
            source_id=data['source_id'],
            timestamp=datetime.fromisoformat(data['timestamp']),
            data=data['data'],
            metadata=data.get('metadata', {}),
            pipeline_name=data.get('pipeline_name'),
            namespace=data.get('namespace', 'default'),
            priority=data.get('priority', 0),
            retry_count=data.get('retry_count', 0)
        )


class SourceConnector(ABC):
    """
    Base class for all source connectors.
    
    Each source type (Kafka, WebSocket, API, Folder, etc.) implements
    this interface to normalize their data into IngestionEvent format.
    """
    
    def __init__(self, config: Dict[str, Any], namespace: str = "default"):
        """
        Initialize connector.
        
        Args:
            config: Source-specific configuration
            namespace: Namespace for this connector
        """
        self.config = config
        self.namespace = namespace
        self.connected = False
        self.callback: Optional[Callable[[IngestionEvent], None]] = None
    
    @abstractmethod
    def connect(self) -> bool:
        """
        Establish connection to source.
        
        Returns:
            True if connection successful, False otherwise
        """
        pass
    
    @abstractmethod
    def normalize(self, raw_data: Any) -> IngestionEvent:
        """
        Normalize raw data to IngestionEvent format.
        
        This converts source-specific data format to the standard
        IngestionEvent format. For folder sources, this wraps the
        file_path in the event (does NOT read file content).
        
        Args:
            raw_data: Source-specific raw data
            
        Returns:
            Normalized IngestionEvent
        """
        pass
    
    @abstractmethod
    def listen(self, callback: Callable[[IngestionEvent], None]) -> None:
        """
        Start listening for incoming data and call callback for each event.
        
        Args:
            callback: Function to call with each IngestionEvent
        """
        pass
    
    @abstractmethod
    def disconnect(self) -> None:
        """Close connection to source."""
        pass
    
    def _route_event(self, event: IngestionEvent) -> None:
        """
        Route event to callback or event bus.
        
        Args:
            event: IngestionEvent to route
        """
        if self.callback:
            try:
                self.callback(event)
            except Exception as e:
                logger.error(f"Error routing event {event.event_id}: {e}", exc_info=True)
        else:
            logger.warning(f"No callback registered for event {event.event_id}")




























