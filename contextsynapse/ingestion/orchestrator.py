"""
Ingestion Orchestrator

Routes ingestion events to appropriate pipelines and manages processing.
"""

from typing import Dict, Any, Optional, Callable
import logging
from .source_connector import IngestionEvent

logger = logging.getLogger(__name__)


class IngestionOrchestrator:
    """
    Routes ingestion events to pipelines and manages processing.
    
    This is a simple in-memory orchestrator. For production, this could
    be replaced with a message queue (RabbitMQ, Kafka, Redis Streams).
    """
    
    def __init__(self, namespace: str = "default"):
        """
        Initialize orchestrator.
        
        Args:
            namespace: Namespace for this orchestrator
        """
        self.namespace = namespace
        self.pipeline_registry: Dict[str, Callable[[IngestionEvent], None]] = {}
        self.event_count = 0
    
    def register_pipeline(
        self,
        pipeline_name: str,
        handler: Callable[[IngestionEvent], None]
    ) -> None:
        """
        Register a pipeline handler.
        
        Args:
            pipeline_name: Name of the pipeline
            handler: Function to handle events for this pipeline
        """
        self.pipeline_registry[pipeline_name] = handler
        logger.info(f"Registered pipeline: {pipeline_name}")
    
    def route_event(self, event: IngestionEvent) -> bool:
        """
        Route event to appropriate pipeline.
        
        Args:
            event: IngestionEvent to route
            
        Returns:
            True if routed successfully, False otherwise
        """
        self.event_count += 1
        
        # Determine target pipeline
        pipeline_name = event.pipeline_name
        
        if not pipeline_name:
            logger.warning(f"No pipeline specified for event {event.event_id}")
            return False
        
        # Get handler
        handler = self.pipeline_registry.get(pipeline_name)
        if not handler:
            logger.warning(f"No handler registered for pipeline: {pipeline_name}")
            return False
        
        # Route to handler
        try:
            handler(event)
            logger.debug(f"Routed event {event.event_id} to pipeline {pipeline_name}")
            return True
        except Exception as e:
            logger.error(f"Error routing event {event.event_id} to pipeline {pipeline_name}: {e}", exc_info=True)
            return False
    
    def get_stats(self) -> Dict[str, Any]:
        """Get orchestrator statistics."""
        return {
            'namespace': self.namespace,
            'registered_pipelines': list(self.pipeline_registry.keys()),
            'total_events': self.event_count
        }




























