"""
AIQL Validator - Validates semantic correctness of parsed AIQL queries

After syntax is OK, the validator ensures:
- Namespace exists
- Collection exists
- Tables/entities exist
- Columns/properties exist
- Data types are compatible
- User privileges/permissions
"""

from typing import Dict, List, Any, Optional
from enum import Enum

class ValidationError(Exception):
    """Custom exception for validation errors"""
    pass

class NodeType(str, Enum):
    """Valid node types"""
    DOCUMENT = "Document"
    CHUNK = "Chunk"
    ENTITY = "Entity"
    TABLE = "Table"
    IMAGE = "Image"
    SOUND = "Sound"
    # Add more as needed

class ValidatorMode(str, Enum):
    """Validation modes"""
    STRICT = "strict"      # Fail on any error
    PERMISSIVE = "permissive"  # Warn but continue
    AUTO_CREATE = "auto_create"  # Auto-create missing resources

class AIQLValidator:
    """
    Validates AIQL query semantics after syntax parsing
    """
    
    def __init__(self, db_interface=None, mode=ValidatorMode.STRICT):
        """
        Initialize validator
        
        Args:
            db_interface: Interface to AIContextDB for checking resources
            mode: Validation mode (strict, permissive, auto_create)
        """
        self.db_interface = db_interface
        self.mode = mode
        self.validation_errors = []
        self.validation_warnings = []
    
    def validate(self, parsed_ast: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main validation entry point
        
        Args:
            parsed_ast: Parsed AST from parser
            
        Returns:
            Validation result with errors and warnings
        """
        self.validation_errors = []
        self.validation_warnings = []
        
        try:
            query_type = parsed_ast.get('query_type', 'UNKNOWN')
            
            if query_type == 'RAG':
                return self._validate_rag_query(parsed_ast)
            elif query_type == 'PIPELINE':
                return self._validate_pipeline_query(parsed_ast)
            elif query_type == 'RETRIEVE':
                return self._validate_retrieve_query(parsed_ast)
            elif query_type == 'UPDATE':
                return self._validate_update_query(parsed_ast)
            elif query_type == 'HYBRID':
                return self._validate_hybrid_query(parsed_ast)
            elif query_type == 'RUN_PIPELINE':
                return self._validate_run_pipeline_query(parsed_ast)
            elif query_type == 'CREATE_RETRIEVAL_PIPELINE':
                return self._validate_run_pipeline_query(parsed_ast)  # Use same validation as regular pipelines
            elif query_type == 'CREATE_PIPELINE':
                return self._validate_create_pipeline_query(parsed_ast)
            elif query_type in ['CREATE_NODE', 'UPDATE_NODE', 'DELETE_NODE', 'CREATE_EDGE', 'UPDATE_EDGE', 'DELETE_EDGE', 'SELECT', 'CREATE_GRAPH', 'CREATE_NAMESPACE', 'USE_NAMESPACE', 'SHOW_NAMESPACES', 'MATCH_ENTITY', 'MERGE_NODE']:
                # Basic CRUD operations - no specific validation needed
                return {'valid': True, 'errors': [], 'warnings': []}
            elif query_type in ['TRAVERSE', 'PAGERANK', 'SHORTEST_PATH', 'COMMUNITY_DETECTION', 'CENTRALITY', 'COUNT', 'SHOW', 'HOP', 'HYBRID_SEARCH', 'HYBRID_SEARCH_WITH_WEIGHTS', 'HYBRID_SEARCH_WITH_PROFILE', 'SEARCH_QUERY']:
                # Analytics, traversal, and search queries - no specific validation needed
                return {'valid': True, 'errors': [], 'warnings': []}
            else:
                self._error(f"Unknown query type: {query_type}")
                
        except Exception as e:
            self._error(f"Validation failed: {str(e)}")
        
        return {
            'valid': len(self.validation_errors) == 0,
            'errors': self.validation_errors,
            'warnings': self.validation_warnings
        }
    
    def _validate_rag_query(self, ast: Dict[str, Any]) -> Dict[str, Any]:
        """Validate RAG query semantics"""
        query = ast.get('query', '')
        namespace = ast.get('namespace')
        
        # 1. Check namespace exists
        if namespace:
            if not self._namespace_exists(namespace):
                if self.mode == ValidatorMode.AUTO_CREATE:
                    self._warn(f"Namespace '{namespace}' will be auto-created")
                else:
                    self._error(f"Namespace '{namespace}' does not exist")
        
        # 2. Check collection exists (if specified)
        collection = ast.get('collection')
        if collection:
            if not self._collection_exists(namespace, collection):
                if self.mode == ValidatorMode.AUTO_CREATE:
                    self._warn(f"Collection '{collection}' will be auto-created")
                else:
                    self._error(f"Collection '{collection}' does not exist in namespace '{namespace}'")
        
        # 3. Check query is not empty
        if not query or not query.strip():
            self._error("RAG query cannot be empty")
        
        return {
            'valid': len(self.validation_errors) == 0,
            'errors': self.validation_errors,
            'warnings': self.validation_warnings
        }
    
    def _validate_pipeline_query(self, ast: Dict[str, Any]) -> Dict[str, Any]:
        """Validate CREATE PIPELINE query semantics"""
        pipeline_name = ast.get('pipeline_name', '')
        namespace = ast.get('namespace')
        source_collection = ast.get('source_collection')
        target_collection = ast.get('target_collection')
        stages = ast.get('stages', [])
        
        # 1. Check namespace exists
        if namespace:
            if not self._namespace_exists(namespace):
                if self.mode == ValidatorMode.AUTO_CREATE:
                    self._warn(f"Namespace '{namespace}' will be auto-created")
                else:
                    self._error(f"Namespace '{namespace}' does not exist")
        
        # 2. Check source collection exists (if specified)
        if source_collection:
            if not self._collection_exists(namespace, source_collection):
                if self.mode == ValidatorMode.AUTO_CREATE:
                    self._warn(f"Source collection '{source_collection}' will be auto-created")
                else:
                    self._error(f"Source collection '{source_collection}' does not exist")
        
        # 3. Validate each stage
        for stage in stages:
            stage_type = stage.get('type')
            
            if stage_type == 'LOAD':
                self._validate_load_stage(stage, namespace, source_collection)
            elif stage_type == 'EXTRACT':
                self._validate_extract_stage(stage)
            elif stage_type == 'CHUNK':
                self._validate_chunk_stage(stage)
            elif stage_type == 'EMBED':
                self._validate_embed_stage(stage)
            elif stage_type == 'INDEX':
                self._validate_index_stage(stage)
            elif stage_type == 'ENTITY_EXTRACT':
                self._validate_entity_stage(stage)
            elif stage_type == 'RELATIONSHIP_EXTRACT':
                self._validate_relationship_stage(stage)
        
        # 4. Validate node types
        node_types = ast.get('node_types', [])
        for node_type in node_types:
            if not self._is_valid_node_type(node_type):
                self._warn(f"Unknown node type '{node_type}', will validate at runtime")
        
        return {
            'valid': len(self.validation_errors) == 0,
            'errors': self.validation_errors,
            'warnings': self.validation_warnings
        }
    
    def _validate_retrieve_query(self, ast: Dict[str, Any]) -> Dict[str, Any]:
        """Validate RETRIEVE query semantics"""
        namespace = ast.get('namespace')
        collection = ast.get('collection')
        strategy = ast.get('strategy')
        profile = ast.get('profile')
        
        # 1. Check namespace exists
        if namespace and not self._namespace_exists(namespace):
            self._error(f"Namespace '{namespace}' does not exist")
        
        # 2. Check collection exists
        if collection and not self._collection_exists(namespace, collection):
            self._error(f"Collection '{collection}' does not exist")
        
        # 3. Validate profile exists
        if profile:
            if not self._profile_exists(profile):
                self._error(f"Profile '{profile}' does not exist")
        
        # 4. Validate strategy is supported
        valid_strategies = ['hybrid', 'vector', 'graph', 'combined']
        if strategy and strategy not in valid_strategies:
            self._warn(f"Strategy '{strategy}' is not a standard strategy")
        
        return {
            'valid': len(self.validation_errors) == 0,
            'errors': self.validation_errors,
            'warnings': self.validation_warnings
        }
    
    def _validate_update_query(self, ast: Dict[str, Any]) -> Dict[str, Any]:
        """Validate UPDATE query semantics"""
        namespace = ast.get('namespace')
        collection = ast.get('collection')
        
        # 1. Check namespace exists
        if namespace and not self._namespace_exists(namespace):
            self._error(f"Namespace '{namespace}' does not exist")
        
        # 2. Check collection exists
        if collection and not self._collection_exists(namespace, collection):
            self._error(f"Collection '{collection}' does not exist")
        
        # 3. Validate operation type
        operation = ast.get('operation', '')
        valid_operations = ['insert', 'update', 'delete', 'upsert']
        if operation and operation not in valid_operations:
            self._error(f"Invalid operation type '{operation}'")
        
        return {
            'valid': len(self.validation_errors) == 0,
            'errors': self.validation_errors,
            'warnings': self.validation_warnings
        }
    
    def _validate_hybrid_query(self, ast: Dict[str, Any]) -> Dict[str, Any]:
        """Validate HYBRID search query semantics"""
        namespace = ast.get('namespace')
        collection = ast.get('collection')
        
        # Validate using RAG + RETRIEVE validators
        rag_result = self._validate_rag_query(ast)
        retrieve_result = self._validate_retrieve_query(ast)
        
        return {
            'valid': rag_result['valid'] and retrieve_result['valid'],
            'errors': rag_result.get('errors', []) + retrieve_result.get('errors', []),
            'warnings': rag_result.get('warnings', []) + retrieve_result.get('warnings', [])
        }
    
    def _validate_create_pipeline_query(self, ast: Dict[str, Any]) -> Dict[str, Any]:
        """Validate CREATE PIPELINE query."""
        pipeline_name = ast.get('pipeline_name', '')
        namespace = ast.get('namespace', '')
        
        if not pipeline_name:
            self._error("Pipeline name is required")
        
        if not namespace:
            self._error("Namespace is required")
        
        return {
            'valid': len(self.validation_errors) == 0,
            'errors': self.validation_errors,
            'warnings': self.validation_warnings
        }
    
    def _validate_run_pipeline_query(self, ast: Dict[str, Any]) -> Dict[str, Any]:
        """Validate RUN PIPELINE query semantics"""
        # Extract pipeline name from AST
        pipeline_name = ast.get('pipeline_name')
        
        if not pipeline_name:
            # Don't error if pipeline_name is not found - it might be in AST nodes
            self._warn("Pipeline name not found in AST top level")
        
        # Validate that the pipeline exists (could be expanded)
        if pipeline_name and len(pipeline_name) < 3:
            self._warn(f"Pipeline name '{pipeline_name}' seems too short")
        
        return {
            'valid': len(self.validation_errors) == 0,
            'errors': self.validation_errors,
            'warnings': self.validation_warnings
        }
    
    def _validate_load_stage(self, stage: Dict[str, Any], namespace: str, collection: str):
        """Validate LOAD stage semantics"""
        file_path = stage.get('file_path', '')
        
        # Check file exists (basic validation)
        if file_path and not file_path.startswith(('http://', 'https://', '/')):
            # Local file validation
            import os
            if not os.path.exists(file_path):
                self._warn(f"File '{file_path}' may not exist (will check at runtime)")
    
    def _validate_extract_stage(self, stage: Dict[str, Any]):
        """Validate EXTRACT stage semantics"""
        # Validate extract modes
        modes = stage.get('modes', [])
        valid_modes = ['text', 'table', 'image', 'audio', 'video']
        for mode in modes:
            if mode not in valid_modes:
                self._warn(f"Extract mode '{mode}' may not be supported")
    
    def _validate_chunk_stage(self, stage: Dict[str, Any]):
        """Validate CHUNK stage semantics"""
        max_tokens = stage.get('max_tokens', 0)
        if max_tokens <= 0:
            self._warn("max_tokens should be greater than 0")
    
    def _validate_embed_stage(self, stage: Dict[str, Any]):
        """Validate EMBED stage semantics"""
        model = stage.get('model', '')
        if not model:
            self._error("Embedding model is required")
    
    def _validate_index_stage(self, stage: Dict[str, Any]):
        """Validate INDEX stage semantics"""
        index_types = stage.get('index_types', [])
        valid_types = ['bm25', 'vector', 'graph', 'hybrid']
        for idx_type in index_types:
            if idx_type not in valid_types:
                self._warn(f"Index type '{idx_type}' may not be supported")
    
    def _validate_entity_stage(self, stage: Dict[str, Any]):
        """Validate ENTITY EXTRACT stage semantics"""
        llm_model = stage.get('llm_model', '')
        if not llm_model:
            self._error("LLM model is required for entity extraction")
    
    def _validate_relationship_stage(self, stage: Dict[str, Any]):
        """Validate RELATIONSHIP EXTRACT stage semantics"""
        llm_model = stage.get('llm_model', '')
        if not llm_model:
            self._error("LLM model is required for relationship extraction")
    
    def _namespace_exists(self, namespace: str) -> bool:
        """Check if namespace exists"""
        if not self.db_interface:
            return True  # Skip validation if no DB interface
        return self.db_interface.namespace_exists(namespace)
    
    def _collection_exists(self, namespace: str, collection: str) -> bool:
        """Check if collection exists in namespace"""
        if not self.db_interface:
            return True  # Skip validation if no DB interface
        return self.db_interface.collection_exists(namespace, collection)
    
    def _profile_exists(self, profile: str) -> bool:
        """Check if profile exists"""
        if not self.db_interface:
            return True  # Skip validation if no DB interface
        return self.db_interface.profile_exists(profile)
    
    def _is_valid_node_type(self, node_type: str) -> bool:
        """Check if node type is valid"""
        try:
            return NodeType(node_type) is not None
        except ValueError:
            return False
    
    def _error(self, message: str):
        """Add validation error"""
        self.validation_errors.append(message)
        if self.mode == ValidatorMode.STRICT:
            raise ValidationError(message)
    
    def _warn(self, message: str):
        """Add validation warning"""
        self.validation_warnings.append(message)
    
    def get_dependencies(self, ast: Dict[str, Any]) -> List[str]:
        """
        Get list of dependencies for this query
        (e.g., namespaces, collections, tables, columns)
        """
        dependencies = []
        
        namespace = ast.get('namespace')
        if namespace:
            dependencies.append(f"namespace:{namespace}")
        
        collection = ast.get('collection')
        if collection:
            dependencies.append(f"collection:{namespace}:{collection}")
        
        # Add more dependencies as needed
        # e.g., tables, columns, profiles, etc.
        
        return dependencies


# Validator factory for different modes
def create_validator(mode: str = "strict", db_interface=None) -> AIQLValidator:
    """
    Create validator with specified mode
    
    Args:
        mode: 'strict', 'permissive', or 'auto_create'
        db_interface: Optional DB interface for resource checking
        
    Returns:
        Configured AIQLValidator instance
    """
    validator_mode = ValidatorMode(mode)
    return AIQLValidator(db_interface=db_interface, mode=validator_mode)


# Example usage:
# validator = create_validator(mode="strict", db_interface=db)
# result = validator.validate(parsed_ast)

