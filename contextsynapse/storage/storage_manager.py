"""Unified storage manager supporting multiple modes."""

import logging
from pathlib import Path
from typing import Dict, Any, Optional
from ..core.graph_structures import GraphNode, GraphEdge
from ..config.mode_config import NamespaceConfig, StorageMode
from .pure_graph_storage import PureGraphStorage
from ..metadata.metadata_db import MetadataDB

logger = logging.getLogger(__name__)


class StorageManager:
    """Unified storage interface supporting multiple modes.
    
    This abstraction allows the same AIQL queries to work with different
    storage backends (pure graph, collection-based, hybrid) without
    changing the query syntax.
    """
    
    def __init__(self, namespace: str, config: Optional[Dict[str, Any]] = None):
        """Initialize storage manager.
        
        Args:
            namespace: Namespace name
            config: Optional configuration dictionary (if None, loads from config file)
        """
        self.namespace = namespace
        self.namespace_config = NamespaceConfig(namespace)
        
        # Override config if provided
        if config:
            self.namespace_config.config.update(config)
        
        self.mode = self.namespace_config.mode
        self.base_path = Path(f"contextcore_data/namespaces/{namespace}")
        
        # Initialize metadata database
        self.metadata_db = None
        try:
            metadata_config = self.namespace_config.metadata_config
            self.metadata_db = MetadataDB(metadata_config)
        except Exception as e:
            logger.warning(f"[EMOJI][EMOJI] Failed to initialize metadata DB: {e}")
        
        # Initialize storage backends based on mode
        self.pure_graph_storage = None
        self.collection_storage = None  # Will use existing _store_node_in_collection
        
        if self.mode == StorageMode.PURE_GRAPH:
            self.pure_graph_storage = PureGraphStorage(namespace, self.base_path)
        elif self.mode == StorageMode.HYBRID:
            self.pure_graph_storage = PureGraphStorage(namespace, self.base_path)
            # Collection storage handled by existing methods
        
        logger.info(f"[EMOJI] StorageManager initialized: mode={self.mode.value}, namespace={namespace}")
    
    def store_node(self, node: GraphNode, collection: Optional[str] = None) -> str:
        """Store node based on configured mode.
        
        This method abstracts the storage implementation so AIQL queries
        don't need to know about the underlying storage mode.
        
        Args:
            node: GraphNode to store
            collection: Collection name (used for collection-based mode)
        
        Returns:
            Path where node was stored
        """
        node_type = node.label if hasattr(node, 'label') else 'Node'
        stored_path = None
        
        try:
            if self.mode == StorageMode.PURE_GRAPH:
                # Store in pure graph structure
                stored_path = self.pure_graph_storage.store_node(node)
                
            elif self.mode == StorageMode.COLLECTION_BASED:
                # Store in collection-based structure (use existing method)
                # This will be handled by the executor's existing _store_node_in_collection
                # We return a placeholder path - actual storage happens in executor
                stored_path = f"collections/{collection}/nodes/{node_type}/{node.id}.json"
                
            elif self.mode == StorageMode.HYBRID:
                # Store in both places
                self.pure_graph_storage.store_node(node)
                stored_path = f"collections/{collection}/nodes/{node_type}/{node.id}.json"
            
            # Store metadata in SQL database
            if self.metadata_db:
                try:
                    node_properties = node.properties if hasattr(node, 'properties') else {}
                    self.metadata_db.store_metadata(
                        entity_id=node.id,
                        namespace=self.namespace,
                        collection=collection,
                        node_type=node_type,
                        entity_type='node',
                        metadata=node_properties
                    )
                except Exception as e:
                    logger.warning(f"[EMOJI][EMOJI] Failed to store metadata for node {node.id}: {e}")
            
            return stored_path or f"{node_type}/{node.id}.json"
            
        except Exception as e:
            logger.error(f"[EMOJI] Failed to store node {node.id}: {e}")
            raise
    
    def store_edge(self, edge: GraphEdge, collection: Optional[str] = None) -> str:
        """Store edge based on configured mode.
        
        Args:
            edge: GraphEdge to store
            collection: Collection name (used for collection-based mode)
        
        Returns:
            Path where edge was stored
        """
        stored_path = None
        
        try:
            if self.mode == StorageMode.PURE_GRAPH:
                # Store in pure graph structure
                stored_path = self.pure_graph_storage.store_edge(edge)
                
            elif self.mode == StorageMode.COLLECTION_BASED:
                # Store in collection-based structure
                # For graph collection, store in centralized graph/edges/
                if collection == 'graph' or not collection:
                    stored_path = f"collections/graph/edges/{edge.label}/{edge.id}.json"
                else:
                    stored_path = f"collections/{collection}/edges/{edge.label}/{edge.id}.json"
                
            elif self.mode == StorageMode.HYBRID:
                # Store in pure graph AND collection-based
                self.pure_graph_storage.store_edge(edge)
                stored_path = f"collections/graph/edges/{edge.label}/{edge.id}.json"
            
            # Store metadata in SQL database
            if self.metadata_db:
                try:
                    edge_properties = edge.properties if hasattr(edge, 'properties') else {}
                    self.metadata_db.store_metadata(
                        entity_id=edge.id,
                        namespace=self.namespace,
                        collection=collection or 'graph',
                        node_type=edge.label,
                        entity_type='edge',
                        metadata=edge_properties
                    )
                except Exception as e:
                    logger.warning(f"[EMOJI][EMOJI] Failed to store metadata for edge {edge.id}: {e}")
            
            return stored_path or f"edges/{edge.label}/{edge.id}.json"
            
        except Exception as e:
            logger.error(f"[EMOJI] Failed to store edge {edge.id}: {e}")
            raise
    
    def get_statistics(self, collection: Optional[str] = None) -> Dict[str, Any]:
        """Get statistics from SQL metadata database.
        
        Args:
            collection: Optional collection name
        
        Returns:
            Statistics dictionary
        """
        if self.metadata_db:
            return self.metadata_db.get_statistics(self.namespace, collection)
        return {}
    
    def update_statistics(self, collection: str, node_count: int = 0, edge_count: int = 0):
        """Update statistics in SQL metadata database.
        
        Args:
            collection: Collection name
            node_count: Number of nodes to add
            edge_count: Number of edges to add
        """
        if self.metadata_db:
            self.metadata_db.update_statistics(
                self.namespace, collection, node_count, edge_count
            )
    
    def record_query(self, query_id: str, query_text: str, query_type: Optional[str],
                    execution_time_ms: float, rows_returned: int,
                    metadata: Optional[Dict] = None):
        """Record query performance metrics.
        
        Args:
            query_id: Unique query identifier
            query_text: Query text
            query_type: Type of query
            execution_time_ms: Execution time in milliseconds
            rows_returned: Number of rows returned
            metadata: Additional metadata
        """
        if self.metadata_db:
            self.metadata_db.record_query(
                query_id, query_text, self.namespace, query_type,
                execution_time_ms, rows_returned, metadata
            )
    
    def close(self):
        """Close storage manager and connections."""
        if self.metadata_db:
            self.metadata_db.close()



