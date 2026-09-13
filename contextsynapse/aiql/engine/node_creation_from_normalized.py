"""
Node Creation from Normalized JSON

Creates graph nodes (Document, Table, Image, Entity) and relationships
from normalized JSON produced by the extraction subsystem.
Uses schema YAML file to define node and edge structures.
"""

import logging
from typing import Dict, List, Optional, Any
from pathlib import Path
import json
from datetime import datetime
import uuid

logger = logging.getLogger(__name__)


class NodeCreatorFromNormalized:
    """
    Creates graph nodes from normalized JSON using schema definitions.
    
    This is used in the CONNECT stage to create Document, Table, Image nodes
    and relationships from the normalized JSON produced by the extraction subsystem.
    Uses schema YAML to ensure nodes and edges conform to the defined schema.
    """
    
    def __init__(self, namespace: str, base_dir: str = "contextcore_data", schema_path: Optional[str] = None):
        """
        Initialize node creator.
        
        Args:
            namespace: Namespace name
            base_dir: Base directory for storage
            schema_path: Optional path to schema YAML file. If None, uses default.
        """
        self.namespace = namespace
        self.base_dir = Path(base_dir)
        
        # Load schema
        if schema_path is None:
            # Default schema path - try multiple locations
            possible_paths = [
                Path(__file__).parent.parent.parent.parent / "config" / "core" / "schema.yaml",
                Path("config/core/schema.yaml"),
                Path("config") / "core" / "schema.yaml"
            ]
            schema_path = None
            for path in possible_paths:
                if path.exists():
                    schema_path = path
                    break
            
            if schema_path is None:
                logger.warning(f"[SCHEMA] Schema file not found in any of: {possible_paths}")
        
        self.schema = None
        self.schema_parser = None
        if schema_path and Path(schema_path).exists():
            try:
                from contextsynapse.schema.schema_parser import SchemaParser
                self.schema_parser = SchemaParser(str(schema_path))
                self.schema = self.schema_parser.parse()
                logger.info(f"[SCHEMA] Loaded schema from {schema_path}: {len(self.schema.node_types)} node types, {len(self.schema.edge_types)} edge types")
                # #region agent log
                import json
                try:
                    with open(r'c:\qgraph\qgraph-app\.cursor\debug.log', 'a', encoding='utf-8') as f:
                        f.write(json.dumps({
                            'sessionId': 'debug-session',
                            'runId': 'schema-load',
                            'hypothesisId': 'G',
                            'location': 'node_creation_from_normalized.py:60',
                            'message': 'Schema loaded successfully',
                            'data': {
                                'schema_path': str(schema_path),
                                'node_types_count': len(self.schema.node_types),
                                'edge_types_count': len(self.schema.edge_types),
                                'node_types': list(self.schema.node_types.keys())[:5]  # First 5
                            },
                            'timestamp': int(__import__('time').time() * 1000)
                        }) + '\n')
                except: pass
                # #endregion
            except Exception as e:
                logger.warning(f"[SCHEMA] Failed to load schema from {schema_path}: {e}. Using default node creation.")
                import traceback
                logger.debug(traceback.format_exc())
                self.schema = None
        else:
            logger.warning(f"[SCHEMA] Schema path not provided or file not found: {schema_path}. Using default node creation.")
            self.schema = None
    
    def create_document_nodes(
        self,
        normalized_doc: Dict[str, Any],
        document_id: str,
        create_per_page: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Create Document nodes from normalized JSON.
        
        Args:
            normalized_doc: Normalized JSON document
            document_id: Document identifier
            create_per_page: If True, create one Document node per page; if False, create one per document
            
        Returns:
            List of Document node properties dictionaries
        """
        nodes = []
        pages = normalized_doc.get('content', {}).get('pages', [])
        metadata = normalized_doc.get('metadata', {})
        source = normalized_doc.get('source', {})
        
        if create_per_page:
            # Create one Document node per page
            for page in pages:
                page_no = page.get('page_no', 1)
                doc_id = f"doc_{document_id}_page_{page_no}"
                
                # Build node properties according to schema
                node_props = self._build_node_properties(
                    node_type='Document',
                    base_props={
                        'doc_id': doc_id,
                        'title': metadata.get('title', f"Page {page_no}"),
                        'text': page.get('flat_text', ''),
                        'source': source.get('file_path', ''),
                        'metadata': {
                            'document_id': document_id,
                            'page_no': page_no,
                            'normalized_version': normalized_doc.get('version', 'v1'),
                            'file_name': source.get('file_name', ''),
                            'file_hash': source.get('file_hash', ''),
                            'extractor': source.get('extractor_version', ''),
                            **metadata
                        },
                        'created_at': datetime.now().isoformat(),
                        'namespace': self.namespace
                    }
                )
                
                nodes.append({
                    'node_id': doc_id,
                    'node_type': 'Document',
                    'properties': node_props
                })
        else:
            # Create one Document node for the entire document
            doc_id = f"doc_{document_id}"
            
            # Combine all page text
            all_text = '\n\n'.join([page.get('flat_text', '') for page in pages])
            
            # Build node properties according to schema
            node_props = self._build_node_properties(
                node_type='Document',
                base_props={
                    'doc_id': doc_id,
                    'title': metadata.get('title', source.get('file_name', 'Document')),
                    'text': all_text,
                    'source': source.get('file_path', ''),
                    'metadata': {
                        'document_id': document_id,
                        'page_count': len(pages),
                        'normalized_version': normalized_doc.get('version', 'v1'),
                        'file_name': source.get('file_name', ''),
                        'file_hash': source.get('file_hash', ''),
                        'extractor': source.get('extractor_version', ''),
                        **metadata
                    },
                    'created_at': datetime.now().isoformat(),
                    'namespace': self.namespace
                }
            )
            
            nodes.append({
                'node_id': doc_id,
                'node_type': 'Document',
                'properties': node_props
            })
        
        # Return node IDs for linking chunks
        return nodes
    
    def create_table_nodes(
        self,
        normalized_doc: Dict[str, Any],
        document_id: str
    ) -> List[Dict[str, Any]]:
        """
        Create Table nodes from normalized JSON.
        
        Args:
            normalized_doc: Normalized JSON document
            document_id: Document identifier
            
        Returns:
            List of Table node properties dictionaries
        """
        nodes = []
        tables = normalized_doc.get('content', {}).get('tables', [])
        source = normalized_doc.get('source', {})
        
        for i, table in enumerate(tables):
            table_id = table.get('id', f"table_{document_id}_{i+1}")
            
            # Extract table data
            table_data = table.get('data', [])
            headers = table_data[0] if table_data else []
            rows = table_data[1:] if len(table_data) > 1 else []
            
            # Build node properties according to schema
            node_props = self._build_node_properties(
                node_type='Table',
                base_props={
                    'table_id': table_id,
                    'title': table.get('caption', f"Table {i+1}"),
                    'headers': headers,
                    'rows': rows,
                    'schema': {
                        'column_count': len(headers),
                        'row_count': len(rows)
                    },
                    'source_doc': document_id,
                    'metadata': {
                        'document_id': document_id,
                        'from_page': table.get('from_page', 0),
                        'table_index': i + 1,
                        'normalized_version': normalized_doc.get('version', 'v1'),
                        'markdown': table.get('markdown', '')
                    },
                    'confidence': 1.0,  # Extracted tables are high confidence
                    'created_at': datetime.now().isoformat(),
                    'namespace': self.namespace
                }
            )
            
            nodes.append({
                'node_id': table_id,
                'node_type': 'Table',
                'properties': node_props
            })
        
        return nodes
    
    def create_image_nodes(
        self,
        normalized_doc: Dict[str, Any],
        document_id: str
    ) -> List[Dict[str, Any]]:
        """
        Create Image nodes from normalized JSON.
        
        Args:
            normalized_doc: Normalized JSON document
            document_id: Document identifier
            
        Returns:
            List of Image node properties dictionaries
        """
        nodes = []
        images = normalized_doc.get('content', {}).get('images', [])
        source = normalized_doc.get('source', {})
        
        for i, image in enumerate(images):
            image_id = image.get('id', f"image_{document_id}_{i+1}")
            
            # Build node properties according to schema
            base_image_props = {
                'image_id': image_id,
                'caption': image.get('caption', ''),
                'description': image.get('description', ''),
                'path': image.get('path', ''),
                'metadata': {
                    'document_id': document_id,
                    'page_no': image.get('page_no', 0),
                    'image_index': i + 1,
                    'normalized_version': normalized_doc.get('version', 'v1'),
                    'ocr_text': image.get('ocr_text', ''),
                    'markdown': image.get('markdown', '')
                },
                'ocr_text': image.get('ocr_text', ''),
                'created_at': datetime.now().isoformat(),
                'namespace': self.namespace
            }
            
            # Add dimensions if available
            if 'width' in image:
                base_image_props['width'] = image['width']
            if 'height' in image:
                base_image_props['height'] = image['height']
            
            node_props = self._build_node_properties(
                node_type='Image',
                base_props=base_image_props
            )
            
            nodes.append({
                'node_id': image_id,
                'node_type': 'Image',
                'properties': node_props
            })
        
        return nodes
    
    def create_edges(
        self,
        document_nodes: List[Dict[str, Any]],
        table_nodes: List[Dict[str, Any]],
        image_nodes: List[Dict[str, Any]],
        edge_types: Optional[Dict[str, str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Create edges between nodes.
        
        Args:
            document_nodes: List of Document node dictionaries
            table_nodes: List of Table node dictionaries
            image_nodes: List of Image node dictionaries
            edge_types: Optional dict mapping relationships (e.g., {'Document->Table': 'HAS_TABLE'})
            
        Returns:
            List of edge dictionaries
        """
        edges = []
        
        # Use schema edge types if available, otherwise use defaults
        # Schema defines: PARENT_OF, EMBEDS, etc.
        if self.schema:
            # Use schema-defined edge types
            # Document -> Table: PARENT_OF or EMBEDS
            # Document -> Image: PARENT_OF or EMBEDS
            doc_to_table_edge = 'EMBEDS' if 'EMBEDS' in self.schema.edge_types else 'PARENT_OF'
            doc_to_image_edge = 'EMBEDS' if 'EMBEDS' in self.schema.edge_types else 'PARENT_OF'
        else:
            # Fallback to simple edge types
            doc_to_table_edge = 'RELATED_TO'
            doc_to_image_edge = 'RELATED_TO'
        
        # Default edge types (can be overridden by edge_types parameter)
        if edge_types is None:
            edge_types = {}
        
        # Link Document to Tables
        if document_nodes and table_nodes:
            edge_type_name = edge_types.get('Document->Table', doc_to_table_edge)
            for doc_node in document_nodes:
                doc_id = doc_node['node_id']
                for table_node in table_nodes:
                    table_id = table_node['node_id']
                    
                    # Check if table belongs to this document (by page or document_id)
                    table_doc_id = table_node['properties'].get('metadata', {}).get('document_id')
                    if table_doc_id == doc_node['properties'].get('metadata', {}).get('document_id'):
                        edge = self._build_edge_according_to_schema(
                            edge_type_name=edge_type_name,
                            source_node_type='Document',
                            target_node_type='Table',
                            source_id=doc_id,
                            target_id=table_id,
                            additional_props={
                                'namespace': self.namespace,
                                'created_at': datetime.now().isoformat()
                            }
                        )
                        if edge:
                            edges.append(edge)
        
        # Link Document to Images
        if document_nodes and image_nodes:
            edge_type_name = edge_types.get('Document->Image', doc_to_image_edge)
            for doc_node in document_nodes:
                doc_id = doc_node['node_id']
                for image_node in image_nodes:
                    image_id = image_node['node_id']
                    
                    # Check if image belongs to this document
                    image_doc_id = image_node['properties'].get('metadata', {}).get('document_id')
                    doc_doc_id = doc_node['properties'].get('metadata', {}).get('document_id')
                    if image_doc_id == doc_doc_id:
                        edge = self._build_edge_according_to_schema(
                            edge_type_name=edge_type_name,
                            source_node_type='Document',
                            target_node_type='Image',
                            source_id=doc_id,
                            target_id=image_id,
                            additional_props={
                                'namespace': self.namespace,
                                'created_at': datetime.now().isoformat()
                            }
                        )
                        if edge:
                            edges.append(edge)
        
        return edges
    
    def create_all_nodes_and_edges(
        self,
        normalized_doc: Dict[str, Any],
        document_id: str,
        node_types: Optional[List[str]] = None,
        create_per_page: bool = False,
        edge_types: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Create all nodes and edges from normalized JSON.
        
        Args:
            normalized_doc: Normalized JSON document
            document_id: Document identifier
            node_types: List of node types to create (e.g., ['Document', 'Table', 'Image'])
            create_per_page: If True, create one Document node per page
            edge_types: Optional dict mapping relationships
            
        Returns:
            Dictionary with created nodes and edges
        """
        if node_types is None:
            node_types = ['Document', 'Table', 'Image']
        
        all_nodes = []
        document_nodes = []
        table_nodes = []
        image_nodes = []
        
        # #region agent log
        import json
        try:
            with open(r'c:\qgraph\qgraph-app\.cursor\debug.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps({
                    'sessionId': 'debug-session',
                    'runId': 'run1',
                    'hypothesisId': 'B',
                    'location': 'node_creation_from_normalized.py:340',
                    'message': 'create_all_nodes_and_edges entry',
                    'data': {
                        'node_types': node_types,
                        'document_id': document_id,
                        'normalized_doc_has_content': 'content' in normalized_doc if isinstance(normalized_doc, dict) else False,
                        'content_keys': list(normalized_doc.get('content', {}).keys()) if isinstance(normalized_doc, dict) else []
                    },
                    'timestamp': int(__import__('time').time() * 1000)
                }) + '\n')
        except Exception as e:
            pass
        # #endregion
        
        # Create Document nodes
        if 'Document' in node_types:
            document_nodes = self.create_document_nodes(normalized_doc, document_id, create_per_page)
            all_nodes.extend(document_nodes)
            # #region agent log
            try:
                with open(r'c:\qgraph\qgraph-app\.cursor\debug.log', 'a', encoding='utf-8') as f:
                    f.write(json.dumps({
                        'sessionId': 'debug-session',
                        'runId': 'run1',
                        'hypothesisId': 'B',
                        'location': 'node_creation_from_normalized.py:343',
                        'message': 'document_nodes created',
                        'data': {'count': len(document_nodes)},
                        'timestamp': int(__import__('time').time() * 1000)
                    }) + '\n')
            except Exception as e:
                pass
            # #endregion
        
        # Create Table nodes
        if 'Table' in node_types:
            table_nodes = self.create_table_nodes(normalized_doc, document_id)
            all_nodes.extend(table_nodes)
            # #region agent log
            try:
                with open(r'c:\qgraph\qgraph-app\.cursor\debug.log', 'a', encoding='utf-8') as f:
                    f.write(json.dumps({
                        'sessionId': 'debug-session',
                        'runId': 'run1',
                        'hypothesisId': 'B',
                        'location': 'node_creation_from_normalized.py:348',
                        'message': 'table_nodes created',
                        'data': {'count': len(table_nodes)},
                        'timestamp': int(__import__('time').time() * 1000)
                    }) + '\n')
            except Exception as e:
                pass
            # #endregion
        
        # Create Image nodes
        if 'Image' in node_types:
            image_nodes = self.create_image_nodes(normalized_doc, document_id)
            all_nodes.extend(image_nodes)
            # #region agent log
            try:
                with open(r'c:\qgraph\qgraph-app\.cursor\debug.log', 'a', encoding='utf-8') as f:
                    f.write(json.dumps({
                        'sessionId': 'debug-session',
                        'runId': 'run1',
                        'hypothesisId': 'B',
                        'location': 'node_creation_from_normalized.py:353',
                        'message': 'image_nodes created',
                        'data': {'count': len(image_nodes)},
                        'timestamp': int(__import__('time').time() * 1000)
                    }) + '\n')
            except Exception as e:
                pass
            # #endregion
        
        # Create edges
        edges = self.create_edges(document_nodes, table_nodes, image_nodes, edge_types)
        
        # #region agent log
        try:
            with open(r'c:\qgraph\qgraph-app\.cursor\debug.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps({
                    'sessionId': 'debug-session',
                    'runId': 'run1',
                    'hypothesisId': 'B',
                    'location': 'node_creation_from_normalized.py:365',
                    'message': 'create_all_nodes_and_edges result',
                    'data': {
                        'total_nodes': len(all_nodes),
                        'total_edges': len(edges),
                        'document_nodes': len(document_nodes),
                        'table_nodes': len(table_nodes),
                        'image_nodes': len(image_nodes)
                    },
                    'timestamp': int(__import__('time').time() * 1000)
                }) + '\n')
        except Exception as e:
            pass
        # #endregion
        
        return {
            'nodes': all_nodes,
            'edges': edges,
            'document_nodes': document_nodes,
            'table_nodes': table_nodes,
            'image_nodes': image_nodes
        }
    
    def _build_node_properties(self, node_type: str, base_props: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build node properties according to schema definition.
        
        Args:
            node_type: Node type name (e.g., 'Document', 'Table', 'Image')
            base_props: Base properties from normalized document
            
        Returns:
            Properties dictionary conforming to schema
        """
        if not self.schema:
            # No schema - return base properties as-is
            return base_props
        
        # Get node type definition from schema
        node_type_def = self.schema.node_types.get(node_type)
        if not node_type_def:
            logger.warning(f"[SCHEMA] Node type '{node_type}' not found in schema, using base properties")
            return base_props
        
        # Build properties according to schema fields
        schema_props = {}
        schema_fields = node_type_def.fields
        
        for field_name, field_def in schema_fields.items():
            # Check if we have this field in base_props
            if field_name in base_props:
                schema_props[field_name] = base_props[field_name]
            elif field_def.required:
                # Required field missing - use default or raise error
                if field_def.default is not None:
                    schema_props[field_name] = field_def.default
                else:
                    logger.warning(f"[SCHEMA] Required field '{field_name}' missing for {node_type}, using empty value")
                    schema_props[field_name] = self._get_default_value_for_field_type(field_def.field_type)
        
        # Add any additional properties from base_props that aren't in schema (for flexibility)
        for key, value in base_props.items():
            if key not in schema_props:
                schema_props[key] = value
        
        return schema_props
    
    def _get_default_value_for_field_type(self, field_type) -> Any:
        """Get default value for a field type."""
        try:
            from contextsynapse.schema.schema_parser import FieldType
        except ImportError:
            # Fallback if schema module not available
            class FieldType:
                STRING = "string"
                INTEGER = "int"
                FLOAT = "float"
                DATETIME = "datetime"
                JSON = "json"
                ARRAY = "array"
                BOOLEAN = "boolean"
        
        if field_type == FieldType.STRING:
            return ""
        elif field_type == FieldType.INTEGER:
            return 0
        elif field_type == FieldType.FLOAT:
            return 0.0
        elif field_type == FieldType.DATETIME:
            return datetime.now().isoformat()
        elif field_type == FieldType.JSON:
            return {}
        elif field_type == FieldType.ARRAY:
            return []
        elif field_type == FieldType.BOOLEAN:
            return False
        else:
            return None
    
    def _build_edge_according_to_schema(
        self,
        edge_type_name: str,
        source_node_type: str,
        target_node_type: str,
        source_id: str,
        target_id: str,
        additional_props: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Build edge according to schema definition.
        
        Args:
            edge_type_name: Edge type name (e.g., 'PARENT_OF', 'EMBEDS')
            source_node_type: Source node type
            target_node_type: Target node type
            source_id: Source node ID
            target_id: Target node ID
            additional_props: Additional edge properties
            
        Returns:
            Edge dictionary or None if edge type doesn't match schema
        """
        if not self.schema:
            # No schema - create basic edge
            return {
                'id': f"{source_id}_to_{target_id}_{edge_type_name}",
                'source': source_id,
                'target': target_id,
                'edge_type': edge_type_name,
                'properties': additional_props or {}
            }
        
        # Get edge type definition from schema
        edge_type_def = self.schema.edge_types.get(edge_type_name)
        if not edge_type_def:
            logger.warning(f"[SCHEMA] Edge type '{edge_type_name}' not found in schema")
            return None
        
        # Check if source and target node types are allowed for this edge
        from_nodes = edge_type_def.from_nodes
        to_nodes = edge_type_def.to_nodes
        
        # Handle list or single value
        if isinstance(from_nodes, str):
            from_nodes = [from_nodes]
        if isinstance(to_nodes, str):
            to_nodes = [to_nodes]
        
        # Check if source node type is allowed
        if from_nodes and source_node_type not in from_nodes:
            logger.debug(f"[SCHEMA] Source node type '{source_node_type}' not allowed for edge '{edge_type_name}'. Allowed: {from_nodes}")
            return None
        
        # Check if target node type is allowed
        if to_nodes and target_node_type not in to_nodes:
            logger.debug(f"[SCHEMA] Target node type '{target_node_type}' not allowed for edge '{edge_type_name}'. Allowed: {to_nodes}")
            return None
        
        # Build edge properties
        edge_props = {
            'relation': edge_type_def.relation,
            **(additional_props or {})
        }
        
        return {
            'id': f"{source_id}_to_{target_id}_{edge_type_name}",
            'source': source_id,
            'target': target_id,
            'edge_type': edge_type_name,
            'properties': edge_props
        }

