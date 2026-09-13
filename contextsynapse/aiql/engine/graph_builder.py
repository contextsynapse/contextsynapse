"""
Graph Builder

Builds and enhances graph connections:
1. After CHUNK: Connect chunks to documents, tables, images
2. After EXTRACT_ENTITIES: Connect entities to chunks, documents, tables
3. After EXTRACT_RELATIONSHIPS: Create relationship edges between entities
"""

import logging
from typing import Dict, List, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class GraphBuilder:
    """
    Builds and enhances graph connections between nodes.
    
    Responsibilities:
    - Connect chunks to documents, tables, images
    - Connect entities to chunks, documents, tables
    - Create relationship edges between entities
    """
    
    def __init__(self, namespace_graph, namespace: str):
        """
        Initialize graph builder.
        
        Args:
            namespace_graph: The graph instance
            namespace: Namespace name
        """
        self.namespace_graph = namespace_graph
        self.namespace = namespace
    
    def connect_chunks_to_documents(
        self,
        chunk_nodes: List[Any],
        document_nodes: List[Any],
        edge_type: str = "HAS_CHUNK"
    ) -> List[Dict[str, Any]]:
        """
        Connect chunks to their source documents.
        
        Args:
            chunk_nodes: List of chunk nodes
            document_nodes: List of document nodes
            edge_type: Edge type to create
            
        Returns:
            List of created edges
        """
        edges_created = []
        
        for chunk_node in chunk_nodes:
            chunk_id = chunk_node.id if hasattr(chunk_node, 'id') else chunk_node.get('id')
            chunk_props = chunk_node.properties if hasattr(chunk_node, 'properties') else chunk_node.get('properties', {})
            
            # Get document_id from chunk source
            source = chunk_props.get('source', {})
            document_id = source.get('document_id')
            document_node_id = source.get('document_node_id')
            
            # Find matching document node
            target_doc_id = None
            
            if document_node_id:
                # Use provided document_node_id
                target_doc_id = document_node_id
            elif document_id:
                # Find document node by document_id
                for doc_node in document_nodes:
                    doc_props = doc_node.properties if hasattr(doc_node, 'properties') else doc_node.get('properties', {})
                    doc_meta = doc_props.get('metadata', {})
                    if doc_meta.get('document_id') == document_id or doc_props.get('document_id') == document_id:
                        target_doc_id = doc_node.id if hasattr(doc_node, 'id') else doc_node.get('id')
                        break
            
            if target_doc_id:
                # Create edge
                edge_id = f"edge_{target_doc_id}_to_{chunk_id}"
                edge = {
                    'id': edge_id,
                    'source': target_doc_id,
                    'target': chunk_id,
                    'type': edge_type,
                    'properties': {
                        'namespace': self.namespace,
                        'created_at': datetime.now().isoformat()
                    }
                }
                edges_created.append(edge)
                logger.debug(f"Connected chunk {chunk_id} to document {target_doc_id}")
        
        return edges_created
    
    def connect_chunks_to_tables(
        self,
        chunk_nodes: List[Any],
        table_nodes: List[Any],
        edge_type: str = "MENTIONS_TABLE"
    ) -> List[Dict[str, Any]]:
        """
        Connect chunks to tables they mention.
        
        Strategy: Check if chunk content mentions table data or if they're on the same page.
        
        Args:
            chunk_nodes: List of chunk nodes
            table_nodes: List of table nodes
            edge_type: Edge type to create
            
        Returns:
            List of created edges
        """
        edges_created = []
        
        for chunk_node in chunk_nodes:
            chunk_id = chunk_node.id if hasattr(chunk_node, 'id') else chunk_node.get('id')
            chunk_props = chunk_node.properties if hasattr(chunk_node, 'properties') else chunk_node.get('properties', {})
            chunk_content = chunk_props.get('content', '')
            chunk_source = chunk_props.get('source', {})
            chunk_page = chunk_source.get('page_no')
            
            # Find tables on the same page or mentioned in chunk
            for table_node in table_nodes:
                table_id = table_node.id if hasattr(table_node, 'id') else table_node.get('id')
                table_props = table_node.properties if hasattr(table_node, 'properties') else table_node.get('properties', {})
                table_meta = table_props.get('metadata', {})
                table_page = table_meta.get('from_page', 0)
                
                # Connect if same page
                if chunk_page and table_page and chunk_page == table_page:
                    edge_id = f"edge_{chunk_id}_to_{table_id}"
                    edge = {
                        'id': edge_id,
                        'source': chunk_id,
                        'target': table_id,
                        'type': edge_type,
                        'properties': {
                            'namespace': self.namespace,
                            'page_no': chunk_page,
                            'created_at': datetime.now().isoformat()
                        }
                    }
                    edges_created.append(edge)
                    logger.debug(f"Connected chunk {chunk_id} to table {table_id} (page {chunk_page})")
        
        return edges_created
    
    def connect_chunks_to_images(
        self,
        chunk_nodes: List[Any],
        image_nodes: List[Any],
        edge_type: str = "MENTIONS_IMAGE"
    ) -> List[Dict[str, Any]]:
        """
        Connect chunks to images they mention.
        
        Strategy: Check if chunk and image are on the same page.
        
        Args:
            chunk_nodes: List of chunk nodes
            image_nodes: List of image nodes
            edge_type: Edge type to create
            
        Returns:
            List of created edges
        """
        edges_created = []
        
        for chunk_node in chunk_nodes:
            chunk_id = chunk_node.id if hasattr(chunk_node, 'id') else chunk_node.get('id')
            chunk_props = chunk_node.properties if hasattr(chunk_node, 'properties') else chunk_node.get('properties', {})
            chunk_source = chunk_props.get('source', {})
            chunk_page = chunk_source.get('page_no')
            
            # Find images on the same page
            for image_node in image_nodes:
                image_id = image_node.id if hasattr(image_node, 'id') else image_node.get('id')
                image_props = image_node.properties if hasattr(image_node, 'properties') else image_node.get('properties', {})
                image_meta = image_props.get('metadata', {})
                image_page = image_meta.get('page_no', 0)
                
                # Connect if same page
                if chunk_page and image_page and chunk_page == image_page:
                    edge_id = f"edge_{chunk_id}_to_{image_id}"
                    edge = {
                        'id': edge_id,
                        'source': chunk_id,
                        'target': image_id,
                        'type': edge_type,
                        'properties': {
                            'namespace': self.namespace,
                            'page_no': chunk_page,
                            'created_at': datetime.now().isoformat()
                        }
                    }
                    edges_created.append(edge)
                    logger.debug(f"Connected chunk {chunk_id} to image {image_id} (page {chunk_page})")
        
        return edges_created
    
    def connect_entities_to_chunks(
        self,
        entity_nodes: List[Any],
        chunk_nodes: List[Any],
        edge_type: str = "MENTIONS"
    ) -> List[Dict[str, Any]]:
        """
        Connect entities to chunks that mention them.
        
        Args:
            entity_nodes: List of entity nodes
            chunk_nodes: List of chunk nodes
            edge_type: Edge type to create
            
        Returns:
            List of created edges
        """
        edges_created = []
        
        for entity_node in entity_nodes:
            entity_id = entity_node.id if hasattr(entity_node, 'id') else entity_node.get('id')
            entity_props = entity_node.properties if hasattr(entity_node, 'properties') else entity_node.get('properties', {})
            entity_name = entity_props.get('name', '')
            entity_source = entity_props.get('source', {})
            source_chunk_id = entity_source.get('chunk_id')
            
            if source_chunk_id:
                # Entity already has source chunk reference
                edge_id = f"edge_{source_chunk_id}_to_{entity_id}"
                edge = {
                    'id': edge_id,
                    'source': source_chunk_id,
                    'target': entity_id,
                    'type': edge_type,
                    'properties': {
                        'namespace': self.namespace,
                        'created_at': datetime.now().isoformat()
                    }
                }
                edges_created.append(edge)
                logger.debug(f"Connected chunk {source_chunk_id} to entity {entity_id}")
            else:
                # Search for chunks containing entity name
                for chunk_node in chunk_nodes:
                    chunk_id = chunk_node.id if hasattr(chunk_node, 'id') else chunk_node.get('id')
                    chunk_props = chunk_node.properties if hasattr(chunk_node, 'properties') else chunk_node.get('properties', {})
                    chunk_content = chunk_props.get('content', '').lower()
                    
                    if entity_name.lower() in chunk_content:
                        edge_id = f"edge_{chunk_id}_to_{entity_id}"
                        edge = {
                            'id': edge_id,
                            'source': chunk_id,
                            'target': entity_id,
                            'type': edge_type,
                            'properties': {
                                'namespace': self.namespace,
                                'created_at': datetime.now().isoformat()
                            }
                        }
                        edges_created.append(edge)
                        logger.debug(f"Connected chunk {chunk_id} to entity {entity_id} (name match)")
        
        return edges_created
    
    def connect_entities_to_documents(
        self,
        entity_nodes: List[Any],
        document_nodes: List[Any],
        edge_type: str = "APPEARS_IN"
    ) -> List[Dict[str, Any]]:
        """
        Connect entities to documents they appear in.
        
        Args:
            entity_nodes: List of entity nodes
            document_nodes: List of document nodes
            edge_type: Edge type to create
            
        Returns:
            List of created edges
        """
        edges_created = []
        
        for entity_node in entity_nodes:
            entity_id = entity_node.id if hasattr(entity_node, 'id') else entity_node.get('id')
            entity_props = entity_node.properties if hasattr(entity_node, 'properties') else entity_node.get('properties', {})
            entity_source = entity_props.get('source', {})
            document_id = entity_source.get('document_id')
            
            if document_id:
                # Find matching document node
                for doc_node in document_nodes:
                    doc_props = doc_node.properties if hasattr(doc_node, 'properties') else doc_node.get('properties', {})
                    doc_meta = doc_props.get('metadata', {})
                    if doc_meta.get('document_id') == document_id or doc_props.get('document_id') == document_id:
                        doc_id = doc_node.id if hasattr(doc_node, 'id') else doc_node.get('id')
                        
                        edge_id = f"edge_{entity_id}_to_{doc_id}"
                        edge = {
                            'id': edge_id,
                            'source': entity_id,
                            'target': doc_id,
                            'type': edge_type,
                            'properties': {
                                'namespace': self.namespace,
                                'created_at': datetime.now().isoformat()
                            }
                        }
                        edges_created.append(edge)
                        logger.debug(f"Connected entity {entity_id} to document {doc_id}")
                        break
        
        return edges_created
    
    def build_relationship_edges(
        self,
        relationship_data: List[Dict[str, Any]],
        entity_nodes: List[Any],
        edge_type: str = "RELATED_TO"
    ) -> List[Dict[str, Any]]:
        """
        Create relationship edges between entities.
        
        Args:
            relationship_data: List of relationship dictionaries with source, target, type
            entity_nodes: List of entity nodes
            edge_type: Default edge type
            
        Returns:
            List of created edges
        """
        edges_created = []
        
        # Build entity lookup by name
        entity_lookup = {}
        for entity_node in entity_nodes:
            entity_id = entity_node.id if hasattr(entity_node, 'id') else entity_node.get('id')
            entity_props = entity_node.properties if hasattr(entity_node, 'properties') else entity_node.get('properties', {})
            entity_name = entity_props.get('name', '')
            entity_lookup[entity_name.lower()] = entity_id
        
        for rel_data in relationship_data:
            source_name = rel_data.get('source_entity', '')
            target_name = rel_data.get('target_entity', '')
            rel_type = rel_data.get('relationship_type', edge_type)
            
            source_id = entity_lookup.get(source_name.lower())
            target_id = entity_lookup.get(target_name.lower())
            
            if source_id and target_id:
                edge_id = f"edge_{source_id}_to_{target_id}_{rel_type}"
                edge = {
                    'id': edge_id,
                    'source': source_id,
                    'target': target_id,
                    'type': rel_type,
                    'properties': {
                        'namespace': self.namespace,
                        'confidence': rel_data.get('confidence', 1.0),
                        'context': rel_data.get('context', ''),
                        'created_at': datetime.now().isoformat()
                    }
                }
                edges_created.append(edge)
                logger.debug(f"Created relationship edge: {source_id} --{rel_type}--> {target_id}")
        
        return edges_created
































