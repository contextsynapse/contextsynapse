"""
Example: Folder Ingestion with Deduplication

This example shows how to:
1. Set up a folder watcher connector
2. Process existing files on startup
3. Watch for new files
4. Prevent duplicate ingestion using DeduplicationManager
"""

import logging
from pathlib import Path
from contextsynapse.ingestion import (
    FolderWatcherConnector,
    IngestionEvent,
    setup_folder_ingestion_system
)
from contextsynapse.extraction.id_generator import DeduplicationManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def ingestion_pipeline_handler(event: IngestionEvent):
    """
    Handle ingestion events from folder connector.
    
    This is where you would:
    1. Extract content from file_path
    2. Process and transform content
    3. Create graph nodes/edges
    """
    logger.info(f"Processing event: {event.event_id}")
    logger.info(f"  File: {event.data.get('file_path')}")
    logger.info(f"  Event type: {event.metadata.get('event_type')}")
    
    # Here you would:
    # 1. Read file from event.data['file_path']
    # 2. Extract content using extraction subsystem
    # 3. Create graph nodes/edges
    # 4. Store in graph database
    
    file_path = event.data.get('file_path')
    if file_path:
        logger.info(f"  Would extract content from: {file_path}")
        # Example: integrate with extraction subsystem
        # from contextsynapse.extraction import run_extraction, ExtractionConfig
        # config = ExtractionConfig(file_path=file_path, ...)
        # document_id = run_extraction(config)


def main():
    """Main example function."""
    
    # Configuration
    folder_path = "/path/to/watch"  # Change this to your folder
    namespace = "example_namespace"
    
    # Initialize deduplication manager
    dedup_manager = DeduplicationManager(namespace=namespace)
    
    # Folder connector configuration
    folder_config = {
        'folder_path': folder_path,
        'pattern': '*.pdf',  # Watch for PDF files
        'recursive': True,   # Scan subdirectories
        'process_existing': True,  # Process existing files on startup
        'process_existing_since': None,  # Process all existing files
        'pipeline_name': 'document_ingestion'
    }
    
    # Set up complete ingestion system
    connector, orchestrator = setup_folder_ingestion_system(
        folder_config=folder_config,
        namespace=namespace,
        ingestion_pipeline_handler=ingestion_pipeline_handler,
        deduplication_manager=dedup_manager
    )
    
    # Connect and start watching
    logger.info(f"Connecting to folder: {folder_path}")
    if connector.connect():
        logger.info("Connected! Starting to watch for files...")
        
        # Start listening for new files
        connector.listen(ingestion_pipeline_handler)
        
        # Keep running (in production, this would be a service)
        try:
            import time
            logger.info("Watching for files... (Press Ctrl+C to stop)")
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Stopping...")
            connector.disconnect()
            logger.info("Stopped")
    else:
        logger.error("Failed to connect to folder")


if __name__ == "__main__":
    main()




























