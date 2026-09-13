"""
AIQL Parser Implementation
Using the EBNF grammar to build AST nodes for execution planning.
"""

import lark
from lark import Tree
from typing import Dict, List, Any, Optional, Union
from dataclasses import dataclass
from enum import Enum
import re
import hashlib
import logging
from functools import lru_cache
from threading import Lock

logger = logging.getLogger(__name__)

# Global parser cache (shared across all instances for better performance)
_global_parse_cache: Dict[str, Dict[str, Any]] = {}
_cache_lock = Lock()
_max_cache_size = 5000  # Increased cache size

class AIQLNodeType(Enum):
    """AST node types for AIQL."""
    VARIABLE_DECL = "VARIABLE_DECL"
    CREATE_GRAPH = "CREATE_GRAPH"
    DROP_GRAPH = "DROP_GRAPH"
    USE_GRAPH = "USE_GRAPH"
    CREATE_NODE = "CREATE_NODE"
    CREATE_EDGE = "CREATE_EDGE"
    RETRIEVE = "RETRIEVE"
    FIND_BY_UUID = "FIND_BY_UUID"
    COMMIT = "COMMIT"
    BEGIN_TRANSACTION = "BEGIN_TRANSACTION"
    COMMIT_TRANSACTION = "COMMIT_TRANSACTION"
    ROLLBACK_TRANSACTION = "ROLLBACK_TRANSACTION"
    DELETE_NODE = "DELETE_NODE"
    DELETE_EDGE = "DELETE_EDGE"
    UPDATE_NODE = "UPDATE_NODE"
    UPDATE_EDGE = "UPDATE_EDGE"
    LOAD_DOCUMENT = "LOAD_DOCUMENT"
    LOAD_FOLDER = "LOAD_FOLDER"
    LOAD_CSV = "LOAD_CSV"
    LOAD_JSON = "LOAD_JSON"
    LOAD_XML = "LOAD_XML"
    LOAD_SQL = "LOAD_SQL"
    LOAD_API = "LOAD_API"
    LOAD_EXCEL = "LOAD_EXCEL"
    CLASSIFY_FILE = "CLASSIFY_FILE"
    CHUNK = "CHUNK"
    EXTRACT = "EXTRACT"
    GENERATE = "GENERATE"
    GENERATE_WITH_MODEL = "GENERATE_WITH_MODEL"
    GENERATE_WITH_PROMPT = "GENERATE_WITH_PROMPT"
    ADD_EMBEDDINGS = "ADD EMBEDDINGS"
    SPARSE_MATCH = "SPARSE MATCH"
    DENSE_MATCH = "DENSE MATCH"
    HYBRID_MATCH = "HYBRID MATCH"
    RERANK = "RERANK"
    TRAVERSE = "TRAVERSE"
    WHERE = "WHERE"
    SELECT = "SELECT"
    COUNT = "COUNT"
    SET_MODE = "SET_MODE"
    SHORTEST_PATH = "SHORTEST_PATH"
    CENTRALITY = "CENTRALITY"
    PARALLEL = "PARALLEL"
    CREATE_INDEX = "CREATE_INDEX"
    SHARD_GRAPH = "SHARD_GRAPH"
    MATCH_ENTITY = "MATCH_ENTITY"
    MATCH = "MATCH"  # Support for "MATCH NODE" syntax
    LIST_ENTITY_TYPES = "LIST_ENTITY_TYPES"
    SHOW = "SHOW"
    SHOW_NAMESPACES = "SHOW_NAMESPACES"
    SHOW_GRAPHS = "SHOW_GRAPHS"
    SHOW_CURRENT_GRAPH = "SHOW_CURRENT_GRAPH"
    DESCRIBE = "DESCRIBE"
    MODEL_DECL = "MODEL_DECL"
    PROMPT_DECL = "PROMPT_DECL"
    CREATE_NAMESPACE = "CREATE_NAMESPACE"
    USE_NAMESPACE = "USE_NAMESPACE"
    SEARCH_QUERY = "SEARCH_QUERY"
    GRANT = "GRANT"
    DEFINE_NODE_TYPE = "DEFINE_NODE_TYPE"
    DEFINE_EDGE_TYPE = "DEFINE_EDGE_TYPE"
    CREATE_VIEW = "CREATE_VIEW"
    MATERIALIZE_VIEW = "MATERIALIZE_VIEW"
    TEMPORAL = "TEMPORAL"
    TEMPORAL_QUERY = "TEMPORAL_QUERY"
    DIFF_NODE = "DIFF_NODE"
    DIFF_EDGES = "DIFF_EDGES"
    READ_URL = "READ_URL"
    READ_DOMAIN = "READ_DOMAIN"
    EXTRACT_FROM_RAW = "EXTRACT_FROM_RAW"
    EXTRACT_ENTITIES_TOP = "EXTRACT_ENTITIES_TOP"
    CREATE_EVALSET = "CREATE_EVALSET"
    ABTEST_RAG = "ABTEST_RAG"
    ATTACH_POLICY = "ATTACH_POLICY"
    SET_GUARDRAILS = "SET_GUARDRAILS"
    PRE_GUARD = "PRE_GUARD"
    POST_GUARD = "POST_GUARD"
    CREATE_FINE_TUNE_DATASET = "CREATE_FINE_TUNE_DATASET"
    FINE_TUNE_MODEL = "FINE_TUNE_MODEL"
    REGISTER_MODEL = "REGISTER_MODEL"
    PROMOTE_MODEL = "PROMOTE_MODEL"
    ROLLBACK_MODEL = "ROLLBACK_MODEL"
    MERGE_RESULTS = "MERGE_RESULTS"
    REASON_ON = "REASON_ON"
    RAG_QUERY = "RAG_QUERY"
    CREATE_STREAM = "CREATE_STREAM"
    DEFINE_FUNCTION = "DEFINE_FUNCTION"
    TRANSACTION = "TRANSACTION"
    EXPLAIN = "EXPLAIN"
    COMMUNITY_DETECTION = "COMMUNITY_DETECTION"
    PAGERANK = "PAGERANK"
    AGGREGATION = "AGGREGATION"
    GLOBAL_STRATEGY = "GLOBAL_STRATEGY"
    # Enhanced RAG Pipeline Node Types
    RAG_PIPELINE = "RAG_PIPELINE"
    FILE_READER = "FILE_READER"
    MULTIMODAL_EXTRACTION = "MULTIMODAL_EXTRACTION"
    SMART_CHUNKING = "SMART_CHUNKING"
    RETRIEVAL_SYSTEM = "RETRIEVAL_SYSTEM"
    EVALUATION_SYSTEM = "EVALUATION_SYSTEM"
    PIPELINE_MANAGEMENT = "PIPELINE_MANAGEMENT"
    CREATE_PIPELINE = "CREATE_PIPELINE"
    RUN_PIPELINE = "RUN_PIPELINE"
    HYBRID_SEARCH = "HYBRID_SEARCH"
    HYBRID_SEARCH_WITH_WEIGHTS = "HYBRID_SEARCH_WITH_WEIGHTS"
    HYBRID_SEARCH_WITH_PROFILE = "HYBRID_SEARCH_WITH_PROFILE"
    RAG_GENERATE = "RAG_GENERATE"
    RAG_QUERY_ONE_SHOT = "RAG_QUERY_ONE_SHOT"
    EVALUATE_RAG = "EVALUATE_RAG"
    INSERT_INTO = "INSERT_INTO"
    UPDATE_PIPELINE = "UPDATE_PIPELINE"
    FOR_LOOP = "FOR_LOOP"
    MERGE_NODE = "MERGE_NODE"
    CREATE_COLLECTION = "CREATE_COLLECTION"
    NEIGHBORS = "NEIGHBORS"
    CREATE_SUBGRAPH = "CREATE_SUBGRAPH"
    VERIFY_BLOCKCHAIN = "VERIFY_BLOCKCHAIN"
    GET_BLOCK = "GET_BLOCK"
    GET_BLOCKCHAIN_LENGTH = "GET_BLOCKCHAIN_LENGTH"
    GET_LATEST_BLOCK = "GET_LATEST_BLOCK"
    GET_AUDIT_TRAIL = "GET_AUDIT_TRAIL"
    GET_MERKLE_ROOT = "GET_MERKLE_ROOT"
    GET_BLOCKS_RANGE = "GET_BLOCKS_RANGE"
    VERIFY_BLOCK = "VERIFY_BLOCK"

@dataclass
class AIQLNode:
    """AST node for AIQL queries."""
    node_type: AIQLNodeType
    parameters: Dict[str, Any]
    children: List['AIQLNode'] = None
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.children is None:
            self.children = []
        if self.metadata is None:
            self.metadata = {}

@dataclass
class TraverseHop:
    """Represents a single hop in a traversal."""
    from_node: str
    from_alias: Optional[str]
    edge_type: str
    edge_alias: Optional[str]
    to_node: str
    to_alias: Optional[str]
    direction: str = "OUTGOING"
    quantifier: Optional[Dict[str, Any]] = None

@dataclass
class VariableDeclaration:
    """Represents a variable declaration."""
    name: str
    value: Any
    store_result: bool = False

class AIQLParser:
    """Parser for AIQL queries using Lark."""
    
    # Class-level parser instance (shared across all instances)
    _shared_parser = None
    _parser_lock = Lock()
    
    def __init__(self):
        # Lazy parser init: the Earley parser takes ~13s to compile from grammar.
        # We defer compilation to first parse() call so that import + init is fast.
        # Once built, the parser is shared across all AIQLParser instances.
        self.variables: Dict[str, Any] = {}

    @property
    def parser(self):
        """Lazy-init the Lark parser on first use."""
        if AIQLParser._shared_parser is None:
            with AIQLParser._parser_lock:
                if AIQLParser._shared_parser is None:
                    from ..grammar import AIQL_GRAMMAR
                    AIQLParser._shared_parser = lark.Lark(AIQL_GRAMMAR, parser='earley')
        return AIQLParser._shared_parser

    # ── Regex fast-path patterns (handles ~90% of queries without Earley) ──
    _FAST_PATTERNS = None

    @classmethod
    def _get_fast_patterns(cls):
        if cls._FAST_PATTERNS is None:
            import re
            cls._FAST_PATTERNS = [
                (re.compile(r'^\s*CREATE\s+GRAPH\s+(\w+)\s*$', re.I), 'CREATE_GRAPH'),
                (re.compile(r'^\s*USE\s+GRAPH\s+(\w+)\s*$', re.I), 'USE_GRAPH'),
                (re.compile(r'^\s*SHOW\s+GRAPHS?\s*$', re.I), 'SHOW_GRAPHS'),
                (re.compile(r'^\s*CREATE\s+NODE\s+(\w+)\s*\{(.+)\}\s*$', re.I | re.DOTALL), 'CREATE_NODE'),
                (re.compile(r'^\s*SELECT\s+\*\s+FROM\s+(\w+)(\s+WHERE\s+(.+))?\s*$', re.I | re.DOTALL), 'SELECT'),
                (re.compile(r'^\s*MATCH\s+NODE\s+(\w+)\s+WHERE\s+(.+)\s*$', re.I | re.DOTALL), 'MATCH'),
                (re.compile(r'^\s*CREATE\s+EDGE\s+(\w+)\s+FROM\s+(\w+)\s+WHERE\s+(.+?)\s+TO\s+(\w+)\s+WHERE\s+(.+?)(\s*\{(.+)\})?\s*$', re.I | re.DOTALL), 'CREATE_EDGE'),
                (re.compile(r'^\s*TRAVERSE\s+FROM\s+(\w+)\s+WHERE\s+(.+?)\s*(?:DIRECTION\s+(\w+))?\s*(?:EDGE_TYPE\s+(\w+))?\s*(?:MAX_DEPTH\s+(\d+))?\s*$', re.I | re.DOTALL), 'TRAVERSE'),
                (re.compile(r'^\s*DELETE\s+NODE\s+(\w+)\s+WHERE\s+(.+)\s*$', re.I | re.DOTALL), 'DELETE_NODE'),
                (re.compile(r'^\s*SHOW\s+STATS?\s*$', re.I), 'SHOW_STATS'),
                (re.compile(r'^\s*SHOW\s+NAMESPACES?\s*$', re.I), 'SHOW_NAMESPACES'),
                (re.compile(r'^\s*USE\s+NAMESPACE\s+(\w+)\s*$', re.I), 'USE_NAMESPACE'),
                (re.compile(r'^\s*CREATE\s+NAMESPACE\s+(\w+)\s*$', re.I), 'CREATE_NAMESPACE'),
            ]
        return cls._FAST_PATTERNS

    def _fast_parse(self, query: str) -> Optional[Dict[str, Any]]:
        """Try regex fast-path for common queries. Returns None if not matched."""
        import re
        for pattern, qtype in self._get_fast_patterns():
            m = pattern.match(query)
            if not m:
                continue
            groups = m.groups()

            if qtype == 'CREATE_GRAPH':
                return {'query_type': qtype, 'ast': {'type': 'create_graph', 'graph_name': groups[0]}, 'ast_nodes': {'type': 'create_graph', 'graph_name': groups[0]}, 'variables': {}, 'success': True, 'error': None}
            elif qtype == 'USE_GRAPH':
                return {'query_type': qtype, 'ast': {'type': 'use_graph', 'graph_name': groups[0]}, 'ast_nodes': {'type': 'use_graph', 'graph_name': groups[0]}, 'variables': {}, 'success': True, 'error': None}
            elif qtype in ('SHOW_GRAPHS', 'SHOW_STATS', 'SHOW_NAMESPACES'):
                return {'query_type': qtype, 'ast': {'type': qtype.lower()}, 'ast_nodes': {'type': qtype.lower()}, 'variables': {}, 'success': True, 'error': None}
            elif qtype == 'CREATE_NODE':
                props = self._parse_props_fast(groups[1])
                return {'query_type': qtype, 'ast': {'type': 'create_node', 'node_type': groups[0], 'properties': props}, 'ast_nodes': {'type': 'create_node', 'node_type': groups[0], 'properties': props}, 'variables': {}, 'success': True, 'error': None}
            elif qtype == 'SELECT':
                ast = {'type': 'select', 'node_type': groups[0], 'select_fields': ['*']}
                if groups[2]:  # WHERE clause
                    ast['where'] = self._parse_where_fast(groups[2])
                return {'query_type': qtype, 'ast': ast, 'ast_nodes': ast, 'variables': {}, 'success': True, 'error': None}
            elif qtype == 'MATCH':
                ast = {'type': 'match', 'node_type': groups[0], 'where': self._parse_where_fast(groups[1])}
                return {'query_type': qtype, 'ast': ast, 'ast_nodes': ast, 'variables': {}, 'success': True, 'error': None}
            elif qtype == 'CREATE_EDGE':
                props = self._parse_props_fast(groups[6]) if groups[6] else {}
                ast = {'type': 'create_edge', 'edge_type': groups[0],
                       'from_type': groups[1], 'from_where': self._parse_where_fast(groups[2]),
                       'to_type': groups[3], 'to_where': self._parse_where_fast(groups[4]),
                       'properties': props}
                return {'query_type': qtype, 'ast': ast, 'ast_nodes': ast, 'variables': {}, 'success': True, 'error': None}
            elif qtype == 'TRAVERSE':
                ast = {'type': 'traverse', 'from_type': groups[0], 'from_where': self._parse_where_fast(groups[1]),
                       'direction': groups[2] or 'out', 'edge_type': groups[3] or None,
                       'max_depth': int(groups[4]) if groups[4] else 3}
                return {'query_type': qtype, 'ast': ast, 'ast_nodes': ast, 'variables': {}, 'success': True, 'error': None}
            elif qtype == 'DELETE_NODE':
                ast = {'type': 'delete_node', 'node_type': groups[0], 'where': self._parse_where_fast(groups[1])}
                return {'query_type': qtype, 'ast': ast, 'ast_nodes': ast, 'variables': {}, 'success': True, 'error': None}
            elif qtype in ('USE_NAMESPACE', 'CREATE_NAMESPACE'):
                return {'query_type': qtype, 'ast': {'type': qtype.lower(), 'namespace': groups[0]}, 'ast_nodes': {'type': qtype.lower(), 'namespace': groups[0]}, 'variables': {}, 'success': True, 'error': None}
        return None

    def _parse_props_fast(self, raw: str) -> Dict[str, Any]:
        """Parse {key: "value", key2: "value2"} quickly."""
        import re
        props = {}
        for m in re.finditer(r'(\w+)\s*:\s*"([^"]*)"', raw):
            props[m.group(1)] = m.group(2)
        for m in re.finditer(r'(\w+)\s*:\s*(\d+(?:\.\d+)?)\b', raw):
            if m.group(1) not in props:
                val = m.group(2)
                props[m.group(1)] = float(val) if '.' in val else int(val)
        return props

    def _parse_where_fast(self, raw: str) -> Dict[str, Any]:
        """Parse simple WHERE clauses: field = "value" or field = number."""
        import re
        conditions = {}
        for m in re.finditer(r'(\w+)\s*=\s*"([^"]*)"', raw):
            conditions[m.group(1)] = m.group(2)
        for m in re.finditer(r'(\w+)\s*=\s*(\d+(?:\.\d+)?)\b', raw):
            if m.group(1) not in conditions:
                val = m.group(2)
                conditions[m.group(1)] = float(val) if '.' in val else int(val)
        return conditions

    def parse(self, query: str) -> Dict[str, Any]:
        """Parse a AIQL query into AST with optimized caching for performance."""
        try:
            # Store current query for context
            self._current_query = query

            # Reset variables for each new query parse
            self.variables = {}

            # Clean query (preserve strings)
            cleaned_query = self._clean_query(query)

            # Create cache key from cleaned query (more reliable)
            cache_key = hashlib.sha256(cleaned_query.encode()).hexdigest()

            # Check global cache first (fast path)
            with _cache_lock:
                if cache_key in _global_parse_cache:
                    cached_result = _global_parse_cache[cache_key].copy()
                    # Reset variables for this instance
                    cached_result['variables'] = {}
                    return cached_result

            # ── Regex fast-path: handles common queries without Earley ──
            fast_result = self._fast_parse(cleaned_query)
            if fast_result is not None:
                with _cache_lock:
                    _global_parse_cache[cache_key] = fast_result.copy()
                return fast_result

            # ── Earley fallback for complex queries (pipelines, aggregations, etc.) ──
            tree = self.parser.parse(cleaned_query)
            
            # Convert to AST
            ast = self._tree_to_ast(tree)
            
            # Detect query type from AST
            query_type = self._detect_query_type(ast)
            
            result = {
                'ast': ast,
                'ast_nodes': ast,  # Also include ast_nodes for compatibility
                'variables': self.variables.copy(),
                'success': True,
                'error': None,
                'query_type': query_type
            }
            
            # Cache the result in global cache
            with _cache_lock:
                # Limit cache size (LRU eviction)
                if len(_global_parse_cache) >= _max_cache_size:
                    # Remove oldest entry (simple FIFO)
                    oldest_key = next(iter(_global_parse_cache))
                    del _global_parse_cache[oldest_key]
                
                # Store result (deep copy to avoid mutation issues)
                _global_parse_cache[cache_key] = {
                    'ast': ast,
                    'ast_nodes': ast,
                    'variables': {},  # Don't cache variables
                    'success': True,
                    'error': None,
                    'query_type': query_type
                }
            
            return result
        except Exception as e:
            return {
                'ast': None,
                'variables': {},
                'success': False,
                'error': str(e)
            }
    
    def _get_cached_parse_result(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """Get cached parse result (deprecated - using global cache now)."""
        # Legacy method for backward compatibility
        with _cache_lock:
            if cache_key in _global_parse_cache:
                return _global_parse_cache[cache_key].copy()
        return None
    
    def _cache_parse_result(self, cache_key: str, result: Dict[str, Any]) -> None:
        """Cache parse result (deprecated - using global cache now)."""
        # Legacy method for backward compatibility
        with _cache_lock:
            if len(_global_parse_cache) >= _max_cache_size:
                oldest_key = next(iter(_global_parse_cache))
                del _global_parse_cache[oldest_key]
            _global_parse_cache[cache_key] = result.copy()
    
    @classmethod
    def clear_cache(cls):
        """Clear the global parser cache."""
        with _cache_lock:
            _global_parse_cache.clear()
    
    @classmethod
    def get_cache_stats(cls) -> Dict[str, Any]:
        """Get parser cache statistics."""
        with _cache_lock:
            return {
                'cache_size': len(_global_parse_cache),
                'max_cache_size': _max_cache_size,
                'cache_hit_rate': 'N/A'  # Would need hit/miss tracking
            }
    
    def _detect_query_type(self, ast: List[AIQLNode]) -> str:
        """Detect query type from AST nodes."""
        if not ast or len(ast) == 0:
            return 'UNKNOWN'
        
        # Check the first node type to determine query type
        first_node = ast[0]
        
        if isinstance(first_node, AIQLNode):
            node_type = first_node.node_type
            
            # Map node type to query type
            if node_type == AIQLNodeType.CREATE_NODE:
                return 'CREATE_NODE'
            elif node_type == AIQLNodeType.UPDATE_NODE:
                return 'UPDATE_NODE'
            elif node_type == AIQLNodeType.DELETE_NODE:
                return 'DELETE_NODE'
            elif node_type == AIQLNodeType.SELECT:
                return 'SELECT'
            elif node_type == AIQLNodeType.CREATE_EDGE:
                return 'CREATE_EDGE'
            elif node_type == AIQLNodeType.CREATE_GRAPH:
                return 'CREATE_GRAPH'
            elif node_type == AIQLNodeType.UPDATE_EDGE:
                return 'UPDATE_EDGE'
            elif node_type == AIQLNodeType.DELETE_EDGE:
                return 'DELETE_EDGE'
            elif node_type == AIQLNodeType.CREATE_NAMESPACE:
                return 'CREATE_NAMESPACE'
            elif node_type == AIQLNodeType.USE_NAMESPACE:
                return 'USE_NAMESPACE'
            elif node_type == AIQLNodeType.USE_GRAPH:
                return 'USE_GRAPH'
            elif node_type == AIQLNodeType.SHOW_NAMESPACES:
                return 'SHOW_NAMESPACES'
            elif node_type == AIQLNodeType.SHOW_GRAPHS:
                return 'SHOW_GRAPHS'
            elif node_type == AIQLNodeType.SHOW_CURRENT_GRAPH:
                return 'SHOW_CURRENT_GRAPH'
            elif node_type == AIQLNodeType.MATCH:
                return 'MATCH'
            elif node_type == AIQLNodeType.MATCH_ENTITY:
                return 'MATCH_ENTITY'
            elif node_type == AIQLNodeType.CREATE_PIPELINE:
                return 'CREATE_PIPELINE'
            elif node_type == AIQLNodeType.RUN_PIPELINE:
                return 'RUN_PIPELINE'
            elif node_type == AIQLNodeType.SET_MODE:
                return 'SET_MODE'
            elif node_type == AIQLNodeType.SEARCH_QUERY:
                return 'SEARCH_QUERY'
            elif node_type == AIQLNodeType.HYBRID_SEARCH:
                return 'HYBRID_SEARCH'
            elif node_type == AIQLNodeType.HYBRID_SEARCH_WITH_WEIGHTS:
                return 'HYBRID_SEARCH_WITH_WEIGHTS'
            elif node_type == AIQLNodeType.HYBRID_SEARCH_WITH_PROFILE:
                return 'HYBRID_SEARCH_WITH_PROFILE'
            elif node_type == AIQLNodeType.TRAVERSE:
                return 'TRAVERSE'
            elif node_type == AIQLNodeType.PAGERANK:
                return 'PAGERANK'
            elif node_type == AIQLNodeType.SHORTEST_PATH:
                return 'SHORTEST_PATH'
            elif node_type == AIQLNodeType.COMMUNITY_DETECTION:
                return 'COMMUNITY_DETECTION'
            elif node_type == AIQLNodeType.CENTRALITY:
                return 'CENTRALITY'
            elif node_type == AIQLNodeType.COUNT:
                return 'COUNT'
            elif node_type == AIQLNodeType.SHOW:
                return 'SHOW'
            elif node_type == AIQLNodeType.RAG_GENERATE:
                return 'RAG_GENERATE'
            elif node_type == AIQLNodeType.RAG_QUERY_ONE_SHOT:
                return 'RAG_QUERY_ONE_SHOT'
            elif node_type == AIQLNodeType.EVALUATE_RAG:
                return 'EVALUATE_RAG'
            elif node_type == AIQLNodeType.INSERT_INTO:
                return 'INSERT_INTO'
            elif node_type == AIQLNodeType.UPDATE_PIPELINE:
                return 'UPDATE_PIPELINE'
            elif node_type == AIQLNodeType.FOR_LOOP:
                return 'FOR_LOOP'
            elif node_type == AIQLNodeType.CREATE_COLLECTION:
                return 'CREATE_COLLECTION'
            elif node_type == AIQLNodeType.VERIFY_BLOCKCHAIN:
                return 'VERIFY_BLOCKCHAIN'
            elif node_type == AIQLNodeType.GET_BLOCK:
                return 'GET_BLOCK'
            elif node_type == AIQLNodeType.GET_BLOCKCHAIN_LENGTH:
                return 'GET_BLOCKCHAIN_LENGTH'
            elif node_type == AIQLNodeType.GET_LATEST_BLOCK:
                return 'GET_LATEST_BLOCK'
            elif node_type == AIQLNodeType.GET_AUDIT_TRAIL:
                return 'GET_AUDIT_TRAIL'
            elif node_type == AIQLNodeType.GET_MERKLE_ROOT:
                return 'GET_MERKLE_ROOT'
            elif node_type == AIQLNodeType.GET_BLOCKS_RANGE:
                return 'GET_BLOCKS_RANGE'
            elif node_type == AIQLNodeType.VERIFY_BLOCK:
                return 'VERIFY_BLOCK'
            else:
                return 'UNKNOWN'
        
        return 'UNKNOWN'  # Ignore cache errors
    
    def _clean_query(self, query: str) -> str:
        """Clean and normalize the query."""
        # Remove comments (but preserve strings that might contain //)
        # Split by lines and process carefully
        lines = query.split('\n')
        cleaned_lines = []
        for line in lines:
            # Only remove comments that are not inside strings
            if '"' not in line and "'" not in line:
                line = re.sub(r'//.*$', '', line)
            cleaned_lines.append(line)
        query = '\n'.join(cleaned_lines)
        # Normalize whitespace but preserve newlines for multi-line queries
        query = re.sub(r'[ \t]+', ' ', query)  # Only collapse spaces/tabs, not newlines
        return query
    
    def _tree_to_ast(self, tree) -> List[AIQLNode]:
        """Convert Lark parse tree to AST nodes."""
        import logging
        logger = logging.getLogger(__name__)
        
        ast_nodes = []
        
        for child in tree.children:
            # Check for top_select FIRST to avoid pipeline ambiguity
            if child.data in ['top_select']:
                # Handle standalone SELECT queries with aggregations
                # Grammar supports:
                # 1. SELECT select_target FROM node_spec WHERE ...
                # 2. SELECT NODE identifier WHERE ... (new syntax)
                # 3. SELECT EDGE WHERE ... (new syntax)
                logger.debug(f"Parsing top_select: {len(child.children)} children")
                
                # Debug: log all children
                for idx, c in enumerate(child.children):
                    logger.debug(f"  Child {idx}: data={getattr(c, 'data', 'N/A')}, value={getattr(c, 'value', 'N/A')}, type={getattr(c, 'type', 'N/A')}")
                
                # Check for new syntax: SELECT NODE identifier or SELECT EDGE
                if len(child.children) >= 2:
                    first_token = child.children[0]
                    second_token = child.children[1]
                    
                    # Check if it's "SELECT NODE identifier" syntax
                    if (hasattr(first_token, 'value') and first_token.value == 'NODE' and
                        hasattr(second_token, 'data') and second_token.data == 'identifier'):
                        # New syntax: SELECT NODE Person WHERE ... or SELECT NODE Person -> Company WHERE ...
                        node_type = self._get_identifier(second_token)
                        params = {
                            'projections': ['*'],
                            'from': node_type,
                            'node_type': node_type
                        }
                        
                        # Parse traverse path if present (SELECT NODE Person -> Company)
                        i = 2
                        traverse_path = []
                        while i < len(child.children):
                            current = child.children[i]
                            if hasattr(current, 'value') and current.value == '->':
                                if i + 1 < len(child.children):
                                    next_node = child.children[i + 1]
                                    if hasattr(next_node, 'data') and next_node.data == 'identifier':
                                        traverse_path.append(self._get_identifier(next_node))
                                        i += 2
                                        continue
                            elif hasattr(current, 'data') and current.data == 'traverse_path':
                                # Parse traverse_path node
                                for hop in current.children:
                                    if hasattr(hop, 'data') and hop.data == 'identifier':
                                        traverse_path.append(self._get_identifier(hop))
                                i += 1
                                continue
                            elif hasattr(current, 'data') and current.data == 'condition':
                                params['where'] = self._parse_condition(current)
                                i += 1
                            elif hasattr(current, 'value') and current.value == 'LIMIT':
                                if i + 1 < len(child.children):
                                    limit_node = child.children[i + 1]
                                    if hasattr(limit_node, 'data') and limit_node.data == 'number':
                                        params['limit'] = self._get_number(limit_node)
                                    i += 2
                                else:
                                    i += 1
                            else:
                                i += 1
                        
                        if traverse_path:
                            params['traverse'] = traverse_path
                        
                        ast_nodes.append(AIQLNode(
                            node_type=AIQLNodeType.SELECT,
                            parameters=params
                        ))
                        continue
                
                    # Check if it's "SELECT EDGE" syntax
                    elif (hasattr(first_token, 'value') and first_token.value == 'EDGE'):
                        # New syntax: SELECT EDGE WHERE ...
                        params = {
                            'projections': ['*'],
                            'edge_only': True
                        }
                        
                        # Parse WHERE and LIMIT
                        i = 1
                        while i < len(child.children):
                            current = child.children[i]
                            if hasattr(current, 'data') and current.data == 'condition':
                                params['where'] = self._parse_condition(current)
                                i += 1
                            elif hasattr(current, 'value') and current.value == 'LIMIT':
                                if i + 1 < len(child.children):
                                    limit_node = child.children[i + 1]
                                    if hasattr(limit_node, 'data') and limit_node.data == 'number':
                                        params['limit'] = self._get_number(limit_node)
                                    i += 2
                                else:
                                    i += 1
                            else:
                                i += 1
                        
                        ast_nodes.append(AIQLNode(
                            node_type=AIQLNodeType.SELECT,
                            parameters=params
                        ))
                        continue
                
                # For standard SELECT queries (not SELECT NODE or SELECT EDGE), use _parse_select
                # But first check if it's a top_select with select_target structure
                # _parse_select expects projection_list, but top_select has select_target
                # So we need to handle top_select structure specially
                if len(child.children) > 0 and hasattr(child.children[0], 'data') and child.children[0].data == 'select_target':
                    # This is top_select format - parse it manually but use _parse_select logic for clauses
                    # Parse select_target first to get projections
                    select_target = child.children[0]
                    select_items = []
                    for select_item in select_target.children:
                        if hasattr(select_item, 'data') and select_item.data == 'select_item':
                            # Check for aggregation function
                            if len(select_item.children) > 0 and hasattr(select_item.children[0], 'data') and select_item.children[0].data == 'aggregation_function':
                                agg_func = self._parse_aggregation_function(select_item.children[0])
                                select_items.append(agg_func)
                            elif len(select_item.children) > 0:
                                # Regular field
                                first_child = select_item.children[0]
                                if hasattr(first_child, 'value') and first_child.value == '*':
                                    select_items.append({'type': 'wildcard', 'field': '*'})
                                elif hasattr(first_child, 'data') and first_child.data == 'qualified_identifier':
                                    select_items.append({'type': 'field', 'field': self._get_qualified_identifier(first_child)})
                                else:
                                    select_items.append({'type': 'field', 'field': self._get_identifier(first_child) if hasattr(first_child, 'data') else str(first_child)})
                    
                    # Convert to projections format
                    projections = []
                    for item in select_items:
                        if item.get('type') == 'wildcard':
                            projections.append({'type': 'wildcard', 'field': '*'})
                        elif item.get('type') == 'aggregation':
                            projections.append(item)  # Keep aggregation as-is
                        else:
                            projections.append({'type': 'field', 'field': item.get('field', '*')})
                    
                    # Now create a modified child node that _parse_select can handle
                    # We'll create params directly and merge with _parse_select's clause parsing
                    params = {
                        'projections': projections,
                        'select_items': select_items
                    }
                    
                    # Parse FROM (node_spec is child[1])
                    if len(child.children) > 1 and hasattr(child.children[1], 'data') and child.children[1].data == 'node_spec':
                        params['from'] = self._get_identifier(child.children[1].children[0])
                    
                    # Parse remaining children: WHERE, GROUP BY, ORDER BY, LIMIT, OFFSET
                    ci = 2  # start after FROM node_spec
                    while ci < len(child.children):
                        cc = child.children[ci]
                        if hasattr(cc, 'data') and cc.data == 'qualified_identifier_list':
                            group_by_fields = []
                            for gb_child in cc.children:
                                if hasattr(gb_child, 'data') and gb_child.data == 'qualified_identifier':
                                    group_by_fields.append(self._get_qualified_identifier(gb_child))
                                elif hasattr(gb_child, 'data') and gb_child.data == 'identifier':
                                    group_by_fields.append(self._get_identifier(gb_child))
                            if group_by_fields:
                                params['group_by'] = group_by_fields
                        elif hasattr(cc, 'data') and cc.data == 'condition':
                            params['where'] = self._parse_condition(cc)
                        elif hasattr(cc, 'data') and cc.data == 'order_by_clause':
                            params['order_by'] = self._parse_order_by_clause(cc)
                        elif hasattr(cc, 'data') and cc.data == 'number':
                            # Standalone numbers: first is LIMIT, second is OFFSET
                            # Extract the Token inside the number Tree
                            num_token = cc.children[0] if cc.children else cc
                            if 'limit' not in params:
                                params['limit'] = self._get_number(num_token)
                            elif 'offset' not in params:
                                params['offset'] = self._get_number(num_token)
                        elif hasattr(cc, 'data') and cc.data == 'variable':
                            if 'limit' not in params:
                                var_name = self._get_identifier(cc.children[0]) if cc.children else str(cc)
                                if isinstance(var_name, str) and var_name.startswith('$'):
                                    var_name = var_name[1:]
                                params['limit'] = self.variables.get(var_name, f"${var_name}")
                        ci += 1

                    ast_nodes.append(AIQLNode(
                        node_type=AIQLNodeType.SELECT,
                        parameters=params
                    ))
                    continue
                
                # Fallback: try _parse_select for other formats
                select_node = self._parse_select(child)
                if select_node:
                    ast_nodes.append(select_node)
                    continue
                
                # Fallback: Original SELECT syntax: SELECT select_target FROM ...
                params = {}
                
                # Parse select_target (first child)
                if len(child.children) > 0:
                    select_target = child.children[0]
                    if hasattr(select_target, 'data') and select_target.data == 'select_target':
                        # Parse select_items (can be aggregation_function, qualified_identifier, "*", "DISTINCT qualified_identifier")
                        select_items = []
                        for select_item in select_target.children:
                            if hasattr(select_item, 'data') and select_item.data == 'select_item':
                                # Check for aggregation function
                                if len(select_item.children) > 0 and hasattr(select_item.children[0], 'data') and select_item.children[0].data == 'aggregation_function':
                                    agg_func = self._parse_aggregation_function(select_item.children[0])
                                    select_items.append(agg_func)
                                elif len(select_item.children) > 0:
                                    # Regular field or DISTINCT
                                    first_child = select_item.children[0]
                                    if hasattr(first_child, 'value') and first_child.value == '*':
                                        select_items.append({'type': 'wildcard', 'field': '*'})
                                    elif hasattr(first_child, 'data') and first_child.data == 'qualified_identifier':
                                        select_items.append({'type': 'field', 'field': self._get_qualified_identifier(first_child)})
                                    else:
                                        # Simple identifier
                                        select_items.append({'type': 'field', 'field': str(first_child)})
                        # Use 'projections' format expected by SelectOperator
                        projections = []
                        for item in select_items:
                            if item.get('type') == 'wildcard':
                                projections.append({'type': 'wildcard', 'field': '*'})
                            else:
                                projections.append({'type': 'field', 'field': item.get('field', '*')})
                        params['projections'] = projections
                        params['select_items'] = select_items  # Also keep for compatibility
                        
                        # If no projections found, default to wildcard
                        if not projections:
                            params['projections'] = [{'type': 'wildcard', 'field': '*'}]
                            params['select_items'] = [{'type': 'wildcard', 'field': '*'}]
                
                # Parse optional clauses: AS, FROM, WHERE, GROUP BY, HAVING, LIMIT
                i = 1
                while i < len(child.children):
                    current = child.children[i]
                    logger.debug(f"  Processing child {i}: data={getattr(current, 'data', 'N/A')}, value={getattr(current, 'value', 'N/A')}")
                    
                    # Check for AS alias (before FROM)
                    if hasattr(current, 'value') and current.value == 'AS' and i + 1 < len(child.children):
                        params['alias'] = self._get_identifier(child.children[i + 1])
                        i += 2
                    
                    # Check for FROM clause
                    # Grammar: ("FROM" node_spec)? - so we need to check for "FROM" token first
                    elif hasattr(current, 'value') and current.value == 'FROM' and i + 1 < len(child.children):
                        # Next child should be node_spec
                        next_child = child.children[i + 1]
                        if hasattr(next_child, 'data') and next_child.data == 'node_spec':
                            params['from'] = self._get_identifier(next_child.children[0])
                            i += 2  # Skip both FROM and node_spec
                        else:
                            # Try to get identifier directly if node_spec structure is different
                            params['from'] = self._get_identifier(next_child)
                            i += 2
                    elif hasattr(current, 'data') and current.data == 'node_spec':
                        # node_spec without explicit FROM token (shouldn't happen but handle it)
                        params['from'] = self._get_identifier(current.children[0])
                        i += 1
                    
                    # Check for WHERE clause
                    elif hasattr(current, 'data') and current.data == 'condition':
                        params['where'] = self._parse_condition(current)
                        i += 1
                    
                    # Check for GROUP BY (identifier_list)
                    elif hasattr(current, 'value') and current.value == 'GROUP' and i + 2 < len(child.children):
                        group_by_list = []
                        group_by_node = child.children[i + 2]
                        if hasattr(group_by_node, 'data') and group_by_node.data == 'identifier_list':
                            for group_child in group_by_node.children:
                                if hasattr(group_child, 'data') and group_child.data == 'identifier':
                                    group_by_list.append(self._get_identifier(group_child))
                        params['group_by'] = group_by_list
                        i += 3
                    
                    # Check for HAVING clause
                    elif hasattr(current, 'value') and current.value == 'HAVING' and i + 1 < len(child.children):
                        having_node = child.children[i + 1]
                        if hasattr(having_node, 'data') and having_node.data == 'condition':
                            params['having'] = self._parse_condition(having_node)
                        i += 2
                    
                    # Check for LIMIT (can be number or variable)
                    elif hasattr(current, 'value') and current.value == 'LIMIT' and i + 1 < len(child.children):
                        limit_value = child.children[i + 1]
                        if hasattr(limit_value, 'data'):
                            if limit_value.data == 'number':
                                params['limit'] = self._get_number(limit_value)
                            elif limit_value.data == 'variable':
                                # Variable in LIMIT - substitute if available
                                var_name = self._get_identifier(limit_value.children[0]) if limit_value.children and len(limit_value.children) > 0 else str(limit_value)
                                if isinstance(var_name, str) and var_name.startswith('$'):
                                    var_name = var_name[1:]
                                if var_name in self.variables:
                                    params['limit'] = self.variables[var_name]
                                else:
                                    params['limit'] = f"${var_name}"
                            else:
                                # Try to parse as number
                                params['limit'] = self._get_number(limit_value)
                        else:
                            # Fallback: try to parse as number
                            params['limit'] = self._get_number(limit_value)
                        i += 2

                    # Check for OFFSET (can be number or variable)
                    elif hasattr(current, 'value') and current.value == 'OFFSET' and i + 1 < len(child.children):
                        offset_value = child.children[i + 1]
                        if hasattr(offset_value, 'data') and offset_value.data == 'number':
                            params['offset'] = self._get_number(offset_value)
                        elif hasattr(offset_value, 'data') and offset_value.data == 'variable':
                            var_name = self._get_identifier(offset_value.children[0]) if offset_value.children else str(offset_value)
                            if isinstance(var_name, str) and var_name.startswith('$'):
                                var_name = var_name[1:]
                            params['offset'] = self.variables.get(var_name, f"${var_name}")
                        else:
                            params['offset'] = self._get_number(offset_value)
                        i += 2
                    elif hasattr(current, 'data') and current.data == 'number':
                        # Standalone numbers after clauses: first is LIMIT, second is OFFSET
                        # (lark consumes the "LIMIT"/"OFFSET" terminals, leaving just the number)
                        if 'limit' not in params:
                            params['limit'] = self._get_number(current)
                        elif 'offset' not in params:
                            params['offset'] = self._get_number(current)
                        i += 1
                    else:
                        i += 1

                logger.info(f"Parsed SELECT params: {params}")
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.SELECT,
                    parameters=params
                ))
            elif child.data == 'run_pipeline_cmd':
                ast_nodes.append(self._parse_run_pipeline_cmd(child))
            elif child.data == 'classify_file_cmd':
                ast_nodes.append(self._parse_classify_file_cmd(child))
            elif child.data in ['create_pipeline', 'create_multimodal_pipeline']:
                # Handle both create_pipeline and create_multimodal_pipeline (alias)
                ast_nodes.append(self._parse_create_multimodal_pipeline(child))
            elif child.data == 'create_retrieval_pipeline':
                # Parse retrieval pipeline creation
                ast_nodes.append(self._parse_create_retrieval_pipeline(child))
            elif child.data == 'retrieval_pipeline':
                # Handle retrieval_pipeline wrapper (which contains create_retrieval_pipeline)
                for subchild in child.children:
                    if hasattr(subchild, 'data') and subchild.data == 'create_retrieval_pipeline':
                        ast_nodes.append(self._parse_create_retrieval_pipeline(subchild))
            elif child.data == 'pipeline':
                # Check if this is actually a standalone SELECT that was mis-parsed
                # If pipeline has only one child that's a select_stage, treat it as top_select
                is_standalone_select = False
                if len(child.children) == 1 and hasattr(child.children[0], 'data'):
                    pipeline_body = child.children[0]
                    if hasattr(pipeline_body, 'data') and pipeline_body.data == 'pipeline_body':
                        if len(pipeline_body.children) == 1:
                            pipeline_item = pipeline_body.children[0]
                            if hasattr(pipeline_item, 'data') and pipeline_item.data == 'stage':
                                stage = pipeline_item.children[0] if pipeline_item.children else None
                                if stage and hasattr(stage, 'data') and stage.data == 'select_stage':
                                    # This is actually a top-level SELECT, not a pipeline
                                    # Re-parse as select_stage
                                    select_node = self._parse_stage(pipeline_item)
                                    if select_node:
                                        ast_nodes.append(select_node)
                                    is_standalone_select = True
                
                if not is_standalone_select:
                    parsed = self._parse_pipeline(child)
                    ast_nodes.extend(parsed)
            elif child.data in ['top_create_edge', 'create_edge']:
                # Handle CREATE EDGE - route to _parse_create_edge for proper handling
                # Check for new simple syntax: CREATE EDGE identifier -> identifier
                if len(child.children) >= 3:
                    first = child.children[0]
                    second = child.children[1]
                    third = child.children[2]
                    
                    # Check for simple arrow syntax: CREATE EDGE Person -> Company
                    # Check if second is a token with value '->' or has 'value' attribute
                    second_is_arrow = False
                    if hasattr(second, 'value') and second.value == '->':
                        second_is_arrow = True
                    elif hasattr(second, 'type') and str(second) == '->':
                        second_is_arrow = True
                    elif str(second) == '->' or (hasattr(second, '__str__') and str(second).strip() == '->'):
                        second_is_arrow = True
                    # Also check if it's a Token object from Lark
                    elif hasattr(second, '__class__') and 'Token' in str(type(second)) and str(second) == '->':
                        second_is_arrow = True
                    
                    # Check if first and third are identifiers
                    first_is_identifier = (hasattr(first, 'data') and first.data == 'identifier') or (hasattr(first, 'type') and first.type == 'IDENTIFIER')
                    third_is_identifier = (hasattr(third, 'data') and third.data == 'identifier') or (hasattr(third, 'type') and third.type == 'IDENTIFIER')
                    
                    if first_is_identifier and second_is_arrow and third_is_identifier:
                        # New simple syntax: CREATE EDGE Person -> Company {props}?
                        source_type = self._get_identifier(first) if hasattr(first, 'data') else str(first)
                        target_type = self._get_identifier(third) if hasattr(third, 'data') else str(third)
                        properties = {}
                        
                        # Check for properties (4th child)
                        if len(child.children) > 3:
                            props_node = child.children[3]
                            if hasattr(props_node, 'data') and props_node.data == 'property_list':
                                properties = self._parse_property_list(props_node)
                        
                        # Default edge type from properties or use a default
                        edge_type = properties.get('type', 'RELATED_TO')
                        if 'type' in properties:
                            # Remove type from properties as it's the edge type
                            edge_type = properties.pop('type')
                        
                        ast_nodes.append(AIQLNode(
                            node_type=AIQLNodeType.CREATE_EDGE,
                            parameters={
                                'edge_type': edge_type,
                                'source_type': source_type,  # Node type (e.g., "Person")
                                'target_type': target_type,  # Node type (e.g., "Company")
                                'source_node': None,  # Will be resolved by executor
                                'target_node': None,  # Will be resolved by executor
                                'source_alias': None,
                                'target_alias': None,
                                'properties': properties,
                                'edge_alias': None
                            }
                        ))
                        continue
                
                # Original CREATE EDGE parsing
                try:
                    parsed_edge = self._parse_create_edge(child)
                    ast_nodes.append(parsed_edge)
                except Exception as e:
                    logger.error(f"Error parsing create_edge: {e}")
                    # Fallback to old parsing logic
                    try:
                        edge_spec = child.children[0]
                        
                        # Check if first child is identifier (edge_type) for new syntax
                        edge_type = None
                        edge_spec_node = None
                        start_idx = 0
                        
                        if len(child.children) > 0:
                            first_child = child.children[0]
                            if hasattr(first_child, 'data') and first_child.data == 'identifier':
                                edge_type = self._get_identifier(first_child)
                                start_idx = 1
                                if len(child.children) > start_idx:
                                    edge_spec_node = child.children[start_idx]
                            else:
                                edge_spec_node = child.children[0]
                        
                        if not edge_spec_node:
                            raise ValueError("Invalid CREATE EDGE syntax")
                        
                        # Check for arrow syntax in edge_spec_node
                        has_arrow = False
                        arrow_idx = -1
                        if edge_spec_node and hasattr(edge_spec_node, 'children'):
                            for i, c in enumerate(edge_spec_node.children):
                                if hasattr(c, 'value') and c.value == '->':
                                    has_arrow = True
                                    arrow_idx = i
                                    break
                        
                        if has_arrow and arrow_idx > 0:
                            # Arrow syntax: (source)->(target) {props}?
                            source_ref = edge_spec_node.children[0]
                            target_ref = edge_spec_node.children[arrow_idx + 1]
                            
                            # Extract source node - could be identifier or node_ref
                            source_node = None
                            if hasattr(source_ref, 'children') and len(source_ref.children) > 0:
                                # node_ref with children
                                first_child = source_ref.children[0]
                                if hasattr(first_child, 'data') and first_child.data == 'identifier':
                                    source_node = self._get_identifier(first_child)
                                elif hasattr(first_child, 'data') and first_child.data == 'typed_node_ref':
                                    # typed_node_ref: type:id
                                    if len(first_child.children) >= 2:
                                        source_node = self._get_identifier(first_child.children[1])
                                    else:
                                        source_node = self._get_identifier(first_child.children[0]) if first_child.children else None
                                else:
                                    source_node = self._get_identifier(first_child) if hasattr(first_child, 'value') else str(first_child)
                            else:
                                # Direct identifier
                                source_node = self._get_identifier(source_ref) if hasattr(source_ref, 'data') else str(source_ref)
                            
                            # Extract target node - same logic
                            target_node = None
                            if hasattr(target_ref, 'children') and len(target_ref.children) > 0:
                                first_child = target_ref.children[0]
                                if hasattr(first_child, 'data') and first_child.data == 'identifier':
                                    target_node = self._get_identifier(first_child)
                                elif hasattr(first_child, 'data') and first_child.data == 'typed_node_ref':
                                    if len(first_child.children) >= 2:
                                        target_node = self._get_identifier(first_child.children[1])
                                    else:
                                        target_node = self._get_identifier(first_child.children[0]) if first_child.children else None
                                else:
                                    target_node = self._get_identifier(first_child) if hasattr(first_child, 'value') else str(first_child)
                            else:
                                target_node = self._get_identifier(target_ref) if hasattr(target_ref, 'data') else str(target_ref)
                            
                            properties = {}
                            if len(edge_spec_node.children) > arrow_idx + 2:
                                props_node = edge_spec_node.children[arrow_idx + 2]
                                if hasattr(props_node, 'data') and props_node.data == 'property_list':
                                    properties = self._parse_property_list(props_node)
                            
                            ast_nodes.append(AIQLNode(
                                node_type=AIQLNodeType.CREATE_EDGE,
                                parameters={
                                    'edge_type': edge_type,
                                    'source_node': source_node,
                                    'target_node': target_node,
                                    'properties': properties,
                                    'source_alias': None,
                                    'target_alias': None,
                                    'edge_alias': None
                                }
                            ))
                        else:
                            # Use _parse_create_edge for other syntaxes
                            parsed_edge = self._parse_create_edge(child)
                            ast_nodes.append(parsed_edge)
                    except Exception as e2:
                        logger.error(f"Fallback parsing also failed: {e2}")
                        # Last resort: Try to parse SRC/DEST syntax directly from child.children
                        try:
                            edge_type = None
                            src_node = None
                            dest_node = None
                            properties = {}
                            
                            # Find edge_type (first identifier)
                            for i, c in enumerate(child.children):
                                if hasattr(c, 'data') and c.data == 'identifier':
                                    edge_type = self._get_identifier(c)
                                    break
                                elif hasattr(c, 'type') and c.type == 'IDENTIFIER':
                                    edge_type = str(c.value) if hasattr(c, 'value') else str(c)
                                    break
                            
                            # Find SRC and DEST
                            for i, c in enumerate(child.children):
                                c_value = None
                                if hasattr(c, 'value'):
                                    c_value = c.value
                                elif isinstance(c, str):
                                    c_value = c
                                
                                if c_value == 'SRC' and i + 1 < len(child.children):
                                    src_child = child.children[i + 1]
                                    if hasattr(src_child, 'data') and src_child.data == 'identifier':
                                        src_node = self._get_identifier(src_child)
                                    elif hasattr(src_child, 'type') and src_child.type == 'IDENTIFIER':
                                        src_node = str(src_child.value) if hasattr(src_child, 'value') else str(src_child)
                                elif c_value == 'DEST' and i + 1 < len(child.children):
                                    dest_child = child.children[i + 1]
                                    if hasattr(dest_child, 'data') and dest_child.data == 'identifier':
                                        dest_node = self._get_identifier(dest_child)
                                    elif hasattr(dest_child, 'type') and dest_child.type == 'IDENTIFIER':
                                        dest_node = str(dest_child.value) if hasattr(dest_child, 'value') else str(dest_child)
                            
                            # Find properties
                            for c in child.children:
                                if hasattr(c, 'data') and c.data == 'property_list':
                                    properties = self._parse_property_list(c)
                            
                            if edge_type and src_node and dest_node:
                                ast_nodes.append(AIQLNode(
                                    node_type=AIQLNodeType.CREATE_EDGE,
                                    parameters={
                                        'edge_type': edge_type,
                                        'source_node': src_node,
                                        'target_node': dest_node,
                                        'properties': properties,
                                        'source_alias': None,
                                        'target_alias': None,
                                        'edge_alias': None
                                    }
                                ))
                            else:
                                raise ValueError(f"Could not parse CREATE EDGE: edge_type={edge_type}, src={src_node}, dest={dest_node}")
                        except Exception as e3:
                            logger.error(f"Last resort parsing also failed: {e3}")
                            # Give up - return empty AST nodes (query will fail gracefully)
                            pass
            elif child.data == 'variable_decl':
                var_decl = self._parse_variable_decl(child)
                # Remove $ prefix if present
                var_name = var_decl.name.lstrip('$')
                self.variables[var_name] = var_decl.value
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.VARIABLE_DECL,
                    parameters={'name': var_name, 'value': var_decl.value}
                ))
            elif child.data == 'rag_generate':
                ast_nodes.append(self._parse_rag_generate(child))
            elif child.data == 'rag_query_one_shot':
                ast_nodes.append(self._parse_rag_query_one_shot(child))
            elif child.data == 'evaluate_rag':
                # The grammar parses it as: evaluate_rag -> [identifier (ON value), parameter_dict (USING), parameter_dict (PARAMETERS), identifier (COLLECTION)]
                # First child is the ON value (identifier)
                if len(child.children) > 0:
                    on_child = child.children[0]
                    if hasattr(on_child, 'data') and on_child.data == 'identifier':
                        # Create a modified node with ON parameter set
                        parsed_node = self._parse_evaluate_rag(child)
                        # Ensure 'on' parameter is set from first child
                        if 'on' not in parsed_node.parameters or not parsed_node.parameters.get('on'):
                            parsed_node.parameters['on'] = self._get_identifier(on_child)
                        ast_nodes.append(parsed_node)
                    else:
                        ast_nodes.append(self._parse_evaluate_rag(child))
                else:
                    ast_nodes.append(self._parse_evaluate_rag(child))
            elif child.data == 'insert_into':
                ast_nodes.append(self._parse_insert_into(child))
            elif child.data == 'update_pipeline':
                ast_nodes.append(self._parse_update_pipeline(child))
            elif child.data == 'for_loop':
                ast_nodes.append(self._parse_for_loop(child))
            elif child.data == 'top_create_collection':
                # Handle CREATE COLLECTION with schema
                ast_nodes.append(self._parse_create_collection(child))
            elif child.data == 'session_statement':
                # Handle session management statements
                session_node = self._parse_session_statement(child)
                if session_node:
                    ast_nodes.append(session_node)
            elif child.data == 'create_function':
                # Handle CREATE FUNCTION
                ast_nodes.append(self._parse_create_function(child))
            elif child.data == 'use_function':
                # Handle USE FUNCTION
                ast_nodes.append(self._parse_use_function_statement(child))
            elif child.data == 'agent_statement':
                # Handle agent statements
                agent_node = self._parse_agent_statement(child)
                if agent_node:
                    ast_nodes.append(agent_node)
            elif child.data == 'memory_statement':
                # Handle memory statements
                memory_node = self._parse_memory_statement(child)
                if memory_node:
                    ast_nodes.append(memory_node)
            elif child.data == 'operational_statement':
                # Handle operational control statements
                op_node = self._parse_operational_statement(child)
                if op_node:
                    ast_nodes.append(op_node)
            elif child.data == 'create_stage':
                stage_node = self._parse_stage(child)
                if stage_node:
                    ast_nodes.append(stage_node)
            elif child.data == 'stage':
                stage_node = self._parse_stage(child)
                if stage_node:
                    ast_nodes.append(stage_node)
            elif child.data in ['top_select']:
                # Handle standalone SELECT queries with aggregations
                # Grammar: SELECT select_target (AS identifier)? (FROM node_spec)? (WHERE condition)? (GROUP BY identifier_list)? (HAVING condition)? (LIMIT number)?
                logger.info(f"Parsing top_select: {len(child.children)} children")
                
                params = {}
                
                # Parse select_target (first child)
                if len(child.children) > 0:
                    select_target = child.children[0]
                    if hasattr(select_target, 'data') and select_target.data == 'select_target':
                        # Parse select_items (can be aggregation_function, qualified_identifier, "*", "DISTINCT qualified_identifier")
                        select_items = []
                        for select_item in select_target.children:
                            if hasattr(select_item, 'data') and select_item.data == 'select_item':
                                # Check for aggregation function
                                if len(select_item.children) > 0 and hasattr(select_item.children[0], 'data') and select_item.children[0].data == 'aggregation_function':
                                    agg_func = self._parse_aggregation_function(select_item.children[0])
                                    select_items.append(agg_func)
                                elif len(select_item.children) > 0:
                                    # Regular field or DISTINCT
                                    first_child = select_item.children[0]
                                    if hasattr(first_child, 'value') and first_child.value == '*':
                                        select_items.append({'type': 'wildcard', 'field': '*'})
                                    elif hasattr(first_child, 'data') and first_child.data == 'qualified_identifier':
                                        select_items.append({'type': 'field', 'field': self._get_qualified_identifier(first_child)})
                                    else:
                                        # Simple identifier
                                        select_items.append({'type': 'field', 'field': str(first_child)})
                        params['select_items'] = select_items
                
                # Parse optional clauses: AS, FROM, WHERE, GROUP BY, HAVING, LIMIT
                i = 1
                while i < len(child.children):
                    current = child.children[i]
                    
                    # Check for AS alias (before FROM)
                    if hasattr(current, 'value') and current.value == 'AS' and i + 1 < len(child.children):
                        params['alias'] = self._get_identifier(child.children[i + 1])
                        i += 2
                    
                    # Check for FROM clause
                    # Grammar: ("FROM" node_spec)? - so we need to check for "FROM" token first
                    elif hasattr(current, 'value') and current.value == 'FROM' and i + 1 < len(child.children):
                        # Next child should be node_spec
                        next_child = child.children[i + 1]
                        if hasattr(next_child, 'data') and next_child.data == 'node_spec':
                            params['from'] = self._get_identifier(next_child.children[0])
                            i += 2  # Skip both FROM and node_spec
                        else:
                            # Try to get identifier directly if node_spec structure is different
                            params['from'] = self._get_identifier(next_child)
                            i += 2
                    elif hasattr(current, 'data') and current.data == 'node_spec':
                        # node_spec without explicit FROM token (shouldn't happen but handle it)
                        params['from'] = self._get_identifier(current.children[0])
                        i += 1
                    
                    # Check for WHERE clause
                    elif hasattr(current, 'data') and current.data == 'condition':
                        params['where'] = self._parse_condition(current)
                        i += 1
                    
                    # Check for GROUP BY (identifier_list)
                    elif hasattr(current, 'value') and current.value == 'GROUP' and i + 2 < len(child.children):
                        group_by_list = []
                        group_by_node = child.children[i + 2]
                        if hasattr(group_by_node, 'data') and group_by_node.data == 'identifier_list':
                            for group_child in group_by_node.children:
                                if hasattr(group_child, 'data') and group_child.data == 'identifier':
                                    group_by_list.append(self._get_identifier(group_child))
                        params['group_by'] = group_by_list
                        i += 3
                    
                    # Check for HAVING clause
                    elif hasattr(current, 'value') and current.value == 'HAVING' and i + 1 < len(child.children):
                        having_node = child.children[i + 1]
                        if hasattr(having_node, 'data') and having_node.data == 'condition':
                            params['having'] = self._parse_condition(having_node)
                        i += 2
                    
                    # Check for LIMIT (can be number or variable)
                    elif hasattr(current, 'value') and current.value == 'LIMIT' and i + 1 < len(child.children):
                        limit_value = child.children[i + 1]
                        if hasattr(limit_value, 'data'):
                            if limit_value.data == 'number':
                                params['limit'] = self._get_number(limit_value)
                            elif limit_value.data == 'variable':
                                # Variable in LIMIT - substitute if available
                                var_name = self._get_identifier(limit_value.children[0]) if limit_value.children and len(limit_value.children) > 0 else str(limit_value)
                                if isinstance(var_name, str) and var_name.startswith('$'):
                                    var_name = var_name[1:]
                                if var_name in self.variables:
                                    params['limit'] = self.variables[var_name]
                                else:
                                    params['limit'] = f"${var_name}"
                            else:
                                # Try to parse as number
                                params['limit'] = self._get_number(limit_value)
                        else:
                            # Fallback: try to parse as number
                            params['limit'] = self._get_number(limit_value)
                        i += 2

                    # Check for OFFSET (can be number or variable)
                    elif hasattr(current, 'value') and current.value == 'OFFSET' and i + 1 < len(child.children):
                        offset_value = child.children[i + 1]
                        if hasattr(offset_value, 'data') and offset_value.data == 'number':
                            params['offset'] = self._get_number(offset_value)
                        elif hasattr(offset_value, 'data') and offset_value.data == 'variable':
                            var_name = self._get_identifier(offset_value.children[0]) if offset_value.children else str(offset_value)
                            if isinstance(var_name, str) and var_name.startswith('$'):
                                var_name = var_name[1:]
                            params['offset'] = self.variables.get(var_name, f"${var_name}")
                        else:
                            params['offset'] = self._get_number(offset_value)
                        i += 2
                    elif hasattr(current, 'data') and current.data == 'number':
                        # Standalone numbers after clauses: first is LIMIT, second is OFFSET
                        # (lark consumes the "LIMIT"/"OFFSET" terminals, leaving just the number)
                        if 'limit' not in params:
                            params['limit'] = self._get_number(current)
                        elif 'offset' not in params:
                            params['offset'] = self._get_number(current)
                        i += 1
                    else:
                        i += 1

                logger.info(f"Parsed SELECT params: {params}")
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.SELECT,
                    parameters=params
                ))
            elif child.data == 'top_begin_transaction':
                # Handle BEGIN TRANSACTION
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.BEGIN_TRANSACTION,
                    parameters={}
                ))
            elif child.data == 'top_commit_transaction':
                # Handle COMMIT TRANSACTION
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.COMMIT_TRANSACTION,
                    parameters={}
                ))
            elif child.data == 'top_rollback_transaction':
                # Handle ROLLBACK TRANSACTION
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.ROLLBACK_TRANSACTION,
                    parameters={}
                ))
            elif child.data in ['top_update_node', 'top_update_edge']:
                # Handle standalone UPDATE NODE/EDGE queries
                logger.info(f"Parsing top_update_node/edge: {len(child.children)} children")
                params = {}
                
                # Parse node_spec or edge_spec
                if len(child.children) > 0:
                    spec = child.children[0]
                    params['node_type'] = self._get_identifier(spec.children[0])
                    if len(spec.children) > 1:
                        alias_node = spec.children[1]
                        if hasattr(alias_node, 'data') and alias_node.data == 'alias':
                            params['alias'] = self._get_identifier(alias_node.children[0])
                
                # Parse WHERE and SET clauses - order can be WHERE then SET, SET then WHERE, or just SET
                set_properties = {}
                where_conditions = []
                
                # Check children - WHERE can come before or after SET
                for i in range(1, len(child.children)):
                    child_node = child.children[i]
                    if hasattr(child_node, 'data'):
                        if child_node.data == 'condition':
                            # WHERE clause
                            where_conditions.append(self._parse_condition(child_node))
                        elif child_node.data == 'update_assignment_list':
                            # SET clause
                            for assignment in child_node.children:
                                if hasattr(assignment, 'data') and assignment.data == 'update_assignment':
                                    if len(assignment.children) >= 2:
                                        key = self._get_identifier(assignment.children[0])
                                        value = self._parse_value(assignment.children[1])
                                        set_properties[key] = value
                    elif hasattr(child_node, 'value'):
                        # Check for explicit SET or WHERE tokens
                        if child_node.value == 'SET' and i + 1 < len(child.children):
                            set_node = child.children[i + 1]
                            if hasattr(set_node, 'data') and set_node.data == 'update_assignment_list':
                                for assignment in set_node.children:
                                    if hasattr(assignment, 'data') and assignment.data == 'update_assignment':
                                        if len(assignment.children) >= 2:
                                            key = self._get_identifier(assignment.children[0])
                                            value = self._parse_value(assignment.children[1])
                                            set_properties[key] = value
                        elif child_node.value == 'WHERE' and i + 1 < len(child.children):
                            where_node = child.children[i + 1]
                            if hasattr(where_node, 'data') and where_node.data == 'condition':
                                where_conditions.append(self._parse_condition(where_node))
                
                params['set'] = set_properties
                params['where'] = where_conditions
                
                node_type = AIQLNodeType.UPDATE_EDGE if child.data == 'top_update_edge' else AIQLNodeType.UPDATE_NODE
                ast_nodes.append(AIQLNode(
                    node_type=node_type,
                    parameters=params
                ))
            elif child.data in ['top_delete_node', 'delete_node']:
                # Handle standalone DELETE NODE queries (including DELETE ALL NODES)
                logger.info(f"Parsing top_delete_node: {len(child.children)} children")
                
                # Check if this is DELETE ALL NODES
                is_all_nodes = False
                where_conditions = None
                start_idx = 0
                
                if len(child.children) >= 2:
                    first_child = child.children[0]
                    second_child = child.children[1]
                    # Check for "ALL" token followed by "NODES"
                    if (hasattr(first_child, 'value') and first_child.value == 'ALL' and
                        hasattr(second_child, 'value') and second_child.value == 'NODES'):
                        is_all_nodes = True
                        start_idx = 2
                
                if is_all_nodes:
                    # Parse WHERE clause if present
                    for i in range(start_idx, len(child.children)):
                        c = child.children[i]
                        if hasattr(c, 'data') and c.data == 'condition':
                            where_conditions = [self._parse_condition(c)]
                            break
                    
                    ast_nodes.append(AIQLNode(
                        node_type=AIQLNodeType.DELETE_NODE,
                        parameters={'node_type': 'ALL', 'where': where_conditions} if where_conditions else {'node_type': 'ALL'}
                    ))
                else:
                    # Regular DELETE NODE with node_spec
                    params = {}
                    
                    # Parse node_spec
                    if len(child.children) > 0:
                        node_spec = child.children[0]
                        if hasattr(node_spec, 'data') and node_spec.data == 'node_spec':
                            params['node_type'] = self._get_identifier(node_spec.children[0])
                            if len(node_spec.children) > 1:
                                alias_node = node_spec.children[1]
                                if hasattr(alias_node, 'data') and alias_node.data == 'alias':
                                    params['alias'] = self._get_identifier(alias_node.children[0])
                    
                    # Parse WHERE clause
                    if len(child.children) > 1:
                        where_node = child.children[1]
                        if hasattr(where_node, 'data') and where_node.data == 'condition':
                            params['where'] = [self._parse_condition(where_node)]
                    
                    ast_nodes.append(AIQLNode(
                        node_type=AIQLNodeType.DELETE_NODE,
                        parameters=params
                    ))
            elif child.data == 'top_delete_edge':
                # Handle standalone DELETE EDGE queries
                logger.info(f"Parsing top_delete_edge: {len(child.children)} children")
                # Use the proper _parse_delete_edge method
                try:
                    ast_node = self._parse_delete_edge(child)
                    if ast_node:
                        ast_nodes.append(ast_node)
                except Exception as e:
                    logger.error(f"Error parsing DELETE EDGE: {e}")
                    # Fallback: try to parse manually
                    params = {}
                    if len(child.children) > 0:
                        edge_spec = child.children[0]
                        params['edge_type'] = self._get_identifier(edge_spec.children[0])
                        # Check for SRC/DEST syntax
                        for i, c in enumerate(child.children):
                            if hasattr(c, 'value') and c.value == 'SRC':
                                if i + 1 < len(child.children):
                                    params['source_node'] = self._get_identifier(child.children[i + 1])
                            elif hasattr(c, 'value') and c.value == 'DEST':
                                if i + 1 < len(child.children):
                                    params['target_node'] = self._get_identifier(child.children[i + 1])
                        # Parse WHERE clause if present (not SRC/DEST)
                        if 'source_node' not in params and len(child.children) > 1:
                            where_node = child.children[1]
                            if hasattr(where_node, 'data') and where_node.data == 'condition':
                                params['where'] = [self._parse_condition(where_node)]
                    ast_nodes.append(AIQLNode(
                        node_type=AIQLNodeType.DELETE_EDGE,
                        parameters=params
                    ))
            elif child.data in ['create_node', 'top_create_node']:
                # Handle create_node directly (both top-level and pipeline versions)
                # CRITICAL: Use the proper _parse_create_node method instead of duplicating logic
                try:
                    ast_node = self._parse_create_node(child)
                    ast_nodes.append(ast_node)
                    continue
                except Exception as e:
                    logger.warning(f"Failed to parse create_node using _parse_create_node: {e}, falling back to legacy parsing")
                    # Fallback to legacy parsing (but this should not be needed)
                    node_spec = child.children[0]
                    node_type = self._get_identifier(node_spec.children[0])
                    
                    params = {'node_type': node_type}
                    
                    # Parse alias FIRST - look for 'AS' keyword followed by identifier
                    # Grammar: node_spec: identifier ("AS" identifier)? ("{" property_list "}")?
                    alias = None
                    for i, subchild in enumerate(node_spec.children):
                        if hasattr(subchild, 'value') and subchild.value == 'AS' and i + 1 < len(node_spec.children):
                            alias_child = node_spec.children[i + 1]
                            if hasattr(alias_child, 'data') and alias_child.data == 'identifier':
                                alias = self._get_identifier(alias_child)
                                params['alias'] = alias
                                break
                        elif hasattr(subchild, 'data') and 'alias' in str(subchild.data):
                            if hasattr(subchild, 'children') and len(subchild.children) > 0:
                                alias = self._get_identifier(subchild.children[0])
                                params['alias'] = alias
                                break
                    
                    # Parse properties if present
                    properties = {}
                    for subchild in node_spec.children:
                        if hasattr(subchild, 'data') and subchild.data == 'property_list':
                            properties = self._parse_property_list(subchild)
                            break
                    params['properties'] = properties
                    
                    # Parse unique key if present
                    if len(child.children) > 1:
                        unique_keys = self._parse_identifier_list(child.children[1])
                        params['unique_keys'] = unique_keys
                    
                    ast_nodes.append(AIQLNode(
                        node_type=AIQLNodeType.CREATE_NODE,
                        parameters={
                            'node_type': node_type,
                            'node_id': node_id,
                            'properties': properties,
                            'alias': alias
                        }
                    ))
                except Exception as e:
                    logger.error(f"Error parsing create_node: {e}")
                    # Create a basic node with just the type
                    if len(child.children) > 0:
                        node_spec = child.children[0]
                        node_type = self._get_identifier(node_spec.children[0])
                        ast_nodes.append(AIQLNode(
                            node_type=AIQLNodeType.CREATE_NODE,
                            parameters={
                                'node_type': node_type,
                                'node_id': None,
                                'properties': {},
                                'alias': None
                            }
                        ))
            elif child.data == 'set_mode_statement':
                # Handle SET MODE statement
                mode = self._get_identifier(child.children[0])
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.SET_MODE,
                    parameters={'mode': mode}
                ))
            elif child.data in ['top_drop_graph', 'drop_graph']:
                # Handle DROP GRAPH statement
                graph_name = self._get_identifier(child.children[0])
                cascade = False
                
                # Check if CASCADE is present in children
                if len(child.children) > 1:
                    for c in child.children[1:]:
                        if isinstance(c, str) and c == 'CASCADE':
                            cascade = True
                        elif hasattr(c, 'data') and 'CASCADE' in str(c.data):
                            cascade = True
                        elif hasattr(c, 'value') and c.value == 'CASCADE':
                            cascade = True
                
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.DROP_GRAPH,
                    parameters={'graph_name': graph_name, 'cascade': cascade}
                ))
            elif child.data in ['search_query_basic', 'search_query_with_weights', 'search_query_multimodal', 'search_query_namespace', 'search_query_collections', 'graph_search_query', 'graph_search_from_via_to', 'search_query']:
                # Handle search queries (DENSE, SPARSE, HYBRID, SEMANTIC, GRAPH)
                # Also handle new syntax: SEARCH NODE Person 'text' or SEARCH EDGE 'text'
                if child.data == 'search_query' and len(child.children) >= 3:
                    # Check for new syntax: SEARCH NODE identifier string or SEARCH EDGE string
                    first = child.children[0]
                    second = child.children[1]
                    third = child.children[2]
                    
                    if (hasattr(first, 'value') and first.value == 'NODE' and
                        hasattr(second, 'data') and second.data == 'identifier' and
                        hasattr(third, 'data') and third.data == 'string'):
                        # New syntax: SEARCH NODE Person 'text'
                        node_type = self._get_identifier(second)
                        query_text = self._get_string_value(third)
                        params = {
                            'query': query_text,
                            'node_type': node_type,
                            'search_type': 'SEMANTIC'
                        }
                        
                        # Parse WHERE and LIMIT if present
                        i = 3
                        while i < len(child.children):
                            current = child.children[i]
                            if hasattr(current, 'data') and current.data == 'search_condition':
                                # Parse search condition
                                if len(current.children) >= 3:
                                    field = self._get_identifier(current.children[0])
                                    op = str(current.children[1].value) if hasattr(current.children[1], 'value') else '='
                                    val = self._parse_value(current.children[2])
                                    params.setdefault('where', []).append({'field': field, 'operator': op, 'value': val})
                                i += 1
                            elif hasattr(current, 'value') and current.value == 'LIMIT':
                                if i + 1 < len(child.children):
                                    limit_node = child.children[i + 1]
                                    if hasattr(limit_node, 'data') and limit_node.data == 'number':
                                        params['limit'] = self._get_number(limit_node)
                                    i += 2
                                else:
                                    i += 1
                            else:
                                i += 1
                        
                        ast_nodes.append(AIQLNode(
                            node_type=AIQLNodeType.SEARCH_QUERY,
                            parameters=params
                        ))
                        continue
                    elif (hasattr(first, 'value') and first.value == 'EDGE' and
                          hasattr(second, 'data') and second.data == 'string'):
                        # New syntax: SEARCH EDGE 'text'
                        query_text = self._get_string_value(second)
                        params = {
                            'query': query_text,
                            'edge_only': True,
                            'search_type': 'SEMANTIC'
                        }
                        
                        # Parse WHERE and LIMIT if present
                        i = 2
                        while i < len(child.children):
                            current = child.children[i]
                            if hasattr(current, 'data') and current.data == 'search_condition':
                                if len(current.children) >= 3:
                                    field = self._get_identifier(current.children[0])
                                    op = str(current.children[1].value) if hasattr(current.children[1], 'value') else '='
                                    val = self._parse_value(current.children[2])
                                    params.setdefault('where', []).append({'field': field, 'operator': op, 'value': val})
                                i += 1
                            elif hasattr(current, 'value') and current.value == 'LIMIT':
                                if i + 1 < len(child.children):
                                    limit_node = child.children[i + 1]
                                    if hasattr(limit_node, 'data') and limit_node.data == 'number':
                                        params['limit'] = self._get_number(limit_node)
                                    i += 2
                                else:
                                    i += 1
                            else:
                                i += 1
                        
                        ast_nodes.append(AIQLNode(
                            node_type=AIQLNodeType.SEARCH_QUERY,
                            parameters=params
                        ))
                        continue
                
                # Original search query parsing
                search_query_node = self._parse_search_query(child)
                if search_query_node:
                    ast_nodes.append(search_query_node)
            elif child.data == 'hybrid_search':
                # Handle hybrid_search (can contain hybrid_search_query, hybrid_search_with_weights, etc.)
                # Parse the first child which should be the actual hybrid search query
                if len(child.children) > 0:
                    hybrid_child = child.children[0]
                    logger.debug(f"[DEBUG] hybrid_search child data: {hybrid_child.data if hasattr(hybrid_child, 'data') else 'N/A'}, children: {len(hybrid_child.children) if hasattr(hybrid_child, 'children') else 0}")
                    if hasattr(hybrid_child, 'data'):
                        if hybrid_child.data == 'hybrid_search_simple':
                            logger.debug(f"[DEBUG] Parsing hybrid_search_simple with {len(hybrid_child.children)} children")
                            # HYBRID SEARCH string "IN" identifier (WITH weight_list)? (LIMIT number)?
                            # Simple syntax: HYBRID SEARCH 'query' IN Chunk LIMIT 5
                            params = {}
                            
                            # Find string (query text) - should be first child
                            for c in hybrid_child.children:
                                if hasattr(c, 'data') and c.data == 'string':
                                    query_val = self._get_string_value(c)
                                    # Strip quotes if present
                                    if isinstance(query_val, str):
                                        query_val = query_val.strip('"\'')
                                    params['query'] = query_val
                                    break
                                elif hasattr(c, 'type') and c.type == 'STRING':
                                    query_val = self._get_string_value(c)
                                    if isinstance(query_val, str):
                                        query_val = query_val.strip('"\'')
                                    params['query'] = query_val
                                    break
                                elif isinstance(c, str) and (c.startswith('"') or c.startswith("'")):
                                    params['query'] = c.strip('"\'')
                                    break
                            
                            # Parse: "IN" identifier, WITH weights, USING MODEL string, LIMIT
                            # Grammar structure: [string, "IN", identifier, (WITH weight_list)?, (USING MODEL string)?, (LIMIT number)?]
                            # Look for identifier (node type) - should be after string and "IN"
                            # Look for weight_list, USING MODEL string, and number (limit) directly
                            found_using = False
                            for i, c in enumerate(hybrid_child.children):
                                # Skip string (already extracted)
                                if hasattr(c, 'data') and c.data == 'string':
                                    continue
                                elif hasattr(c, 'type') and c.type == 'STRING':
                                    # Check if this is part of "USING MODEL string"
                                    if found_using:
                                        # This is the model name string
                                        model_name = self._get_string_value(c)
                                        if isinstance(model_name, str):
                                            model_name = model_name.strip('"\'')
                                        params['model'] = model_name
                                        found_using = False
                                        continue
                                    continue
                                
                                # Check for "USING" keyword
                                if isinstance(c, str) and c.upper() == 'USING':
                                    found_using = True
                                    # Check if next token is "MODEL"
                                    if i + 1 < len(hybrid_child.children):
                                        next_c = hybrid_child.children[i + 1]
                                        if isinstance(next_c, str) and next_c.upper() == 'MODEL':
                                            found_using = True
                                            continue
                                
                                # Check for "MODEL" keyword
                                if isinstance(c, str) and c.upper() == 'MODEL':
                                    found_using = True
                                    continue
                                
                                # Look for identifier (node type) - this should be the one after "IN"
                                if hasattr(c, 'data') and 'identifier' in str(c.data):
                                    if 'type' not in params:
                                        params['type'] = self._get_identifier(c)
                                        continue
                                
                                # Look for weight_list
                                if hasattr(c, 'data') and c.data == 'weight_list':
                                    weights = {}
                                    for wc in c.children:
                                        if hasattr(wc, 'data') and wc.data == 'weight_item':
                                            key = self._get_identifier(wc.children[0])
                                            val = self._get_number(wc.children[1])
                                            weights[key] = val
                                    params['weights'] = weights
                                    continue
                                
                                # Look for number (limit)
                                if hasattr(c, 'data') and c.data == 'number':
                                    if 'limit' not in params:
                                        params['limit'] = self._get_number(c)
                                    continue
                            
                            # Use active namespace from executor if available
                            executor_ref = getattr(self, 'executor', None)
                            if executor_ref and hasattr(executor_ref, 'active_namespace'):
                                params['namespace'] = executor_ref.active_namespace
                            
                            # Set default limit if not provided
                            if 'limit' not in params or params['limit'] == 0:
                                params['limit'] = 10
                            
                            logger.debug(f"[DEBUG] hybrid_search_simple parsed params: {params}")
                            
                            ast_nodes.append(AIQLNode(
                                node_type=AIQLNodeType.HYBRID_SEARCH,
                                parameters=params
                            ))
                        elif hybrid_child.data == 'hybrid_search_query':
                            # HYBRID SEARCH string IN NAMESPACE identifier (USING COLLECTION identifier)? (MODE mode_type)? (LIMIT number)?
                            # Grammar structure: [string, "IN", "NAMESPACE", identifier, ...]
                            params = {}
                            
                            # Find string (query text) - should be first child
                            for c in hybrid_child.children:
                                if hasattr(c, 'data') and c.data == 'string':
                                    params['query'] = self._get_string_value(c)
                                    break
                                elif hasattr(c, 'type') and c.type == 'STRING':
                                    params['query'] = self._get_string_value(c)
                                    break
                            
                            # Parse remaining tokens: IN NAMESPACE identifier, USING COLLECTION, MODE, LIMIT
                            i = 0
                            while i < len(hybrid_child.children):
                                c = hybrid_child.children[i]
                                
                                # Check for "IN" followed by "NAMESPACE"
                                if hasattr(c, 'value') and c.value == 'IN':
                                    if i + 1 < len(hybrid_child.children):
                                        next_c = hybrid_child.children[i + 1]
                                        if hasattr(next_c, 'value') and next_c.value == 'NAMESPACE':
                                            # Next should be identifier
                                            if i + 2 < len(hybrid_child.children):
                                                params['namespace'] = self._get_identifier(hybrid_child.children[i + 2])
                                                i += 3
                                                continue
                                
                                # Check for "USING" followed by "COLLECTION"
                                if hasattr(c, 'value') and c.value == 'USING':
                                    if i + 1 < len(hybrid_child.children):
                                        next_c = hybrid_child.children[i + 1]
                                        if hasattr(next_c, 'value') and next_c.value == 'COLLECTION':
                                            if i + 2 < len(hybrid_child.children):
                                                params['collection'] = self._get_identifier(hybrid_child.children[i + 2])
                                                i += 3
                                                continue
                                
                                # Check for "MODE"
                                if hasattr(c, 'value') and c.value == 'MODE':
                                    if i + 1 < len(hybrid_child.children):
                                        params['mode'] = self._get_identifier(hybrid_child.children[i + 1])
                                        i += 2
                                        continue
                                
                                # Check for "LIMIT"
                                if hasattr(c, 'value') and c.value == 'LIMIT':
                                    if i + 1 < len(hybrid_child.children):
                                        params['limit'] = self._get_number(hybrid_child.children[i + 1])
                                        i += 2
                                        continue
                                
                                # Check for mode_type directly
                                if hasattr(c, 'data') and c.data == 'mode_type':
                                    params['mode'] = self._get_identifier(c)
                                
                                i += 1
                            
                            ast_nodes.append(AIQLNode(
                                node_type=AIQLNodeType.HYBRID_SEARCH,
                                parameters=params
                            ))
                        elif hybrid_child.data == 'hybrid_search_with_weights':
                            # HYBRID SEARCH string IN NAMESPACE identifier USING COLLECTION identifier WITH WEIGHTS weight_configuration (LIMIT number)?
                            params = {}
                            if len(hybrid_child.children) > 0:
                                params['query'] = self._get_string_value(hybrid_child.children[0])
                            # Parse namespace, collection, weights, limit
                            for i, c in enumerate(hybrid_child.children):
                                if hasattr(c, 'value') and c.value == 'NAMESPACE' and i + 1 < len(hybrid_child.children):
                                    params['namespace'] = self._get_identifier(hybrid_child.children[i + 1])
                                elif hasattr(c, 'value') and c.value == 'COLLECTION' and i + 1 < len(hybrid_child.children):
                                    params['collection'] = self._get_identifier(hybrid_child.children[i + 1])
                                elif hasattr(c, 'data') and c.data == 'weight_configuration':
                                    # Parse weights: (DENSE = number, SPARSE = number, GRAPH = number)
                                    weights = {}
                                    for wc in c.children:
                                        if hasattr(wc, 'value'):
                                            if wc.value == 'DENSE' and i + 2 < len(hybrid_child.children):
                                                weights['dense'] = self._get_number(hybrid_child.children[i + 2])
                                            elif wc.value == 'SPARSE' and i + 2 < len(hybrid_child.children):
                                                weights['sparse'] = self._get_number(hybrid_child.children[i + 2])
                                            elif wc.value == 'GRAPH' and i + 2 < len(hybrid_child.children):
                                                weights['graph'] = self._get_number(hybrid_child.children[i + 2])
                                    params['weights'] = weights
                                elif hasattr(c, 'value') and c.value == 'LIMIT' and i + 1 < len(hybrid_child.children):
                                    params['limit'] = self._get_number(hybrid_child.children[i + 1])
                            
                            ast_nodes.append(AIQLNode(
                                node_type=AIQLNodeType.HYBRID_SEARCH_WITH_WEIGHTS,
                                parameters=params
                            ))
                        elif hybrid_child.data == 'hybrid_search_with_profile':
                            # HYBRID SEARCH string IN NAMESPACE identifier USING COLLECTION identifier WITH "PROFILE" identifier (LIMIT number)?
                            params = {}
                            if len(hybrid_child.children) > 0:
                                params['query'] = self._get_string_value(hybrid_child.children[0])
                            for i, c in enumerate(hybrid_child.children):
                                if hasattr(c, 'value') and c.value == 'NAMESPACE' and i + 1 < len(hybrid_child.children):
                                    params['namespace'] = self._get_identifier(hybrid_child.children[i + 1])
                                elif hasattr(c, 'value') and c.value == 'COLLECTION' and i + 1 < len(hybrid_child.children):
                                    params['collection'] = self._get_identifier(hybrid_child.children[i + 1])
                                elif hasattr(c, 'value') and c.value == 'PROFILE' and i + 1 < len(hybrid_child.children):
                                    params['profile'] = self._get_identifier(hybrid_child.children[i + 1])
                                elif hasattr(c, 'value') and c.value == 'LIMIT' and i + 1 < len(hybrid_child.children):
                                    params['limit'] = self._get_number(hybrid_child.children[i + 1])
                            
                            ast_nodes.append(AIQLNode(
                                node_type=AIQLNodeType.HYBRID_SEARCH_WITH_PROFILE,
                                parameters=params
                            ))
                        else:
                            # Fallback: try to parse as search_query
                            search_query_node = self._parse_search_query(hybrid_child)
                            if search_query_node:
                                ast_nodes.append(search_query_node)
            elif child.data == 'search_query':
                # Handle standalone search_query
                search_query_node = self._parse_search_query(child)
                if search_query_node:
                    ast_nodes.append(search_query_node)
            elif child.data == 'top_create_namespace':
                # top_create_namespace: "CREATE" "NAMESPACE" identifier ("MODE" namespace_mode)?
                namespace_name = None
                mode = 'PERSISTENT'  # Default to PERSISTENT
                
                for c in child.children:
                    if hasattr(c, 'data'):
                        if c.data == 'identifier':
                            namespace_name = self._get_identifier(c)
                        elif c.data == 'namespace_mode':
                            # Extract mode value (INTERACTIVE or PERSISTENT)
                            # namespace_mode might be a token directly or have a child
                            if hasattr(c, 'children') and len(c.children) > 0:
                                # Check if child has value
                                mode_token = c.children[0]
                                if hasattr(mode_token, 'value'):
                                    mode = mode_token.value.upper()
                                elif hasattr(mode_token, 'type'):
                                    mode = mode_token.type.upper()
                            elif hasattr(c, 'value'):
                                mode = c.value.upper()
                            elif hasattr(c, 'type'):
                                mode = c.type.upper()
                    elif hasattr(c, 'value'):
                        if c.value == 'MODE':
                            # Mode keyword - next child should be the mode value
                            continue
                        elif namespace_name is None:
                            # Could be identifier value
                            namespace_name = c.value
                
                if namespace_name:
                    ast_nodes.append(AIQLNode(
                        node_type=AIQLNodeType.CREATE_NAMESPACE,
                        parameters={'namespace_name': namespace_name, 'mode': mode}
                    ))
            elif child.data == 'top_create_namespace_old':
                # Handle CREATE NAMESPACE
                namespace_name = self._get_identifier(child.children[0])
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.CREATE_NAMESPACE,
                    parameters={'namespace_name': namespace_name}
                ))
            elif child.data == 'top_use_namespace':
                # top_use_namespace: "USE" "NAMESPACE" identifier ("MODE" namespace_mode)?
                namespace_name = None
                mode = None  # Auto-detect if not specified
                
                for c in child.children:
                    if hasattr(c, 'data'):
                        if c.data == 'identifier':
                            namespace_name = self._get_identifier(c)
                        elif c.data == 'namespace_mode':
                            # Extract mode value (INTERACTIVE or PERSISTENT)
                            # namespace_mode might be a token directly or have a child
                            if hasattr(c, 'children') and len(c.children) > 0:
                                # Check if child has value
                                mode_token = c.children[0]
                                if hasattr(mode_token, 'value'):
                                    mode = mode_token.value.upper()
                                elif hasattr(mode_token, 'type'):
                                    mode = mode_token.type.upper()
                            elif hasattr(c, 'value'):
                                mode = c.value.upper()
                            elif hasattr(c, 'type'):
                                mode = c.type.upper()
                    elif hasattr(c, 'value'):
                        if c.value == 'MODE':
                            # Mode keyword - next child should be the mode value
                            continue
                        elif namespace_name is None:
                            # Could be identifier value
                            namespace_name = c.value
                
                if namespace_name:
                    params = {'namespace_name': namespace_name}
                    if mode:
                        params['mode'] = mode
                    ast_nodes.append(AIQLNode(
                        node_type=AIQLNodeType.USE_NAMESPACE,
                        parameters=params
                    ))
            elif child.data == 'top_use_namespace_old':
                # Handle USE NAMESPACE
                namespace_name = self._get_identifier(child.children[0])
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.USE_NAMESPACE,
                    parameters={'namespace_name': namespace_name}
                ))
            elif child.data == 'top_show_namespaces':
                # Handle SHOW NAMESPACES
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.SHOW_NAMESPACES,
                    parameters={}
                ))
            elif child.data == 'top_show_collections':
                # Handle SHOW COLLECTIONS
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.SHOW,
                    parameters={'target': 'COLLECTIONS', 'show_type': 'COLLECTIONS'}
                ))
            elif child.data == 'aggregate_query':
                # Handle AGGREGATE query
                # aggregate_query: "AGGREGATE" aggregate_function "(" (qualified_identifier | "*") ")" ("FROM" node_spec)? ("WHERE" condition)?
                # Note: In Lark, token choices like aggregate_function might not preserve the token in children
                # We need to check the original query or use a different approach
                func_name = None
                arg = None
                from_node = None
                where_condition = None
                
                # Try to extract function name from the original query string as fallback
                query_upper = getattr(self, '_current_query', '').upper()
                for func in ['COUNT', 'SUM', 'AVG', 'MIN', 'MAX']:
                    if f'AGGREGATE {func}' in query_upper:
                        func_name = func
                        logger.debug(f"Found function from query string: {func_name}")
                        break
                
                # Parse children: AGGREGATE, aggregate_function, "(", arg, ")", optional FROM, optional WHERE
                i = 0
                while i < len(child.children):
                    c = child.children[i]
                    if hasattr(c, 'data'):
                        if c.data == 'aggregate_function':
                            # aggregate_function is a token choice - try to extract from children or node itself
                            if c.children and len(c.children) > 0:
                                first_child = c.children[0]
                                if hasattr(first_child, 'type') and first_child.type in ['COUNT', 'SUM', 'AVG', 'MIN', 'MAX']:
                                    func_name = first_child.type
                                elif hasattr(first_child, 'value'):
                                    func_name = str(first_child.value).upper()
                                else:
                                    func_name = self._get_identifier(first_child)
                            # If still no func_name, it was already set from query string above
                        elif c.data == 'qualified_identifier':
                            arg = self._get_qualified_identifier(c)
                        elif c.data == 'node_spec':
                            from_node = self._get_identifier(c.children[0]) if c.children else None
                        elif c.data == 'condition':
                            where_condition = self._parse_condition(c)
                    elif hasattr(c, 'value'):
                        if c.value == '*':
                            arg = '*'
                        elif c.value == 'FROM' and i + 1 < len(child.children):
                            next_c = child.children[i + 1]
                            if hasattr(next_c, 'data') and next_c.data == 'node_spec':
                                from_node = self._get_identifier(next_c.children[0]) if next_c.children else None
                                i += 1
                        elif c.value == 'WHERE' and i + 1 < len(child.children):
                            next_c = child.children[i + 1]
                            if hasattr(next_c, 'data') and next_c.data == 'condition':
                                where_condition = self._parse_condition(next_c)
                                i += 1
                    i += 1
                
                if func_name:
                    logger.debug(f"Creating AGGREGATION node: function={func_name}, arg={arg}, from={from_node}")
                    ast_nodes.append(AIQLNode(
                        node_type=AIQLNodeType.AGGREGATION,
                        parameters={
                            'function': func_name,
                            'arg': arg or '*',
                            'from': from_node,
                            'where': where_condition
                        }
                    ))
                else:
                    logger.warning(f"aggregate_query parsed but no function_name found")
                    i += 1
                
                if func_name:
                    logger.debug(f"Creating AGGREGATION node: function={func_name}, arg={arg}, from={from_node}")
                    ast_nodes.append(AIQLNode(
                        node_type=AIQLNodeType.AGGREGATION,
                        parameters={
                            'function': func_name,
                            'arg': arg or '*',
                            'from': from_node,
                            'where': where_condition
                        }
                    ))
                else:
                    logger.warning(f"aggregate_query parsed but no function_name found. Children: {[str(c) for c in child.children]}")
            elif child.data == 'top_create_index':
                # Handle CREATE INDEX
                # top_create_index: "CREATE" "INDEX" identifier "ON" identifier "(" identifier_list ")" "TYPE" index_type ("MODEL" "=" (string | variable))?
                index_name = None
                node_type = None
                fields = []
                index_type = None
                model = None
                
                # Parse children: index_name, ON, node_type, fields, TYPE, index_type, optional MODEL
                i = 0
                found_on = False
                found_type = False
                while i < len(child.children):
                    c = child.children[i]
                    if hasattr(c, 'data'):
                        if c.data == 'identifier':
                            if index_name is None:
                                index_name = self._get_identifier(c)
                            elif found_on and node_type is None:
                                node_type = self._get_identifier(c)
                        elif c.data == 'identifier_list':
                            # Extract field names
                            for field_child in c.children:
                                if hasattr(field_child, 'data') and field_child.data == 'identifier':
                                    fields.append(self._get_identifier(field_child))
                        elif c.data == 'index_type':
                            # index_type is a token, get its value
                            if c.children and len(c.children) > 0:
                                index_type = str(c.children[0].value) if hasattr(c.children[0], 'value') else str(c.children[0])
                            else:
                                index_type = str(c.value) if hasattr(c, 'value') else None
                        elif c.data in ['string', 'variable']:
                            # MODEL value
                            if c.data == 'string':
                                model = self._get_string_value(c)
                            elif c.data == 'variable':
                                var_name = self._get_identifier(c.children[0]) if c.children else None
                                if var_name and var_name.startswith('$'):
                                    var_name = var_name[1:]
                                if var_name in self.variables:
                                    model = self.variables[var_name]
                                else:
                                    model = f"${var_name}"
                    elif hasattr(c, 'value'):
                        # Check for tokens
                        if c.value == 'ON':
                            found_on = True
                        elif c.value == 'TYPE':
                            found_type = True
                            # Look ahead for index_type token
                            if i + 1 < len(child.children):
                                next_c = child.children[i + 1]
                                if hasattr(next_c, 'value') and next_c.value in ['SPARSE', 'DENSE', 'VECTOR', 'GRAPH']:
                                    index_type = str(next_c.value).upper()
                                    i += 1  # Skip the index_type token
                        elif found_type and index_type is None and c.value in ['SPARSE', 'DENSE', 'VECTOR', 'GRAPH']:
                            # Found index_type token
                            index_type = str(c.value).upper()
                    i += 1
                
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.CREATE_INDEX,
                    parameters={
                        'index_name': index_name,
                        'node_type': node_type,
                        'fields': fields,
                        'index_type': index_type,
                        'model': model
                    }
                ))
            elif child.data == 'top_show_indexes':
                # Handle SHOW INDEXES
                # top_show_indexes: "SHOW" "INDEXES" ("IN" "COLLECTION" identifier)?
                collection = None
                
                # Check for "IN COLLECTION" clause
                for i, c in enumerate(child.children):
                    if hasattr(c, 'value') and c.value == 'IN' and i + 2 < len(child.children):
                        if hasattr(child.children[i+1], 'value') and child.children[i+1].value == 'COLLECTION':
                            collection = self._get_identifier(child.children[i+2])
                
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.SHOW,
                    parameters={'target': 'INDEXES', 'show_type': 'INDEXES', 'collection': collection}
                ))
            elif child.data == 'top_show_pipelines':
                # Handle SHOW PIPELINES
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.SHOW,
                    parameters={'target': 'PIPELINES', 'show_type': 'PIPELINES'}
                ))
            elif child.data == 'top_match':
                # Handle both Cypher-style MATCH queries and simple "MATCH NODE" syntax
                # Grammar: "MATCH" match_pattern (return_clause)? ... OR "MATCH" "NODE" identifier ("AS" identifier)? ("WHERE" condition)? ...
                params = {}
                match_pattern = None
                return_clause = None
                order_by = None
                limit = None
                where_clause = None  # CRITICAL: Initialize where_clause to avoid UnboundLocalError
                
                # Check if this is the simple "MATCH NODE" syntax (no match_pattern child)
                has_match_pattern = False
                node_type = None
                node_alias = None
                
                # Find match_pattern, return_clause, order_by, limit, where_clause
                for c in child.children:
                    if hasattr(c, 'data'):
                        if c.data == 'match_pattern':
                            match_pattern = c
                            has_match_pattern = True
                        elif c.data == 'return_clause':
                            return_clause = c
                        elif c.data == 'order_by_clause':
                            order_by = self._parse_order_by_clause(c)
                        elif c.data == 'where_clause':
                            # WHERE clause can appear after match_pattern
                            where_clause = self._parse_where_clause(c)
                        elif c.data == 'condition':
                            where_clause = [self._parse_condition(c)]
                        elif c.data == 'identifier':
                            # This might be the node type in "MATCH NODE identifier"
                            if not has_match_pattern and node_type is None:
                                node_type = self._get_identifier(c)
                        elif c.data in ['number', 'variable']:
                            limit = self._get_value(c)
                        elif c.data == 'limit_clause':
                            # LIMIT clause - extract the number
                            for limit_child in c.children:
                                if hasattr(limit_child, 'data') and limit_child.data in ['number', 'variable']:
                                    limit = self._get_value(limit_child)
                                    break
                                elif hasattr(limit_child, 'type') and limit_child.type == 'NUMBER':
                                    limit = int(limit_child.value)
                                    break
                    elif hasattr(c, 'value'):
                        # Check for "NODE" keyword or "AS" keyword
                        if c.value == 'NODE' and not has_match_pattern:
                            # This is "MATCH NODE" syntax - next identifier is the node type
                            pass  # Will be handled by identifier above
                        elif c.value == 'AS' and not has_match_pattern:
                            # Next identifier is the alias
                            for i, next_c in enumerate(child.children):
                                if next_c == c and i + 1 < len(child.children):
                                    next_identifier = child.children[i + 1]
                                    if hasattr(next_identifier, 'data') and next_identifier.data == 'identifier':
                                        node_alias = self._get_identifier(next_identifier)
                                    break
                
                # Parse match_pattern to extract nodes and edges
                nodes = []
                edges = []
                # where_clause may already be set from above parsing, but ensure it's initialized
                if where_clause is None:
                    where_clause = None
                
                # Handle simple "MATCH NODE" syntax
                if not has_match_pattern and node_type:
                    # Simple "MATCH NODE Entity WHERE ... RETURN ..." syntax
                    params = {
                        'node_type': node_type,
                        'operation': 'MATCH_NODE'
                    }
                    if node_alias:
                        params['alias'] = node_alias
                    if where_clause:
                        params['where'] = where_clause
                    if return_clause:
                        params['return'] = self._parse_return_clause(return_clause)
                    if order_by:
                        params['order_by'] = order_by
                    if limit:
                        # Extract limit value properly - handle Tree objects
                        limit_value = None
                        if hasattr(limit, 'data'):
                            if limit.data in ['number', 'variable']:
                                limit_value = self._get_value(limit)
                            elif limit.data == 'limit_clause':
                                # LIMIT clause - extract the number from children
                                for limit_child in limit.children:
                                    if hasattr(limit_child, 'data') and limit_child.data in ['number', 'variable']:
                                        limit_value = self._get_value(limit_child)
                                        break
                                    elif hasattr(limit_child, 'type') and limit_child.type == 'NUMBER':
                                        limit_value = int(limit_child.value)
                                        break
                            else:
                                # Try to extract value anyway
                                try:
                                    limit_value = self._get_value(limit)
                                except:
                                    # If that fails, try to get number from children
                                    if hasattr(limit, 'children'):
                                        for child in limit.children:
                                            if hasattr(child, 'type') and child.type == 'NUMBER':
                                                limit_value = int(child.value)
                                                break
                                            elif hasattr(child, 'value') and str(child.value).isdigit():
                                                limit_value = int(child.value)
                                                break
                        elif isinstance(limit, (int, float)):
                            limit_value = int(limit)
                        elif isinstance(limit, str) and limit.isdigit():
                            limit_value = int(limit)
                        else:
                            # Last resort: try to convert
                            try:
                                limit_value = int(limit)
                            except:
                                limit_value = limit
                        if limit_value is not None:
                            params['limit'] = limit_value
                    
                    ast_nodes.append(AIQLNode(
                        node_type=AIQLNodeType.MATCH,
                        parameters=params
                    ))
                    continue
                
                if match_pattern:
                    match_path = None
                    for pattern_child in match_pattern.children:
                        if hasattr(pattern_child, 'data'):
                            if pattern_child.data == 'match_path':
                                match_path = pattern_child
                            elif pattern_child.data == 'condition':
                                where_clause = [self._parse_condition(pattern_child)]
                            elif pattern_child.data == 'where_clause':
                                where_clause = self._parse_where_clause(pattern_child)
                
                # Also check for WHERE clause outside match_pattern (after MATCH)
                for c in child.children:
                    if hasattr(c, 'data') and c.data == 'where_clause':
                        where_clause = self._parse_where_clause(c)
                        break
                
                if match_pattern:
                    match_path = None
                    for pattern_child in match_pattern.children:
                        if hasattr(pattern_child, 'data'):
                            if pattern_child.data == 'match_path':
                                match_path = pattern_child
                    
                    if match_path:
                        # Parse nodes and edges from match_path
                        for path_child in match_path.children:
                            if hasattr(path_child, 'data'):
                                if path_child.data == 'match_node':
                                    # Extract node info
                                    # Grammar: "(" identifier (":" identifier)? ("AS" identifier)? ("{" property_list "}")? ")"
                                    # So structure is: identifier (alias), then optional ":" identifier (type), then optional "AS" identifier, then optional property_list
                                    node_type = None
                                    node_alias = None
                                    node_props = {}
                                    as_alias = None
                                    
                                    # Parse children in order
                                    i = 0
                                    while i < len(path_child.children):
                                        nc = path_child.children[i]
                                        if hasattr(nc, 'data'):
                                            if nc.data == 'identifier':
                                                # First identifier is the alias (or type if no colon follows)
                                                if i + 1 < len(path_child.children):
                                                    next_nc = path_child.children[i + 1]
                                                    # Check if next is colon (then this is alias, next identifier is type)
                                                    if hasattr(next_nc, 'value') and next_nc.value == ':':
                                                        node_alias = self._get_identifier(nc)
                                                        i += 2  # Skip colon
                                                        if i < len(path_child.children) and hasattr(path_child.children[i], 'data') and path_child.children[i].data == 'identifier':
                                                            node_type = self._get_identifier(path_child.children[i])
                                                            i += 1
                                                    else:
                                                        # No colon, this is the type (no alias)
                                                        node_type = self._get_identifier(nc)
                                                        i += 1
                                                else:
                                                    # Last identifier, assume it's the type
                                                    node_type = self._get_identifier(nc)
                                                    i += 1
                                            elif nc.data == 'property_list':
                                                node_props = self._parse_property_list(nc)
                                                i += 1
                                            else:
                                                i += 1
                                        elif hasattr(nc, 'value') and nc.value == 'AS':
                                            # AS keyword - next identifier is the alias
                                            i += 1
                                            if i < len(path_child.children) and hasattr(path_child.children[i], 'data') and path_child.children[i].data == 'identifier':
                                                as_alias = self._get_identifier(path_child.children[i])
                                                i += 1
                                        else:
                                            i += 1
                                    
                                    # Use AS alias if provided, otherwise use the parsed alias
                                    final_alias = as_alias if as_alias else node_alias
                                    nodes.append({'type': node_type, 'alias': final_alias, 'properties': node_props})
                                elif path_child.data == 'match_edge_arrow':
                                    # Extract edge from match_edge_arrow
                                    # Grammar: "-" match_edge "->" | "<-" match_edge "-" | "-" match_edge "-"
                                    # match_edge: "[" (identifier ":")? ":"? identifier ("|" identifier)* ...]
                                    for edge_child in path_child.children:
                                        if hasattr(edge_child, 'data') and edge_child.data == 'match_edge':
                                            # Extract edge info
                                            # Structure: (identifier ":")? ":"? identifier
                                            # First identifier (if followed by ":") is alias, then ":" then identifier is type
                                            # Or just identifier is type (no alias)
                                            edge_type = None
                                            edge_alias = None
                                            edge_props = {}
                                            as_alias = None
                                            
                                            # Parse children in order
                                            i = 0
                                            while i < len(edge_child.children):
                                                ec = edge_child.children[i]
                                                if hasattr(ec, 'data'):
                                                    if ec.data == 'identifier':
                                                        # Check if this is followed by colon (alias:type pattern)
                                                        if i + 1 < len(edge_child.children):
                                                            next_ec = edge_child.children[i + 1]
                                                            if hasattr(next_ec, 'value') and next_ec.value == ':':
                                                                edge_alias = self._get_identifier(ec)
                                                                i += 2  # Skip colon
                                                                # Next identifier should be the type
                                                                if i < len(edge_child.children) and hasattr(edge_child.children[i], 'data') and edge_child.children[i].data == 'identifier':
                                                                    edge_type = self._get_identifier(edge_child.children[i])
                                                                    i += 1
                                                            else:
                                                                # No colon, this is the type
                                                                edge_type = self._get_identifier(ec)
                                                                i += 1
                                                        else:
                                                            # Last identifier, assume type
                                                            edge_type = self._get_identifier(ec)
                                                            i += 1
                                                    elif ec.data == 'property_list':
                                                        edge_props = self._parse_property_list(ec)
                                                        i += 1
                                                    else:
                                                        i += 1
                                                elif hasattr(ec, 'value') and ec.value == 'AS':
                                                    # AS keyword
                                                    i += 1
                                                    if i < len(edge_child.children) and hasattr(edge_child.children[i], 'data') and edge_child.children[i].data == 'identifier':
                                                        as_alias = self._get_identifier(edge_child.children[i])
                                                        i += 1
                                                else:
                                                    i += 1
                                            
                                            final_alias = as_alias if as_alias else edge_alias
                                            edges.append({'type': edge_type, 'alias': final_alias, 'properties': edge_props})
                
                params = {
                    'pattern': {'nodes': nodes, 'edges': edges},
                    'where': where_clause or [],
                    'return': return_clause,
                    'order_by': order_by,
                    'limit': limit
                }
                
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.MATCH_ENTITY,
                    parameters=params
                ))
            elif child.data in ['top_delete_node', 'delete_node']:
                for i in range(len(child.children) - 1):
                    if hasattr(child.children[i], 'value') and child.children[i].value == 'LIMIT' and i + 1 < len(child.children):
                        limit_value = child.children[i + 1]
                        if hasattr(limit_value, 'data'):
                            if limit_value.data == 'number':
                                params['limit'] = self._get_number(limit_value)
                            elif limit_value.data == 'variable':
                                var_name = self._get_identifier(limit_value.children[0]) if limit_value.children and len(limit_value.children) > 0 else str(limit_value)
                                if isinstance(var_name, str) and var_name.startswith('$'):
                                    var_name = var_name[1:]
                                if var_name in self.variables:
                                    params['limit'] = self.variables[var_name]
                                else:
                                    params['limit'] = f"${var_name}"
                        else:
                            params['limit'] = self._get_number(limit_value)
                        break
                
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.MATCH_ENTITY,
                    parameters=params
                ))
            elif child.data == 'top_pagerank':
                # Handle PAGERANK query: "PAGERANK" ("ON" identifier)? (("PARAMETERS" "(" parameter_dict ")") | ("ITERATIONS" number)? ("DAMPING_FACTOR" | "DAMPING") number?)? ("RETURN" return_clause)? ("ORDER" "BY" order_by_clause)? ("LIMIT" (number | variable))?
                params = {}
                i = 0
                while i < len(child.children):
                    c = child.children[i]
                    if hasattr(c, 'value') and c.value == 'ON' and i + 1 < len(child.children):
                        params['on'] = self._get_identifier(child.children[i + 1])
                        i += 2
                    elif hasattr(c, 'data') and c.data == 'parameter_dict':
                        params.update(self._extract_parameter_dict(c))
                        i += 1
                    elif hasattr(c, 'value') and c.value in ['ITERATIONS', 'DAMPING_FACTOR', 'DAMPING'] and i + 1 < len(child.children):
                        key = c.value.lower().replace('_', '_')
                        params[key] = self._get_number(child.children[i + 1])
                        i += 2
                    elif hasattr(c, 'data') and c.data == 'return_clause':
                        params['return'] = self._parse_return_clause(c)
                        i += 1
                    elif hasattr(c, 'value') and c.value == 'RETURN' and i + 1 < len(child.children):
                        # Handle RETURN return_expression ("," return_expression)* for PAGERANK
                        return_exprs = []
                        j = i + 1
                        while j < len(child.children):
                            expr_child = child.children[j]
                            if hasattr(expr_child, 'data') and expr_child.data == 'return_expression':
                                # Extract identifier from return_expression
                                if expr_child.children:
                                    expr_id = self._get_identifier(expr_child.children[0]) if hasattr(expr_child.children[0], 'data') and expr_child.children[0].data == 'identifier' else str(expr_child.children[0])
                                    return_exprs.append(expr_id)
                            elif hasattr(expr_child, 'data') and expr_child.data == 'identifier':
                                return_exprs.append(self._get_identifier(expr_child))
                            elif hasattr(expr_child, 'value') and expr_child.value == ',':
                                j += 1
                                continue
                            else:
                                break
                            j += 1
                        if return_exprs:
                            params['return'] = return_exprs
                        i = j
                    elif hasattr(c, 'data') and c.data == 'order_by_clause':
                        params['order_by'] = self._parse_order_by_clause(c)
                        i += 1
                    elif hasattr(c, 'value') and c.value == 'LIMIT' and i + 1 < len(child.children):
                        limit_val = child.children[i + 1]
                        if hasattr(limit_val, 'data') and limit_val.data == 'number':
                            params['limit'] = self._get_number(limit_val)
                        elif hasattr(limit_val, 'data') and limit_val.data == 'variable':
                            var_name = self._get_identifier(limit_val.children[0]) if limit_val.children else None
                            params['limit'] = f"${var_name}" if var_name else None
                        i += 2
                    else:
                        i += 1
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.PAGERANK,
                    parameters=params
                ))
            elif child.data == 'top_hop_query':
                # Handle HOP query
                params = {}
                if len(child.children) >= 2:
                    params['from_type'] = self._get_identifier(child.children[0])
                    params['to_type'] = self._get_identifier(child.children[1])
                if len(child.children) >= 3:
                    params['depth'] = self._get_number(child.children[2])
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.TRAVERSE,
                    parameters=params
                ))
            elif child.data == 'top_shortest_path':
                # Handle SHORTEST_PATH query: "SHORTEST_PATH" "FROM" (string | variable | typed_node_ref) "TO" (string | variable | typed_node_ref) ("VIA" "EDGES" "[" identifier_list "]")? ("MAX_DEPTH" number)? ("WEIGHT_FIELD" identifier)? ("RETURN" return_clause)?
                # The grammar parses it as: top_shortest_path -> [string (FROM), string (TO), string_list (VIA EDGES), ...]
                params = {}
                source = None
                target = None
                via_edges = []
                
                # Parse children - first two should be strings (FROM and TO values)
                if len(child.children) >= 2:
                    # First child is FROM value (string)
                    source_child = child.children[0]
                    if hasattr(source_child, 'data'):
                        if source_child.data == 'string':
                            source = self._get_string(source_child)
                        elif source_child.data == 'identifier':
                            source = self._get_identifier(source_child)
                    elif hasattr(source_child, 'value'):
                        source = str(source_child.value).strip('"\'')
                    
                    # Second child is TO value (string)
                    target_child = child.children[1]
                    if hasattr(target_child, 'data'):
                        if target_child.data == 'string':
                            target = self._get_string(target_child)
                        elif target_child.data == 'identifier':
                            target = self._get_identifier(target_child)
                    elif hasattr(target_child, 'value'):
                        target = str(target_child.value).strip('"\'')
                    
                    # Third child might be string_list (VIA EDGES)
                    if len(child.children) >= 3:
                        edges_child = child.children[2]
                        if hasattr(edges_child, 'data') and edges_child.data == 'string_list':
                            for edge_item in edges_child.children:
                                if hasattr(edge_item, 'data'):
                                    if edge_item.data == 'string':
                                        via_edges.append(self._get_string(edge_item).strip('"\''))
                                    elif edge_item.data == 'identifier':
                                        via_edges.append(self._get_identifier(edge_item))
                                elif hasattr(edge_item, 'value'):
                                    via_edges.append(str(edge_item.value).strip('"\''))
                
                params['source'] = source
                params['target'] = target
                if via_edges:
                    params['via_edges'] = via_edges
                
                # Parse FROM and TO (old method - keep for compatibility)
                source_found = False
                target_found = False
                i = 0
                while i < len(child.children):
                    c = child.children[i]
                    if hasattr(c, 'value') and c.value == 'FROM' and i + 1 < len(child.children):
                        next_c = child.children[i + 1]
                        if hasattr(next_c, 'data') and next_c.data == 'string':
                            params['source'] = self._get_string_value(next_c)
                        elif hasattr(next_c, 'data') and next_c.data == 'variable':
                            var_name = self._get_identifier(next_c.children[0]) if next_c.children else None
                            params['source'] = f"${var_name}" if var_name else None
                        elif hasattr(next_c, 'data') and next_c.data == 'typed_node_ref':
                            type_part = self._get_identifier(next_c.children[0]) if next_c.children else ''
                            id_part = self._get_identifier(next_c.children[1]) if len(next_c.children) > 1 else ''
                            params['source'] = f"{type_part}:{id_part}"
                        elif hasattr(next_c, 'data') and next_c.data == 'identifier':
                            params['source'] = self._get_identifier(next_c)
                        source_found = True
                        i += 2
                    elif hasattr(c, 'value') and c.value == 'TO' and i + 1 < len(child.children):
                        next_c = child.children[i + 1]
                        if hasattr(next_c, 'data') and next_c.data == 'string':
                            params['target'] = self._get_string_value(next_c)
                        elif hasattr(next_c, 'data') and next_c.data == 'variable':
                            var_name = self._get_identifier(next_c.children[0]) if next_c.children else None
                            params['target'] = f"${var_name}" if var_name else None
                        elif hasattr(next_c, 'data') and next_c.data == 'typed_node_ref':
                            type_part = self._get_identifier(next_c.children[0]) if next_c.children else ''
                            id_part = self._get_identifier(next_c.children[1]) if len(next_c.children) > 1 else ''
                            params['target'] = f"{type_part}:{id_part}"
                        elif hasattr(next_c, 'data') and next_c.data == 'identifier':
                            params['target'] = self._get_identifier(next_c)
                        target_found = True
                        i += 2
                    elif hasattr(c, 'value') and c.value == 'VIA' and i + 2 < len(child.children):
                        if hasattr(child.children[i+1], 'value') and child.children[i+1].value == 'EDGES':
                            edges_node = child.children[i + 2]
                            edge_types = []
                            if hasattr(edges_node, 'data'):
                                if edges_node.data == 'identifier_list':
                                    for edge_child in edges_node.children:
                                        if hasattr(edge_child, 'data') and edge_child.data == 'identifier':
                                            edge_types.append(self._get_identifier(edge_child))
                                        elif hasattr(edge_child, 'data') and edge_child.data == 'string':
                                            # Handle quoted strings in identifier_list
                                            edge_types.append(self._get_string_value(edge_child))
                                elif edges_node.data == 'string_list':
                                    for edge_child in edges_node.children:
                                        if hasattr(edge_child, 'data') and edge_child.data == 'string':
                                            edge_types.append(self._get_string_value(edge_child))
                                elif hasattr(edges_node, 'value'):
                                    # Handle direct bracket/paren content: ["RELATED_TO","MENTIONS"]
                                    # Try to parse as a list of strings/identifiers
                                    if edges_node.value in ['[', '(']:
                                        # Parse children as edge types
                                        for edge_child in edges_node.children:
                                            if hasattr(edge_child, 'data'):
                                                if edge_child.data == 'identifier':
                                                    edge_types.append(self._get_identifier(edge_child))
                                                elif edge_child.data == 'string':
                                                    edge_types.append(self._get_string_value(edge_child))
                                            elif hasattr(edge_child, 'value') and edge_child.value not in [',', '[', ']', '(', ')']:
                                                # Try to extract as string
                                                edge_types.append(str(edge_child.value))
                            elif hasattr(edges_node, 'children'):
                                # Fallback: try to parse children directly
                                for edge_child in edges_node.children:
                                    if hasattr(edge_child, 'data'):
                                        if edge_child.data == 'identifier':
                                            edge_types.append(self._get_identifier(edge_child))
                                        elif edge_child.data == 'string':
                                            edge_types.append(self._get_string_value(edge_child))
                                    elif hasattr(edge_child, 'value') and edge_child.value not in [',', '[', ']', '(', ')']:
                                        edge_types.append(str(edge_child.value))
                            params['via_edges'] = edge_types
                            i += 3
                    elif hasattr(c, 'value') and c.value == 'MAX_DEPTH' and i + 1 < len(child.children):
                        params['max_depth'] = self._get_number(child.children[i + 1])
                        i += 2
                    elif hasattr(c, 'value') and c.value == 'WEIGHT_FIELD' and i + 1 < len(child.children):
                        params['weight_field'] = self._get_identifier(child.children[i + 1])
                        i += 2
                    elif hasattr(c, 'data') and c.data == 'return_clause':
                        params['return'] = self._parse_return_clause(c)
                        i += 1
                    else:
                        i += 1
                
                # Parse MAX_DEPTH
                for i, c in enumerate(child.children):
                    if hasattr(c, 'value') and c.value == 'MAX_DEPTH' and i + 1 < len(child.children):
                        params['max_depth'] = self._get_number(child.children[i + 1])
                        break
                
                # Parse WEIGHT_FIELD
                for i, c in enumerate(child.children):
                    if hasattr(c, 'value') and c.value == 'WEIGHT_FIELD' and i + 1 < len(child.children):
                        params['weight_attribute'] = self._get_identifier(child.children[i + 1])
                        break
                
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.SHORTEST_PATH,
                    parameters=params
                ))
            elif child.data == 'top_community_detection':
                # Handle COMMUNITY DETECTION query
                params = {}
                # Parse children for algorithm, resolution, iterations
                for c in child.children:
                    if hasattr(c, 'data') and 'algorithm' in str(c.data).lower():
                        params['algorithm'] = self._get_string_value(c.children[0])
                    elif isinstance(c, (int, float)) or (hasattr(c, 'type') and c.type == 'NUMBER'):
                        if 'resolution' not in params:
                            params['resolution'] = self._get_number(c) if hasattr(c, 'type') else c
                        else:
                            params['iterations'] = self._get_number(c) if hasattr(c, 'type') else c
                    elif hasattr(c, 'data') and 'identifier' in str(c.data):
                        params['on'] = self._get_identifier(c)
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.COMMUNITY_DETECTION,
                    parameters=params
                ))
            elif child.data == 'top_graph_summary':
                # Handle GRAPH SUMMARY query
                params = {'target': 'GRAPH_SUMMARY'}
                
                # Parse ON clause with node types
                for c in child.children:
                    if hasattr(c, 'data') and 'identifier' in str(c.data):
                        if 'on' not in params:
                            params['on'] = []
                        params['on'].append(self._get_identifier(c))
                
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.SHOW,
                    parameters=params
                ))
            elif child.data == 'top_create_collection':
                # Handle CREATE COLLECTION with schema
                ast_nodes.append(self._parse_create_collection(child))
            elif child.data == 'top_use_collection':
                # Handle USE COLLECTION
                collection_name = self._get_identifier(child.children[0])
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.USE_GRAPH,
                    parameters={'graph_name': collection_name}
                ))
            elif child.data == 'top_traverse':
                # Handle TRAVERSE query
                # Grammar supports two forms:
                # 1. "TRAVERSE" traverse_spec where_stage? return_clause? ("LIMIT" (number | variable))?
                # 2. "TRAVERSE" "FROM" identifier ("WHERE" condition)? ... ("VIA" ("(" identifier_list ")" | identifier))? ("TO" identifier)? ("MAX" "DEPTH" number)? ...
                params = {}
                
                # Check if this is the "TRAVERSE FROM ..." syntax (second form)
                has_from_syntax = False
                for i, c in enumerate(child.children):
                    if hasattr(c, 'value') and c.value == 'FROM':
                        has_from_syntax = True
                        # Extract FROM identifier
                        if i + 1 < len(child.children):
                            from_identifier = child.children[i + 1]
                            params['from_type'] = self._get_identifier(from_identifier) if hasattr(from_identifier, 'data') else (from_identifier.value if hasattr(from_identifier, 'value') else str(from_identifier))
                        break
                
                # Parse traverse_spec (FROM ... TO syntax or VIA EDGE ... TO syntax)
                if not has_from_syntax and len(child.children) >= 1:
                    spec = child.children[0]
                    if hasattr(spec, 'data'):
                        if spec.data == 'from_traversal':
                            # FROM identifier TO identifier
                            if len(spec.children) >= 2:
                                params['from_type'] = self._get_identifier(spec.children[0])
                                params['to_type'] = self._get_identifier(spec.children[1])
                        elif spec.data == 'verbose_traversal':
                            # Handle VIA EDGE ... TO syntax: node_ref VIA EDGE edge_ref TO node_ref
                            # Find node_ref, VIA, EDGE, edge_ref, TO/DEST, node_ref pattern
                            from_type = None
                            edge_type = None
                            to_type = None
                            
                            # Look for pattern: identifier VIA EDGE identifier TO identifier
                            for i, c in enumerate(spec.children):
                                if hasattr(c, 'value'):
                                    if c.value == 'VIA':
                                        # Next should be EDGE, then edge_ref, then TO/DEST, then node_ref
                                        if i + 1 < len(spec.children) and hasattr(spec.children[i+1], 'value') and spec.children[i+1].value == 'EDGE':
                                            if i + 2 < len(spec.children):
                                                edge_ref = spec.children[i+2]
                                                edge_type = self._get_identifier(edge_ref) if hasattr(edge_ref, 'data') else (edge_ref.value if hasattr(edge_ref, 'value') else str(edge_ref))
                                                params['edge_type'] = edge_type
                                                # Look for TO or DEST
                                                if i + 3 < len(spec.children):
                                                    dest_token = spec.children[i+3]
                                                    if hasattr(dest_token, 'value') and dest_token.value in ['TO', 'DEST']:
                                                        if i + 4 < len(spec.children):
                                                            to_node = spec.children[i+4]
                                                            to_type = self._get_identifier(to_node) if hasattr(to_node, 'data') else (to_node.value if hasattr(to_node, 'value') else str(to_node))
                                                            params['to_type'] = to_type
                                    elif c.value in ['TO', 'DEST']:
                                        # Found TO/DEST, previous should be edge, next should be node
                                        if i > 0 and i + 1 < len(spec.children):
                                            edge_ref = spec.children[i-1]
                                            edge_type = self._get_identifier(edge_ref) if hasattr(edge_ref, 'data') else (edge_ref.value if hasattr(edge_ref, 'value') else str(edge_ref))
                                            params['edge_type'] = edge_type
                                            to_node = spec.children[i+1]
                                            to_type = self._get_identifier(to_node) if hasattr(to_node, 'data') else (to_node.value if hasattr(to_node, 'value') else str(to_node))
                                            params['to_type'] = to_type
                                elif hasattr(c, 'data') and c.data == 'node_ref':
                                    # First node_ref is the from_type
                                    if from_type is None:
                                        from_type = self._get_identifier(c)
                                        params['from_type'] = from_type
                            
                            # Also try to extract from identifier tokens directly
                            if 'from_type' not in params:
                                for i, c in enumerate(spec.children):
                                    if hasattr(c, 'value') and c.value not in ['VIA', 'EDGE', 'TO', 'DEST'] and not isinstance(c.value, (int, float)):
                                        if 'from_type' not in params:
                                            params['from_type'] = c.value
                                        elif 'edge_type' not in params and 'from_type' in params:
                                            params['edge_type'] = c.value
                                        elif 'to_type' not in params and 'edge_type' in params:
                                            params['to_type'] = c.value
                                            break
                    else:
                        # Handle other traverse_spec types
                        params['spec'] = str(spec)
                
                # Parse WHERE clause - can appear after FROM or after match_pattern
                where_idx = 1
                # Check all children for WHERE clause
                for i, c in enumerate(child.children):
                    if hasattr(c, 'value') and c.value == 'WHERE':
                        # Found WHERE keyword, next should be condition
                        if i + 1 < len(child.children):
                            condition_node = child.children[i + 1]
                            if hasattr(condition_node, 'data') and condition_node.data == 'condition':
                                params['where'] = self._parse_condition(condition_node)
                            else:
                                # Try to parse as condition anyway
                                params['where'] = self._parse_condition(condition_node)
                        break
                    elif hasattr(c, 'data') and c.data == 'condition':
                        # Condition node directly
                        params['where'] = self._parse_condition(c)
                        break
                    elif hasattr(c, 'data') and c.data == 'where_clause':
                        params['where'] = self._parse_where_clause(c)
                        break
                
                # Parse RETURN clause - check all children for return_clause
                for return_idx in range(where_idx, len(child.children)):
                    if hasattr(child.children[return_idx], 'data') and child.children[return_idx].data == 'return_clause':
                        return_clause = child.children[return_idx]
                        # Parse return expressions
                        return_exprs = []
                        for expr in return_clause.children:
                            if hasattr(expr, 'data') and expr.data == 'return_expression':
                                # Extract the expression value
                                if hasattr(expr, 'children') and len(expr.children) > 0:
                                    expr_value = expr.children[0]
                                    if hasattr(expr_value, 'value'):
                                        return_exprs.append(expr_value.value)
                                    elif hasattr(expr_value, 'data'):
                                        if expr_value.data == 'qualified_identifier':
                                            return_exprs.append(self._get_qualified_identifier(expr_value))
                                        elif expr_value.data == 'return_function':
                                            return_exprs.append(expr_value.children[0].value if expr_value.children else 'PATH_LENGTH')
                                        else:
                                            return_exprs.append(self._get_qualified_identifier(expr_value))
                                    else:
                                        return_exprs.append(self._get_qualified_identifier(expr_value))
                                else:
                                    return_exprs.append(self._get_qualified_identifier(expr))
                            else:
                                return_exprs.append(self._get_qualified_identifier(expr))
                        params['return'] = return_exprs
                        where_idx = return_idx + 1
                        break
                
                # Parse FROM...VIA...TO...MAX DEPTH syntax (if has_from_syntax)
                if has_from_syntax:
                    # Parse VIA clause - check for VIA (identifier_list) or VIA identifier
                    for i in range(len(child.children)):
                        if hasattr(child.children[i], 'value') and child.children[i].value == 'VIA':
                            if i + 1 < len(child.children):
                                via_child = child.children[i + 1]
                                # Check if it's a list: (identifier_list) or just identifier
                                if hasattr(via_child, 'data'):
                                    if via_child.data == 'identifier_list':
                                        # Extract list of identifiers
                                        via_edges = []
                                        for id_child in via_child.children:
                                            via_edges.append(self._get_identifier(id_child))
                                        params['via'] = via_edges
                                    else:
                                        # Single identifier
                                        params['via'] = [self._get_identifier(via_child)]
                                elif hasattr(via_child, 'value'):
                                    # Single identifier token
                                    params['via'] = [via_child.value]
                                elif isinstance(via_child, list):
                                    # List of identifiers
                                    params['via'] = [self._get_identifier(c) for c in via_child]
                                break
                    
                    # Parse TO clause
                    for i in range(len(child.children)):
                        if hasattr(child.children[i], 'value') and child.children[i].value == 'TO':
                            if i + 1 < len(child.children):
                                to_identifier = child.children[i + 1]
                                params['to_type'] = self._get_identifier(to_identifier) if hasattr(to_identifier, 'data') else (to_identifier.value if hasattr(to_identifier, 'value') else str(to_identifier))
                                break
                
                # Parse MAX DEPTH - check for "MAX" "DEPTH" number pattern (works for both syntaxes)
                for i in range(len(child.children)):
                    if hasattr(child.children[i], 'value'):
                        if child.children[i].value == 'MAX' and i + 1 < len(child.children):
                            next_child = child.children[i + 1]
                            if hasattr(next_child, 'value') and next_child.value == 'DEPTH' and i + 2 < len(child.children):
                                depth_value = child.children[i + 2]
                                params['max_depth'] = self._get_number(depth_value)
                                break
                        elif child.children[i].value == 'MAX_DEPTH' and i + 1 < len(child.children):
                            depth_value = child.children[i + 1]
                            params['max_depth'] = self._get_number(depth_value)
                            break
                
                # Parse LIMIT clause
                for i in range(where_idx, len(child.children)):
                    if hasattr(child.children[i], 'value') and child.children[i].value == 'LIMIT' and i + 1 < len(child.children):
                        limit_value = child.children[i + 1]
                        if hasattr(limit_value, 'data'):
                            if limit_value.data == 'number':
                                params['limit'] = self._get_number(limit_value)
                            elif limit_value.data == 'variable':
                                var_name = self._get_identifier(limit_value.children[0]) if limit_value.children and len(limit_value.children) > 0 else str(limit_value)
                                if isinstance(var_name, str) and var_name.startswith('$'):
                                    var_name = var_name[1:]
                                if var_name in self.variables:
                                    params['limit'] = self.variables[var_name]
                                else:
                                    params['limit'] = f"${var_name}"
                        else:
                            params['limit'] = self._get_number(limit_value)
                        break
                
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.TRAVERSE,
                    parameters=params
                ))
            elif child.data == 'top_diff_node':
                # Handle DIFF NODE: "DIFF" "NODE" node_spec "AT" string "VS" string
                ast_nodes.append(self._parse_diff_node(child))
            elif child.data == 'top_diff_edges':
                # Handle DIFF EDGES: "DIFF" "EDGES" identifier "FOR" diff_edge_spec "AT" string "VS" string
                ast_nodes.append(self._parse_diff_edges(child))
            elif child.data == 'top_read_url':
                ast_nodes.append(self._parse_read_url(child))
            elif child.data == 'top_read_domain':
                ast_nodes.append(self._parse_read_domain(child))
            elif child.data == 'top_extract_from_raw':
                ast_nodes.append(self._parse_extract_from_raw(child))
            elif child.data == 'top_extract_entities':
                ast_nodes.append(self._parse_extract_entities_top(child))
            elif child.data == 'top_create_evalset':
                ast_nodes.append(self._parse_create_evalset(child))
            elif child.data == 'top_abtest_rag':
                ast_nodes.append(self._parse_abtest_rag(child))
            elif child.data == 'top_attach_policy':
                ast_nodes.append(self._parse_attach_policy(child))
            elif child.data == 'top_set_guardrails':
                ast_nodes.append(self._parse_set_guardrails(child))
            elif child.data == 'top_pre_guard':
                ast_nodes.append(self._parse_pre_guard(child))
            elif child.data == 'top_post_guard':
                ast_nodes.append(self._parse_post_guard(child))
            elif child.data == 'top_create_fine_tune_dataset':
                ast_nodes.append(self._parse_create_fine_tune_dataset(child))
            elif child.data == 'top_fine_tune_model':
                ast_nodes.append(self._parse_fine_tune_model(child))
            elif child.data == 'top_register_model':
                ast_nodes.append(self._parse_register_model(child))
            elif child.data == 'top_promote_model':
                ast_nodes.append(self._parse_promote_model(child))
            elif child.data == 'top_rollback_model':
                ast_nodes.append(self._parse_rollback_model(child))
            elif child.data == 'top_merge_results':
                ast_nodes.append(self._parse_merge_results(child))
            elif child.data == 'top_rerank':
                ast_nodes.append(self._parse_rerank(child))
            elif child.data == 'top_reason':
                ast_nodes.append(self._parse_reason_on(child))
            elif child.data == 'top_blockchain_verify':
                ast_nodes.append(self._parse_blockchain_verify(child))
            elif child.data == 'top_blockchain_block':
                ast_nodes.append(self._parse_blockchain_block(child))
            elif child.data == 'top_blockchain_length':
                ast_nodes.append(self._parse_blockchain_length(child))
            elif child.data == 'top_blockchain_latest':
                ast_nodes.append(self._parse_blockchain_latest(child))
            elif child.data == 'top_blockchain_audit':
                ast_nodes.append(self._parse_blockchain_audit(child))
            elif child.data == 'top_blockchain_merkle':
                ast_nodes.append(self._parse_blockchain_merkle(child))
            elif child.data == 'top_blockchain_blocks_range':
                ast_nodes.append(self._parse_blockchain_blocks_range(child))
            elif child.data == 'top_blockchain_verify_block':
                ast_nodes.append(self._parse_blockchain_verify_block(child))
            elif child.data == 'top_neighbors':
                # Handle NEIGHBORS FROM: "NEIGHBORS" "FROM" node_spec ("DEPTH" number)? ("VIA" "(" identifier_list ")")? return_clause? ("LIMIT" (number | variable))?
                ast_nodes.append(self._parse_neighbors(child))
            elif child.data == 'top_find_by_uuid':
                # Handle FIND_BY_UUID: "FIND_BY_UUID" (string | variable) | "FIND" "BY" "UUID" (string | variable)
                uuid_value = None
                if len(child.children) >= 1:
                    uuid_node = child.children[-1]  # Last child is the UUID
                    if hasattr(uuid_node, 'value'):
                        uuid_value = uuid_node.value
                    elif hasattr(uuid_node, 'data'):
                        if uuid_node.data == 'string':
                            uuid_value = self._get_string(uuid_node.children[0]) if uuid_node.children else None
                        else:
                            uuid_value = self._get_identifier(uuid_node)
                
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.FIND_BY_UUID,
                    parameters={'uuid': uuid_value}
                ))
            elif child.data == 'top_create_subgraph':
                # Handle CREATE SUBGRAPH: "CREATE" "SUBGRAPH" identifier "AS" "MATCH" match_pattern return_clause
                ast_nodes.append(self._parse_create_subgraph(child))
            elif child.data == 'top_merge_node':
                # Handle MERGE NODE
                if len(child.children) >= 1:
                    node_spec = child.children[0]
                    node_type = self._get_identifier(node_spec.children[0])
                    
                    # Parse properties if present
                    properties = {}
                    if len(node_spec.children) >= 3:
                        properties_node = node_spec.children[2]
                        if hasattr(properties_node, 'data') and properties_node.data == 'property_list':
                            properties = self._parse_property_list(properties_node)
                    
                    ast_nodes.append(AIQLNode(
                        node_type=AIQLNodeType.CREATE_NODE,
                        parameters={
                            'node_type': node_type,
                            'properties': properties,
                            'merge': True
                        }
                ))
            elif child.data == 'top_create_graph':
                # Handle CREATE GRAPH
                graph_name = self._get_identifier(child.children[0])
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.CREATE_GRAPH,
                    parameters={'graph_name': graph_name}
                ))
            elif child.data == 'top_create_graph_as':
                # Handle CREATE GRAPH AS (with pipeline)
                graph_name = self._get_identifier(child.children[0])
                # Parse graph_pipeline if present
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.CREATE_GRAPH,
                    parameters={'graph_name': graph_name, 'pipeline': True}
                ))
            elif child.data == 'top_create_graph_with_structure':
                # Handle CREATE GRAPH with THEN statements
                # Create separate AST nodes: CREATE GRAPH first, then CREATE NODE/EDGE statements
                graph_name = self._get_identifier(child.children[0])
                
                # First, create the CREATE GRAPH node
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.CREATE_GRAPH,
                    parameters={'graph_name': graph_name}
                ))
                
                # Then, parse and add each THEN statement as a separate AST node
                # Parse THEN statements (children[1:] should be create_graph_statement nodes)
                for i in range(1, len(child.children)):
                    stmt_node = child.children[i]
                    if hasattr(stmt_node, 'data') and stmt_node.data == 'create_graph_statement':
                        # create_graph_statement wraps CREATE NODE or CREATE EDGE
                        # Try to parse as CREATE NODE first, then CREATE EDGE
                        parsed = False
                        # Check if it matches CREATE NODE pattern
                        if len(stmt_node.children) > 0:
                            # Try parsing as CREATE NODE
                            try:
                                # create_graph_statement: "CREATE" "NODE" node_spec ...
                                # The structure should be similar to top_create_node
                                node_ast = self._parse_create_node(stmt_node)
                                ast_nodes.append(node_ast)
                                parsed = True
                            except:
                                pass
                        
                        # If not parsed as CREATE NODE, try CREATE EDGE
                        if not parsed:
                            try:
                                edge_ast = self._parse_create_edge(stmt_node)
                                ast_nodes.append(edge_ast)
                                parsed = True
                            except:
                                pass
                        
                        # If still not parsed, log warning
                        if not parsed:
                            logger.warning(f"Could not parse create_graph_statement at index {i}")
            elif child.data == 'top_create_graph_from_schema':
                # Handle CREATE GRAPH FROM SCHEMA
                graph_name = self._get_identifier(child.children[0])
                schema_file = self._get_string(child.children[1])
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.CREATE_GRAPH,
                    parameters={'graph_name': graph_name, 'from_schema': True, 'schema_file': schema_file}
                ))
            elif child.data == 'top_use_graph':
                # Handle USE GRAPH
                graph_name = self._get_identifier(child.children[0])
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.USE_GRAPH,
                    parameters={'graph_name': graph_name}
                ))
            elif child.data == 'top_show_graphs':
                # Handle SHOW GRAPHS
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.SHOW_GRAPHS,
                    parameters={}
                ))
            elif child.data == 'top_show_current_graph':
                # Handle SHOW CURRENT GRAPH
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.SHOW_CURRENT_GRAPH,
                    parameters={}
                ))
            elif child.data == 'top_show_stats':
                # Handle SHOW STATS
                ast_nodes.append(AIQLNode(
                    node_type=AIQLNodeType.SHOW,
                    parameters={'target': 'STATS'}
                ))
            elif child.data == 'top_describe':
                # Handle DESCRIBE
                ast_nodes.append(self._parse_describe_stage(child))
        
        return ast_nodes
    
    def _parse_search_query(self, node) -> Optional[AIQLNode]:
        """Parse DENSE/SPARSE/HYBRID/SEMANTIC/GRAPH SEARCH queries."""
        try:
            # Extract search type - could be first child or a token
            search_type = None
            if len(node.children) > 0:
                search_type_node = node.children[0]
                if hasattr(search_type_node, 'data') and search_type_node.data == 'search_type':
                    # search_type is a rule node
                    if search_type_node.children:
                        search_type = self._get_identifier(search_type_node.children[0])
                    else:
                        # Try to get from value
                        search_type = str(search_type_node).upper() if 'DENSE' in str(search_type_node).upper() or 'SPARSE' in str(search_type_node).upper() or 'HYBRID' in str(search_type_node).upper() or 'SEMANTIC' in str(search_type_node).upper() or 'GRAPH' in str(search_type_node).upper() else None
                elif hasattr(search_type_node, 'value'):
                    search_type = str(search_type_node.value).upper()
                else:
                    # Try to extract from the node data or string representation
                    node_str = str(node.data) if hasattr(node, 'data') else str(node)
                    for st in ['DENSE', 'SPARSE', 'HYBRID', 'SEMANTIC', 'GRAPH']:
                        if st in node_str.upper():
                            search_type = st
                            break
                    if not search_type:
                        search_type = self._get_identifier(search_type_node)
            
            if not search_type:
                logger.error(f"Could not extract search_type from node: {node}")
                return None
            
            # Extract search string or variable - need to find it after search_type
            query_value = None
            # Skip search_type and find the query value
            query_idx = 1
            if len(node.children) > query_idx:
                query_child = node.children[query_idx]
                if hasattr(query_child, 'data'):
                    if query_child.data == 'string':
                        query_value = self._get_string_value(query_child)
                    elif query_child.data == 'variable':
                        var_name = self._get_identifier(query_child.children[0]) if query_child.children else None
                        if var_name and var_name.startswith('$'):
                            var_name = var_name[1:]
                        if var_name in self.variables:
                            query_value = self.variables[var_name]
                        else:
                            query_value = f"${var_name}"  # Keep as variable reference
                elif hasattr(query_child, 'value'):
                    query_value = str(query_child.value)
            
            # Extract the rest based on query structure
            params = {
                'search_type': search_type,
                'query': query_value
            }
            
            # Parse remaining children - start after query value
            # Find where the query value ends
            i = query_idx + 1
            if query_value is None and len(node.children) > query_idx:
                # If we didn't find query_value, try next child
                i = query_idx + 1
            else:
                i = query_idx + 1
            
            model = None
            parameters_dict = {}
            while i < len(node.children):
                child = node.children[i]
                
                if hasattr(child, 'data'):
                    if child.data == 'identifier_list':
                        # Multi-type search like IN (Document, Table)
                        types = [self._get_identifier(c) for c in child.children]
                        params['types'] = types
                    elif child.data == 'weight_list':
                        # Weight list like WITH semantic=0.6, keyword=0.4
                        weights = {}
                        for wc in child.children:
                            if hasattr(wc, 'data') and wc.data == 'weight_item':
                                key = self._get_identifier(wc.children[0])
                                val = self._get_number(wc.children[1])
                                weights[key] = val
                        params['weights'] = weights
                    elif child.data == 'mode_type':
                        params['mode'] = self._get_identifier(child)
                    elif child.data == 'parameter_clause':
                        # Parse PARAMETERS clause
                        if len(child.children) > 0:
                            param_dict_node = child.children[0]
                            if hasattr(param_dict_node, 'data') and param_dict_node.data == 'parameter_dict':
                                parameters_dict = self._parse_parameter_dict(param_dict_node) if hasattr(self, '_parse_parameter_dict') else {}
                    elif child.data == 'parameter_dict':
                        parameters_dict = self._parse_parameter_dict(child) if hasattr(self, '_parse_parameter_dict') else {}
                elif isinstance(child, str):
                    if child == 'LIMIT' and i + 1 < len(node.children):
                        limit_val = node.children[i + 1]
                        if hasattr(limit_val, 'data') and limit_val.data == 'variable':
                            var_name = self._get_identifier(limit_val.children[0]) if limit_val.children else None
                            if var_name and var_name.startswith('$'):
                                var_name = var_name[1:]
                            if var_name in self.variables:
                                params['limit'] = self.variables[var_name]
                            else:
                                params['limit'] = f"${var_name}"
                        else:
                            params['limit'] = self._get_number(limit_val)
                        i += 1
                    elif child == 'NAMESPACE' and i + 1 < len(node.children):
                        params['namespace'] = self._get_identifier(node.children[i + 1])
                        i += 1
                    elif child == 'USING' and i + 1 < len(node.children):
                        if isinstance(node.children[i + 1], str) and node.children[i + 1] == 'COLLECTION':
                            params['collection'] = self._get_identifier(node.children[i + 2])
                            i += 2
                        elif isinstance(node.children[i + 1], str) and node.children[i + 1] == 'MODEL':
                            # USING MODEL
                            if i + 2 < len(node.children):
                                model_node = node.children[i + 2]
                                if hasattr(model_node, 'data') and model_node.data == 'variable':
                                    var_name = self._get_identifier(model_node.children[0]) if model_node.children else None
                                    if var_name and var_name.startswith('$'):
                                        var_name = var_name[1:]
                                    if var_name in self.variables:
                                        model = self.variables[var_name]
                                    else:
                                        model = f"${var_name}"
                                elif hasattr(model_node, 'data') and model_node.data == 'string':
                                    model = self._get_string_value(model_node)
                                else:
                                    model = self._get_identifier(model_node)
                            i += 2
                        else:
                            params['collection'] = self._get_identifier(node.children[i + 1])
                            i += 1
                    elif child == 'MODE' and i + 1 < len(node.children):
                        params['mode'] = self._get_identifier(node.children[i + 1])
                        i += 1
                    elif child == 'IN' and i + 1 < len(node.children):
                        if isinstance(node.children[i + 1], str) and node.children[i + 1] == 'NAMESPACE':
                            # Skip, handled above
                            pass
                        else:
                            # Single type like IN Document
                            params['type'] = self._get_identifier(node.children[i + 1])
                            i += 1
                    elif child == 'VIA' and i + 1 < len(node.children):
                        # GRAPH SEARCH VIA (edge_types)
                        via_node = node.children[i + 1]
                        if hasattr(via_node, 'data') and via_node.data == 'identifier_list':
                            edge_types = [self._get_identifier(c) for c in via_node.children]
                            params['via'] = edge_types
                        i += 1
                    elif child == 'DEPTH' and i + 1 < len(node.children):
                        params['depth'] = self._get_number(node.children[i + 1])
                        i += 1
                    elif child == 'FROM' and i + 1 < len(node.children):
                        # GRAPH SEARCH FROM node_type
                        params['from'] = self._get_identifier(node.children[i + 1])
                        i += 1
                    elif child == 'TO' and i + 1 < len(node.children):
                        # GRAPH SEARCH TO node_type
                        params['to'] = self._get_identifier(node.children[i + 1])
                        i += 1
                    elif child == 'COLLECTIONS' and i + 1 < len(node.children):
                        # SEMANTIC SEARCH IN COLLECTIONS (list)
                        collections_node = node.children[i + 1]
                        if hasattr(collections_node, 'data') and collections_node.data == 'identifier_list':
                            collections = [self._get_identifier(c) for c in collections_node.children]
                            params['collections'] = collections
                        i += 1
                i += 1
            
            if model:
                params['model'] = model
            if parameters_dict:
                params['parameters'] = parameters_dict
            
            return AIQLNode(
                node_type=AIQLNodeType.SEARCH_QUERY,
                parameters=params
            )
        except Exception as e:
            logger.error(f"Error parsing search query: {e}")
            return None
    
    def _parse_pipeline(self, pipeline_node) -> List[AIQLNode]:
        """Parse pipeline stages with THEN and PARALLEL blocks."""
        stages = []
        pipeline_seed_id = None
        
        # All pipelines now require PIPELINE SEED syntax
        if hasattr(pipeline_node, 'data') and pipeline_node.data == 'pipeline_with_seed':
            # Extract seed ID from first child (string)
            if len(pipeline_node.children) > 0:
                seed_node = pipeline_node.children[0]
                pipeline_seed_id = self._get_string_value(seed_node)
                logger.info(f"[DEBUG] Pipeline SEED ID: {pipeline_seed_id}")
            
            # Process pipeline body (second child)
            if len(pipeline_node.children) > 1:
                pipeline_body = pipeline_node.children[1]
                stages = self._parse_pipeline_body(pipeline_body, pipeline_seed_id)
        elif hasattr(pipeline_node, 'data') and pipeline_node.data == 'pipeline_without_seed':
            # Process pipeline body (first child)
            if len(pipeline_node.children) > 0:
                pipeline_body = pipeline_node.children[0]
                stages = self._parse_pipeline_body(pipeline_body, None)
        elif hasattr(pipeline_node, 'data') and pipeline_node.data == 'pipeline':
            # This is a top-level pipeline node, process its children
            for child in pipeline_node.children:
                if hasattr(child, 'data'):
                    if child.data == 'pipeline_with_seed':
                        stages.extend(self._parse_pipeline(child))
                    elif child.data == 'pipeline_without_seed':
                        stages.extend(self._parse_pipeline(child))
        else:
            # Regular pipeline without seed ID - this might be a pipeline_body directly
            stages = self._parse_pipeline_body(pipeline_node, None)
        
        return stages
    
    def _get_string_value(self, node) -> str:
        """Extract string value from a Lark node."""
        if hasattr(node, 'value'):
            return node.value
        elif hasattr(node, 'children') and len(node.children) > 0:
            return self._get_string_value(node.children[0])
        return str(node)
    
    def _parse_pipeline_body(self, pipeline_body, seed_id: str = None) -> List[AIQLNode]:
        """Parse pipeline body with optional seed ID context."""
        stages = []
        
        logger.debug(f"[DEBUG] _parse_pipeline_body: processing {len(pipeline_body.children)} children")
        
        for i, child in enumerate(pipeline_body.children):
            logger.debug(f"[DEBUG] Child {i}: {child.data if hasattr(child, 'data') else type(child)}")
            if hasattr(child, 'data'):
                if child.data == 'stage':
                    logger.debug(f"[DEBUG] Processing stage node")
                    stage_node = self._parse_stage(child)
                    if stage_node:
                        logger.debug(f"[DEBUG] Stage node created: {stage_node.node_type.value}")
                        # Add pipeline seed ID to stage parameters if available
                        if seed_id and hasattr(stage_node, 'parameters'):
                            stage_node.parameters['pipeline_seed_id'] = seed_id
                        stages.append(stage_node)
                    else:
                        logger.debug(f"[DEBUG] Stage node creation failed")
                elif child.data == 'variable_decl':
                    # Parse variable declaration
                    var_decl = self._parse_variable_decl(child)
                    # Remove $ prefix if present
                    var_name = var_decl.name.lstrip('$')
                    self.variables[var_name] = var_decl.value
                    stages.append(AIQLNode(
                        node_type=AIQLNodeType.VARIABLE_DECL,
                        parameters={'name': var_name, 'value': var_decl.value}
                    ))
                elif child.data == 'THEN':
                    # THEN means sequential execution - continue processing
                    pass
                elif child.data == 'parallel_stage':
                    # Parse the parallel stage
                    parallel_stages = []
                    for stage_child in child.children:
                        if hasattr(stage_child, 'data') and stage_child.data == 'stage':
                            stage_node = self._parse_stage(stage_child)
                            if stage_node:
                                parallel_stages.append(stage_node)
                    
                    if parallel_stages:
                        if len(parallel_stages) == 1:
                            stages.extend(parallel_stages)
                        else:
                            # Create a PARALLEL node for the group
                            parallel_node = AIQLNode(
                                node_type=AIQLNodeType.PARALLEL,
                                parameters={'stages': parallel_stages}
                            )
                            stages.append(parallel_node)
                elif child.data == 'PARALLEL':
                    # PARALLEL keyword - next child should be parallel_block
                    pass
                elif child.data == 'parallel_block':
                    # Parse the parallel block
                    parallel_stages = []
                    for stage_child in child.children:
                        if hasattr(stage_child, 'data') and stage_child.data == 'stage':
                            stage_node = self._parse_stage(stage_child)
                            if stage_node:
                                parallel_stages.append(stage_node)
                    
                    if parallel_stages:
                        if len(parallel_stages) == 1:
                            stages.extend(parallel_stages)
                        else:
                            # Create a PARALLEL node for the group
                            parallel_node = AIQLNode(
                                node_type=AIQLNodeType.PARALLEL,
                                parameters={'stages': parallel_stages}
                            )
                            stages.append(parallel_node)
        
        return stages
    
    def _parse_stage(self, stage_node) -> Optional[AIQLNode]:
        """Parse individual stage."""
        stage_type = stage_node.data
        
        if stage_type == 'stage':
            # Handle stage -> create_stage/create_node/create_edge
            inner_stage = stage_node.children[0]
            return self._parse_stage(inner_stage)
        elif stage_type == 'create_stage':
            # Handle nested create_stage -> create_graph/create_node/create_edge
            inner_stage = stage_node.children[0]
            return self._parse_stage(inner_stage)
        elif stage_type == 'drop_stage':
            # Handle nested drop_stage -> drop_graph
            inner_stage = stage_node.children[0]
            return self._parse_stage(inner_stage)
        elif stage_type == 'create_graph':
            return self._parse_create_graph(stage_node)
        elif stage_type == 'drop_graph':
            return self._parse_drop_graph(stage_node)
        elif stage_type == 'use_stage':
            return self._parse_use_graph(stage_node)
        elif stage_type == 'create_node':
            return self._parse_create_node(stage_node)
        elif stage_type == 'create_edge':
            return self._parse_create_edge(stage_node)
        elif stage_type == 'create_materialized_view':
            return self._parse_create_materialized_view(stage_node)
        elif stage_type == 'refresh_materialized_view':
            return self._parse_refresh_materialized_view(stage_node)
        elif stage_type == 'drop_materialized_view':
            return self._parse_drop_materialized_view(stage_node)
        elif stage_type == 'commit_stage':
            return self._parse_commit_stage(stage_node)
        elif stage_type == 'load_stage':
            return self._parse_load_stage(stage_node)
        elif stage_type == 'chunk_stage':
            return self._parse_chunk_stage(stage_node)
        elif stage_type == 'extract_stage':
            return self._parse_extract_stage(stage_node)
        elif stage_type == 'generate_stage':
            return self._parse_generate_stage(stage_node)
        elif stage_type == 'embedding_stage':
            return self._parse_embedding_stage(stage_node)
        elif stage_type == 'sparse_match':
            return self._parse_sparse_match(stage_node)
        elif stage_type == 'dense_match':
            return self._parse_dense_match(stage_node)
        elif stage_type == 'hybrid_match':
            return self._parse_hybrid_match(stage_node)
        elif stage_type == 'rerank_stage':
            return self._parse_rerank_stage(stage_node)
        elif stage_type == 'traverse_stage':
            return self._parse_traverse_stage(stage_node)
        elif stage_type == 'extract_from_folder_stage':
            return self._parse_extract_from_folder_stage(stage_node)
        elif stage_type == 'chunk_by_semantic_stage':
            return self._parse_chunk_by_semantic_stage(stage_node)
        elif stage_type == 'embed_into_stage':
            return self._parse_embed_into_stage(stage_node)
        elif stage_type == 'hybrid_search_stage':
            return self._parse_hybrid_search_stage(stage_node)
        elif stage_type == 'traverse_stage_pipeline':
            return self._parse_traverse_stage_pipeline(stage_node)
        elif stage_type == 'generate_stage_pipeline':
            return self._parse_generate_stage_pipeline(stage_node)
        elif stage_type == 'evaluate_rag_stage':
            return self._parse_evaluate_rag_stage_pipeline(stage_node)
        elif stage_type == 'insert_into_stage':
            return self._parse_insert_into_stage_pipeline(stage_node)
        elif stage_type == 'where_stage':
            return self._parse_where_stage(stage_node)
        elif stage_type == 'select_stage':
            return self._parse_select_stage(stage_node)
        elif stage_type == 'analytics_stage':
            # analytics_stage wraps pagerank, shortest_path, community, centrality
            # Unwrap and parse the actual analytics operation
            if len(stage_node.children) > 0:
                actual_stage = stage_node.children[0]
                return self._parse_stage(actual_stage)
            return None
        elif stage_type == 'pagerank_stage':
            return self._parse_pagerank_stage(stage_node)
        elif stage_type == 'shortest_path_stage':
            return self._parse_shortest_path_stage(stage_node)
        elif stage_type == 'community_stage':
            return self._parse_community_stage(stage_node)
        elif stage_type == 'retrieval_system_stage':
            return self._parse_retrieval_system_stage(stage_node)
        elif stage_type == 'retrieve_query':
            return self._parse_retrieve_query(stage_node)
        elif stage_type == 'rag_query':
            return self._parse_rag_query(stage_node)
        elif stage_type == 'index_stage':
            return self._parse_index_stage(stage_node)
        elif stage_type == 'shard_stage':
            return self._parse_shard_stage(stage_node)
        elif stage_type == 'schema_stage':
            return self._parse_schema_stage(stage_node)
        elif stage_type == 'model_stage':
            return self._parse_model_stage(stage_node)
        elif stage_type == 'prompt_stage':
            return self._parse_prompt_stage(stage_node)
        elif stage_type == 'define_node_stage':
            return self._parse_define_node_stage(stage_node)
        elif stage_type == 'define_edge_stage':
            return self._parse_define_edge_stage(stage_node)
        elif stage_type == 'namespace_stage':
            return self._parse_namespace_stage(stage_node)
        elif stage_type == 'grant_stage':
            return self._parse_grant_stage(stage_node)
        elif stage_type == 'view_stage':
            return self._parse_view_stage(stage_node)
        elif stage_type == 'materialized_view_stage':
            return self._parse_materialized_view_stage(stage_node)
        elif stage_type == 'temporal_stage':
            return self._parse_temporal_stage(stage_node)
        elif stage_type == 'temporal_query':
            return self._parse_temporal_query(stage_node)
        elif stage_type == 'rag_pipeline_stage':
            return self._parse_rag_pipeline_stage(stage_node)
        elif stage_type == 'return_stage':
            return self._parse_return_stage(stage_node)
        elif stage_type == 'match_entity_stage':
            return self._parse_match_entity_stage(stage_node)
        elif stage_type == 'file_reader_stage':
            return self._parse_file_reader_stage(stage_node)
        elif stage_type == 'multimodal_extraction_stage':
            return self._parse_multimodal_extraction_stage(stage_node)
        elif stage_type == 'smart_chunking_stage':
            return self._parse_smart_chunking_stage(stage_node)
        elif stage_type == 'evaluation_stage':
            return self._parse_evaluation_stage(stage_node)
        elif stage_type == 'pipeline_management_stage':
            return self._parse_pipeline_management_stage(stage_node)
        elif stage_type == 'pagerank_stage':
            return self._parse_pagerank_stage(stage_node)
        elif stage_type == 'shortest_path_stage':
            return self._parse_shortest_path_stage(stage_node)
        elif stage_type == 'community_stage':
            return self._parse_community_stage(stage_node)
        elif stage_type == 'delete_stage':
            return self._parse_delete_stage(stage_node)
        elif stage_type == 'update_stage':
            return self._parse_update_stage(stage_node)
        elif stage_type == 'generation_stage':
            return self._parse_generation_stage(stage_node)
        elif stage_type == 'aggregation_stage':
            return self._parse_aggregation_stage(stage_node)
        elif stage_type == 'global_strategy':
            return self._parse_global_strategy(stage_node)
        elif stage_type == 'count_stage':
            return self._parse_count_stage(stage_node)
        
        return None
    
    def _parse_create_graph(self, node) -> AIQLNode:
        """Parse CREATE GRAPH statement."""
        graph_name = self._get_identifier(node.children[0])
        return AIQLNode(
            node_type=AIQLNodeType.CREATE_GRAPH,
            parameters={'graph_name': graph_name}
        )
    
    def _parse_drop_graph(self, node) -> AIQLNode:
        """Parse DROP GRAPH statement."""
        graph_name = self._get_identifier(node.children[0])
        cascade = False
        
        # Check if CASCADE is present
        if len(node.children) > 1:
            for child in node.children[1:]:
                if hasattr(child, 'data') and 'CASCADE' in str(child.data):
                    cascade = True
                elif hasattr(child, 'value') and child.value == 'CASCADE':
                    cascade = True
        
        return AIQLNode(
            node_type=AIQLNodeType.DROP_GRAPH,
            parameters={'graph_name': graph_name, 'cascade': cascade}
        )
    
    def _parse_use_graph(self, node) -> AIQLNode:
        """Parse USE GRAPH statement."""
        graph_name = self._get_identifier(node.children[0])
        return AIQLNode(
            node_type=AIQLNodeType.USE_GRAPH,
            parameters={'graph_name': graph_name}
        )
    
    def _parse_create_node(self, node) -> AIQLNode:
        """Parse CREATE NODE statement."""
        node_spec = node.children[0]
        node_type = self._get_identifier(node_spec.children[0])
        
        params = {'node_type': node_type}
        
        # CRITICAL: Grammar: node_spec: identifier ("AS" identifier)? ("{" property_list "}")?
        # The "AS" keyword is consumed by the grammar, so the parse tree structure is:
        # - child[0]: identifier (node_type)
        # - child[1]: identifier (alias, if present) OR property_list
        # - child[2]: property_list (if alias was present)
        
        # Check for alias - if we have 2+ children and child[1] is an identifier (not property_list), it's the alias
        alias = None
        properties = {}
        
        if len(node_spec.children) >= 2:
            second_child = node_spec.children[1]
            # Check if second child is an identifier (alias) or property_list
            if hasattr(second_child, 'data'):
                if second_child.data == 'identifier':
                    # This is the alias
                    alias = self._get_identifier(second_child)
                    params['alias'] = alias
                    # Properties would be in child[2] if present
                    if len(node_spec.children) >= 3:
                        third_child = node_spec.children[2]
                        if hasattr(third_child, 'data') and third_child.data == 'property_list':
                            properties = self._parse_property_list(third_child)
                elif second_child.data == 'property_list':
                    # No alias, properties are in child[1]
                    properties = self._parse_property_list(second_child)
        
        # Also check for 'AS' token explicitly (in case grammar structure is different)
        for i in range(len(node_spec.children)):
            child = node_spec.children[i]
            if hasattr(child, 'value') and child.value == 'AS' and i + 1 < len(node_spec.children):
                alias_child = node_spec.children[i + 1]
                if hasattr(alias_child, 'data') and alias_child.data == 'identifier':
                    params['alias'] = self._get_identifier(alias_child)
                    break
        
        params['properties'] = properties
        
        # Parse unique key if present
        if len(node.children) > 1:
            unique_keys = self._parse_identifier_list(node.children[1])
            params['unique_keys'] = unique_keys
        
        return AIQLNode(
            node_type=AIQLNodeType.CREATE_NODE,
            parameters=params
        )
    
    def _parse_retrieval_stage(self, node) -> AIQLNode:
        """Parse retrieval_stage which can contain retrieve_query, sparse_match, etc."""
        # Get the inner stage (retrieve_query, sparse_match, etc.)
        if len(node.children) > 0:
            inner_stage = node.children[0]
            return self._parse_stage(inner_stage)
        return None
    
    def _parse_retrieve_query(self, node) -> AIQLNode:
        """Parse RETRIEVE query statement."""
        params = {}
        
        # Parse identifier (node type or "ALL")
        if len(node.children) > 0:
            identifier_node = node.children[0]
            if hasattr(identifier_node, 'data') and identifier_node.data == 'identifier':
                identifier = self._get_identifier(identifier_node)
                params['target_type'] = identifier
            else:
                params['target_type'] = 'ALL'
        
        # Parse WHERE clause if present
        if len(node.children) > 1:
            where_node = node.children[1]
            if hasattr(where_node, 'data') and where_node.data == 'where_stage':
                params['where'] = self._parse_where_clause(where_node)
        
        return AIQLNode(
            node_type=AIQLNodeType.RETRIEVE,
            parameters=params
        )
    
    def _parse_rag_query(self, node) -> AIQLNode:
        """Parse RAG QUERY statement."""
        params = {}
        
        # Parse query string
        if len(node.children) > 0:
            params['query'] = self._get_string(node.children[0])
        
        # Parse optional IN clause
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value == 'IN':
                if i + 1 < len(node.children):
                    params['namespace'] = self._get_identifier(node.children[i + 1])
        
        # Parse optional USING clause
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value == 'USING':
                if i + 1 < len(node.children):
                    params['using'] = self._get_identifier(node.children[i + 1])
        
        # Parse optional LIMIT clause
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value == 'LIMIT':
                if i + 1 < len(node.children):
                    params['limit'] = self._get_number(node.children[i + 1])
        
        return AIQLNode(
            node_type=AIQLNodeType.RAG_QUERY,
            parameters=params
        )
    
    def _parse_create_edge(self, node) -> AIQLNode:
        """Parse CREATE EDGE statement with multiple syntaxes:
        1. CREATE EDGE edge_type (source)->(target) {props}
        2. CREATE EDGE (source) edge_type {props} (target)  
        3. CREATE EDGE edge_type {props} SRC source DEST target
        4. CREATE EDGE edge_type {props} (simple edge creation)
        """
        # Check if edge_type is the first child (new syntax: CREATE EDGE identifier ...)
        edge_type = None
        edge_spec_node = None
        start_idx = 0
        
        if len(node.children) > 0:
            first_child = node.children[0]
            # Check if first child is an identifier (edge_type)
            if hasattr(first_child, 'data') and first_child.data == 'identifier':
                edge_type = self._get_identifier(first_child)
                start_idx = 1
                if len(node.children) > start_idx:
                    edge_spec_node = node.children[start_idx]
            else:
                # Old syntax: edge_spec_with_endpoints is first
                edge_spec_node = node.children[0]
        
        if not edge_spec_node:
            raise ValueError("Invalid CREATE EDGE syntax: missing edge specification")
        
        params = {}
        
        # Check which syntax is being used by looking at the structure
        # For CREATE EDGE edge_type (source)->(target) {props}
        # The structure is: node.children[0] = identifier (edge_type)
        #                  node.children[1] = edge_spec_with_endpoints
        
        # edge_spec_with_endpoints can be:
        # 1. (node_ref)->(node_ref) {props}? - arrow syntax
        # 2. (node_ref) edge_spec (node_ref) - old syntax
        # 3. edge_from_to - SRC/DEST syntax
        
        # Check for SRC/DEST syntax first (edge_from_to)
        has_src_dest = False
        src_node = None
        dest_node = None
        src_where = None
        dest_where = None
        src_type = None
        dest_type = None
        
        # Check if edge_spec_node is edge_from_to (has SRC and DEST)
        # Also check if node.children contains SRC/DEST tokens (grammar might parse differently)
        # Also check if edge_spec_node has a nested edge_from_to child
        has_edge_from_to_data = hasattr(edge_spec_node, 'data') and edge_spec_node.data == 'edge_from_to'
        has_nested_edge_from_to = False
        nested_edge_from_to_node = None
        
        # Check if edge_spec_node has a child that is edge_from_to
        if hasattr(edge_spec_node, 'children'):
            for child in edge_spec_node.children:
                if hasattr(child, 'data') and child.data == 'edge_from_to':
                    has_nested_edge_from_to = True
                    nested_edge_from_to_node = child
                    break
        
        has_src_dest_tokens = False
        # Quick check: scan node.children for SRC/DEST tokens
        for child in node.children:
            if hasattr(child, 'value') and child.value in ['SRC', 'DEST']:
                has_src_dest_tokens = True
                break
        
        # If we found a nested edge_from_to, use it instead
        if has_nested_edge_from_to and nested_edge_from_to_node:
            edge_spec_node = nested_edge_from_to_node
            has_edge_from_to_data = True
        
        if has_edge_from_to_data or has_src_dest_tokens:
            has_src_dest = True
            # edge_from_to structure can be:
            # 1. identifier (edge_type), [AS alias], [props], SRC node_selector, DEST node_selector, [props]
            # 2. identifier (edge_type), SRC node_selector, DEST node_selector, [props]
            # 3. identifier "CONNECT" node_selector DEST node_selector [props] (no SRC token)
            # 4. identifier node_selector node_selector [props] (implicit SRC/DEST - first is SRC, second is DEST)
            
            # First child should be edge_type identifier
            # But if edge_type is already set from node.children[0], skip this
            if not edge_type and len(edge_spec_node.children) > 0:
                first_child = edge_spec_node.children[0]
                if hasattr(first_child, 'data') and first_child.data == 'identifier':
                    edge_type = self._get_identifier(first_child)
            
            # Check if we have explicit SRC/DEST tokens or implicit structure
            # Also check recursively in nested children (grammar might nest edge_from_to)
            has_explicit_src_dest = False
            def check_for_src_dest(node_to_check):
                """Recursively check for SRC/DEST tokens."""
                if hasattr(node_to_check, 'children'):
                    for child in node_to_check.children:
                        if hasattr(child, 'value') and child.value in ['SRC', 'DEST']:
                            return True
                        # Recursively check nested children
                        if hasattr(child, 'children') and check_for_src_dest(child):
                            return True
                return False
            
            has_explicit_src_dest = check_for_src_dest(edge_spec_node)
            
            if not has_explicit_src_dest:
                # Implicit structure: [props?] node_selector node_selector [props?]
                # First node_selector is SRC, second is DEST
                node_selector_idx = 0
                for i, child in enumerate(edge_spec_node.children):
                    if hasattr(child, 'data') and child.data == 'node_selector':
                        selector_info = self._parse_node_selector(child)
                        if node_selector_idx == 0:
                            # First node_selector is SRC
                            src_node = selector_info.get('node_id') or selector_info.get('node_type')
                            if selector_info.get('node_type'):
                                src_type = selector_info['node_type']
                            if selector_info.get('where_conditions'):
                                src_where = [selector_info['where_conditions']]
                            node_selector_idx += 1
                        elif node_selector_idx == 1:
                            # Second node_selector is DEST
                            dest_node = selector_info.get('node_id') or selector_info.get('node_type')
                            if selector_info.get('node_type'):
                                dest_type = selector_info['node_type']
                            if selector_info.get('where_conditions'):
                                dest_where = [selector_info['where_conditions']]
                            break
            else:
                # Explicit SRC/DEST tokens - find them
                # Find SRC and DEST tokens
                for i, child in enumerate(edge_spec_node.children):
                    if hasattr(child, 'value') and child.value == 'SRC':
                        src_selector = None
                        if i + 1 < len(edge_spec_node.children):
                            src_selector = edge_spec_node.children[i + 1]
                        # node_selector can be: string, identifier WHERE condition, or identifier
                        if src_selector:
                            if hasattr(src_selector, 'data'):
                                if src_selector.data == 'string':
                                    src_node = self._get_string(src_selector)
                                elif src_selector.data == 'identifier':
                                    src_node = self._get_identifier(src_selector)
                                    # Check if next child is WHERE
                                    if i + 2 < len(edge_spec_node.children):
                                        next_child = edge_spec_node.children[i + 2]
                                        if hasattr(next_child, 'value') and next_child.value == 'WHERE':
                                            if i + 3 < len(edge_spec_node.children):
                                                src_where = [self._parse_condition(edge_spec_node.children[i + 3])]
                                                src_type = src_node
                                elif src_selector.data == 'node_selector':
                                    # node_selector might be a wrapper
                                    if hasattr(src_selector, 'children') and len(src_selector.children) > 0:
                                        selector_child = src_selector.children[0]
                                        if hasattr(selector_child, 'data') and selector_child.data == 'identifier':
                                            src_node = self._get_identifier(selector_child)
                                            # Check for WHERE in node_selector
                                            if len(src_selector.children) > 1:
                                                where_child = src_selector.children[1]
                                                if hasattr(where_child, 'data') and where_child.data == 'condition':
                                                    src_where = [self._parse_condition(where_child)]
                                                    src_type = src_node
                            elif hasattr(src_selector, 'type') and src_selector.type == 'IDENTIFIER':
                                # Direct identifier token
                                src_node = str(src_selector.value) if hasattr(src_selector, 'value') else str(src_selector)
                    elif hasattr(child, 'value') and child.value == 'DEST':
                        dest_selector = None
                        if i + 1 < len(edge_spec_node.children):
                            dest_selector = edge_spec_node.children[i + 1]
                        # node_selector can be: string, identifier WHERE condition, or identifier
                        if dest_selector:
                            if hasattr(dest_selector, 'data'):
                                if dest_selector.data == 'string':
                                    dest_node = self._get_string(dest_selector)
                                elif dest_selector.data == 'identifier':
                                    dest_node = self._get_identifier(dest_selector)
                                    # Check if next child is WHERE
                                    if i + 2 < len(edge_spec_node.children):
                                        next_child = edge_spec_node.children[i + 2]
                                        if hasattr(next_child, 'value') and next_child.value == 'WHERE':
                                            if i + 3 < len(edge_spec_node.children):
                                                dest_where = [self._parse_condition(edge_spec_node.children[i + 3])]
                                                dest_type = dest_node
                                elif dest_selector.data == 'node_selector':
                                    # node_selector might be a wrapper
                                    if hasattr(dest_selector, 'children') and len(dest_selector.children) > 0:
                                        selector_child = dest_selector.children[0]
                                        if hasattr(selector_child, 'data') and selector_child.data == 'identifier':
                                            dest_node = self._get_identifier(selector_child)
                                            # Check for WHERE in node_selector
                                            if len(dest_selector.children) > 1:
                                                where_child = dest_selector.children[1]
                                                if hasattr(where_child, 'data') and where_child.data == 'condition':
                                                    dest_where = [self._parse_condition(where_child)]
                                                    dest_type = dest_node
                            elif hasattr(dest_selector, 'type') and dest_selector.type == 'IDENTIFIER':
                                # Direct identifier token
                                dest_node = str(dest_selector.value) if hasattr(dest_selector, 'value') else str(dest_selector)
        
        # Also check for SRC/DEST in the node.children directly (if edge_spec_node doesn't have it)
        # This handles: CREATE EDGE edge_type SRC ... DEST ... (where edge_type is separate)
        # CRITICAL: Check node.children for SRC/DEST even if edge_spec_node doesn't have edge_from_to
        # This is needed when the grammar parses it differently
        # Also check edge_spec_node.children if we haven't found src_node/dest_node yet
        if (src_node is None or dest_node is None):
            # First try edge_spec_node.children (always check if src/dest not found)
            if edge_spec_node and hasattr(edge_spec_node, 'children'):
                for i, child in enumerate(edge_spec_node.children):
                    child_value = None
                    if hasattr(child, 'value'):
                        child_value = child.value
                    elif hasattr(child, 'type') and hasattr(child, 'value'):
                        child_value = child.value
                    
                    if child_value == 'SRC' and src_node is None:
                        if i + 1 < len(edge_spec_node.children):
                            src_sel = edge_spec_node.children[i + 1]
                            if hasattr(src_sel, 'data') and src_sel.data == 'identifier':
                                src_node = self._get_identifier(src_sel)
                            elif hasattr(src_sel, 'type') and src_sel.type == 'IDENTIFIER':
                                src_node = str(src_sel.value) if hasattr(src_sel, 'value') else str(src_sel)
                    elif child_value == 'DEST' and dest_node is None:
                        if i + 1 < len(edge_spec_node.children):
                            dest_sel = edge_spec_node.children[i + 1]
                            if hasattr(dest_sel, 'data') and dest_sel.data == 'identifier':
                                dest_node = self._get_identifier(dest_sel)
                            elif hasattr(dest_sel, 'type') and dest_sel.type == 'IDENTIFIER':
                                dest_node = str(dest_sel.value) if hasattr(dest_sel, 'value') else str(dest_sel)
            
            # Then try node.children
            for i, child in enumerate(node.children):
                # Check if child is a Token with value 'SRC' or 'DEST'
                child_value = None
                if hasattr(child, 'value'):
                    child_value = child.value
                elif hasattr(child, 'type') and hasattr(child, 'value'):
                    child_value = child.value
                elif isinstance(child, str):
                    child_value = child
                
                if child_value == 'SRC':
                    has_src_dest = True
                    src_selector = None
                    if i + 1 < len(node.children):
                        src_selector = node.children[i + 1]
                    if src_selector:
                        if hasattr(src_selector, 'data'):
                            if src_selector.data == 'string':
                                src_node = self._get_string(src_selector)
                            elif src_selector.data == 'identifier':
                                src_node = self._get_identifier(src_selector)
                                # Check for WHERE clause
                                if i + 2 < len(node.children):
                                    next_child = node.children[i + 2]
                                    next_value = None
                                    if hasattr(next_child, 'value'):
                                        next_value = next_child.value
                                    elif isinstance(next_child, str):
                                        next_value = next_child
                                    if next_value == 'WHERE' and i + 3 < len(node.children):
                                        src_where = [self._parse_condition(node.children[i + 3])]
                                        src_type = src_node
                            elif src_selector.data == 'node_selector':
                                # node_selector wrapper
                                if hasattr(src_selector, 'children') and len(src_selector.children) > 0:
                                    selector_child = src_selector.children[0]
                                    if hasattr(selector_child, 'data'):
                                        if selector_child.data == 'string':
                                            src_node = self._get_string(selector_child)
                                        elif selector_child.data == 'identifier':
                                            src_node = self._get_identifier(selector_child)
                                            # Check for WHERE in node_selector
                                            if len(src_selector.children) > 1:
                                                where_child = src_selector.children[1]
                                                if hasattr(where_child, 'data') and where_child.data == 'condition':
                                                    src_where = [self._parse_condition(where_child)]
                                                    src_type = src_node
                        elif hasattr(src_selector, 'type') and src_selector.type == 'IDENTIFIER':
                            # Direct identifier token
                            src_node = str(src_selector.value) if hasattr(src_selector, 'value') else str(src_selector)
                elif child_value == 'DEST':
                    dest_selector = None
                    if i + 1 < len(node.children):
                        dest_selector = node.children[i + 1]
                    if dest_selector:
                        if hasattr(dest_selector, 'data'):
                            if dest_selector.data == 'string':
                                dest_node = self._get_string(dest_selector)
                            elif dest_selector.data == 'identifier':
                                dest_node = self._get_identifier(dest_selector)
                                # Check for WHERE clause
                                if i + 2 < len(node.children):
                                    next_child = node.children[i + 2]
                                    next_value = None
                                    if hasattr(next_child, 'value'):
                                        next_value = next_child.value
                                    elif isinstance(next_child, str):
                                        next_value = next_child
                                    if next_value == 'WHERE' and i + 3 < len(node.children):
                                        dest_where = [self._parse_condition(node.children[i + 3])]
                                        dest_type = dest_node
                            elif dest_selector.data == 'node_selector':
                                # node_selector wrapper
                                if hasattr(dest_selector, 'children') and len(dest_selector.children) > 0:
                                    selector_child = dest_selector.children[0]
                                    if hasattr(selector_child, 'data'):
                                        if selector_child.data == 'string':
                                            dest_node = self._get_string(selector_child)
                                        elif selector_child.data == 'identifier':
                                            dest_node = self._get_identifier(selector_child)
                                            # Check for WHERE in node_selector
                                            if len(dest_selector.children) > 1:
                                                where_child = dest_selector.children[1]
                                                if hasattr(where_child, 'data') and where_child.data == 'condition':
                                                    dest_where = [self._parse_condition(where_child)]
                                                    dest_type = dest_node
                        elif hasattr(dest_selector, 'type') and dest_selector.type == 'IDENTIFIER':
                            # Direct identifier token
                            dest_node = str(dest_selector.value) if hasattr(dest_selector, 'value') else str(dest_selector)
        
        # Check if edge_spec_with_endpoints has arrow syntax
        # The parse tree shows: edge_spec_with_endpoints has children: [node_ref, node_ref, property_list]
        # But the arrow might be implicit or the grammar structure is different
        
        # Try to detect arrow syntax by checking if we have two node_refs
        has_arrow = False
        arrow_idx = -1
        
        if not has_src_dest:
            # Check if edge_spec_with_endpoints has the structure: node_ref, node_ref (implicit arrow)
            if len(edge_spec_node.children) >= 2:
                first_child = edge_spec_node.children[0]
                second_child = edge_spec_node.children[1]
                if (hasattr(first_child, 'data') and first_child.data == 'node_ref' and
                    hasattr(second_child, 'data') and second_child.data == 'node_ref'):
                    # This is arrow syntax: (source)->(target)
                    has_arrow = True
                    arrow_idx = 0  # Arrow is between children[0] and children[1]
            
            # Also check for explicit arrow token
            for i, child in enumerate(edge_spec_node.children):
                if hasattr(child, 'value') and child.value == '->':
                    has_arrow = True
                    arrow_idx = i
                    break
        
        if has_arrow:
            # Arrow syntax: (source)->(target) {props}?
            # For CREATE EDGE edge_type (source)->(target) {props}
            # edge_type is already extracted above from node.children[0]
            # The structure is: edge_spec_with_endpoints = [node_ref (source), node_ref (target), property_list?]
            if arrow_idx == 0:
                # Arrow is implicit between first two node_refs
                if len(edge_spec_node.children) >= 2:
                    source_ref = edge_spec_node.children[0]
                    target_ref = edge_spec_node.children[1]
                else:
                    raise ValueError("Invalid arrow syntax: missing source or target node")
            else:
                # Arrow is explicit token
                if len(edge_spec_node.children) > arrow_idx + 1:
                    source_ref = edge_spec_node.children[0]
                    target_ref = edge_spec_node.children[arrow_idx + 1]
                else:
                    raise ValueError(f"Invalid arrow syntax: missing target node (arrow_idx={arrow_idx}, children={len(edge_spec_node.children)})")
            
            # Extract source node
            source_node = None
            if hasattr(source_ref, 'children') and len(source_ref.children) > 0:
                first_child = source_ref.children[0]
                if hasattr(first_child, 'data') and first_child.data == 'identifier':
                    source_node = self._get_identifier(first_child)
                elif hasattr(first_child, 'data') and first_child.data == 'typed_node_ref':
                    if len(first_child.children) >= 2:
                        source_node = self._get_identifier(first_child.children[1])
                    else:
                        source_node = self._get_identifier(first_child.children[0]) if first_child.children else None
                else:
                    source_node = self._get_identifier(first_child) if hasattr(first_child, 'value') else str(first_child)
            else:
                source_node = self._get_identifier(source_ref) if hasattr(source_ref, 'data') else str(source_ref)
            
            # Extract target node
            target_node = None
            if hasattr(target_ref, 'children') and len(target_ref.children) > 0:
                first_child = target_ref.children[0]
                if hasattr(first_child, 'data') and first_child.data == 'identifier':
                    target_node = self._get_identifier(first_child)
                elif hasattr(first_child, 'data') and first_child.data == 'typed_node_ref':
                    if len(first_child.children) >= 2:
                        target_node = self._get_identifier(first_child.children[1])
                    else:
                        target_node = self._get_identifier(first_child.children[0]) if first_child.children else None
                else:
                    target_node = self._get_identifier(first_child) if hasattr(first_child, 'value') else str(first_child)
            else:
                target_node = self._get_identifier(target_ref) if hasattr(target_ref, 'data') else str(target_ref)
            
            # Parse properties if present (after target)
            properties = {}
            # Properties are the third child if arrow_idx == 0, or after arrow_idx + 1
            props_idx = 2 if arrow_idx == 0 else arrow_idx + 2
            if len(edge_spec_node.children) > props_idx:
                props_node = edge_spec_node.children[props_idx]
                if hasattr(props_node, 'data') and props_node.data == 'property_list':
                    properties = self._parse_property_list(props_node)
            
            # edge_type should already be set from the CREATE EDGE statement above
            # If not, it means the grammar structure is different
            if not edge_type:
                # Try to find edge_type in the edge_spec_node structure
                # This shouldn't happen for CREATE EDGE edge_type (source)->(target) syntax
                # But handle it gracefully
                pass
            
            params = {
                'edge_type': edge_type,  # Set from CREATE EDGE identifier
                'source_node': source_node,
                'target_node': target_node,
                'source_alias': None,
                'target_alias': None,
                'properties': properties,
                'edge_alias': None
            }
        elif has_src_dest:
            # SRC/DEST syntax: CREATE EDGE edge_type SRC node_selector DEST node_selector {props}?
            # Use this branch if we detected SRC/DEST tokens, even if parsing failed
            # This ensures we don't fall into the wrong else branch
            # Parse properties if present
            properties = {}
            # Properties might be in edge_spec_node or after DEST
            if edge_spec_node:
                for child in edge_spec_node.children:
                    if hasattr(child, 'data') and child.data == 'property_list':
                        properties = self._parse_property_list(child)
            # Also check node.children for properties
            for child in node.children:
                if hasattr(child, 'data') and child.data == 'property_list':
                    properties = self._parse_property_list(child)
            
            params = {
                'edge_type': edge_type or 'EDGE',  # Default if not found
                'source_node': src_node,
                'target_node': dest_node,
                'source_type': src_type,
                'target_type': dest_type,
                'source_where': src_where,
                'target_where': dest_where,
                'source_alias': None,
                'target_alias': None,
                'properties': properties,
                'edge_alias': None
            }
        else:
            # Syntax 1: (source) edge_type {props} (target)
            # Parse source node reference
            if len(edge_spec_node.children) == 0:
                raise ValueError("Invalid CREATE EDGE syntax: missing edge specification")
            source_ref = edge_spec_node.children[0]
            if hasattr(source_ref, 'children') and len(source_ref.children) > 0:
                source_node = self._get_identifier(source_ref.children[0])
                source_alias = None
                if len(source_ref.children) > 1:
                    source_alias = self._get_identifier(source_ref.children[1])
            else:
                source_node = self._get_identifier(source_ref) if hasattr(source_ref, 'data') else str(source_ref)
                source_alias = None
            
            # Parse edge specification
            if len(edge_spec_node.children) < 2:
                raise ValueError("Invalid CREATE EDGE syntax: missing edge type specification")
            edge_spec = edge_spec_node.children[1]
            if hasattr(edge_spec, 'children') and len(edge_spec.children) > 0:
                edge_type = self._get_identifier(edge_spec.children[0])
            else:
                edge_type = self._get_identifier(edge_spec) if hasattr(edge_spec, 'data') else str(edge_spec)
            
            params = {
                'edge_type': edge_type,
                'source_node': source_node,
                'source_alias': source_alias,
                'properties': {}
            }
            
            # Parse edge alias if present
            if len(edge_spec.children) > 1 and hasattr(edge_spec.children[1], 'data') and edge_spec.children[1].data == 'identifier':
                params['edge_alias'] = self._get_identifier(edge_spec.children[1])
            else:
                params['edge_alias'] = None
            
            # Parse properties if present
            for child in edge_spec.children[1:]:
                if hasattr(child, 'data') and child.data == 'property_list':
                    params['properties'] = self._parse_property_list(child)
            
            # Parse target node reference
            target_ref = edge_spec_node.children[2]
            target_node = self._get_identifier(target_ref.children[0])
            target_alias = None
            if len(target_ref.children) > 1:
                target_alias = self._get_identifier(target_ref.children[1])
            
            params['target_node'] = target_node
            params['target_alias'] = target_alias
        
        # Parse unique key if present (outside edge_spec_with_endpoints)
        if len(node.children) > 1:
            for child in node.children[1:]:
                if hasattr(child, 'data') and child.data == 'identifier_list':
                    params['unique_keys'] = self._parse_identifier_list(child)
        
        if 'unique_keys' not in params:
            params['unique_keys'] = []
        
        return AIQLNode(
            node_type=AIQLNodeType.CREATE_EDGE,
            parameters=params
        )
    
    def _parse_simple_edge_spec(self, node) -> AIQLNode:
        """Parse simple edge specification without endpoints."""
        params = {}
        
        # Parse edge type (first child should be identifier)
        edge_type = self._get_identifier(node.children[0])
        params['edge_type'] = edge_type
        params['edge_alias'] = None
        params['properties'] = {}
        params['source_node'] = None
        params['target_node'] = None
        params['source_alias'] = None
        params['target_alias'] = None
        
        # Parse edge alias if present
        if len(node.children) > 1 and hasattr(node.children[1], 'data') and node.children[1].data == 'identifier':
            params['edge_alias'] = self._get_identifier(node.children[1])
        
        # Parse properties if present
        for child in node.children[1:]:
            if hasattr(child, 'data') and child.data == 'property_list':
                params['properties'] = self._parse_property_list(child)
        
        params['unique_keys'] = []
        
        return AIQLNode(
            node_type=AIQLNodeType.CREATE_EDGE,
            parameters=params
        )
    
    def _parse_commit_stage(self, node) -> AIQLNode:
        """Parse COMMIT statement."""
        return AIQLNode(
            node_type=AIQLNodeType.COMMIT,
            parameters={}
        )
    
    def _parse_load_stage(self, node) -> AIQLNode:
        """Parse LOAD statement for various data sources."""
        params = {}
        
        # Iterate through children to find load type, path, target, and options
        for i, child in enumerate(node.children):
            if hasattr(child, 'data'):
                if child.data == 'load_document':
                    params['load_type'] = 'DOCUMENT'
                    if len(child.children) > 0:
                        params['path'] = self._get_string(child.children[0])
                elif child.data == 'load_folder':
                    params['load_type'] = 'FOLDER'
                    if len(child.children) > 0:
                        params['path'] = self._get_string(child.children[0])
                elif child.data == 'load_csv':
                    params['load_type'] = 'CSV'
                    if len(child.children) > 0:
                        params['path'] = self._get_string(child.children[0])
                elif child.data == 'load_json':
                    params['load_type'] = 'JSON'
                    if len(child.children) > 0:
                        params['path'] = self._get_string(child.children[0])
                elif child.data == 'load_xml':
                    params['load_type'] = 'XML'
                    if len(child.children) > 0:
                        params['path'] = self._get_string(child.children[0])
                elif child.data == 'load_sql':
                    params['load_type'] = 'SQL'
                    if len(child.children) > 0:
                        params['path'] = self._get_string(child.children[0])
                elif child.data == 'load_excel':
                    params['load_type'] = 'EXCEL'
                    if len(child.children) > 0:
                        params['path'] = self._get_string(child.children[0])
                elif child.data == 'load_api_source':
                    params['load_type'] = 'API'
                    if len(child.children) > 0:
                        params['path'] = self._get_string(child.children[0])
                elif child.data == 'identifier':
                    # This could be INTO target or FROM source
                    if 'target' not in params:
                        params['target'] = self._get_identifier(child)
                    else:
                        params['from_source'] = self._get_identifier(child)
                elif 'options' in child.data:
                    params['options'] = self._parse_load_options(child)
        
        # Set default values
        if 'load_type' not in params:
            params['load_type'] = 'DOCUMENT'
        if 'path' not in params:
            params['path'] = ''
        if 'target' not in params:
            params['target'] = 'default'
        
        
        # Map load type to node type
        node_type_map = {
            'DOCUMENT': AIQLNodeType.LOAD_DOCUMENT,
            'FOLDER': AIQLNodeType.LOAD_FOLDER,
            'CSV': AIQLNodeType.LOAD_CSV,
            'JSON': AIQLNodeType.LOAD_JSON,
            'XML': AIQLNodeType.LOAD_XML,
            'SQL': AIQLNodeType.LOAD_SQL,
            'API': AIQLNodeType.LOAD_API,
            'EXCEL': AIQLNodeType.LOAD_EXCEL
        }
        
        node_type = node_type_map.get(params['load_type'], AIQLNodeType.LOAD_DOCUMENT)
        
        return AIQLNode(
            node_type=node_type,
            parameters=params
        )
    
    def _parse_load_options(self, node) -> Dict[str, Any]:
        """Parse load options (CSV, JSON, XML, SQL, API options)."""
        options = {}
        
        for child in node.children:
            if hasattr(child, 'data'):
                if child.data == 'csv_option':
                    key = child.children[0].value.lower() if hasattr(child.children[0], 'value') else str(child.children[0]).lower()
                    value = self._get_value(child.children[2])
                    options[key] = value
                elif child.data == 'json_option':
                    key = child.children[0].value.lower() if hasattr(child.children[0], 'value') else str(child.children[0]).lower()
                    value = self._get_value(child.children[2])
                    options[key] = value
                elif child.data == 'xml_option':
                    key = child.children[0].value.lower() if hasattr(child.children[0], 'value') else str(child.children[0]).lower()
                    value = self._get_value(child.children[2])
                    options[key] = value
                elif child.data == 'sql_option':
                    key = child.children[0].value.lower() if hasattr(child.children[0], 'value') else str(child.children[0]).lower()
                    value = self._get_value(child.children[2])
                    options[key] = value
                elif child.data == 'api_option':
                    key = child.children[0].value.lower() if hasattr(child.children[0], 'value') else str(child.children[0]).lower()
                    value = self._get_value(child.children[2])
                    options[key] = value
                elif child.data == 'load_option':
                    key = self._get_identifier(child.children[0])
                    value = self._get_value(child.children[2])
                    options[key] = value
        
        return options
    
    def _parse_traverse_stage(self, node) -> AIQLNode:
        """Parse TRAVERSE statement with optional strategy and options.
        Grammar: "TRAVERSE" strategy? traverse_spec strategy_suffix? traversal_options?
        """
        strategy = 'DFS'  # Default strategy
        traverse_spec = None
        max_depth = None
        min_depth = None
        limit = None
        
        # Parse all children
        for child in node.children:
            if hasattr(child, 'data'):
                # Debug logging
                logger.debug(f"[DEBUG] Parsing traverse_stage child: data='{child.data}', children={len(child.children) if hasattr(child, 'children') else 0}")
                
                # Check for strategy at beginning
                if child.data == 'strategy':
                    if len(child.children) > 0:
                        strategy_token = child.children[0]
                        # Extract strategy value - it might be a token or have a value attribute
                        if hasattr(strategy_token, 'type') and strategy_token.type in ['DFS', 'BFS']:
                            strategy = strategy_token.type
                        elif hasattr(strategy_token, 'value') and strategy_token.value in ['DFS', 'BFS']:
                            strategy = strategy_token.value
                
                # Check for traverse_spec types
                elif child.data in ['inline_traversal', 'verbose_traversal', 'recursive_traversal', 'simple_traversal', 'traverse_spec']:
                    logger.debug(f"[DEBUG] Found traverse_spec: {child.data}")
                    traverse_spec = child
                
                # Check for strategy_suffix at end
                elif child.data == 'strategy_suffix':
                    if len(child.children) > 0:
                        # strategy_suffix contains one child which is the DFS or BFS token
                        strategy_token = child.children[0]
                        # The token can be accessed directly
                        if hasattr(strategy_token, 'type') and strategy_token.type in ['DFS', 'BFS']:
                            strategy = strategy_token.type
                        elif hasattr(strategy_token, 'value') and strategy_token.value in ['DFS', 'BFS']:
                            strategy = strategy_token.value
                
                # Check for traversal_options
                elif child.data == 'traversal_options':
                    for option in child.children:
                        if hasattr(option, 'data') and option.data == 'traversal_option':
                            # Extract option type and value
                            # Structure: traversal_option > [TOKEN (MAX_DEPTH/MIN_DEPTH/LIMIT), number tree]
                            if len(option.children) >= 2:
                                option_type = option.children[0]
                                value_node = option.children[1]
                                
                                # Extract the actual number value
                                if hasattr(value_node, 'data') and value_node.data == 'number':
                                    # value_node is a tree with data='number', extract first child token
                                    if len(value_node.children) > 0:
                                        number_token = value_node.children[0]
                                        value = int(number_token.value) if hasattr(number_token, 'value') else None
                                    else:
                                        value = None
                                elif hasattr(value_node, 'value'):
                                    # value_node is directly a token
                                    value = int(value_node.value)
                                else:
                                    value = None
                                
                                # Assign to appropriate variable based on option type
                                if hasattr(option_type, 'type'):
                                    if option_type.type == 'MAX_DEPTH' and value is not None:
                                        max_depth = value
                                    elif option_type.type == 'MIN_DEPTH' and value is not None:
                                        min_depth = value
                                    elif option_type.type == 'LIMIT' and value is not None:
                                        limit = value
        
        # Parse the traverse_spec
        hops = []
        if traverse_spec:
            if traverse_spec.data == 'inline_traversal':
                hops = self._parse_inline_traversal(traverse_spec)
            elif traverse_spec.data == 'verbose_traversal':
                hops = self._parse_verbose_traversal(traverse_spec)
                # Check if strategy is embedded in verbose_traversal children (overrides default)
                for child in traverse_spec.children:
                    if hasattr(child, 'type') and child.type in ['DFS', 'BFS']:
                        strategy = child.type
                        break
                    elif hasattr(child, 'value') and child.value in ['DFS', 'BFS']:
                        strategy = child.value
                        break
            elif traverse_spec.data == 'recursive_traversal':
                hops = self._parse_recursive_traversal(traverse_spec)
            elif traverse_spec.data == 'simple_traversal':
                hops = self._parse_simple_traversal(traverse_spec)
            elif traverse_spec.data == 'traverse_spec' and len(traverse_spec.children) > 0:
                # traverse_spec is a wrapper, check its child
                child = traverse_spec.children[0]
                if child.data == 'inline_traversal':
                    hops = self._parse_inline_traversal(child)
                elif child.data == 'verbose_traversal':
                    hops = self._parse_verbose_traversal(child)
                    # Check if strategy is embedded in verbose_traversal children (overrides default)
                    for vchild in child.children:
                        if hasattr(vchild, 'type') and vchild.type in ['DFS', 'BFS']:
                            strategy = vchild.type
                            break
                        elif hasattr(vchild, 'value') and vchild.value in ['DFS', 'BFS']:
                            strategy = vchild.value
                            break
                elif child.data == 'recursive_traversal':
                    hops = self._parse_recursive_traversal(child)
                elif child.data == 'simple_traversal':
                    hops = self._parse_simple_traversal(child)
        else:
            # Fallback: if no traverse_spec found, try to parse as simple traversal
            # This handles cases where the grammar doesn't correctly identify traverse_spec
            logger.debug(f"[DEBUG] No traverse_spec found, checking for fallback simple traversal")
            
            # Look for SRC token in children
            for child in node.children:
                if hasattr(child, 'value') and child.value == 'SRC':
                    # Found SRC token, look for the next node_ref
                    from_index = node.children.index(child)
                    if from_index + 1 < len(node.children):
                        next_child = node.children[from_index + 1]
                        if hasattr(next_child, 'data') and next_child.data == 'node_ref':
                            logger.debug(f"[DEBUG] Found fallback simple traversal with SRC and node_ref")
                            # Create a simple traversal node manually
                            hops = self._parse_simple_traversal(next_child)
                            break
            
            # If still no hops found, try regex-based parsing like QGQL
            if not hops and hasattr(self, '_current_query'):
                import re
                query = self._current_query
                logger.debug(f"[DEBUG] Trying regex fallback for query: {query}")
                
                # Pattern: TRAVERSE SRC "node1" (simple syntax)
                pattern = r'TRAVERSE\s+SRC\s+([a-zA-Z_][a-zA-Z0-9_]*)'
                match = re.match(pattern, query, re.IGNORECASE)
                
                if match:
                    node_name = match.group(1)
                    logger.debug(f"[DEBUG] Regex matched node: {node_name}")
                    
                    # Create a simple TraverseHop manually
                    hop = TraverseHop(
                        from_node=node_name,
                        from_alias=None,
                        edge_type='*',  # Wildcard for simple traversal
                        edge_alias=None,
                        to_node=None,  # Simple traversal doesn't specify target
                        to_alias=None,
                        direction='OUTGOING',  # Default direction
                        quantifier={'min': 1, 'max': None, 'type': 'one_or_more'}
                    )
                    hops = [hop]
                    logger.debug(f"[DEBUG] Created fallback hop: {hop}")
        
        params = {
            'hops': hops,
            'strategy': strategy
        }
        
        # Add optional parameters if specified
        if max_depth is not None:
            params['max_depth'] = max_depth
        if min_depth is not None:
            params['min_depth'] = min_depth
        if limit is not None:
            params['limit'] = limit
        
        return AIQLNode(
            node_type=AIQLNodeType.TRAVERSE,
            parameters=params
        )
    
    def _parse_inline_traversal(self, node) -> List[TraverseHop]:
        """Parse inline traversal: Node:a -EDGE-> Node:b -EDGE-> Node:c"""
        hops = []
        children = node.children
        
        # First node
        from_node = self._get_identifier(children[0].children[0])
        from_alias = None
        if len(children[0].children) > 1:
            from_alias = self._get_identifier(children[0].children[1])
        
        # Process hops
        for i in range(1, len(children), 2):
            if i + 1 < len(children):
                edge_ref = children[i]
                to_ref = children[i + 1]
                
                edge_type = self._get_identifier(edge_ref.children[0])
                edge_alias = None
                if len(edge_ref.children) > 1:
                    edge_alias = self._get_identifier(edge_ref.children[1])
                
                to_node = self._get_identifier(to_ref.children[0])
                to_alias = None
                if len(to_ref.children) > 1:
                    to_alias = self._get_identifier(to_ref.children[1])
                
                hop = TraverseHop(
                    from_node=from_node,
                    from_alias=from_alias,
                    edge_type=edge_type,
                    edge_alias=edge_alias,
                    to_node=to_node,
                    to_alias=to_alias,
                    direction="OUTGOING"
                )
                hops.append(hop)
                
                # Update for next iteration
                from_node = to_node
                from_alias = to_alias
    
    def _parse_verbose_traversal(self, node) -> List[TraverseHop]:
        """Parse verbose traversal: FROM Node AS a OUTGOING EDGE TO Node AS b OR Node VIA EDGE Edge TO Node
        Also handles optional STRATEGY (DFS|BFS) at the end and chained hops
        Also handles mixed format: SRC Node -EDGE-> Node"""
        children = node.children
        
        # Filter out strategy tokens (BFS/DFS) at the end - they're handled by _parse_traverse_stage
        data_children = [c for c in children if hasattr(c, 'data')]
        
        hops = []
        
        # Check if it's mixed SRC format with inline traversal: SRC Node -EDGE-> Node
        if len(data_children) > 0 and data_children[0].data == 'node_ref':
            # Check if next is inline traversal pattern
            if len(data_children) >= 2:
                # Look for inline_traversal pattern starting after SRC
                # Find where inline_traversal pattern starts (look for "-" pattern)
                i = 1
                while i < len(children):
                    if hasattr(children[i], 'value') and children[i].value == '-':
                        # This is mixed format: SRC node_ref -edge_ref-> node_ref
                        from_ref = data_children[0]
                        from_node = self._get_identifier(from_ref.children[0]) if hasattr(from_ref, 'children') and len(from_ref.children) > 0 else self._get_identifier(from_ref)
                        from_alias = None
                        if hasattr(from_ref, 'children') and len(from_ref.children) > 1:
                            from_alias = self._get_identifier(from_ref.children[1])
                        
                        # Parse the inline_traversal part
                        inline_part = node.children[1]  # Should be the inline_traversal part
                        if hasattr(inline_part, 'data') and inline_part.data == 'inline_traversal':
                            inline_hops = self._parse_inline_traversal(inline_part)
                            hops.extend(inline_hops)
                        else:
                            # Manually parse: -edge_ref-> node_ref
                            edge_node = None
                            to_node = None
                            for j in range(i + 1, len(children)):
                                c = children[j]
                                if hasattr(c, 'data') and c.data == 'edge_ref':
                                    edge_node = c
                                elif hasattr(c, 'data') and c.data == 'node_ref':
                                    to_node = c
                                    break
                            
                            if edge_node and to_node:
                                edge_type = self._get_identifier(edge_node.children[0]) if hasattr(edge_node, 'children') and len(edge_node.children) > 0 else self._get_identifier(edge_node)
                                to_node_id = self._get_identifier(to_node.children[0]) if hasattr(to_node, 'children') and len(to_node.children) > 0 else self._get_identifier(to_node)
                                to_alias = None
                                if hasattr(to_node, 'children') and len(to_node.children) > 1:
                                    to_alias = self._get_identifier(to_node.children[1])
                                
                                hop = TraverseHop(
                                    from_node=from_node,
                                    from_alias=from_alias,
                                    edge_type=edge_type,
                                    edge_alias=None,
                                    to_node=to_node_id,
                                    to_alias=to_alias,
                                    direction="OUTGOING"
                                )
                                hops.append(hop)
                        break
                    i += 1
        
        # If no mixed format, check if it's standard SRC syntax
        if len(hops) == 0 and len(data_children) >= 4 and data_children[0].data == 'node_ref' and len(data_children) > 1 and hasattr(data_children[1], 'data') and data_children[1].data == 'direction':
            # SRC syntax: SRC node_ref direction edge_ref DEST node_ref [chained_hops]
            from_ref = data_children[0]
            if hasattr(from_ref, 'children') and len(from_ref.children) > 0:
                from_node = self._get_identifier(from_ref.children[0])
                from_alias = None
                if len(from_ref.children) > 1:
                    from_alias = self._get_identifier(from_ref.children[1])
            else:
                # SRC ref might be a direct identifier
                from_node = self._get_identifier(from_ref)
                from_alias = None
            
            # Direction
            direction_node = data_children[1]
            if hasattr(direction_node, 'children') and len(direction_node.children) > 0:
                direction = direction_node.children[0].value
            else:
                direction = 'OUTGOING'  # Default
            
            # Edge
            edge_ref = data_children[2]
            if hasattr(edge_ref, 'children') and len(edge_ref.children) > 0:
                edge_type = self._get_identifier(edge_ref.children[0])
                edge_alias = None
                if len(edge_ref.children) > 1:
                    edge_alias = self._get_identifier(edge_ref.children[1])
            else:
                # Edge ref might be a direct identifier
                edge_type = self._get_identifier(edge_ref)
                edge_alias = None
            
            # DEST node
            to_ref = data_children[3]
            if hasattr(to_ref, 'children') and len(to_ref.children) > 0:
                to_node = self._get_identifier(to_ref.children[0])
                to_alias = None
                if len(to_ref.children) > 1:
                    to_alias = self._get_identifier(to_ref.children[1])
            else:
                # DEST ref might be a direct identifier
                to_node = self._get_identifier(to_ref)
                to_alias = None
            
            # Create first hop
            hop = TraverseHop(
                from_node=from_node,
                from_alias=from_alias,
                edge_type=edge_type,
                edge_alias=edge_alias,
                to_node=to_node,
                to_alias=to_alias,
                direction=direction
            )
            hops.append(hop)
            
            # Check for chained hops
            if len(data_children) > 4:
                # Look for chained_hops pattern
                remaining_children = data_children[4:]
                current_from = to_node  # Next hop starts from the previous destination
                
                # Parse chained hops: direction edge_ref DEST node_ref [direction edge_ref DEST node_ref ...]
                i = 0
                while i < len(remaining_children):
                    if i + 2 < len(remaining_children):
                        # direction edge_ref DEST node_ref
                        chained_direction_node = remaining_children[i]
                        chained_edge_ref = remaining_children[i + 1]
                        chained_to_ref = remaining_children[i + 2]
                        
                        # Parse direction
                        if hasattr(chained_direction_node, 'children') and len(chained_direction_node.children) > 0:
                            chained_direction = chained_direction_node.children[0].value
                        else:
                            chained_direction = 'OUTGOING'
                        
                        # Parse edge
                        if hasattr(chained_edge_ref, 'children') and len(chained_edge_ref.children) > 0:
                            chained_edge_type = self._get_identifier(chained_edge_ref.children[0])
                            chained_edge_alias = None
                            if len(chained_edge_ref.children) > 1:
                                chained_edge_alias = self._get_identifier(chained_edge_ref.children[1])
                        else:
                            chained_edge_type = self._get_identifier(chained_edge_ref)
                            chained_edge_alias = None
                        
                        # Parse destination
                        if hasattr(chained_to_ref, 'children') and len(chained_to_ref.children) > 0:
                            chained_to_node = self._get_identifier(chained_to_ref.children[0])
                            chained_to_alias = None
                            if len(chained_to_ref.children) > 1:
                                chained_to_alias = self._get_identifier(chained_to_ref.children[1])
                        else:
                            chained_to_node = self._get_identifier(chained_to_ref)
                            chained_to_alias = None
                        
                        # Create chained hop
                        chained_hop = TraverseHop(
                            from_node=current_from,
                            from_alias=None,
                            edge_type=chained_edge_type,
                            edge_alias=chained_edge_alias,
                            to_node=chained_to_node,
                            to_alias=chained_to_alias,
                            direction=chained_direction
                        )
                        hops.append(chained_hop)
                        
                        # Update current_from for next iteration
                        current_from = chained_to_node
                        
                        i += 3  # Move to next set of direction edge_ref DEST node_ref
                    else:
                        break
                        
        elif len(data_children) >= 3:
            # VIA syntax: node_ref traverse_edge_pattern node_ref
            # Structure: node_ref VIA EDGE traverse_edge_pattern TO node_ref
            from_ref = data_children[0]
            from_node = self._get_identifier(from_ref.children[0])
            from_alias = None
            if len(from_ref.children) > 1:
                from_alias = self._get_identifier(from_ref.children[1])
            
            # Direction is OUTGOING by default for VIA syntax
            direction = 'OUTGOING'
            
            # Edge pattern (could be edge_ref, edge_wildcard, or recursive_edge_pattern)
            edge_pattern = data_children[1]
            if edge_pattern.data == 'traverse_edge_pattern':
                # Unwrap traverse_edge_pattern
                edge_pattern = edge_pattern.children[0]
            
            if edge_pattern.data == 'edge_wildcard':
                edge_type = '*'
                edge_alias = None
            elif edge_pattern.data == 'edge_ref':
                edge_type = self._get_identifier(edge_pattern.children[0])
                edge_alias = None
                if len(edge_pattern.children) > 1:
                    edge_alias = self._get_identifier(edge_pattern.children[1])
            elif edge_pattern.data == 'recursive_edge_pattern':
                # Handle recursive patterns
                if len(edge_pattern.children) > 0:
                    first_child = edge_pattern.children[0]
                    if hasattr(first_child, 'value'):
                        edge_type = first_child.value if first_child.value != '*' else '*'
                    else:
                        edge_type = self._get_identifier(first_child)
                else:
                    edge_type = '*'
                edge_alias = None
            else:
                edge_type = '*'
                edge_alias = None
            
            # TO node
            to_ref = data_children[2]
            to_node = self._get_identifier(to_ref.children[0])
            to_alias = None
            if len(to_ref.children) > 1:
                to_alias = self._get_identifier(to_ref.children[1])
                
            hop = TraverseHop(
                from_node=from_node,
                from_alias=from_alias,
                edge_type=edge_type,
                edge_alias=edge_alias,
                to_node=to_node,
                to_alias=to_alias,
                direction=direction
            )
            hops.append(hop)
        else:
            # Fallback - create a minimal hop
            from_node = 'unknown'
            from_alias = None
            edge_type = '*'
            edge_alias = None
            to_node = 'unknown'
            to_alias = None
            direction = 'OUTGOING'
            
            hop = TraverseHop(
                from_node=from_node,
                from_alias=from_alias,
                edge_type=edge_type,
                edge_alias=edge_alias,
                to_node=to_node,
                to_alias=to_alias,
                direction=direction
            )
            hops.append(hop)
        
        return hops
    
    def _parse_recursive_traversal(self, node) -> List[TraverseHop]:
        """Parse recursive traversal: Node AS p VIA EDGE CITE+ TO Node AS p2"""
        children = node.children
        
        # Ensure we have enough children
        if len(children) < 3:
            # Not enough children, return empty or default
            return []
        
        # Parse: node_ref VIA EDGE recursive_edge_pattern TO node_ref
        from_ref = children[0]
        from_node = self._get_identifier(from_ref.children[0])
        from_alias = None
        if len(from_ref.children) > 1:
            from_alias = self._get_identifier(from_ref.children[1])
        
        # Parse recursive edge pattern: identifier quantifier
        # Lark doesn't include literals in children, so actual structure is:
        # children[0]=node_ref, [1]=recursive_edge_pattern, [2]=node_ref
        recursive_pattern = children[1]  # The recursive_edge_pattern
        
        # Check if recursive_pattern has enough children
        if len(recursive_pattern.children) < 2:
            # Handle case where quantifier might be embedded differently
            if len(recursive_pattern.children) == 1:
                child = recursive_pattern.children[0]
                if hasattr(child, 'data') and child.data == 'identifier':
                    edge_type = self._get_identifier(child)
                else:
                    edge_type = str(child)
                quantifier = {'min': 1, 'max': None, 'type': 'one_or_more'}  # Default to +
            else:
                edge_type = '*'
                quantifier = {'min': 0, 'max': None, 'type': 'zero_or_more'}
        else:
            edge_type = self._get_identifier(recursive_pattern.children[0])
            quantifier_node = recursive_pattern.children[1]
            # Parse quantifier
            quantifier = self._parse_quantifier(quantifier_node)
        
        # TO node
        to_ref = children[2]  # The target node_ref
        to_node = self._get_identifier(to_ref.children[0])
        to_alias = None
        if len(to_ref.children) > 1:
            to_alias = self._get_identifier(to_ref.children[1])
        
        hop = TraverseHop(
            from_node=from_node,
            from_alias=from_alias,
            edge_type=edge_type,
            edge_alias=None,
            to_node=to_node,
            to_alias=to_alias,
            direction='OUTGOING',
            quantifier=quantifier  # Add quantifier to hop
        )
        
        return [hop]
    
    def _parse_simple_traversal(self, node) -> List[TraverseHop]:
        """Parse simple traversal: FROM Person WHERE condition DEPTH number"""
        children = node.children
        
        if len(children) < 1:
            return []
        
        # Parse: SRC node_ref where_stage? ("DEPTH" number)?
        from_ref = children[0]  # node_ref
        from_node = self._get_identifier(from_ref.children[0])
        from_alias = None
        if len(from_ref.children) > 1:
            from_alias = self._get_identifier(from_ref.children[1])
        
        # Parse optional WHERE clause and DEPTH
        where_condition = None
        depth = None
        
        for i in range(1, len(children)):
            child = children[i]
            if hasattr(child, 'data'):
                if child.data == 'where_stage':
                    where_condition = self._parse_where_stage(child)
                elif hasattr(child, 'value') and child.value == 'DEPTH':
                    # DEPTH is followed by a number
                    if i + 1 < len(children):
                        depth_node = children[i + 1]
                        if hasattr(depth_node, 'data') and depth_node.data == 'number':
                            depth = int(self._get_number(depth_node))
        
        hop = TraverseHop(
            from_node=from_node,
            from_alias=from_alias,
            edge_type='*',  # Wildcard for simple traversal
            edge_alias=None,
            to_node=None,  # Simple traversal doesn't specify target
            to_alias=None,
            direction='OUTGOING',  # Default direction
            quantifier={'min': 1, 'max': depth, 'type': 'range'} if depth else {'min': 1, 'max': None, 'type': 'one_or_more'}
        )
        
        return [hop]
    
    def _parse_quantifier(self, node) -> Dict[str, Any]:
        """Parse quantifier: +, *, ?, {n}, {n,m}, {n,}, {,m}"""
        if hasattr(node, 'value'):
            # Simple quantifiers: +, *, ?
            quantifier_value = node.value
            if quantifier_value == '+':
                return {'min': 1, 'max': None, 'type': 'one_or_more'}
            elif quantifier_value == '*':
                return {'min': 0, 'max': None, 'type': 'zero_or_more'}
            elif quantifier_value == '?':
                return {'min': 0, 'max': 1, 'type': 'zero_or_one'}
        elif hasattr(node, 'data') and node.data == 'range_quantifier':
            # Range quantifiers: {n}, {n,m}, {n,}, {,m}
            return self._parse_range_quantifier(node)
        
        # Default: single occurrence
        return {'min': 1, 'max': 1, 'type': 'exact'}
    
    def _parse_range_quantifier(self, node) -> Dict[str, Any]:
        """Parse range quantifier: {n}, {n,m}, {n,}, {,m}"""
        # Parse the numbers from the range quantifier
        numbers = []
        for child in node.children:
            if hasattr(child, 'data') and child.data == 'number':
                numbers.append(self._get_number(child.children[0]))
        
        if len(numbers) == 1:
            # {n} - exactly n occurrences
            return {'min': numbers[0], 'max': numbers[0], 'type': 'exact'}
        elif len(numbers) == 2:
            # {n,m} - between n and m occurrences
            return {'min': numbers[0], 'max': numbers[1], 'type': 'range'}
        elif len(numbers) == 0:
            # {,m} - up to m occurrences (check for comma at start)
            return {'min': 0, 'max': None, 'type': 'range'}
        else:
            # {n,} - n or more occurrences
            return {'min': numbers[0], 'max': None, 'type': 'range'}
    
    def _parse_where_stage(self, node) -> AIQLNode:
        """Parse WHERE clause."""
        conditions = self._parse_condition(node.children[0])
        return AIQLNode(
            node_type=AIQLNodeType.WHERE,
            parameters={'conditions': conditions}
        )
    
    def _parse_select_stage(self, node) -> AIQLNode:
        """Parse SELECT stage."""
        return self._parse_select(node)
    
    def _parse_select(self, node) -> AIQLNode:
        """Parse SELECT clause with FROM, WHERE, GROUP BY, ORDER BY, and LIMIT."""
        params = {}
        
        # Debug: Log all children
        # Parse projections
        params['projections'] = self._parse_projection_list(node.children[0])
        
        # Parse FROM clause if present
        # Grammar has: ("FROM" node_spec)? where node_spec: identifier ("AS" identifier)? ("{" property_list "}")?
        from_index = 1
        if len(node.children) > from_index:
            child = node.children[from_index]
            # Check for from_clause (used in select_stage)
            if hasattr(child, 'data') and child.data == 'from_clause':
                params['from'] = self._get_identifier(child.children[0])
                from_index += 1
            # Check for node_spec directly (used in top_select)
            elif hasattr(child, 'data') and child.data == 'node_spec':
                params['from'] = self._get_identifier(child.children[0])
                from_index += 1
            # Also check if the child is a token with value "FROM" and next child is node_spec or variable
            elif hasattr(child, 'type') and child.type == 'FROM':
                if len(node.children) > from_index + 1:
                    next_child = node.children[from_index + 1]
                    if hasattr(next_child, 'data') and next_child.data == 'node_spec':
                        params['from'] = self._get_identifier(next_child.children[0])
                        from_index += 2
                    elif hasattr(next_child, 'data') and next_child.data == 'variable':
                        var_name = self._get_identifier(next_child.children[0]) if next_child.children else None
                        if var_name and var_name.startswith('$'):
                            params['from'] = var_name
                        else:
                            params['from'] = f"${var_name}" if var_name else None
                        params['from_is_variable'] = True
                        from_index += 2
            # Check for variable directly (FROM variable without explicit FROM token)
            elif hasattr(child, 'data') and child.data == 'variable':
                var_name = self._get_identifier(child.children[0]) if child.children else None
                if var_name and var_name.startswith('$'):
                    params['from'] = var_name
                else:
                    params['from'] = f"${var_name}" if var_name else None
                params['from_is_variable'] = True
                from_index += 1
        
        # Parse AS OF clause if present (temporal query)
        # Parse temporal clause (as_of_clause or for_system_time)
        if len(node.children) > from_index and hasattr(node.children[from_index], 'data'):
            if node.children[from_index].data == 'as_of_clause':
                params['as_of'] = self._parse_as_of_clause(node.children[from_index])
                from_index += 1
            elif node.children[from_index].data == 'for_system_time':
                params['for_system_time'] = self._parse_for_system_time(node.children[from_index])
                from_index += 1
        
        # Parse WHERE clause if present
        if len(node.children) > from_index and hasattr(node.children[from_index], 'data') and node.children[from_index].data == 'where_clause':
            params['where'] = self._parse_where_clause(node.children[from_index])
            from_index += 1
        
        # Parse GROUP BY clause if present
        # Check for group_by_clause first
        if len(node.children) > from_index and hasattr(node.children[from_index], 'data') and node.children[from_index].data == 'group_by_clause':
            params['group_by'] = self._parse_group_by_clause(node.children[from_index])
            from_index += 1
        # Also check for qualified_identifier_list (used in top_select grammar)
        elif len(node.children) > from_index and hasattr(node.children[from_index], 'data') and node.children[from_index].data == 'qualified_identifier_list':
            # This is GROUP BY - parse the identifier list
            group_by_fields = []
            for child in node.children[from_index].children:
                if hasattr(child, 'data') and child.data == 'qualified_identifier':
                    group_by_fields.append(self._get_qualified_identifier(child))
                elif hasattr(child, 'data') and child.data == 'identifier':
                    group_by_fields.append(self._get_identifier(child))
            if group_by_fields:
                params['group_by'] = group_by_fields
                from_index += 1
        
        # Parse HAVING clause if present (comes after GROUP BY)
        if len(node.children) > from_index and hasattr(node.children[from_index], 'data') and node.children[from_index].data == 'having_clause':
            # Skip HAVING for now
            from_index += 1
        
        # Parse ORDER BY clause if present
        if len(node.children) > from_index and hasattr(node.children[from_index], 'data') and node.children[from_index].data == 'order_by_clause':
            params['order_by'] = self._parse_order_by_clause(node.children[from_index])
            from_index += 1
        
        # Parse USE INDEX clause if present
        if len(node.children) > from_index:
            child = node.children[from_index]
            if hasattr(child, 'value') and child.value == 'USE' and from_index + 2 < len(node.children):
                if hasattr(node.children[from_index+1], 'value') and node.children[from_index+1].value == 'INDEX':
                    index_name = self._get_identifier(node.children[from_index+2])
                    params['use_index'] = index_name
                    from_index += 3
        
        # Parse LIMIT clause if present
        if len(node.children) > from_index and hasattr(node.children[from_index], 'data') and node.children[from_index].data == 'limit_clause':
            limit_clause = node.children[from_index]
            if hasattr(limit_clause, 'children') and len(limit_clause.children) > 0:
                limit_token = limit_clause.children[0]
                while hasattr(limit_token, 'children') and len(limit_token.children) > 0 and not hasattr(limit_token, 'value'):
                    limit_token = limit_token.children[0]
                limit_value = self._get_number(limit_token)
            else:
                limit_value = self._get_number(limit_clause)
            params['limit'] = limit_value
            from_index += 1
        # Also handle inline LIMIT: lark consumes "LIMIT" terminal, leaving just a number node
        elif len(node.children) > from_index and hasattr(node.children[from_index], 'data') and node.children[from_index].data == 'number':
            params['limit'] = self._get_number(node.children[from_index])
            from_index += 1

        # Parse OFFSET clause if present (number node after LIMIT)
        if 'limit' in params and len(node.children) > from_index and hasattr(node.children[from_index], 'data') and node.children[from_index].data == 'number':
            params['offset'] = self._get_number(node.children[from_index])
            from_index += 1
        
        # Debug: Check all remaining children for GROUP BY, HAVING, ORDER BY, LIMIT
        if len(node.children) > from_index:
            i = from_index
            while i < len(node.children):
                child = node.children[i]
                
                # Check for GROUP BY
                if hasattr(child, 'data') and child.data == 'group_by_clause':
                    params['group_by'] = self._parse_group_by_clause(child)
                    i += 1
                elif hasattr(child, 'value') and child.value == 'GROUP':
                    if i + 2 < len(node.children):
                        if (hasattr(node.children[i + 1], 'value') and 
                            node.children[i + 1].value == 'BY'):
                            group_by_node = node.children[i + 2]
                            if hasattr(group_by_node, 'data') and group_by_node.data == 'identifier_list':
                                params['group_by'] = self._parse_group_by_clause(group_by_node)
                            elif hasattr(group_by_node, 'data') and group_by_node.data == 'identifier':
                                params['group_by'] = [self._get_identifier(group_by_node)]
                            i += 3
                        else:
                            i += 1
                    else:
                        i += 1
                # Check for HAVING
                elif hasattr(child, 'data') and child.data == 'having_clause':
                    i += 1
                # Check for ORDER BY
                elif hasattr(child, 'data') and child.data == 'order_by_clause':
                    params['order_by'] = self._parse_order_by_clause(child)
                    i += 1
                # Check for LIMIT
                elif hasattr(child, 'data') and child.data == 'limit_clause':
                    if hasattr(child, 'children') and len(child.children) > 0:
                        limit_token = child.children[0]
                        while hasattr(limit_token, 'children') and len(limit_token.children) > 0 and not hasattr(limit_token, 'value'):
                            limit_token = limit_token.children[0]
                        params['limit'] = self._get_number(limit_token)
                    i += 1
                else:
                    i += 1
        
        return AIQLNode(
            node_type=AIQLNodeType.SELECT,
            parameters=params
        )
    
    def _parse_where_clause(self, node) -> List[Dict[str, Any]]:
        """Parse WHERE clause conditions."""
        conditions = []
        for child in node.children:
            if hasattr(child, 'data') and child.data == 'select_condition':
                # Try to get field as qualified_identifier first (handles e.type, d.title)
                field = None
                if len(child.children) > 0:
                    field_child = child.children[0]
                    if hasattr(field_child, 'data') and field_child.data == 'qualified_identifier':
                        field = self._get_qualified_identifier(field_child)
                    elif hasattr(field_child, 'data') and field_child.data == 'identifier':
                        field = self._get_identifier(field_child)
                    else:
                        # Try to extract as string
                        field = str(field_child) if hasattr(field_child, 'value') else self._get_identifier(field_child)
                
                operator = self._get_operator(child.children[1]) if len(child.children) > 1 else '='
                value = self._parse_value(child.children[2]) if len(child.children) > 2 else None
                
                condition = {
                    'field': field,
                    'operator': operator,
                    'value': value
                }
                conditions.append(condition)
            elif hasattr(child, 'data') and child.data == 'condition':
                # Handle condition directly
                condition = self._parse_condition(child)
                if condition:
                    conditions.append(condition)
        return conditions
    
    def _parse_as_of_clause(self, node) -> Dict[str, str]:
        """
        Parse AS OF clause for temporal queries.
        
        Returns dict like:
            {'type': 'timestamp', 'value': '2025-10-11 10:00:00'}
            {'type': 'commit', 'value': 'commit-uuid'}
            {'type': 'run', 'value': 'agent-run-123'}
        """
        # node is as_of_clause, children[0] is as_of_spec
        as_of_spec = node.children[0]
        
        # as_of_spec has 2 children: type token and value string
        if len(as_of_spec.children) >= 2:
            type_token = as_of_spec.children[0]
            value_token = as_of_spec.children[1]
            
            as_of_type = type_token.value.lower()  # 'TIMESTAMP', 'COMMIT', or 'RUN'
            as_of_value = self._get_string(value_token)  # Extract string value
            
            return {
                'type': as_of_type,
                'value': as_of_value
            }
        
        # Fallback
        return {'type': 'timestamp', 'value': str(as_of_spec)}
    
    def _parse_for_system_time(self, node) -> Dict[str, Any]:
        """
        Parse FOR SYSTEM_TIME clause for version history queries.
        
        Returns dict like:
            {'type': 'all'}
            {'type': 'from_to', 'from': '2025-10-01', 'to': '2025-10-11'}
            {'type': 'between', 'start': '2025-10-01', 'end': '2025-10-11'}
        """
        # node is for_system_time, children[0] is system_time_spec
        system_time_spec = node.children[0]
        
        # Check first token to determine type
        if len(system_time_spec.children) == 1:
            # ALL
            spec_type = system_time_spec.children[0]
            if hasattr(spec_type, 'value') and spec_type.value == 'ALL':
                return {'type': 'all'}
        elif len(system_time_spec.children) == 3:
            # FROM ... TO ...
            from_val = self._get_string(system_time_spec.children[1])
            to_val = self._get_string(system_time_spec.children[2])
            return {'type': 'from_to', 'from': from_val, 'to': to_val}
        elif len(system_time_spec.children) == 4:
            # BETWEEN ... AND ...
            start_val = self._get_string(system_time_spec.children[1])
            end_val = self._get_string(system_time_spec.children[3])
            return {'type': 'between', 'start': start_val, 'end': end_val}
        
        # Fallback
        return {'type': 'all'}
    
    def _get_operator(self, node) -> str:
        """Extract operator from node."""
        if hasattr(node, 'value'):
            return node.value
        elif hasattr(node, 'children') and len(node.children) > 0:
            return self._get_operator(node.children[0])
        return str(node)
    
    def _parse_hybrid_match(self, node) -> AIQLNode:
        """Parse HYBRID retrieval."""
        query = self._get_string(node.children[0])
        params = {'query': query}
        
        # Parse IN clause
        if len(node.children) > 1:
            params['target'] = self._get_identifier(node.children[1])
        
        # Parse WITH weights
        if len(node.children) > 2:
            weights = self._parse_weights(node.children[2])
            params['weights'] = weights
        
        # Parse LIMIT
        if len(node.children) > 3:
            params['limit'] = self._get_number(node.children[3])
        
        return AIQLNode(
            node_type=AIQLNodeType.HYBRID_MATCH,
            parameters=params
        )
    
    # Helper methods
    def _get_identifier(self, node) -> str:
        """Extract identifier from node."""
        if hasattr(node, 'value'):
            value = node.value
            # Strip quotes if it's a string literal
            if isinstance(value, str) and len(value) >= 2 and value.startswith('"') and value.endswith('"'):
                return value[1:-1]  # Remove first and last character (quotes)
            return value
        elif hasattr(node, 'children') and len(node.children) > 0:
            # Handle Tree objects - get the first child
            return self._get_identifier(node.children[0])
        return str(node)
    
    def _get_qualified_identifier(self, node) -> str:
        """Extract qualified identifier (e.g., p.city, node.prop, Entity:e1, e1.name) from node."""
        if hasattr(node, 'value'):
            return node.value
        elif hasattr(node, 'data'):
            if node.data == 'qualified_identifier':
                # qualified_identifier: identifier ("." identifier)? | typed_node_ref ("." identifier)?
                parts = []
                for child in node.children:
                    if hasattr(child, 'value'):
                        parts.append(child.value)
                    elif hasattr(child, 'data') and child.data == 'typed_node_ref':
                        # Handle Entity:e1 format
                        type_part = self._get_identifier(child.children[0]) if child.children else ''
                        id_part = self._get_identifier(child.children[1]) if len(child.children) > 1 else ''
                        parts.append(f"{type_part}:{id_part}")
                    elif hasattr(child, 'children'):
                        parts.append(self._get_identifier(child))
                return '.'.join(parts)
            elif node.data == 'typed_node_ref':
                # Handle Entity:e1 directly
                type_part = self._get_identifier(node.children[0]) if node.children else ''
                id_part = self._get_identifier(node.children[1]) if len(node.children) > 1 else ''
                return f"{type_part}:{id_part}"
        elif hasattr(node, 'children') and len(node.children) > 0:
            return self._get_qualified_identifier(node.children[0])
        return str(node)
    
    def _get_string(self, node) -> str:
        """Extract string value from node, stripping both single and double quotes."""
        def strip_quotes(s):
            """Strip both single and double quotes from string."""
            if isinstance(s, str):
                # Strip quotes from both ends
                s = s.strip('"').strip("'")
            return s
        
        if hasattr(node, 'value'):
            return strip_quotes(node.value)
        elif hasattr(node, 'children') and len(node.children) > 0:
            # Handle Tree objects with string children
            child = node.children[0]
            if hasattr(child, 'value'):
                return strip_quotes(child.value)
            return strip_quotes(str(child))
        return strip_quotes(str(node))
    
    def _get_number(self, node) -> Union[int, float]:
        """Extract number from node."""
        if hasattr(node, 'value'):
            try:
                return int(node.value)
            except ValueError:
                return float(node.value)
        return 0
    

    def _parse_edge_type_from_to(self, node) -> AIQLNode:
        """Parse edge_type_from_to: identifier {props}? SRC node_selector DEST node_selector"""
        params = {}
        
        # First child is edge type (identifier)
        edge_type = self._get_identifier(node.children[0])
        params['edge_type'] = edge_type
        params['edge_alias'] = None
        params['properties'] = {}
        params['source_node'] = None
        params['target_node'] = None
        params['source_type'] = None
        params['target_type'] = None
        params['source_where'] = None
        params['target_where'] = None
        
        # Parse remaining children
        source_parsed = False
        for child in node.children[1:]:
            if hasattr(child, 'data'):
                if child.data == 'property_list':
                    params['properties'] = self._parse_property_list(child)
                elif child.data == 'node_selector':
                    # Parse node_selector
                    selector_info = self._parse_node_selector(child)
                    if not source_parsed:
                        params['source_node'] = selector_info.get('node_id')
                        params['source_type'] = selector_info.get('node_type')
                        params['source_where'] = selector_info.get('where_conditions')
                        source_parsed = True
                    else:
                        params['target_node'] = selector_info.get('node_id')
                        params['target_type'] = selector_info.get('node_type')
                        params['target_where'] = selector_info.get('where_conditions')
        
        params['source_alias'] = None
        params['target_alias'] = None
        
        return AIQLNode(
            node_type=AIQLNodeType.CREATE_EDGE,
            parameters=params
        )
    
    def _parse_node_selector(self, node) -> Dict[str, Any]:
        """Parse node_selector for CREATE EDGE.
        Can be:
        - string: UUID "abc-123..."
        - identifier WHERE condition: node type with filter
        - identifier: node type without filter
        """
        result = {
            'node_id': None,
            'node_type': None,
            'where_conditions': None
        }
        
        # Check structure of children
        if len(node.children) == 1:
            first_child = node.children[0]
            
            if hasattr(first_child, 'data') and first_child.data == 'string':
                # Direct UUID reference
                result['node_id'] = first_child.children[0].value.strip('"')
            elif hasattr(first_child, 'data') and first_child.data == 'identifier':
                # Plain identifier (no WHERE clause) - treat as node ID for CREATE EDGE
                identifier = self._get_identifier(first_child)
                result['node_id'] = identifier
        
        elif len(node.children) >= 2:
            # identifier WHERE condition
            first_child = node.children[0]
            
            if hasattr(first_child, 'data') and first_child.data == 'identifier':
                result['node_type'] = self._get_identifier(first_child)
                
                # Look for condition in remaining children
                for child in node.children[1:]:
                    if hasattr(child, 'data') and child.data == 'condition':
                        result['where_conditions'] = self._parse_condition(child)
                        break
        
        return result
    
    def _parse_condition(self, node) -> List[Dict[str, Any]]:
        """Parse WHERE condition for node selector (supports AND/OR/parentheses)."""
        return self._parse_condition_recursive(node)
    
    def _parse_condition_recursive(self, node) -> Any:
        """Recursively parse conditions with OR/AND/parentheses."""
        if not hasattr(node, 'data'):
            return []
        
        if node.data == 'condition':
            # condition -> or_condition
            return self._parse_condition_recursive(node.children[0])
        elif node.data == 'or_condition':
            # or_condition: and_condition ("OR" and_condition)*
            and_conditions = []
            for child in node.children:
                if hasattr(child, 'data') and child.data == 'and_condition':
                    and_conditions.append(self._parse_condition_recursive(child))
            
            if len(and_conditions) == 1:
                return and_conditions[0]
            else:
                return {'OR': and_conditions}
        elif node.data == 'and_condition':
            # and_condition: simple_condition ("AND" simple_condition)*
            simple_conditions = []
            for child in node.children:
                if hasattr(child, 'data') and child.data == 'simple_condition':
                    simple_conditions.append(self._parse_condition_recursive(child))
            
            if len(simple_conditions) == 1:
                return simple_conditions[0]
            else:
                return simple_conditions  # AND is represented as a list
        elif node.data == 'simple_condition':
            # simple_condition: "(" condition ")" | "NOT" simple_condition | qualified_identifier operator value
            if len(node.children) == 1:
                # Parenthesized condition
                return self._parse_condition_recursive(node.children[0])
            elif len(node.children) >= 2 and hasattr(node.children[0], 'value') and node.children[0].value == 'NOT':
                # NOT simple_condition
                inner_condition = self._parse_condition_recursive(node.children[1])
                return {'operator': 'NOT', 'condition': inner_condition}
            elif len(node.children) >= 3:
                # (qualified_identifier | aggregation_function) operator value
                field_node = node.children[0]
                # Check if it's an aggregation function
                if hasattr(field_node, 'data') and field_node.data == 'aggregation_function':
                    # Parse aggregation function (e.g., COUNT(*))
                    agg_func = self._parse_aggregation_function(field_node)
                    field = f"{agg_func.get('function', 'COUNT')}({agg_func.get('target', '*')})"
                elif hasattr(field_node, 'data') and field_node.data == 'qualified_identifier':
                    # Handle qualified identifier (e.g., node.property)
                    if len(field_node.children) == 1:
                        field = self._get_identifier(field_node.children[0])
                    else:
                        # node.property format
                        parts = [self._get_identifier(child) for child in field_node.children]
                        field = '.'.join(parts)
                else:
                    field = self._get_identifier(field_node)
                
                # Check for multi-token operators like "STARTS WITH", "ENDS WITH", "NOT IN"
                operator = None
                value = None
                value_idx = 2
                
                if len(node.children) > 1:
                    op_node = node.children[1]
                    if isinstance(op_node, str):
                        operator = op_node
                    elif hasattr(op_node, 'value'):
                        operator = str(op_node.value)
                    else:
                        operator = self._get_operator(op_node)
                    
                    # Check for "STARTS WITH" or "ENDS WITH" - these are two tokens
                    if operator == 'STARTS' and len(node.children) > 2:
                        next_token = node.children[2]
                        if isinstance(next_token, str) and next_token == 'WITH':
                            operator = 'STARTS WITH'
                            value_idx = 3
                        elif hasattr(next_token, 'value') and str(next_token.value) == 'WITH':
                            operator = 'STARTS WITH'
                            value_idx = 3
                    elif operator == 'ENDS' and len(node.children) > 2:
                        next_token = node.children[2]
                        if isinstance(next_token, str) and next_token == 'WITH':
                            operator = 'ENDS WITH'
                            value_idx = 3
                        elif hasattr(next_token, 'value') and str(next_token.value) == 'WITH':
                            operator = 'ENDS WITH'
                            value_idx = 3
                    elif operator == 'NOT' and len(node.children) > 2:
                        next_token = node.children[2]
                        if isinstance(next_token, str) and next_token == 'IN':
                            operator = 'NOT IN'
                            value_idx = 3
                        elif hasattr(next_token, 'value') and str(next_token.value) == 'IN':
                            operator = 'NOT IN'
                            value_idx = 3
                
                # Parse value - check if it's a list for IN operator
                if value_idx < len(node.children):
                    value_node = node.children[value_idx]
                    if hasattr(value_node, 'data') and value_node.data == 'value_list':
                        # IN [value1, value2, ...]
                        value = [self._parse_value(child) for child in value_node.children]
                    elif hasattr(value_node, 'data') and value_node.data == 'list':
                        # List syntax [item1, item2, ...]
                        value = [self._parse_value(child) for child in value_node.children] if value_node.children else []
                    else:
                        value = self._parse_value(value_node)
                # If value is a string that looks like a variable, try to substitute
                if isinstance(value, str) and value.startswith('$') and len(value) > 1:
                    var_name = value[1:]
                    if var_name in self.variables:
                        value = self.variables[var_name]
                
                return {
                    'field': field,
                    'operator': operator,
                    'value': value
                }
        
        return []
    
    def _parse_property_list(self, node) -> Dict[str, Any]:
        """Parse property list and substitute variables in values."""
        properties = {}
        for child in node.children:
            if child.data == 'property':
                key = self._get_identifier(child.children[0])
                value = self._parse_value(child.children[1])
                # Fallback: If value is a string that looks like a variable, try to substitute
                if isinstance(value, str) and value.startswith('$') and len(value) > 1:
                    var_name = value[1:]  # Remove $ prefix
                    if var_name in self.variables:
                        value = self.variables[var_name]
                        logger.debug(f"[OK] Substituted variable ${var_name} = {value} in property {key}")
                    else:
                        logger.debug(f"[WARN] Variable ${var_name} not found in self.variables: {list(self.variables.keys())}")
                properties[key] = value
        return properties
    
    def _parse_value(self, node) -> Any:
        """Parse value from node."""
        if hasattr(node, 'data'):
            if node.data == 'string':
                return self._get_string(node.children[0])
            elif node.data == 'number':
                return self._get_number(node.children[0])
            elif node.data == 'boolean':
                if len(node.children) > 0:
                    return node.children[0].value == 'TRUE'
                else:
                    # Handle case where boolean node has no children
                    # Check if the node itself has a value attribute
                    if hasattr(node, 'value'):
                        return node.value == 'TRUE'
                    # Fallback: check the raw text
                    return str(node).upper() == 'TRUE'
            elif node.data == 'identifier':
                return self._get_identifier(node.children[0])
            elif node.data == 'variable':
                # Handle variable reference
                if node.children and len(node.children) > 0:
                    var_token = node.children[0]
                    if hasattr(var_token, 'value'):
                        var_name = var_token.value
                        if var_name.startswith('$'):
                            var_name = var_name[1:]
                        else:
                            # Try to extract from token
                            var_name = str(var_token).replace('$', '')
                    else:
                        var_name = str(node.children[0]).replace('$', '')
                else:
                    var_name = str(node).replace('$', '')
                # Return variable value or original if not found
                if var_name in self.variables:
                    return self.variables[var_name]
                # Return as variable reference for executor to handle
                return f"${var_name}"
            elif node.data == 'list':
                # Parse list and substitute variables in list items
                items = []
                for list_child in node.children:
                    if hasattr(list_child, 'data') and list_child.data == 'value':
                        items.append(self._parse_value(list_child))
                    else:
                        items.append(self._parse_value(list_child))
                return items
            elif node.data == 'subquery':
                # Handle subquery - return special marker that executor can detect
                # Subquery contains a pipeline, which could be a select_stage or other operations
                pipeline_node = node.children[0]
                # For now, just parse it as a nested query structure
                # The executor will need to evaluate this
                return {'__subquery__': True, 'pipeline': pipeline_node}
            elif node.data == 'embed_function':
                # Parse EMBED function: "EMBED" (string | variable) ("USING" "MODEL" (string | variable))? ("PARAMETERS" "(" parameter_dict ")")?
                embed_params = {}
                text_value = None
                model = None
                for i, child in enumerate(node.children):
                    if hasattr(child, 'data'):
                        if child.data == 'string':
                            text_value = self._get_string_value(child)
                        elif child.data == 'variable':
                            var_name = self._get_identifier(child.children[0]) if child.children else None
                            text_value = f"${var_name}" if var_name else None
                        elif child.data == 'parameter_dict':
                            embed_params.update(self._extract_parameter_dict(child))
                    elif hasattr(child, 'value'):
                        if child.value == 'USING' and i + 2 < len(node.children):
                            if hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'MODEL':
                                model_child = node.children[i+2]
                                if hasattr(model_child, 'data'):
                                    if model_child.data == 'string':
                                        model = self._get_string_value(model_child)
                                    elif model_child.data == 'variable':
                                        var_name = self._get_identifier(model_child.children[0]) if model_child.children else None
                                        model = f"${var_name}" if var_name else None
                return {'__function__': 'EMBED', 'text': text_value, 'model': model, 'parameters': embed_params}
            elif node.data == 'semantic_hash_function':
                # Parse SEMANTIC_HASH function: "SEMANTIC_HASH" (string | variable) ("USING" "MODEL" (string | variable))?
                hash_params = {}
                text_value = None
                model = None
                for i, child in enumerate(node.children):
                    if hasattr(child, 'data'):
                        if child.data == 'string':
                            text_value = self._get_string_value(child)
                        elif child.data == 'variable':
                            var_name = self._get_identifier(child.children[0]) if child.children else None
                            text_value = f"${var_name}" if var_name else None
                    elif hasattr(child, 'value'):
                        if child.value == 'USING' and i + 2 < len(node.children):
                            if hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'MODEL':
                                model_child = node.children[i+2]
                                if hasattr(model_child, 'data'):
                                    if model_child.data == 'string':
                                        model = self._get_string_value(model_child)
                                    elif model_child.data == 'variable':
                                        var_name = self._get_identifier(model_child.children[0]) if model_child.children else None
                                        model = f"${var_name}" if var_name else None
                return {'__function__': 'SEMANTIC_HASH', 'text': text_value, 'model': model}
            elif node.data == 'now_function':
                # Parse NOW() function: "NOW" "(" ")"
                return {'__function__': 'NOW'}
            elif node.data == 'value':
                # Handle nested value nodes
                return self._parse_value(node.children[0])
        elif hasattr(node, 'value'):
            val = node.value
            # If it's a string that looks like a variable, try to substitute
            if isinstance(val, str) and val.startswith('$') and len(val) > 1:
                var_name = val[1:]  # Remove $ prefix
                if var_name in self.variables:
                    return self.variables[var_name]
            return val
        elif hasattr(node, 'children') and len(node.children) > 0:
            # Handle Tree objects - get the first child
            return self._parse_value(node.children[0])
        else:
            # Last resort: check if str(node) looks like a variable
            node_str = str(node)
            if node_str.startswith('$') and len(node_str) > 1:
                var_name = node_str[1:]
                if var_name in self.variables:
                    return self.variables[var_name]
        return str(node)
    
    def _parse_identifier_list(self, node) -> List[str]:
        """Parse identifier list."""
        identifiers = []
        for child in node.children:
            if child.data == 'identifier':
                identifiers.append(self._get_identifier(child))
        return identifiers
    
    def _parse_condition_v2(self, node) -> List[Dict[str, Any]]:
        """Parse WHERE conditions (legacy method for backward compatibility)."""
        # Use the new recursive parser
        result = self._parse_condition_recursive(node)
        
        # Convert to list format if needed for backward compatibility
        if isinstance(result, dict) and 'OR' not in result:
            return [result]
        elif isinstance(result, list):
            return result
        else:
            return [result]
    
    def _parse_projection_list(self, node) -> List[Dict[str, Any]]:
        """Parse SELECT projections including aggregation functions."""
        projections = []
        for child in node.children:
            if child.data == 'projection':
                projection = self._parse_projection(child)
                projections.append(projection)
        return projections
    
    def _parse_projection(self, node) -> Dict[str, Any]:
        """Parse a single projection (field, wildcard, aggregation, or typed entity)."""
        if len(node.children) == 0:
            # Handle "*" wildcard
            return {'type': 'wildcard', 'field': '*'}
        
        first_child = node.children[0]
        
        # Check for explicit type keywords: NODE, EDGE, PROPERTY
        if hasattr(first_child, 'type') and first_child.type in ('NODE', 'EDGE', 'PROPERTY'):
            entity_type = first_child.value  # NODE, EDGE, or PROPERTY
            
            # Get the identifier/qualified_identifier
            if len(node.children) > 1:
                identifier_child = node.children[1]
                if hasattr(identifier_child, 'data') and identifier_child.data == 'qualified_identifier':
                    field = self._get_qualified_identifier(identifier_child)
                else:
                    field = self._get_identifier(identifier_child)
            else:
                field = None
            
            # Check for alias
            alias = None
            if len(node.children) > 2:
                alias_child = node.children[2]
                if hasattr(alias_child, 'value'):
                    alias = alias_child.value
                elif hasattr(alias_child, 'data') and alias_child.data == 'identifier':
                    alias = self._get_identifier(alias_child)
            
            return {
                'type': entity_type.lower(),  # 'node', 'edge', or 'property'
                'field': field,
                'alias': alias
            }
        
        # Check if it's an aggregation function
        if hasattr(first_child, 'data') and first_child.data == 'aggregation_function':
            return self._parse_aggregation_function(first_child)
        
        # Handle regular field with optional alias (backward compatible)
        if hasattr(first_child, 'value'):
            # It's a token (identifier or *)
            if first_child.value == '*':
                return {'type': 'wildcard', 'field': '*'}
            else:
                field = first_child.value
        elif hasattr(first_child, 'data') and first_child.data == 'qualified_identifier':
            field = self._get_qualified_identifier(first_child)
        else:
            field = self._get_identifier(first_child)
        
        alias = None
        
        # Check for alias
        if len(node.children) > 1:
            alias_child = node.children[1]
            if hasattr(alias_child, 'value'):
                alias = alias_child.value
            elif hasattr(alias_child, 'data') and alias_child.data == 'identifier':
                alias = self._get_identifier(alias_child)
        
        return {'type': 'field', 'field': field, 'alias': alias}
    
    def _parse_aggregation_function(self, node) -> Dict[str, Any]:
        """Parse aggregation function (COUNT, SUM, AVG, MIN, MAX, DISTINCT, DEGREE, CENTRALITY, etc.)."""
        # Get function name from first child (now it's a token)
        func_child = node.children[0]
        if hasattr(func_child, 'value'):
            func_name = func_child.value.upper()
        else:
            func_name = str(func_child).upper()
        
        # Get the argument - handle count_target or agg_target
        arg = '*'
        count_type = None  # For explicit COUNT(NODE x) or COUNT(EDGE x)
        
        if len(node.children) > 1:
            arg_child = node.children[1]
            
            # Check if it's count_target with type keyword
            if hasattr(arg_child, 'data') and arg_child.data == 'count_target':
                # count_target has explicit NODE/EDGE/DISTINCT keywords
                if len(arg_child.children) > 0:
                    first_token = arg_child.children[0]
                    
                    # Check for type keywords
                    if hasattr(first_token, 'type') and first_token.type in ('NODE', 'EDGE', 'DISTINCT'):
                        count_type = first_token.value
                        
                        # Get the identifier after the keyword
                        if len(arg_child.children) > 1:
                            id_child = arg_child.children[1]
                            if hasattr(id_child, 'data') and id_child.data == 'qualified_identifier':
                                arg = self._get_qualified_identifier(id_child)
                            else:
                                arg = self._get_identifier(id_child)
                    elif hasattr(first_token, 'value') and first_token.value == '*':
                        arg = '*'
                    elif hasattr(first_token, 'data') and first_token.data == 'qualified_identifier':
                        arg = self._get_qualified_identifier(first_token)
                    else:
                        arg = self._get_identifier(first_token)
            
            # Check if it's agg_target (for SUM/AVG/etc)
            elif hasattr(arg_child, 'data') and arg_child.data == 'agg_target':
                if len(arg_child.children) > 0:
                    target_child = arg_child.children[0]
                    if hasattr(target_child, 'data') and target_child.data == 'qualified_identifier':
                        arg = self._get_qualified_identifier(target_child)
                    else:
                        arg = self._get_identifier(target_child)
            
            # Backward compatible: direct value or identifier
            elif hasattr(arg_child, 'value'):
                arg = arg_child.value
            elif hasattr(arg_child, 'data') and arg_child.data == 'qualified_identifier':
                # Handle qualified identifiers (e.g., p.city)
                arg = self._get_qualified_identifier(arg_child)
            elif hasattr(arg_child, 'children') and len(arg_child.children) > 0:
                # It's an identifier node or tree
                if hasattr(arg_child.children[0], 'value'):
                    arg = arg_child.children[0].value
                elif hasattr(arg_child.children[0], 'data') and arg_child.children[0].data == 'qualified_identifier':
                    arg = self._get_qualified_identifier(arg_child.children[0])
                else:
                    arg = self._get_identifier(arg_child)
            else:
                arg = str(arg_child)
        
        # Check for alias
        alias = None
        if len(node.children) > 2:
            alias_child = node.children[2]
            if hasattr(alias_child, 'value'):
                alias = alias_child.value
            elif hasattr(alias_child, 'data') and alias_child.data == 'identifier':
                alias = self._get_identifier(alias_child)
        
        result = {
            'type': 'aggregation',
            'function': func_name,
            'argument': arg,
            'alias': alias
        }
        
        # Add count_type if it was explicitly specified (NODE, EDGE, DISTINCT)
        if count_type:
            result['count_type'] = count_type
        
        return result
    
    def _parse_group_by_clause(self, node) -> List[str]:
        """Parse GROUP BY clause."""
        fields = []
        for child in node.children:
            if hasattr(child, 'data') and child.data == 'identifier':
                fields.append(self._get_identifier(child))
        return fields
    
    def _parse_order_by_clause(self, node) -> List[Dict[str, Any]]:
        """Parse ORDER BY clause."""
        order_specs = []
        for child in node.children:
            if hasattr(child, 'data') and child.data == 'order_spec':
                # Parse field (can be qualified_identifier or identifier)
                field = None
                if len(child.children) > 0:
                    field_node = child.children[0]
                    if hasattr(field_node, 'data') and field_node.data == 'qualified_identifier':
                        field = self._get_qualified_identifier(field_node)
                    elif hasattr(field_node, 'data') and field_node.data == 'identifier':
                        field = self._get_identifier(field_node)
                    else:
                        field = self._get_identifier(field_node)
                
                direction = 'ASC'  # Default
                
                # Check for explicit direction
                if len(child.children) > 1:
                    direction_token = child.children[1]
                    if hasattr(direction_token, 'value'):
                        direction = direction_token.value.upper()
                
                order_specs.append({
                    'field': field,
                    'direction': direction
                })
        return order_specs
    
    def _parse_weights(self, node) -> Dict[str, float]:
        """Parse weight specifications."""
        weights = {}
        for child in node.children:
            if child.data == 'weight_spec':
                key = self._get_identifier(child.children[0])
                value = self._get_number(child.children[1])
                weights[key] = value
        return weights
    
    def _parse_variable_decl(self, node) -> VariableDeclaration:
        """Parse variable declaration."""
        # Get the variable name from the first child (which should be a variable token)
        var_token = node.children[0]  # This is the 'variable' node
        
        # The variable node has one child which is the IDENTIFIER token
        if hasattr(var_token, 'children') and len(var_token.children) > 0:
            identifier_token = var_token.children[0]
            if hasattr(identifier_token, 'value'):
                var_name = identifier_token.value  # This should be '$author' or 'author'
            elif hasattr(var_token, 'value'):
                var_name = var_token.value
            else:
                var_name = str(identifier_token)
        elif hasattr(var_token, 'value'):
            var_name = var_token.value
        else:
            # Fallback
            var_name = str(var_token)
        
        # Remove $ prefix if present
        if var_name.startswith('$'):
            var_name = var_name[1:]
        
        # Parse value - can be a simple value or a search query
        value = None
        store_result = False
        
        # Check for search query types and functions
        if len(node.children) > 1:
            value_node = node.children[1]
            if hasattr(value_node, 'data'):
                if value_node.data in ['search_query_basic', 'search_query_collections', 'graph_search_query', 'graph_search_from_via_to']:
                    # Parse as search query
                    search_node = self._parse_search_query(value_node)
                    if search_node:
                        value = search_node
                        # Check for STORE RESULT
                        if len(node.children) > 2:
                            for i in range(2, len(node.children)):
                                child = node.children[i]
                                if isinstance(child, str) and child.upper() == 'STORE':
                                    if i + 1 < len(node.children) and isinstance(node.children[i + 1], str) and node.children[i + 1].upper() == 'RESULT':
                                        store_result = True
                                        break
                elif value_node.data in ['embed_function', 'semantic_hash_function', 'now_function']:
                    # Parse as function call
                    value = self._parse_value(value_node)
                else:
                    value = self._parse_value(value_node)
            else:
                value = self._parse_value(value_node)
        
        # Check for STORE RESULT in remaining children
        if not store_result and len(node.children) > 2:
            for i in range(2, len(node.children)):
                child = node.children[i]
                if isinstance(child, str) and child.upper() == 'STORE':
                    if i + 1 < len(node.children) and isinstance(node.children[i + 1], str) and node.children[i + 1].upper() == 'RESULT':
                        store_result = True
                        break
        
        var_decl = VariableDeclaration(name=var_name, value=value)
        if store_result:
            var_decl.store_result = True
        return var_decl
    
    def _parse_rag_generate(self, node) -> AIQLNode:
        """Parse RAG GENERATE statement."""
        params = {}
        
        # Parse query (string or variable)
        if len(node.children) > 0:
            first_child = node.children[0]
            if hasattr(first_child, 'data') and first_child.data == 'variable':
                params['query'] = self._get_identifier(first_child.children[0]) if first_child.children else None
                params['query_is_variable'] = True
            else:
                params['query'] = self._get_string(first_child)
                params['query_is_variable'] = False
        
        # Parse USING MODEL
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value == 'USING':
                if i + 2 < len(node.children) and hasattr(node.children[i + 1], 'value') and node.children[i + 1].value == 'MODEL':
                    model_child = node.children[i + 2]
                    if hasattr(model_child, 'data') and model_child.data == 'variable':
                        params['model'] = self._get_identifier(model_child.children[0]) if model_child.children else None
                        params['model_is_variable'] = True
                    else:
                        params['model'] = self._get_string(model_child)
                        params['model_is_variable'] = False
                    break
        
        # Parse CONTEXT FROM
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value == 'CONTEXT':
                if i + 2 < len(node.children) and hasattr(node.children[i + 1], 'value') and node.children[i + 1].value == 'FROM':
                    context_child = node.children[i + 2]
                    if hasattr(context_child, 'data') and context_child.data == 'variable':
                        params['context_from'] = self._get_identifier(context_child.children[0]) if context_child.children else None
                    break
        
        # Parse PARAMETERS
        for i, child in enumerate(node.children):
            if hasattr(child, 'data') and child.data == 'parameter_dict':
                params['parameters'] = self._extract_parameter_dict(child)
                break
        
        # Parse GUARDRAILS
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value == 'GUARDRAILS':
                if i + 1 < len(node.children) and hasattr(node.children[i + 1], 'data') and node.children[i + 1].data == 'parameter_dict':
                    params['guardrails'] = self._extract_parameter_dict(node.children[i + 1])
                    break
        
        # Parse STORE RESULT
        store_result = False
        store_in_var = None
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value == 'STORE':
                if i + 1 < len(node.children) and hasattr(node.children[i + 1], 'value') and node.children[i + 1].value == 'RESULT':
                    store_result = True
                    if i + 2 < len(node.children) and hasattr(node.children[i + 2], 'value') and node.children[i + 2].value == 'IN':
                        if i + 3 < len(node.children):
                            var_child = node.children[i + 3]
                            if hasattr(var_child, 'data') and var_child.data == 'variable':
                                store_in_var = self._get_identifier(var_child.children[0]) if var_child.children else None
                    break
        
        params['store_result'] = store_result
        if store_in_var:
            params['store_in'] = store_in_var
        
        return AIQLNode(node_type=AIQLNodeType.RAG_GENERATE, parameters=params)
    
    def _parse_rag_query_one_shot(self, node) -> AIQLNode:
        """Parse RAG QUERY one-shot statement."""
        params = {}
        
        # Parse query (string or variable)
        if len(node.children) > 0:
            first_child = node.children[0]
            if hasattr(first_child, 'data') and first_child.data == 'variable':
                params['query'] = self._get_identifier(first_child.children[0]) if first_child.children else None
                params['query_is_variable'] = True
            else:
                params['query'] = self._get_string(first_child)
                params['query_is_variable'] = False
        
        # Parse IN collection
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value == 'IN':
                if i + 1 < len(node.children):
                    params['collection'] = self._get_identifier(node.children[i + 1])
                    break
        
        # Parse USING { ... }
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value == 'USING':
                if i + 1 < len(node.children) and hasattr(node.children[i + 1], 'data') and node.children[i + 1].data == 'parameter_dict':
                    params['using'] = self._extract_parameter_dict(node.children[i + 1])
                    break
        
        # Parse PARAMETERS
        for i, child in enumerate(node.children):
            if hasattr(child, 'data') and child.data == 'parameter_dict':
                if 'using' not in params:  # Only if USING wasn't already parsed
                    params['parameters'] = self._extract_parameter_dict(child)
                break
        
        # Parse STORE RESULT IN
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value == 'STORE':
                if i + 1 < len(node.children) and hasattr(node.children[i + 1], 'value') and node.children[i + 1].value == 'RESULT':
                    if i + 2 < len(node.children) and hasattr(node.children[i + 2], 'value') and node.children[i + 2].value == 'IN':
                        if i + 3 < len(node.children):
                            var_child = node.children[i + 3]
                            if hasattr(var_child, 'data') and var_child.data == 'variable':
                                params['store_in'] = self._get_identifier(var_child.children[0]) if var_child.children else None
                    break
        
        return AIQLNode(node_type=AIQLNodeType.RAG_QUERY_ONE_SHOT, parameters=params)
    
    def _parse_evaluate_rag(self, node) -> AIQLNode:
        """Parse EVALUATE RAG statement."""
        params = {}
        
        # The grammar parses it as: evaluate_rag -> [identifier (ON value), parameter_dict (USING), parameter_dict (PARAMETERS), identifier (COLLECTION)]
        # First child is the ON value (identifier) - grammar doesn't include ON token, just the value
        if len(node.children) > 0:
            on_child = node.children[0]
            if hasattr(on_child, 'data'):
                if on_child.data == 'identifier':
                    params['on'] = self._get_identifier(on_child)
                elif on_child.data == 'variable':
                    params['on'] = self._get_identifier(on_child.children[0]) if on_child.children else None
                else:
                    params['on'] = self._get_identifier(on_child) if hasattr(on_child, 'children') else str(on_child)
            elif hasattr(on_child, 'value'):
                params['on'] = str(on_child.value)
        
        # Also check for ON token in children (for compatibility with other formats)
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value == 'ON':
                if i + 1 < len(node.children):
                    var_child = node.children[i + 1]
                    if hasattr(var_child, 'data'):
                        if var_child.data == 'identifier':
                            params['on'] = self._get_identifier(var_child)
                        elif var_child.data == 'variable':
                            params['on'] = self._get_identifier(var_child.children[0]) if var_child.children else None
                    elif hasattr(var_child, 'value'):
                        params['on'] = str(var_child.value)
                break
        
        # Parse USING MODEL or USING {dict}
        # The grammar structure: evaluate_rag -> [identifier (ON), parameter_dict (USING), parameter_dict (PARAMETERS), identifier (COLLECTION)]
        # Child 1 should be USING parameter_dict, Child 2 should be PARAMETERS parameter_dict
        using_dict = None
        parameters_dict = None
        
        # Check for USING token first (for USING MODEL format)
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value == 'USING':
                if i + 2 < len(node.children) and hasattr(node.children[i + 1], 'value') and node.children[i + 1].value == 'MODEL':
                    model_child = node.children[i + 2]
                    if hasattr(model_child, 'data') and model_child.data == 'variable':
                        params['model'] = self._get_identifier(model_child.children[0]) if model_child.children else None
                        params['model_is_variable'] = True
                    else:
                        params['model'] = self._get_string(model_child)
                        params['model_is_variable'] = False
                    break
                elif i + 1 < len(node.children) and hasattr(node.children[i + 1], 'data') and node.children[i + 1].data == 'parameter_dict':
                    # USING {dict} format
                    using_dict = self._extract_parameter_dict(node.children[i + 1])
                    params['using'] = using_dict
                    # Extract retriever_model and generator_model if present
                    if 'retriever_model' in using_dict:
                        params['retriever_model'] = using_dict['retriever_model']
                    if 'generator_model' in using_dict:
                        params['generator_model'] = using_dict['generator_model']
                    break
        
        # If USING wasn't found via token, check if child 1 is parameter_dict (USING)
        if not using_dict and len(node.children) > 1:
            child1 = node.children[1]
            if hasattr(child1, 'data') and child1.data == 'parameter_dict':
                # This is likely the USING parameter_dict
                using_dict = self._extract_parameter_dict(child1)
                params['using'] = using_dict
                # Extract retriever_model and generator_model if present
                if 'retriever_model' in using_dict:
                    params['retriever_model'] = using_dict['retriever_model']
                if 'generator_model' in using_dict:
                    params['generator_model'] = using_dict['generator_model']
        
        # Parse PARAMETERS - child 2 should be PARAMETERS parameter_dict
        if len(node.children) > 2:
            child2 = node.children[2]
            if hasattr(child2, 'data') and child2.data == 'parameter_dict':
                parameters_dict = self._extract_parameter_dict(child2)
                params['parameters'] = parameters_dict
        else:
            # Fallback: search for parameter_dict
            for i, child in enumerate(node.children):
                if hasattr(child, 'data') and child.data == 'parameter_dict':
                    # Skip if this is the USING dict (child 1)
                    if i != 1:
                        params['parameters'] = self._extract_parameter_dict(child)
                        break
        
        # Parse STORE RESULT IN variable or STORE IN COLLECTION
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value == 'STORE':
                if i + 1 < len(node.children) and hasattr(node.children[i + 1], 'value'):
                    if node.children[i + 1].value == 'RESULT' and i + 2 < len(node.children):
                        # STORE RESULT IN variable
                        if hasattr(node.children[i + 2], 'value') and node.children[i + 2].value == 'IN' and i + 3 < len(node.children):
                            var_child = node.children[i + 3]
                            if hasattr(var_child, 'data') and var_child.data == 'variable':
                                params['store_in'] = self._get_identifier(var_child.children[0]) if var_child.children else None
                    elif node.children[i + 1].value == 'IN' and i + 2 < len(node.children):
                        # STORE IN COLLECTION
                        if hasattr(node.children[i + 2], 'value') and node.children[i + 2].value == 'COLLECTION' and i + 3 < len(node.children):
                            params['store_collection'] = self._get_identifier(node.children[i + 3])
                break
        
        return AIQLNode(node_type=AIQLNodeType.EVALUATE_RAG, parameters=params)
    
    def _parse_insert_into(self, node) -> AIQLNode:
        """Parse INSERT INTO statement."""
        params = {}
        
        # Parse collection name
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value == 'INTO':
                if i + 1 < len(node.children):
                    params['collection'] = self._get_identifier(node.children[i + 1])
                    break
        
        # Parse VALUES { ... }
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value == 'VALUES':
                if i + 1 < len(node.children) and hasattr(node.children[i + 1], 'data') and node.children[i + 1].data == 'property_list':
                    params['values'] = self._parse_property_list(node.children[i + 1])
                    break
        
        return AIQLNode(node_type=AIQLNodeType.INSERT_INTO, parameters=params)
    
    def _parse_update_pipeline(self, node) -> AIQLNode:
        """Parse UPDATE PIPELINE statement."""
        params = {}
        
        # Parse pipeline name
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value == 'PIPELINE':
                if i + 1 < len(node.children):
                    params['pipeline'] = self._get_identifier(node.children[i + 1])
                    break
        
        # Parse SET { ... }
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value == 'SET':
                if i + 1 < len(node.children) and hasattr(node.children[i + 1], 'data') and node.children[i + 1].data == 'property_list':
                    params['set'] = self._parse_property_list(node.children[i + 1])
                    break
        
        return AIQLNode(node_type=AIQLNodeType.UPDATE_PIPELINE, parameters=params)
    
    def _parse_for_loop(self, node) -> AIQLNode:
        """Parse FOR loop statement."""
        params = {}
        
        # Parse FOR each variable IN ...
        loop_var = None
        loop_source = None
        
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value == 'each':
                if i + 1 < len(node.children):
                    var_child = node.children[i + 1]
                    if hasattr(var_child, 'data') and var_child.data == 'variable':
                        loop_var = self._get_identifier(var_child.children[0]) if var_child.children else None
                if i + 2 < len(node.children) and hasattr(node.children[i + 2], 'value') and node.children[i + 2].value == 'IN':
                    if i + 3 < len(node.children):
                        source_child = node.children[i + 3]
                        if hasattr(source_child, 'data') and source_child.data == 'variable':
                            loop_source = {'type': 'variable', 'value': self._get_identifier(source_child.children[0]) if source_child.children else None}
                        elif hasattr(source_child, 'data') and source_child.data == 'top_select':
                            loop_source = {'type': 'select', 'value': self._parse_select(source_child)}
                    break
        
        params['loop_variable'] = loop_var
        params['loop_source'] = loop_source
        
        # Parse DO ... END body
        body_statements = []
        in_do = False
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value == 'DO':
                in_do = True
                continue
            if hasattr(child, 'value') and child.value == 'END':
                break
            if in_do:
                if hasattr(child, 'data'):
                    if child.data == 'variable_decl':
                        body_statements.append(self._parse_variable_decl(child))
                    elif child.data == 'top_select':
                        body_statements.append(self._parse_select(child))
                    # Add more statement types as needed
        
        params['body'] = body_statements
        
        return AIQLNode(node_type=AIQLNodeType.FOR_LOOP, parameters=params)
    
    def _parse_create_collection(self, node) -> AIQLNode:
        """Parse CREATE COLLECTION statement with optional schema."""
        params = {}
        
        # Parse collection name (first identifier)
        if len(node.children) > 0:
            collection_name = self._get_identifier(node.children[0])
            params['collection_name'] = collection_name
        
        # Parse schema if present (fields: [...])
        schema = {}
        for child in node.children:
            if hasattr(child, 'data') and child.data == 'collection_schema':
                fields = []
                for field_child in child.children:
                    if hasattr(field_child, 'data') and field_child.data == 'field_definition':
                        field_name = None
                        field_type = None
                        for fd_child in field_child.children:
                            if hasattr(fd_child, 'data') and fd_child.data == 'identifier':
                                if field_name is None:
                                    field_name = self._get_identifier(fd_child)
                                elif field_type is None:
                                    # Check if it's a type_spec
                                    field_type = self._get_identifier(fd_child)
                            elif hasattr(fd_child, 'data') and fd_child.data == 'type_spec':
                                # type_spec is a token choice
                                if fd_child.children:
                                    field_type = str(fd_child.children[0].value) if hasattr(fd_child.children[0], 'value') else str(fd_child.children[0])
                                else:
                                    field_type = str(fd_child.value) if hasattr(fd_child, 'value') else 'string'
                        if field_name and field_type:
                            fields.append({field_name: field_type})
                schema['fields'] = fields
                params['schema'] = schema
        
        return AIQLNode(node_type=AIQLNodeType.CREATE_COLLECTION, parameters=params)
    
    def _parse_diff_node(self, node) -> AIQLNode:
        """Parse DIFF NODE: "DIFF" "NODE" node_spec "AT" string "VS" string"""
        params = {}
        
        # Parse node_spec (first child)
        node_spec = None
        timestamp1 = None
        timestamp2 = None
        
        for i, child in enumerate(node.children):
            if hasattr(child, 'data') and child.data == 'node_spec':
                node_spec = child
                # Extract node type and properties
                if len(child.children) >= 1:
                    node_type = self._get_identifier(child.children[0])
                    params['node_type'] = node_type
                # Extract WHERE condition if present
                if len(child.children) >= 2:
                    where_node = child.children[1]
                    if hasattr(where_node, 'data') and where_node.data == 'where_clause':
                        params['where'] = self._parse_where_clause(where_node)
            elif hasattr(child, 'type') and child.type == 'STRING':
                # Extract timestamps
                timestamp_str = self._get_string_value(child)
                if timestamp1 is None:
                    timestamp1 = timestamp_str
                elif timestamp2 is None:
                    timestamp2 = timestamp_str
        
        # Also check for AT and VS tokens
        for i, child in enumerate(node.children):
            if hasattr(child, 'value'):
                if child.value == 'AT' and i + 1 < len(node.children):
                    next_child = node.children[i + 1]
                    if hasattr(next_child, 'type') and next_child.type == 'STRING':
                        timestamp1 = self._get_string_value(next_child)
                elif child.value == 'VS' and i + 1 < len(node.children):
                    next_child = node.children[i + 1]
                    if hasattr(next_child, 'type') and next_child.type == 'STRING':
                        timestamp2 = self._get_string_value(next_child)
        
        params['timestamp1'] = timestamp1
        params['timestamp2'] = timestamp2
        
        return AIQLNode(
            node_type=AIQLNodeType.DIFF_NODE,
            parameters=params
        )
    
    def _parse_diff_edges(self, node) -> AIQLNode:
        """Parse DIFF EDGES: "DIFF" "EDGES" identifier "FOR" diff_edge_spec "AT" string "VS" string"""
        params = {}
        
        edge_type = None
        source = None
        target = None
        timestamp1 = None
        timestamp2 = None
        
        for i, child in enumerate(node.children):
            if hasattr(child, 'data') and child.data == 'identifier':
                if edge_type is None:
                    edge_type = self._get_identifier(child)
            elif hasattr(child, 'data') and child.data == 'diff_edge_spec':
                # Parse diff_edge_spec: "source" "=" string | "target" "=" string | "source" "=" string "AND" "target" "=" string
                for j, spec_child in enumerate(child.children):
                    if hasattr(spec_child, 'value') and spec_child.value == 'source':
                        if j + 2 < len(child.children) and hasattr(child.children[j+1], 'value') and child.children[j+1].value == '=':
                            source = self._get_string_value(child.children[j+2])
                    elif hasattr(spec_child, 'value') and spec_child.value == 'target':
                        if j + 2 < len(child.children) and hasattr(child.children[j+1], 'value') and child.children[j+1].value == '=':
                            target = self._get_string_value(child.children[j+2])
            elif hasattr(child, 'type') and child.type == 'STRING':
                # Extract timestamps
                timestamp_str = self._get_string_value(child)
                if timestamp1 is None:
                    timestamp1 = timestamp_str
                elif timestamp2 is None:
                    timestamp2 = timestamp_str
        
        # Also check for AT and VS tokens
        for i, child in enumerate(node.children):
            if hasattr(child, 'value'):
                if child.value == 'AT' and i + 1 < len(node.children):
                    next_child = node.children[i + 1]
                    if hasattr(next_child, 'type') and next_child.type == 'STRING':
                        timestamp1 = self._get_string_value(next_child)
                elif child.value == 'VS' and i + 1 < len(node.children):
                    next_child = node.children[i + 1]
                    if hasattr(next_child, 'type') and next_child.type == 'STRING':
                        timestamp2 = self._get_string_value(next_child)
        
        params['edge_type'] = edge_type
        params['source'] = source
        params['target'] = target
        params['timestamp1'] = timestamp1
        params['timestamp2'] = timestamp2
        
        return AIQLNode(
            node_type=AIQLNodeType.DIFF_EDGES,
            parameters=params
        )
    
    def _parse_read_url(self, node) -> AIQLNode:
        """Parse READ URL: "READ" "URL" string ("PARAMETERS" "(" parameter_dict ")")? ("STORE" "AS" "RAW" identifier)?"""
        params = {}
        for i, child in enumerate(node.children):
            if hasattr(child, 'type') and child.type == 'STRING':
                params['url'] = self._get_string_value(child)
            elif hasattr(child, 'data') and child.data == 'parameter_dict':
                params['parameters'] = self._extract_parameter_dict(child)
            elif hasattr(child, 'value') and child.value == 'STORE' and i + 3 < len(node.children):
                if hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'AS':
                    if hasattr(node.children[i+2], 'value') and node.children[i+2].value == 'RAW':
                        params['store_as'] = self._get_identifier(node.children[i+3])
        return AIQLNode(node_type=AIQLNodeType.READ_URL, parameters=params)
    
    def _parse_read_domain(self, node) -> AIQLNode:
        """Parse READ DOMAIN: "READ" "DOMAIN" string ("PARAMETERS" "(" parameter_dict ")")? ("STORE" "AS" "RAW" identifier)?"""
        params = {}
        for i, child in enumerate(node.children):
            if hasattr(child, 'type') and child.type == 'STRING':
                params['domain'] = self._get_string_value(child)
            elif hasattr(child, 'data') and child.data == 'parameter_dict':
                params['parameters'] = self._extract_parameter_dict(child)
            elif hasattr(child, 'value') and child.value == 'STORE' and i + 3 < len(node.children):
                if hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'AS':
                    if hasattr(node.children[i+2], 'value') and node.children[i+2].value == 'RAW':
                        params['store_as'] = self._get_identifier(node.children[i+3])
        return AIQLNode(node_type=AIQLNodeType.READ_DOMAIN, parameters=params)
    
    def _parse_extract_from_raw(self, node) -> AIQLNode:
        """Parse EXTRACT FROM RAW: "EXTRACT" "FROM" "RAW" (identifier | variable) ("AS" extract_target)? ("PARAMETERS" "(" parameter_dict ")")? ("STORE" "AS" node_spec)? ("LINK" link_spec)?"""
        params = {}
        for i, child in enumerate(node.children):
            if hasattr(child, 'data') and child.data in ['identifier', 'variable']:
                params['raw_source'] = self._get_identifier(child) if child.data == 'identifier' else (self._get_identifier(child.children[0]) if child.children else None)
            elif hasattr(child, 'data') and child.data == 'extract_target':
                # Parse extract_target: "NODE" "ONLY" "TYPE" identifier | "EDGE" "ONLY" "TYPE" identifier | "NODE" "TYPE" identifier "AND" "EDGE" "TYPE" identifier
                for j, target_child in enumerate(child.children):
                    if hasattr(target_child, 'value') and target_child.value == 'NODE':
                        if j + 2 < len(child.children) and hasattr(child.children[j+1], 'value') and child.children[j+1].value == 'ONLY':
                            params['node_only'] = True
                            params['node_type'] = self._get_identifier(child.children[j+3]) if j + 3 < len(child.children) else None
                        elif j + 1 < len(child.children) and hasattr(child.children[j+1], 'value') and child.children[j+1].value == 'TYPE':
                            params['node_type'] = self._get_identifier(child.children[j+2]) if j + 2 < len(child.children) else None
                    elif hasattr(target_child, 'value') and target_child.value == 'EDGE':
                        if j + 2 < len(child.children) and hasattr(child.children[j+1], 'value') and child.children[j+1].value == 'ONLY':
                            params['edge_only'] = True
                            params['edge_type'] = self._get_identifier(child.children[j+3]) if j + 3 < len(child.children) else None
                        elif j + 1 < len(child.children) and hasattr(child.children[j+1], 'value') and child.children[j+1].value == 'TYPE':
                            params['edge_type'] = self._get_identifier(child.children[j+2]) if j + 2 < len(child.children) else None
            elif hasattr(child, 'data') and child.data == 'parameter_dict':
                params['parameters'] = self._extract_parameter_dict(child)
            elif hasattr(child, 'data') and child.data == 'node_spec':
                params['store_as'] = self._get_identifier(child.children[0]) if child.children else None
            elif hasattr(child, 'data') and child.data == 'link_spec':
                # Parse link_spec: "(" node_spec "TO" node_spec ")"
                if len(child.children) >= 3:
                    params['link_from'] = self._get_identifier(child.children[0].children[0]) if child.children[0].children else None
                    params['link_to'] = self._get_identifier(child.children[2].children[0]) if len(child.children) > 2 and child.children[2].children else None
        return AIQLNode(node_type=AIQLNodeType.EXTRACT_FROM_RAW, parameters=params)
    
    def _parse_extract_entities_top(self, node) -> AIQLNode:
        """Parse EXTRACT ENTITIES: "EXTRACT" "ENTITIES" "FROM" (identifier | "(" identifier_list ")") ("USING" "MODEL" (string | variable))? ("PARAMETERS" "(" parameter_dict ")")? ("STORE" "AS" "NODE" "TYPE" identifier)? ("LINK" "TO" identifier "ON" identifier)?"""
        params = {}
        for i, child in enumerate(node.children):
            if hasattr(child, 'data') and child.data in ['identifier', 'identifier_list']:
                if child.data == 'identifier':
                    params['from'] = [self._get_identifier(child)]
                else:
                    params['from'] = [self._get_identifier(c) for c in child.children if hasattr(c, 'data') and c.data == 'identifier']
            elif hasattr(child, 'value') and child.value == 'USING' and i + 2 < len(node.children):
                if hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'MODEL':
                    model_node = node.children[i + 2]
                    if hasattr(model_node, 'data') and model_node.data == 'variable':
                        params['model'] = self._get_identifier(model_node.children[0]) if model_node.children else None
                        params['model_is_variable'] = True
                    else:
                        params['model'] = self._get_string_value(model_node) if hasattr(model_node, 'type') and model_node.type == 'STRING' else self._get_identifier(model_node)
                        params['model_is_variable'] = False
            elif hasattr(child, 'data') and child.data == 'parameter_dict':
                params['parameters'] = self._extract_parameter_dict(child)
            elif hasattr(child, 'value') and child.value == 'STORE' and i + 3 < len(node.children):
                if hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'AS':
                    if hasattr(node.children[i+2], 'value') and node.children[i+2].value == 'NODE':
                        if hasattr(node.children[i+3], 'value') and node.children[i+3].value == 'TYPE':
                            params['store_as_node_type'] = self._get_identifier(node.children[i+4]) if i + 4 < len(node.children) else None
            elif hasattr(child, 'value') and child.value == 'LINK' and i + 3 < len(node.children):
                if hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'TO':
                    params['link_to'] = self._get_identifier(node.children[i+2])
                    if hasattr(node.children[i+3], 'value') and node.children[i+3].value == 'ON':
                        params['link_on'] = self._get_identifier(node.children[i+4]) if i + 4 < len(node.children) else None
        return AIQLNode(node_type=AIQLNodeType.EXTRACT_ENTITIES_TOP, parameters=params)
    
    def _parse_create_evalset(self, node) -> AIQLNode:
        """Parse CREATE EVALSET: "CREATE" "EVALSET" (identifier | variable) "FROM" "NAMESPACE" (identifier | variable) ("PARAMETERS" "(" parameter_dict ")")? ("STORE" "IN" "COLLECTION" identifier)?"""
        params = {}
        for i, child in enumerate(node.children):
            if hasattr(child, 'data') and child.data in ['identifier', 'variable']:
                if 'evalset_name' not in params:
                    params['evalset_name'] = self._get_identifier(child) if child.data == 'identifier' else (self._get_identifier(child.children[0]) if child.children else None)
                elif 'namespace' not in params:
                    params['namespace'] = self._get_identifier(child) if child.data == 'identifier' else (self._get_identifier(child.children[0]) if child.children else None)
            elif hasattr(child, 'data') and child.data == 'parameter_dict':
                params['parameters'] = self._extract_parameter_dict(child)
            elif hasattr(child, 'value') and child.value == 'STORE' and i + 2 < len(node.children):
                if hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'IN':
                    if hasattr(node.children[i+2], 'value') and node.children[i+2].value == 'COLLECTION':
                        params['store_in_collection'] = self._get_identifier(node.children[i+3]) if i + 3 < len(node.children) else None
        return AIQLNode(node_type=AIQLNodeType.CREATE_EVALSET, parameters=params)
    
    def _parse_abtest_rag(self, node) -> AIQLNode:
        """Parse ABTEST RAG: "ABTEST" "RAG" "ON" (identifier | variable) "VARIANTS" "(" parameter_dict ")" ("PARAMETERS" "(" parameter_dict ")")? ("METRICS" "[" identifier_list "]")? ("STORE" "IN" "COLLECTION" identifier)?"""
        params = {}
        for i, child in enumerate(node.children):
            if hasattr(child, 'data') and child.data in ['identifier', 'variable']:
                if 'eval_set' not in params:
                    params['eval_set'] = self._get_identifier(child) if child.data == 'identifier' else (self._get_identifier(child.children[0]) if child.children else None)
            elif hasattr(child, 'value') and child.value == 'VARIANTS' and i + 1 < len(node.children):
                if hasattr(node.children[i+1], 'data') and node.children[i+1].data == 'parameter_dict':
                    params['variants'] = self._extract_parameter_dict(node.children[i+1])
            elif hasattr(child, 'data') and child.data == 'parameter_dict':
                params['parameters'] = self._extract_parameter_dict(child)
            elif hasattr(child, 'data') and child.data == 'identifier_list':
                params['metrics'] = [self._get_identifier(c) for c in child.children if hasattr(c, 'data') and c.data == 'identifier']
            elif hasattr(child, 'value') and child.value == 'STORE' and i + 2 < len(node.children):
                if hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'IN':
                    if hasattr(node.children[i+2], 'value') and node.children[i+2].value == 'COLLECTION':
                        params['store_in_collection'] = self._get_identifier(node.children[i+3]) if i + 3 < len(node.children) else None
        return AIQLNode(node_type=AIQLNodeType.ABTEST_RAG, parameters=params)
    
    def _parse_attach_policy(self, node) -> AIQLNode:
        """Parse ATTACH POLICY: "ATTACH" "POLICY" string "TO" ("NAMESPACE" | "PIPELINE") (identifier | variable)"""
        params = {}
        for i, child in enumerate(node.children):
            if hasattr(child, 'type') and child.type == 'STRING':
                params['policy_path'] = self._get_string_value(child)
            elif hasattr(child, 'value') and child.value in ['NAMESPACE', 'PIPELINE']:
                params['target_type'] = child.value
                if i + 1 < len(node.children):
                    target_node = node.children[i + 1]
                    if hasattr(target_node, 'data') and target_node.data == 'identifier':
                        params['target'] = self._get_identifier(target_node)
                    elif hasattr(target_node, 'data') and target_node.data == 'variable':
                        params['target'] = self._get_identifier(target_node.children[0]) if target_node.children else None
                        params['target_is_variable'] = True
        return AIQLNode(node_type=AIQLNodeType.ATTACH_POLICY, parameters=params)
    
    def _parse_set_guardrails(self, node) -> AIQLNode:
        """Parse SET GUARDRAILS: SET GUARDRAILS FOR (NAMESPACE | PIPELINE) (identifier | variable) with parameter_dict"""
        params = {}
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value in ['NAMESPACE', 'PIPELINE']:
                params['target_type'] = child.value
                if i + 1 < len(node.children):
                    target_node = node.children[i + 1]
                    if hasattr(target_node, 'data') and target_node.data == 'identifier':
                        params['target'] = self._get_identifier(target_node)
                    elif hasattr(target_node, 'data') and target_node.data == 'variable':
                        params['target'] = self._get_identifier(target_node.children[0]) if target_node.children else None
                        params['target_is_variable'] = True
            elif hasattr(child, 'data') and child.data == 'parameter_dict':
                params['guardrails'] = self._extract_parameter_dict(child)
        return AIQLNode(node_type=AIQLNodeType.SET_GUARDRAILS, parameters=params)
    
    def _parse_pre_guard(self, node) -> AIQLNode:
        """Parse PRE_GUARD: "PRE_GUARD" "CHECK" ("QUERY" | "ANSWER") ("USING" "POLICY" string)? ("ON" string)?"""
        params = {}
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value in ['QUERY', 'ANSWER']:
                params['check_type'] = child.value
            elif hasattr(child, 'value') and child.value == 'USING' and i + 2 < len(node.children):
                if hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'POLICY':
                    params['policy'] = self._get_string_value(node.children[i+2])
            elif hasattr(child, 'value') and child.value == 'ON' and i + 1 < len(node.children):
                params['on'] = self._get_string_value(node.children[i+1])
        return AIQLNode(node_type=AIQLNodeType.PRE_GUARD, parameters=params)
    
    def _parse_post_guard(self, node) -> AIQLNode:
        """Parse POST_GUARD: "POST_GUARD" "CHECK" ("QUERY" | "ANSWER") ("USING" "POLICY" string)? ("REQUIRE" "{" parameter_dict "}")?"""
        params = {}
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value in ['QUERY', 'ANSWER']:
                params['check_type'] = child.value
            elif hasattr(child, 'value') and child.value == 'USING' and i + 2 < len(node.children):
                if hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'POLICY':
                    params['policy'] = self._get_string_value(node.children[i+2])
            elif hasattr(child, 'value') and child.value == 'REQUIRE' and i + 1 < len(node.children):
                if hasattr(node.children[i+1], 'data') and node.children[i+1].data == 'parameter_dict':
                    params['require'] = self._extract_parameter_dict(node.children[i+1])
        return AIQLNode(node_type=AIQLNodeType.POST_GUARD, parameters=params)
    
    def _parse_create_fine_tune_dataset(self, node) -> AIQLNode:
        """Parse CREATE FINE_TUNE_DATASET: "CREATE" "FINE_TUNE_DATASET" string "FROM" "NAMESPACE" (identifier | variable) ("PARAMETERS" "(" parameter_dict ")")? ("STORE" "IN" string)?"""
        params = {}
        for i, child in enumerate(node.children):
            if hasattr(child, 'type') and child.type == 'STRING':
                if 'dataset_name' not in params:
                    params['dataset_name'] = self._get_string_value(child)
                elif 'store_in' not in params:
                    params['store_in'] = self._get_string_value(child)
            elif hasattr(child, 'data') and child.data in ['identifier', 'variable']:
                params['namespace'] = self._get_identifier(child) if child.data == 'identifier' else (self._get_identifier(child.children[0]) if child.children else None)
            elif hasattr(child, 'data') and child.data == 'parameter_dict':
                params['parameters'] = self._extract_parameter_dict(child)
        return AIQLNode(node_type=AIQLNodeType.CREATE_FINE_TUNE_DATASET, parameters=params)
    
    def _parse_fine_tune_model(self, node) -> AIQLNode:
        """Parse FINE_TUNE MODEL: "FINE_TUNE" "MODEL" (string | variable) "USING" "DATASET" string ("PARAMETERS" "(" parameter_dict ")")? ("STORE" "AS" string)?"""
        params = {}
        for i, child in enumerate(node.children):
            if hasattr(child, 'type') and child.type == 'STRING':
                if 'base_model' not in params and i == 0:
                    params['base_model'] = self._get_string_value(child)
                elif 'dataset' not in params:
                    params['dataset'] = self._get_string_value(child)
                elif 'store_as' not in params:
                    params['store_as'] = self._get_string_value(child)
            elif hasattr(child, 'data') and child.data == 'variable':
                params['base_model'] = self._get_identifier(child.children[0]) if child.children else None
                params['base_model_is_variable'] = True
            elif hasattr(child, 'data') and child.data == 'parameter_dict':
                params['parameters'] = self._extract_parameter_dict(child)
        return AIQLNode(node_type=AIQLNodeType.FINE_TUNE_MODEL, parameters=params)
    
    def _parse_register_model(self, node) -> AIQLNode:
        """Parse REGISTER MODEL: REGISTER MODEL string WITH parameter_dict"""
        params = {}
        for i, child in enumerate(node.children):
            if hasattr(child, 'type') and child.type == 'STRING':
                params['model_name'] = self._get_string_value(child)
            elif hasattr(child, 'data') and child.data == 'parameter_dict':
                params['metadata'] = self._extract_parameter_dict(child)
        return AIQLNode(node_type=AIQLNodeType.REGISTER_MODEL, parameters=params)
    
    def _parse_promote_model(self, node) -> AIQLNode:
        """Parse PROMOTE MODEL: "PROMOTE" "MODEL" string "TO" string ("IF" condition)?"""
        params = {}
        for i, child in enumerate(node.children):
            if hasattr(child, 'type') and child.type == 'STRING':
                if 'model_name' not in params:
                    params['model_name'] = self._get_string_value(child)
                else:
                    params['to'] = self._get_string_value(child)
            elif hasattr(child, 'value') and child.value == 'IF' and i + 1 < len(node.children):
                params['condition'] = self._parse_condition_recursive(node.children[i+1])
        return AIQLNode(node_type=AIQLNodeType.PROMOTE_MODEL, parameters=params)
    
    def _parse_rollback_model(self, node) -> AIQLNode:
        """Parse ROLLBACK MODEL: "ROLLBACK" "MODEL" string "TO" string ("REASON" string)?"""
        params = {}
        for i, child in enumerate(node.children):
            if hasattr(child, 'type') and child.type == 'STRING':
                if 'model_name' not in params:
                    params['model_name'] = self._get_string_value(child)
                elif 'to' not in params:
                    params['to'] = self._get_string_value(child)
                else:
                    params['reason'] = self._get_string_value(child)
        return AIQLNode(node_type=AIQLNodeType.ROLLBACK_MODEL, parameters=params)
    
    def _parse_merge_results(self, node) -> AIQLNode:
        """Parse MERGE RESULTS: "MERGE" "RESULTS" "(" (identifier | variable) ("," (identifier | variable))* ")" ("PARAMETERS" "(" parameter_dict ")")? ("STORE" "RESULT" ("IN" variable)?)?"""
        params = {'sources': []}
        for i, child in enumerate(node.children):
            if hasattr(child, 'data') and child.data in ['identifier', 'variable']:
                var_name = self._get_identifier(child) if child.data == 'identifier' else (self._get_identifier(child.children[0]) if child.children else None)
                params['sources'].append(var_name)
            elif hasattr(child, 'data') and child.data == 'parameter_dict':
                params['parameters'] = self._extract_parameter_dict(child)
            elif hasattr(child, 'value') and child.value == 'STORE' and i + 1 < len(node.children):
                if hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'RESULT':
                    if i + 2 < len(node.children) and hasattr(node.children[i+2], 'value') and node.children[i+2].value == 'IN':
                        store_var = node.children[i+3]
                        if hasattr(store_var, 'data') and store_var.data == 'variable':
                            params['store_result_in'] = self._get_identifier(store_var.children[0]) if store_var.children else None
        return AIQLNode(node_type=AIQLNodeType.MERGE_RESULTS, parameters=params)
    
    def _parse_blockchain_verify(self, tree):
        """Parse VERIFY BLOCKCHAIN query."""
        namespace = None
        for i, child in enumerate(tree.children):
            if hasattr(child, 'value') and child.value == 'FOR' and i + 1 < len(tree.children):
                namespace = self._get_identifier(tree.children[i + 1])
                break
        
        return AIQLNode(
            node_type=AIQLNodeType.VERIFY_BLOCKCHAIN,
            parameters={'namespace': namespace}
        )
    
    def _parse_blockchain_block(self, tree):
        """Parse GET BLOCK <number> query."""
        block_number = None
        namespace = None
        
        for i, child in enumerate(tree.children):
            if hasattr(child, 'type') and child.type == 'NUMBER':
                block_number = int(child.value)
            elif hasattr(child, 'data') and child.data == 'number':
                block_number = self._get_number(child)
            elif hasattr(child, 'value') and isinstance(child.value, (int, float)):
                block_number = int(child.value)
            elif hasattr(child, 'value') and child.value == 'FOR' and i + 1 < len(tree.children):
                namespace = self._get_identifier(tree.children[i + 1])
        
        return AIQLNode(
            node_type=AIQLNodeType.GET_BLOCK,
            parameters={'block_number': block_number, 'namespace': namespace}
        )
    
    def _parse_blockchain_length(self, tree):
        """Parse GET BLOCKCHAIN LENGTH query."""
        namespace = None
        for i, child in enumerate(tree.children):
            if hasattr(child, 'value') and child.value == 'FOR' and i + 1 < len(tree.children):
                namespace = self._get_identifier(tree.children[i + 1])
                break
        
        return AIQLNode(
            node_type=AIQLNodeType.GET_BLOCKCHAIN_LENGTH,
            parameters={'namespace': namespace}
        )
    
    def _parse_blockchain_latest(self, tree):
        """Parse GET LATEST BLOCK query."""
        namespace = None
        for i, child in enumerate(tree.children):
            if hasattr(child, 'value') and child.value == 'FOR' and i + 1 < len(tree.children):
                namespace = self._get_identifier(tree.children[i + 1])
                break
        
        return AIQLNode(
            node_type=AIQLNodeType.GET_LATEST_BLOCK,
            parameters={'namespace': namespace}
        )
    
    def _parse_blockchain_audit(self, tree):
        """Parse GET AUDIT TRAIL query."""
        namespace = None
        filters = {}
        
        for i, child in enumerate(tree.children):
            if hasattr(child, 'value') and child.value == 'FOR' and i + 1 < len(tree.children):
                namespace = self._get_identifier(tree.children[i + 1])
            elif hasattr(child, 'data') and child.data == 'blockchain_audit_filter':
                # Parse filter conditions
                for filter_item in child.children:
                    if hasattr(filter_item, 'data') and filter_item.data == 'blockchain_filter_item':
                        # Extract filter key and value
                        if len(filter_item.children) >= 3:
                            key = self._get_identifier(filter_item.children[0])
                            op = filter_item.children[1].value if hasattr(filter_item.children[1], 'value') else None
                            value_node = filter_item.children[2]
                            value = self._extract_parameter_value(value_node)
                            if op == '=':
                                filters[key] = value
        
        return AIQLNode(
            node_type=AIQLNodeType.GET_AUDIT_TRAIL,
            parameters={'namespace': namespace, 'filters': filters}
        )
    
    def _parse_blockchain_merkle(self, tree):
        """Parse GET MERKLE ROOT query."""
        namespace = None
        for i, child in enumerate(tree.children):
            if hasattr(child, 'value') and child.value == 'FOR' and i + 1 < len(tree.children):
                namespace = self._get_identifier(tree.children[i + 1])
                break
        
        return AIQLNode(
            node_type=AIQLNodeType.GET_MERKLE_ROOT,
            parameters={'namespace': namespace}
        )
    
    def _parse_blockchain_blocks_range(self, tree):
        """Parse GET BLOCKS FROM <start> TO <end> query."""
        start_block = None
        end_block = None
        namespace = None
        
        # The grammar is: "GET" "BLOCKS" "FROM" number "TO" number ("FOR" identifier)?
        # Lark extracts only non-terminals, so children are: [number_node (FROM), number_node (TO), identifier? (FOR)]
        # First child is the FROM number
        if len(tree.children) >= 1:
            start_node = tree.children[0]
            if hasattr(start_node, 'data') and start_node.data == 'number':
                start_block = self._get_number(start_node)
            elif hasattr(start_node, 'type') and start_node.type == 'NUMBER':
                start_block = int(start_node.value)
        
        # Second child is the TO number
        if len(tree.children) >= 2:
            end_node = tree.children[1]
            if hasattr(end_node, 'data') and end_node.data == 'number':
                end_block = self._get_number(end_node)
            elif hasattr(end_node, 'type') and end_node.type == 'NUMBER':
                end_block = int(end_node.value)
        
        # Third child (if present) is the FOR identifier
        if len(tree.children) >= 3:
            namespace = self._get_identifier(tree.children[2])
        
        return AIQLNode(
            node_type=AIQLNodeType.GET_BLOCKS_RANGE,
            parameters={'start_block': start_block, 'end_block': end_block, 'namespace': namespace}
        )
    
    def _parse_blockchain_verify_block(self, tree):
        """Parse VERIFY BLOCK <number> query."""
        block_number = None
        namespace = None
        
        for i, child in enumerate(tree.children):
            if hasattr(child, 'type') and child.type == 'NUMBER':
                block_number = int(child.value)
            elif hasattr(child, 'data') and child.data == 'number':
                block_number = self._get_number(child)
            elif hasattr(child, 'value') and isinstance(child.value, (int, float)):
                block_number = int(child.value)
            elif hasattr(child, 'value') and child.value == 'FOR' and i + 1 < len(tree.children):
                namespace = self._get_identifier(tree.children[i + 1])
        
        return AIQLNode(
            node_type=AIQLNodeType.VERIFY_BLOCK,
            parameters={'block_number': block_number, 'namespace': namespace}
        )
    
    def _parse_rerank(self, node) -> AIQLNode:
        """Parse RERANK: "RERANK" (identifier | variable) ("PARAMETERS" "(" parameter_dict ")")? ("STORE" "RESULT" ("IN" variable)?)?"""
        params = {}
        for i, child in enumerate(node.children):
            if hasattr(child, 'data') and child.data in ['identifier', 'variable']:
                if child.data == 'variable':
                    var_name = self._get_identifier(child.children[0]) if child.children else None
                    params['source'] = f"${var_name}" if var_name else None
                else:
                    params['source'] = self._get_identifier(child)
            elif hasattr(child, 'data') and child.data == 'parameter_dict':
                params['parameters'] = self._extract_parameter_dict(child)
            elif hasattr(child, 'value') and child.value == 'STORE' and i + 1 < len(node.children):
                if hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'RESULT':
                    if i + 2 < len(node.children) and hasattr(node.children[i+2], 'value') and node.children[i+2].value == 'IN':
                        store_var = node.children[i+3]
                        if hasattr(store_var, 'data') and store_var.data == 'variable':
                            params['store_result_in'] = self._get_identifier(store_var.children[0]) if store_var.children else None
        return AIQLNode(node_type=AIQLNodeType.RERANK, parameters=params)
    
    def _parse_reason_on(self, node) -> AIQLNode:
        """Parse REASON ON: "REASON" "ON" ("COLLECTION" | identifier) (identifier | variable) ("USING" "MODEL" (string | variable))? ("PARAMETERS" "(" parameter_dict ")")? ("STORE" "RESULT" "AS" variable)?"""
        params = {}
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value == 'COLLECTION':
                params['on_type'] = 'COLLECTION'
            elif hasattr(child, 'data') and child.data == 'identifier':
                if 'on_type' not in params:
                    params['on_type'] = self._get_identifier(child)
                elif 'on_source' not in params:
                    params['on_source'] = self._get_identifier(child)
            elif hasattr(child, 'data') and child.data == 'variable':
                params['on_source'] = self._get_identifier(child.children[0]) if child.children else None
                params['on_source_is_variable'] = True
            elif hasattr(child, 'value') and child.value == 'USING' and i + 2 < len(node.children):
                if hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'MODEL':
                    model_node = node.children[i + 2]
                    if hasattr(model_node, 'data') and model_node.data == 'variable':
                        params['model'] = self._get_identifier(model_node.children[0]) if model_node.children else None
                        params['model_is_variable'] = True
                    else:
                        params['model'] = self._get_string_value(model_node) if hasattr(model_node, 'type') and model_node.type == 'STRING' else self._get_identifier(model_node)
                        params['model_is_variable'] = False
            elif hasattr(child, 'data') and child.data == 'parameter_dict':
                params['parameters'] = self._extract_parameter_dict(child)
            elif hasattr(child, 'value') and child.value == 'STORE' and i + 2 < len(node.children):
                if hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'RESULT':
                    if hasattr(node.children[i+2], 'value') and node.children[i+2].value == 'AS':
                        store_var = node.children[i+3]
                        if hasattr(store_var, 'data') and store_var.data == 'variable':
                            params['store_result_as'] = self._get_identifier(store_var.children[0]) if store_var.children else None
        return AIQLNode(node_type=AIQLNodeType.REASON_ON, parameters=params)
    
    def _parse_neighbors(self, node) -> AIQLNode:
        """Parse NEIGHBORS FROM: "NEIGHBORS" "FROM" node_spec ("DEPTH" number)? ("VIA" "(" identifier_list ")")? return_clause? ("LIMIT" (number | variable))?"""
        params = {}
        
        # Parse node_spec (first child after FROM)
        node_spec = None
        depth = 1  # Default depth
        via_edges = []
        return_clause = None
        limit = None
        
        where_clause = None
        for i, child in enumerate(node.children):
            if hasattr(child, 'data') and child.data == 'node_spec':
                node_spec = child
                # Extract node type
                if len(child.children) >= 1:
                    node_type = self._get_identifier(child.children[0])
                    params['node_type'] = node_type
            elif hasattr(child, 'data') and child.data == 'where_clause':
                # WHERE clause appears after node_spec in grammar
                where_clause = self._parse_where_clause(child)
                params['where'] = where_clause
            elif hasattr(child, 'data') and child.data == 'condition':
                # Also handle condition directly
                where_clause = [self._parse_condition(child)]
                params['where'] = where_clause
            elif hasattr(child, 'value') and child.value == 'DEPTH' and i + 1 < len(node.children):
                depth_node = node.children[i + 1]
                if hasattr(depth_node, 'data') and depth_node.data == 'number':
                    depth = self._get_number(depth_node)
                elif hasattr(depth_node, 'type') and depth_node.type == 'NUMBER':
                    depth = float(depth_node.value) if hasattr(depth_node, 'value') else 1
            elif hasattr(child, 'data') and child.data == 'identifier_list':
                # VIA edges
                for id_child in child.children:
                    if hasattr(id_child, 'data') and id_child.data == 'identifier':
                        via_edges.append(self._get_identifier(id_child))
            elif hasattr(child, 'data') and child.data == 'return_clause':
                return_clause = child
            elif hasattr(child, 'value') and child.value == 'LIMIT' and i + 1 < len(node.children):
                limit_node = node.children[i + 1]
                if hasattr(limit_node, 'data') and limit_node.data == 'number':
                    limit = self._get_number(limit_node)
                elif hasattr(limit_node, 'data') and limit_node.data == 'variable':
                    var_name = self._get_identifier(limit_node.children[0]) if limit_node.children else None
                    if var_name and var_name.startswith('$'):
                        var_name = var_name[1:]
                    limit = f"${var_name}" if var_name else None
        
        params['depth'] = depth
        params['via_edges'] = via_edges
        params['limit'] = limit
        if return_clause:
            params['return'] = self._parse_return_clause(return_clause)
        
        return AIQLNode(
            node_type=AIQLNodeType.NEIGHBORS,
            parameters=params
        )
    
    def _parse_create_subgraph(self, node) -> AIQLNode:
        """Parse CREATE SUBGRAPH: "CREATE" "SUBGRAPH" identifier "AS" "MATCH" match_pattern return_clause"""
        params = {}
        
        # Extract subgraph name (first identifier)
        subgraph_name = None
        match_pattern = None
        return_clause = None
        
        for i, child in enumerate(node.children):
            if hasattr(child, 'data') and child.data == 'identifier':
                if subgraph_name is None:
                    subgraph_name = self._get_identifier(child)
            elif hasattr(child, 'data') and child.data == 'match_pattern':
                match_pattern = child
            elif hasattr(child, 'data') and child.data == 'return_clause':
                return_clause = child
        
        # Parse match_pattern
        if match_pattern:
            match_path = None
            where_clause = None
            for child in match_pattern.children:
                if hasattr(child, 'data') and child.data == 'match_path':
                    match_path = child
                elif hasattr(child, 'data') and child.data == 'where_clause':
                    where_clause = self._parse_where_clause(child)
            
            # Parse match_path to extract nodes and edges
            nodes = []
            edges = []
            if match_path:
                for path_child in match_path.children:
                    if hasattr(path_child, 'data') and path_child.data == 'match_node':
                        # Extract node info - same logic as in top_match
                        node_type = None
                        node_alias = None
                        node_props = {}
                        as_alias = None
                        i = 0
                        while i < len(path_child.children):
                            nc = path_child.children[i]
                            if hasattr(nc, 'data'):
                                if nc.data == 'identifier':
                                    if i + 1 < len(path_child.children):
                                        next_nc = path_child.children[i + 1]
                                        if hasattr(next_nc, 'value') and next_nc.value == ':':
                                            node_alias = self._get_identifier(nc)
                                            i += 2
                                            if i < len(path_child.children) and hasattr(path_child.children[i], 'data') and path_child.children[i].data == 'identifier':
                                                node_type = self._get_identifier(path_child.children[i])
                                                i += 1
                                        else:
                                            node_type = self._get_identifier(nc)
                                            i += 1
                                    else:
                                        node_type = self._get_identifier(nc)
                                        i += 1
                                elif nc.data == 'property_list':
                                    node_props = self._parse_property_list(nc)
                                    i += 1
                                else:
                                    i += 1
                            elif hasattr(nc, 'value') and nc.value == 'AS':
                                i += 1
                                if i < len(path_child.children) and hasattr(path_child.children[i], 'data') and path_child.children[i].data == 'identifier':
                                    as_alias = self._get_identifier(path_child.children[i])
                                    i += 1
                            else:
                                i += 1
                        final_alias = as_alias if as_alias else node_alias
                        nodes.append({'type': node_type, 'alias': final_alias, 'properties': node_props})
                    elif hasattr(path_child, 'data') and path_child.data == 'match_edge_arrow':
                        # Extract edge from match_edge_arrow - same logic as in top_match
                        for edge_child in path_child.children:
                            if hasattr(edge_child, 'data') and edge_child.data == 'match_edge':
                                edge_type = None
                                edge_alias = None
                                edge_props = {}
                                as_alias = None
                                i = 0
                                while i < len(edge_child.children):
                                    ec = edge_child.children[i]
                                    if hasattr(ec, 'data'):
                                        if ec.data == 'identifier':
                                            if i + 1 < len(edge_child.children):
                                                next_ec = edge_child.children[i + 1]
                                                if hasattr(next_ec, 'value') and next_ec.value == ':':
                                                    edge_alias = self._get_identifier(ec)
                                                    i += 2
                                                    if i < len(edge_child.children) and hasattr(edge_child.children[i], 'data') and edge_child.children[i].data == 'identifier':
                                                        edge_type = self._get_identifier(edge_child.children[i])
                                                        i += 1
                                                else:
                                                    edge_type = self._get_identifier(ec)
                                                    i += 1
                                            else:
                                                edge_type = self._get_identifier(ec)
                                                i += 1
                                        elif ec.data == 'property_list':
                                            edge_props = self._parse_property_list(ec)
                                            i += 1
                                        else:
                                            i += 1
                                    elif hasattr(ec, 'value') and ec.value == 'AS':
                                        i += 1
                                        if i < len(edge_child.children) and hasattr(edge_child.children[i], 'data') and edge_child.children[i].data == 'identifier':
                                            as_alias = self._get_identifier(edge_child.children[i])
                                            i += 1
                                    else:
                                        i += 1
                                final_alias = as_alias if as_alias else edge_alias
                                edges.append({'type': edge_type, 'alias': final_alias, 'properties': edge_props})
                    elif hasattr(path_child, 'data') and path_child.data == 'match_edge':
                        # Fallback for direct match_edge (without arrow)
                        edge_type = None
                        edge_alias = None
                        edge_props = {}
                        for ec in path_child.children:
                            if hasattr(ec, 'data') and ec.data == 'identifier':
                                if edge_type is None:
                                    edge_type = self._get_identifier(ec)
                            elif hasattr(ec, 'data') and ec.data == 'property_list':
                                edge_props = self._parse_property_list(ec)
                        edges.append({'type': edge_type, 'alias': edge_alias, 'properties': edge_props})
            
            params['match_path'] = {'nodes': nodes, 'edges': edges}
            if where_clause:
                params['where'] = where_clause
        
        if return_clause:
            params['return'] = self._parse_return_clause(return_clause)
        
        params['subgraph_name'] = subgraph_name
        
        return AIQLNode(
            node_type=AIQLNodeType.CREATE_SUBGRAPH,
            parameters=params
        )
    
    def _parse_return_clause(self, node) -> List[str]:
        """Parse RETURN clause."""
        return_exprs = []
        for child in node.children:
            if hasattr(child, 'data') and child.data == 'return_expression':
                # Extract identifier or variable
                for expr_child in child.children:
                    if hasattr(expr_child, 'data') and expr_child.data == 'qualified_identifier':
                        return_exprs.append(self._get_qualified_identifier(expr_child))
                    elif hasattr(expr_child, 'data') and expr_child.data == 'variable':
                        var_name = self._get_identifier(expr_child.children[0]) if expr_child.children else None
                        return_exprs.append(f"${var_name}" if var_name else "*")
                    elif hasattr(expr_child, 'value') and expr_child.value == '*':
                        return_exprs.append('*')
        return return_exprs
    
    # Additional parsing methods for other stages
    def _parse_chunk_stage(self, node) -> AIQLNode:
        """Parse CHUNK stage."""
        strategy = self._get_identifier(node.children[0].children[0])
        params = {'strategy': strategy}
        
        if len(node.children[0].children) > 1:
            params['parameters'] = self._parse_parameter_list(node.children[0].children[1])
        
        return AIQLNode(
            node_type=AIQLNodeType.CHUNK,
            parameters=params
        )
    
    def _parse_extract_stage(self, node) -> AIQLNode:
        """Parse EXTRACT stage."""
        model_spec = node.children[0]
        model = self._get_identifier(model_spec.children[0])
        params = {'model': model}
        
        if len(model_spec.children) > 1:
            params['parameters'] = self._parse_parameter_list(model_spec.children[1])
        
        return AIQLNode(
            node_type=AIQLNodeType.EXTRACT,
            parameters=params
        )
    
    def _parse_extract_from_folder_stage(self, node) -> AIQLNode:
        """Parse EXTRACT FROM FOLDER stage."""
        params = {}
        
        # Parse FROM FOLDER string
        folder_path = None
        detect_types = []
        node_types = []
        
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value == 'FROM' and i + 2 < len(node.children):
                if hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'FOLDER':
                    folder_path = self._get_string(node.children[i+2])
                    params['folder_path'] = folder_path
            elif hasattr(child, 'value') and child.value == 'DETECT':
                # Parse DETECT (detect_types)
                if i + 1 < len(node.children):
                    detect_node = node.children[i + 1]
                    if hasattr(detect_node, 'data') and detect_node.data == 'detect_types':
                        for dt_child in detect_node.children:
                            if hasattr(dt_child, 'data') and dt_child.data == 'detect_type':
                                detect_types.append(self._get_identifier(dt_child))
                    params['detect_types'] = detect_types
            elif hasattr(child, 'value') and child.value == 'STORE':
                # Parse STORE AS NODE TYPES
                if i + 3 < len(node.children) and hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'AS':
                    if hasattr(node.children[i+2], 'value') and node.children[i+2].value == 'NODE':
                        if hasattr(node.children[i+3], 'value') and node.children[i+3].value == 'TYPES':
                            if i + 4 < len(node.children):
                                types_node = node.children[i + 4]
                                if hasattr(types_node, 'data') and types_node.data == 'node_types':
                                    for nt_child in types_node.children:
                                        node_types.append(self._get_identifier(nt_child))
                                params['node_types'] = node_types
        
        return AIQLNode(
            node_type=AIQLNodeType.EXTRACT,
            parameters={'type': 'extract_from_folder', **params}
        )
    
    def _parse_chunk_by_semantic_stage(self, node) -> AIQLNode:
        """Parse CHUNK BY semantic stage."""
        params = {}
        
        # Parse CHUNK BY chunk_method
        chunk_method = None
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value == 'BY' and i + 1 < len(node.children):
                method_node = node.children[i + 1]
                if hasattr(method_node, 'data') and method_node.data == 'chunk_method':
                    chunk_method = self._get_identifier(method_node.children[0]) if method_node.children else 'semantic'
                else:
                    chunk_method = self._get_identifier(method_node)
                params['chunk_method'] = chunk_method
            elif hasattr(child, 'data') and child.data == 'parameter_dict':
                params['parameters'] = self._extract_parameter_dict(child)
            elif hasattr(child, 'value') and child.value == 'FROM' and i + 1 < len(node.children):
                params['from'] = self._get_identifier(node.children[i + 1])
            elif hasattr(child, 'value') and child.value == 'STORE':
                # Parse STORE AS NODE TYPE
                if i + 3 < len(node.children) and hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'AS':
                    if hasattr(node.children[i+2], 'value') and node.children[i+2].value == 'NODE':
                        if hasattr(node.children[i+3], 'value') and node.children[i+3].value == 'TYPE':
                            if i + 4 < len(node.children):
                                params['store_as_node_type'] = self._get_identifier(node.children[i + 4])
            elif hasattr(child, 'value') and child.value == 'LINK':
                # Parse LINK TO identifier ON identifier
                if i + 2 < len(node.children) and hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'TO':
                    link_to = self._get_identifier(node.children[i + 2])
                    if i + 4 < len(node.children) and hasattr(node.children[i+3], 'value') and node.children[i+3].value == 'ON':
                        link_on = self._get_identifier(node.children[i + 4])
                        params['link_to'] = link_to
                        params['link_on'] = link_on
        
        return AIQLNode(
            node_type=AIQLNodeType.CHUNK,
            parameters={'type': 'chunk_by_semantic', **params}
        )
    
    def _parse_embed_into_stage(self, node) -> AIQLNode:
        """Parse EMBED INTO stage."""
        params = {}
        
        model = None
        into_node = None
        
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value == 'USING' and i + 2 < len(node.children):
                if hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'MODEL':
                    model_node = node.children[i + 2]
                    if hasattr(model_node, 'data') and model_node.data == 'variable':
                        var_name = self._get_identifier(model_node.children[0]) if model_node.children else None
                        params['model'] = var_name
                        params['model_is_variable'] = True
                    else:
                        params['model'] = self._get_string(model_node)
                        params['model_is_variable'] = False
            elif hasattr(child, 'data') and child.data == 'parameter_dict':
                params['parameters'] = self._extract_parameter_dict(child)
            elif hasattr(child, 'value') and child.value == 'INTO' and i + 1 < len(node.children):
                into_node = self._get_identifier(node.children[i + 1])
                params['into'] = into_node
        
        return AIQLNode(
            node_type=AIQLNodeType.ADD_EMBEDDINGS,
            parameters={'type': 'embed_into', **params}
        )
    
    def _parse_hybrid_search_stage(self, node) -> AIQLNode:
        """Parse HYBRID SEARCH stage."""
        params = {}
        
        query = None
        collection = None
        model = None
        store_result_as = None
        
        for i, child in enumerate(node.children):
            if i == 0:
                # First child is the query (string or variable)
                if hasattr(child, 'data') and child.data == 'variable':
                    query = self._get_identifier(child.children[0]) if child.children else None
                    params['query'] = query
                    params['query_is_variable'] = True
                else:
                    query = self._get_string(child)
                    params['query'] = query
                    params['query_is_variable'] = False
            elif hasattr(child, 'value') and child.value == 'IN' and i + 1 < len(node.children):
                collection = self._get_identifier(node.children[i + 1])
                params['collection'] = collection
            elif hasattr(child, 'value') and child.value == 'USING' and i + 2 < len(node.children):
                if hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'MODEL':
                    model_node = node.children[i + 2]
                    if hasattr(model_node, 'data') and model_node.data == 'variable':
                        model = self._get_identifier(model_node.children[0]) if model_node.children else None
                        params['model'] = model
                        params['model_is_variable'] = True
                    else:
                        model = self._get_string(model_node)
                        params['model'] = model
                        params['model_is_variable'] = False
            elif hasattr(child, 'data') and child.data == 'parameter_dict':
                params['parameters'] = self._extract_parameter_dict(child)
            elif hasattr(child, 'value') and child.value == 'STORE':
                # Parse STORE RESULT AS identifier
                if i + 2 < len(node.children) and hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'RESULT':
                    if hasattr(node.children[i+2], 'value') and node.children[i+2].value == 'AS':
                        if i + 3 < len(node.children):
                            store_result_as = self._get_identifier(node.children[i + 3])
                            params['store_result_as'] = store_result_as
        
        return AIQLNode(
            node_type=AIQLNodeType.HYBRID_SEARCH,
            parameters={'type': 'hybrid_search_stage', **params}
        )
    
    def _parse_traverse_stage_pipeline(self, node) -> AIQLNode:
        """Parse TRAVERSE stage in pipeline context."""
        params = {}
        
        from_source = None
        via_edges = []
        to_target = None
        store_result_as = None
        
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value == 'FROM' and i + 1 < len(node.children):
                from_node = node.children[i + 1]
                if hasattr(from_node, 'data') and from_node.data == 'variable':
                    from_source = self._get_identifier(from_node.children[0]) if from_node.children else None
                    params['from_is_variable'] = True
                else:
                    from_source = self._get_identifier(from_node)
                    params['from_is_variable'] = False
                params['from'] = from_source
            elif hasattr(child, 'value') and child.value == 'VIA' and i + 1 < len(node.children):
                via_node = node.children[i + 1]
                if hasattr(via_node, 'data') and via_node.data == 'identifier_list':
                    for id_child in via_node.children:
                        if hasattr(id_child, 'data') and id_child.data == 'identifier':
                            via_edges.append(self._get_identifier(id_child))
                    params['via'] = via_edges
            elif hasattr(child, 'value') and child.value == 'TO' and i + 1 < len(node.children):
                to_target = self._get_identifier(node.children[i + 1])
                params['to'] = to_target
            elif hasattr(child, 'data') and child.data == 'parameter_dict':
                params['parameters'] = self._extract_parameter_dict(child)
            elif hasattr(child, 'value') and child.value == 'STORE':
                # Parse STORE RESULT AS identifier
                if i + 2 < len(node.children) and hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'RESULT':
                    if hasattr(node.children[i+2], 'value') and node.children[i+2].value == 'AS':
                        if i + 3 < len(node.children):
                            store_result_as = self._get_identifier(node.children[i + 3])
                            params['store_result_as'] = store_result_as
        
        return AIQLNode(
            node_type=AIQLNodeType.TRAVERSE,
            parameters={'type': 'traverse_stage', **params}
        )
    
    def _parse_generate_stage_pipeline(self, node) -> AIQLNode:
        """Parse GENERATE stage in pipeline context."""
        params = {}
        
        model = None
        prompt = None
        user_query = None
        context_sources = []
        store_result_as = None
        
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value == 'USING' and i + 2 < len(node.children):
                if hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'MODEL':
                    model_node = node.children[i + 2]
                    if hasattr(model_node, 'data') and model_node.data == 'variable':
                        model = self._get_identifier(model_node.children[0]) if model_node.children else None
                        params['model'] = model
                        params['model_is_variable'] = True
                    else:
                        model = self._get_string(model_node)
                        params['model'] = model
                        params['model_is_variable'] = False
            elif hasattr(child, 'value') and child.value == 'PROMPT' and i + 1 < len(node.children):
                prompt = self._get_string(node.children[i + 1])
                params['prompt'] = prompt
            elif hasattr(child, 'value') and child.value == 'USER_QUERY' and i + 1 < len(node.children):
                query_node = node.children[i + 1]
                if hasattr(query_node, 'data') and query_node.data == 'variable':
                    user_query = self._get_identifier(query_node.children[0]) if query_node.children else None
                    params['user_query'] = user_query
                    params['user_query_is_variable'] = True
                else:
                    user_query = self._get_string(query_node)
                    params['user_query'] = user_query
                    params['user_query_is_variable'] = False
            elif hasattr(child, 'value') and child.value == 'CONTEXT' and i + 2 < len(node.children):
                if hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'FROM':
                    context_node = node.children[i + 2]
                    if hasattr(context_node, 'data') and context_node.data == 'context_sources':
                        for cs_child in context_node.children:
                            if hasattr(cs_child, 'data') and cs_child.data == 'variable':
                                var_name = self._get_identifier(cs_child.children[0]) if cs_child.children else None
                                context_sources.append(var_name)
                            else:
                                context_sources.append(self._get_identifier(cs_child))
                    params['context_from'] = context_sources
            elif hasattr(child, 'data') and child.data == 'parameter_dict':
                params['parameters'] = self._extract_parameter_dict(child)
            elif hasattr(child, 'value') and child.value == 'GUARDRAILS' and i + 1 < len(node.children):
                guardrails_node = node.children[i + 1]
                if hasattr(guardrails_node, 'data') and guardrails_node.data == 'parameter_dict':
                    params['guardrails'] = self._extract_parameter_dict(guardrails_node)
            elif hasattr(child, 'value') and child.value == 'STORE':
                # Parse STORE RESULT AS identifier
                if i + 2 < len(node.children) and hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'RESULT':
                    if hasattr(node.children[i+2], 'value') and node.children[i+2].value == 'AS':
                        if i + 3 < len(node.children):
                            store_result_as = self._get_identifier(node.children[i + 3])
                            params['store_result_as'] = store_result_as
        
        return AIQLNode(
            node_type=AIQLNodeType.GENERATE,
            parameters={'type': 'generate_stage', **params}
        )
    
    def _parse_evaluate_rag_stage_pipeline(self, node) -> AIQLNode:
        """Parse EVALUATE RAG stage in pipeline context."""
        params = {}
        
        on_var = None
        model = None
        store_result_as = None
        
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value == 'ON' and i + 1 < len(node.children):
                on_node = node.children[i + 1]
                if hasattr(on_node, 'data') and on_node.data == 'variable':
                    on_var = self._get_identifier(on_node.children[0]) if on_node.children else None
                else:
                    on_var = self._get_identifier(on_node)
                params['on'] = on_var
            elif hasattr(child, 'value') and child.value == 'USING' and i + 2 < len(node.children):
                if hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'MODEL':
                    model_node = node.children[i + 2]
                    if hasattr(model_node, 'data') and model_node.data == 'variable':
                        model = self._get_identifier(model_node.children[0]) if model_node.children else None
                        params['model'] = model
                        params['model_is_variable'] = True
                    else:
                        model = self._get_string(model_node)
                        params['model'] = model
                        params['model_is_variable'] = False
            elif hasattr(child, 'data') and child.data == 'parameter_dict':
                params['parameters'] = self._extract_parameter_dict(child)
            elif hasattr(child, 'value') and child.value == 'STORE':
                # Parse STORE RESULT AS identifier
                if i + 2 < len(node.children) and hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'RESULT':
                    if hasattr(node.children[i+2], 'value') and node.children[i+2].value == 'AS':
                        if i + 3 < len(node.children):
                            store_result_as = self._get_identifier(node.children[i + 3])
                            params['store_result_as'] = store_result_as
        
        return AIQLNode(
            node_type=AIQLNodeType.EVALUATE_RAG,
            parameters={'type': 'evaluate_rag_stage', **params}
        )
    
    def _parse_insert_into_stage_pipeline(self, node) -> AIQLNode:
        """Parse INSERT INTO stage in pipeline context."""
        params = {}
        
        collection = None
        values = {}
        
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value == 'INTO' and i + 1 < len(node.children):
                collection = self._get_identifier(node.children[i + 1])
                params['collection'] = collection
            elif hasattr(child, 'value') and child.value == 'VALUES' and i + 1 < len(node.children):
                values_node = node.children[i + 1]
                if hasattr(values_node, 'data') and values_node.data == 'property_list':
                    values = self._parse_property_list(values_node)
                    params['values'] = values
        
        return AIQLNode(
            node_type=AIQLNodeType.INSERT_INTO,
            parameters={'type': 'insert_into_stage', **params}
        )
    
    def _parse_generate_stage(self, node) -> AIQLNode:
        """Parse GENERATE stage."""
        model_spec = node.children[0]
        model = self._get_identifier(model_spec.children[0])
        params = {'model': model}
        
        if len(model_spec.children) > 1:
            params['parameters'] = self._parse_parameter_list(model_spec.children[1])
        
        return AIQLNode(
            node_type=AIQLNodeType.GENERATE,
            parameters=params
        )
    
    def _parse_embedding_stage(self, node) -> AIQLNode:
        """Parse ADD EMBEDDINGS stage."""
        model_spec = node.children[0]
        model = self._get_identifier(model_spec.children[0])
        params = {'model': model}
        
        if len(model_spec.children) > 1:
            params['parameters'] = self._parse_parameter_list(model_spec.children[1])
        
        if len(node.children) > 1:
            params['target'] = self._get_identifier(node.children[1])
        
        return AIQLNode(
            node_type=AIQLNodeType.ADD_EMBEDDINGS,
            parameters=params
        )
    
    def _parse_sparse_match(self, node) -> AIQLNode:
        """Parse SPARSE MATCH."""
        query = self._get_string(node.children[0])
        params = {'query': query}
        
        if len(node.children) > 1:
            params['target'] = self._get_identifier(node.children[1])
        if len(node.children) > 2:
            params['limit'] = self._get_number(node.children[2])
        
        return AIQLNode(
            node_type=AIQLNodeType.SPARSE_MATCH,
            parameters=params
        )
    
    def _parse_dense_match(self, node) -> AIQLNode:
        """Parse DENSE MATCH."""
        query = self._get_string(node.children[0])
        params = {'query': query}
        
        # Parse MODEL parameter if present
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value == 'MODEL':
                if i + 2 < len(node.children) and hasattr(node.children[i + 1], 'value') and node.children[i + 1].value == '=':
                    params['model'] = self._get_identifier(node.children[i + 2])
        
        # Parse IN/AS clause
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value in ['IN', 'AS']:
                if i + 1 < len(node.children):
                    params['target'] = self._get_identifier(node.children[i + 1])
        
        # Parse LIMIT clause
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value == 'LIMIT':
                if i + 1 < len(node.children):
                    params['limit'] = self._get_number(node.children[i + 1])
        
        return AIQLNode(
            node_type=AIQLNodeType.DENSE_MATCH,
            parameters=params
        )
    
    def _parse_rerank_stage(self, node) -> AIQLNode:
        """Parse RERANK stage."""
        model = self._get_identifier(node.children[0])
        params = {'model': model}
        
        if len(node.children) > 1:
            params['limit'] = self._get_number(node.children[1])
        
        return AIQLNode(
            node_type=AIQLNodeType.RERANK,
            parameters=params
        )
    
    def _parse_pagerank_stage(self, node) -> AIQLNode:
        """Parse PAGERANK stage."""
        params = {}
        
        # Parse children - the grammar captures identifier and number directly
        for child in node.children:
            if hasattr(child, 'data'):
                if child.data == 'identifier':
                    # This is the target graph/node type from ON clause
                    params['target'] = self._get_identifier(child)
                elif child.data == 'number':
                    # This is the iterations from ITERATIONS clause
                    # Extract the actual number value from the child
                    if hasattr(child, 'children') and len(child.children) > 0:
                        num_value = child.children[0]
                        params['iterations'] = int(str(num_value))
                    else:
                        params['iterations'] = self._get_number(child)
        
        return AIQLNode(
            node_type=AIQLNodeType.PAGERANK,
            parameters=params
        )
    
    def _parse_pagerank_params(self, node) -> Dict[str, Any]:
        """Parse PageRank WITH parameters."""
        params = {}
        
        for child in node.children:
            if hasattr(child, 'data') and child.data == 'pagerank_param':
                # Parse individual parameter
                if len(child.children) >= 3:
                    param_name = child.children[0].value
                    param_value = self._get_number(child.children[2])
                    if param_name == 'DAMPING':
                        params['damping'] = param_value
                    elif param_name == 'ITERATIONS':
                        params['iterations'] = param_value
        
        return params
    
    def _parse_community_stage(self, node) -> AIQLNode:
        """Parse COMMUNITY_DETECTION stage."""
        params = {}
        
        if len(node.children) > 0:
            params['algorithm'] = self._get_identifier(node.children[0])
        
        return AIQLNode(
            node_type=AIQLNodeType.COMMUNITY_DETECTION,
            parameters=params
        )
    
    def _parse_index_stage(self, node) -> AIQLNode:
        """Parse CREATE INDEX stage."""
        index_type = node.children[0].children[0].value
        target = self._get_identifier(node.children[1])
        params = {'index_type': index_type, 'target': target}
        
        if len(node.children) > 2:
            params['by'] = self._get_identifier(node.children[2])
        
        return AIQLNode(
            node_type=AIQLNodeType.CREATE_INDEX,
            parameters=params
        )
    
    def _parse_shard_stage(self, node) -> AIQLNode:
        """Parse SHARD GRAPH stage."""
        graph_name = self._get_identifier(node.children[0])
        shard_key = self._get_identifier(node.children[1])
        
        return AIQLNode(
            node_type=AIQLNodeType.SHARD_GRAPH,
            parameters={'graph_name': graph_name, 'shard_key': shard_key}
        )
    
    def _parse_schema_stage(self, node) -> AIQLNode:
        """Parse schema stage (MATCH, LIST ENTITIES, SHOW, or DESCRIBE)."""
        inner_stage = node.children[0]
        stage_type = inner_stage.data
        
        if stage_type == 'match_entity_stage':
            return self._parse_match_entity_stage(inner_stage)
        elif stage_type == 'list_entities_stage':
            return self._parse_list_entities_stage(inner_stage)
        elif stage_type == 'show_stage':
            return self._parse_show_stage(inner_stage)
        elif stage_type == 'describe_stage':
            return self._parse_describe_stage(inner_stage)
        else:
            return None
    
    def _parse_match_entity_stage(self, node) -> AIQLNode:
        """Parse MATCH entity stage."""
        entity_pattern = node.children[0] if len(node.children) > 0 else None
        return_clause = node.children[1] if len(node.children) > 1 else None
        
        if not entity_pattern:
            raise ValueError("No entity pattern found in MATCH entity stage")
        
        # Parse entity pattern - entity_pattern contains either node_pattern or edge_pattern
        inner_pattern = entity_pattern.children[0] if entity_pattern.children else None
        if not inner_pattern:
            raise ValueError("Invalid entity pattern structure")
            
        if inner_pattern.data == 'node_pattern':
            entity_type = 'NODE'
            identifier = self._get_identifier(inner_pattern.children[0])
            alias = None
            if len(inner_pattern.children) > 1:
                alias = self._get_identifier(inner_pattern.children[1])
        elif inner_pattern.data == 'edge_pattern':
            entity_type = 'EDGE'
            identifier = self._get_identifier(inner_pattern.children[0])
            alias = None
            if len(inner_pattern.children) > 1:
                alias = self._get_identifier(inner_pattern.children[1])
        else:
            raise ValueError(f"Unknown entity pattern: {inner_pattern.data}")
        
        # Parse return clause (optional) - can be entity_return_clause or regular return_clause
        return_type = '*'
        return_alias = None
        if return_clause:
            # Check if it's entity_return_clause or regular return_clause
            if hasattr(return_clause, 'data') and return_clause.data == 'return_clause':
                # Regular return_clause: "RETURN" return_expression ("," return_expression)*
                # Take first expression
                if hasattr(return_clause, 'children') and len(return_clause.children) > 0:
                    return_expr = return_clause.children[0]
                    if hasattr(return_expr, 'data') and return_expr.data == 'return_expression':
                        if hasattr(return_expr, 'children') and len(return_expr.children) > 0:
                            expr_val = return_expr.children[0]
                            if hasattr(expr_val, 'value') and expr_val.value == '*':
                                return_type = '*'
                            elif hasattr(expr_val, 'data') and expr_val.data == 'qualified_identifier':
                                return_type = self._get_qualified_identifier(expr_val)
                            else:
                                return_type = self._get_qualified_identifier(expr_val)
            elif hasattr(return_clause, 'children') and len(return_clause.children) > 0:
                # entity_return_clause
                return_expression = return_clause.children[0]
        if len(return_clause.children) > 1:
            return_alias = self._get_identifier(return_clause.children[1])
        
        # Parse return expression
        # The return_expression contains the actual expression as a child
        if hasattr(return_expression, 'children') and len(return_expression.children) > 0:
            actual_expression = return_expression.children[0]
            
            if hasattr(actual_expression, 'data'):
                if actual_expression.data == 'count_expression':
                    # count_expression now has either COUNT_STAR or COUNT_ID + identifier + )
                    if len(actual_expression.children) == 1:
                        # COUNT_STAR case
                        count_target = '*'
                    elif len(actual_expression.children) == 3:
                        # COUNT_ID + identifier + ) case
                        count_target = self._get_identifier(actual_expression.children[1])
                    else:
                        count_target = '*'
                    return_type = f"count({count_target})"
                elif actual_expression.data == 'properties_expression':
                    return_type = 'properties'
                elif actual_expression.data == 'schema_expression':
                    return_type = 'schema'
                elif actual_expression.data == 'wildcard_expression':
                    return_type = '*'
                elif actual_expression.data == 'qualified_identifier':
                    return_type = self._get_qualified_identifier(actual_expression)
                else:
                    return_type = '*'
        
        return AIQLNode(
            node_type=AIQLNodeType.MATCH_ENTITY,
            parameters={
                'entity_type': entity_type,
                'identifier': identifier,
                'alias': alias,
                'return_type': return_type,
                'return_alias': return_alias
            }
        )
    
    def _parse_list_entities_stage(self, node) -> AIQLNode:
        """Parse LIST ENTITIES or SCHEMA stage."""
        return AIQLNode(
            node_type=AIQLNodeType.LIST_ENTITY_TYPES,
            parameters={}
        )
    
    def _parse_return_stage(self, node) -> AIQLNode:
        """Parse RETURN stage in pipeline."""
        if len(node.children) == 0:
            return AIQLNode(
                node_type=AIQLNodeType.SELECT,
                parameters={'select': ['*']}
            )
        
        return_expr = node.children[0]
        
        # Handle variable, wildcard, or qualified identifier
        if hasattr(return_expr, 'data'):
            if return_expr.data == 'variable':
                # Extract variable name (remove $)
                var_name = self._get_identifier(return_expr.children[0]) if return_expr.children else str(return_expr).replace('$', '')
                if var_name.startswith('$'):
                    var_name = var_name[1:]
                # Get variable value from context
                var_value = self.variables.get(var_name, f"${var_name}")
                return AIQLNode(
                    node_type=AIQLNodeType.SELECT,
                    parameters={'select': [var_value], 'return_variable': var_name}
                )
            elif hasattr(return_expr, 'value') and return_expr.value == '*':
                return AIQLNode(
                    node_type=AIQLNodeType.SELECT,
                    parameters={'select': ['*']}
                )
        elif hasattr(return_expr, 'value'):
            val = return_expr.value
            if val == '*':
                return AIQLNode(
                    node_type=AIQLNodeType.SELECT,
                    parameters={'select': ['*']}
                )
            elif val.startswith('$'):
                var_name = val[1:]
                var_value = self.variables.get(var_name, val)
                return AIQLNode(
                    node_type=AIQLNodeType.SELECT,
                    parameters={'select': [var_value], 'return_variable': var_name}
                )
        
        # Default: return all
        return AIQLNode(
            node_type=AIQLNodeType.SELECT,
            parameters={'select': ['*']}
        )
    
    def _parse_show_stage(self, node) -> AIQLNode:
        """Parse SHOW stage."""
        show_target = node.children[0]
        
        # show_target contains the specific rule (show_graph, show_node, etc.)
        # We need to get the actual child rule
        if hasattr(show_target, 'children') and len(show_target.children) > 0:
            actual_target = show_target.children[0]
            target_rule = actual_target.data
        else:
            target_rule = show_target.data
        
        # Extract the actual target type from the rule name
        if target_rule == 'show_node':
            target_type = 'NODE'
        elif target_rule == 'show_graph':
            target_type = 'GRAPH'
        elif target_rule == 'show_edge':
            target_type = 'EDGE'
        elif target_rule == 'show_index':
            target_type = 'INDEX'
        elif target_rule == 'show_model':
            target_type = 'MODEL'
        elif target_rule == 'show_prompt':
            target_type = 'PROMPT'
        elif target_rule == 'show_namespaces':
            return AIQLNode(
                node_type=AIQLNodeType.SHOW_NAMESPACES,
                parameters={}
            )
        elif target_rule == 'show_stats':
            target_type = 'STATS'
        else:
            target_type = 'NODE'  # Default fallback
        
        return AIQLNode(
            node_type=AIQLNodeType.SHOW,
            parameters={'target': target_type}
        )
    
    def _parse_describe_stage(self, node) -> AIQLNode:
        """Parse DESCRIBE stage."""
        describe_target = node.children[0]
        
        # describe_target now has specific rules (describe_graph, describe_node, etc.)
        # Extract the actual target type from the rule name
        if describe_target.data == 'describe_node':
            target_type = 'NODE'
        elif describe_target.data == 'describe_graph':
            target_type = 'GRAPH'
        elif describe_target.data == 'describe_edge':
            target_type = 'EDGE'
        else:
            target_type = 'NODE'  # Default fallback
        
        # Extract the identifier from the children
        if len(describe_target.children) > 0:
            identifier = self._get_identifier(describe_target.children[0])
        else:
            identifier = ''
        
        return AIQLNode(
            node_type=AIQLNodeType.DESCRIBE,
            parameters={'target_type': target_type, 'identifier': identifier}
        )
    
    def _parse_model_stage(self, node) -> AIQLNode:
        """Parse MODEL declaration stage."""
        # MODEL identifier AS identifier model_config
        model_name = self._get_identifier(node.children[0])
        alias = self._get_identifier(node.children[1])
        
        # Parse model configuration
        model_config = {}
        if len(node.children) > 2:
            config_node = node.children[2]
            if hasattr(config_node, 'children'):
                for param_node in config_node.children:
                    if hasattr(param_node, 'children') and len(param_node.children) >= 2:
                        param_name = self._get_identifier(param_node.children[0])
                        param_value = self._get_identifier(param_node.children[1])
                        model_config[param_name] = param_value
        
        return AIQLNode(
            node_type=AIQLNodeType.MODEL_DECL,
            parameters={
                'model_name': model_name,
                'alias': alias,
                'config': model_config
            }
        )
    
    def _parse_prompt_stage(self, node) -> AIQLNode:
        """Parse PROMPT declaration stage."""
        # PROMPT identifier AS string
        prompt_name = self._get_identifier(node.children[0])
        prompt_content = self._get_string(node.children[1])
        
        return AIQLNode(
            node_type=AIQLNodeType.PROMPT_DECL,
            parameters={
                'prompt_name': prompt_name,
                'content': prompt_content
            }
        )
    
    def _parse_define_node_stage(self, node) -> AIQLNode:
        """Parse DEFINE NODE TYPE stage."""
        # DEFINE NODE TYPE identifier (node_field_list)
        node_type_name = self._get_identifier(node.children[0])
        
        # Parse field list
        fields = []
        if len(node.children) > 1:
            field_list_node = node.children[1]
            if hasattr(field_list_node, 'children'):
                for field_node in field_list_node.children:
                    if hasattr(field_node, 'children') and len(field_node.children) >= 2:
                        field_name = self._get_identifier(field_node.children[0])
                        field_type = self._get_identifier(field_node.children[1])
                        
                        # Parse constraints
                        constraints = []
                        if len(field_node.children) > 2:
                            constraint_node = field_node.children[2]
                            if hasattr(constraint_node, 'children'):
                                for constraint in constraint_node.children:
                                    constraints.append(self._get_identifier(constraint))
                        
                        fields.append({
                            'name': field_name,
                            'type': field_type,
                            'constraints': constraints
                        })
        
        return AIQLNode(
            node_type=AIQLNodeType.DEFINE_NODE_TYPE,
            parameters={
                'node_type_name': node_type_name,
                'fields': fields
            }
        )
    
    def _parse_define_edge_stage(self, node) -> AIQLNode:
        """Parse DEFINE EDGE TYPE stage."""
        # DEFINE EDGE TYPE identifier (edge_field_list) FROM node_type TO node_type
        edge_type_name = self._get_identifier(node.children[0])
        
        # Parse field list
        fields = []
        if len(node.children) > 1:
            field_list_node = node.children[1]
            if hasattr(field_list_node, 'children'):
                for field_node in field_list_node.children:
                    if hasattr(field_node, 'children') and len(field_node.children) >= 2:
                        field_name = self._get_identifier(field_node.children[0])
                        field_type = self._get_identifier(field_node.children[1])
                        
                        # Parse constraints
                        constraints = []
                        if len(field_node.children) > 2:
                            constraint_node = field_node.children[2]
                            if hasattr(constraint_node, 'children'):
                                for constraint in constraint_node.children:
                                    constraints.append(self._get_identifier(constraint))
                        
                        fields.append({
                            'name': field_name,
                            'type': field_type,
                            'constraints': constraints
                        })
        
        # Parse FROM and TO node types
        from_node_type = ''
        to_node_type = ''
        if len(node.children) > 2:
            from_node_type = self._get_identifier(node.children[2])
        if len(node.children) > 3:
            to_node_type = self._get_identifier(node.children[3])
        
        return AIQLNode(
            node_type=AIQLNodeType.DEFINE_EDGE_TYPE,
            parameters={
                'edge_type_name': edge_type_name,
                'fields': fields,
                'from_node_type': from_node_type,
                'to_node_type': to_node_type
            }
        )
    
    def _parse_namespace_stage(self, node) -> AIQLNode:
        """Parse NAMESPACE stage (CREATE NAMESPACE or USE NAMESPACE)."""
        # namespace_stage: "CREATE" "NAMESPACE" identifier | "USE" "NAMESPACE" identifier
        
        # Debug logging
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"[DEBUG] Parsing namespace_stage with {len(node.children)} children")
        for i, child in enumerate(node.children):
            logger.info(f"   Child {i}: data='{child.data}', value='{getattr(child, 'value', 'N/A')}'")
        
        # The grammar should parse CREATE NAMESPACE as separate tokens, but let's handle both cases
        if len(node.children) >= 3:
            # Case 1: CREATE NAMESPACE identifier (3 children)
            if hasattr(node.children[0], 'value') and node.children[0].value == 'CREATE':
                namespace_name = self._get_identifier(node.children[2])
                return AIQLNode(
                    node_type=AIQLNodeType.CREATE_NAMESPACE,
                    parameters={'namespace_name': namespace_name}
                )
            elif hasattr(node.children[0], 'value') and node.children[0].value == 'USE':
                namespace_name = self._get_identifier(node.children[2])
                return AIQLNode(
                    node_type=AIQLNodeType.USE_NAMESPACE,
                    parameters={'namespace_name': namespace_name}
                )
        elif len(node.children) == 1:
            # Case 2: Only identifier child (CREATE NAMESPACE might be parsed as single token)
            # We need to look at the parent to determine if it's CREATE or USE
            namespace_name = self._get_identifier(node.children[0])
            
            # Check the query context to determine if it's CREATE or USE
            # This is a workaround for the grammar parsing issue
            if hasattr(self, '_current_query'):
                query_upper = self._current_query.upper()
                if 'USE NAMESPACE' in query_upper:
                    logger.info(f"[DEBUG] Detected USE NAMESPACE for identifier: {namespace_name}")
                    return AIQLNode(
                        node_type=AIQLNodeType.USE_NAMESPACE,
                        parameters={'namespace_name': namespace_name}
                    )
            
            # Default to CREATE NAMESPACE
            logger.info(f"[DEBUG] Assuming CREATE NAMESPACE for identifier: {namespace_name}")
            return AIQLNode(
                node_type=AIQLNodeType.CREATE_NAMESPACE,
                parameters={'namespace_name': namespace_name}
            )
        
        logger.error(f"[ERROR] Unable to parse namespace_stage with {len(node.children)} children")
        return None
    
    def _parse_grant_stage(self, node) -> AIQLNode:
        """Parse GRANT stage."""
        # grant_stage: "GRANT" grant_permission "ON" grant_target "TO" identifier
        if len(node.children) < 7:
            return None
            
        permission = self._get_identifier(node.children[1])
        target_type = self._get_identifier(node.children[3])
        target_name = self._get_identifier(node.children[4])
        grantee = self._get_identifier(node.children[6])
        
        return AIQLNode(
            node_type=AIQLNodeType.GRANT,
            parameters={
                'permission': permission,
                'target_type': target_type,
                'target_name': target_name,
                'grantee': grantee
            }
        )
    
    def _parse_view_stage(self, node) -> AIQLNode:
        """Parse CREATE VIEW stage."""
        # view_stage: "CREATE" "VIEW" identifier "AS" string
        view_name = "unknown"
        view_definition = "unknown"
        
        # Look for identifier and string in children
        for child in node.children:
            if hasattr(child, 'data') and child.data == 'identifier':
                view_name = self._get_identifier(child)
            elif hasattr(child, 'data') and child.data == 'string':
                view_definition = self._get_string(child)
            elif hasattr(child, 'value') and child.value not in ['CREATE', 'VIEW', 'AS']:
                # This might be the identifier or string
                if view_name == "unknown":
                    view_name = str(child.value)
                else:
                    view_definition = str(child.value)
        
        return AIQLNode(
            node_type=AIQLNodeType.CREATE_VIEW,
            parameters={
                'view_name': view_name,
                'view_definition': view_definition
            }
        )
    
    def _parse_materialized_view_stage(self, node) -> AIQLNode:
        """
        Parse materialized view operations.
        
        Supports:
        - CREATE MATERIALIZED CONTEXT VIEW
        - REFRESH MATERIALIZED VIEW
        - DROP MATERIALIZED VIEW
        """
        # First child is the operation type
        if not node.children:
            return AIQLNode(node_type=AIQLNodeType.MATERIALIZE_VIEW, parameters={})
        
        operation_node = node.children[0]
        operation_type = operation_node.data if hasattr(operation_node, 'data') else None
        
        if operation_type == 'create_materialized_view':
            return self._parse_create_materialized_view(operation_node)
        elif operation_type == 'refresh_materialized_view':
            return self._parse_refresh_materialized_view(operation_node)
        elif operation_type == 'drop_materialized_view':
            return self._parse_drop_materialized_view(operation_node)
        else:
            # Fallback to old behavior
            return AIQLNode(node_type=AIQLNodeType.MATERIALIZE_VIEW, parameters={})
    
    def _parse_create_materialized_view(self, node) -> AIQLNode:
        """Parse CREATE MATERIALIZED CONTEXT VIEW."""
        params = {
            'operation': 'create',
            'view_name': None,
            'query': None,
            'max_tokens': 8000,
            'refresh_trigger': None,
            'context_params': {}
        }
        
        # Extract view name
        for child in node.children:
            if hasattr(child, 'data') and child.data == 'identifier':
                params['view_name'] = self._get_identifier(child)
                break
        
        # Extract materialized_view_body (contains retrieval_stage)
        for child in node.children:
            if hasattr(child, 'data') and child.data == 'materialized_view_body':
                # Parse the retrieval stage
                for body_child in child.children:
                    if hasattr(body_child, 'data') and body_child.data == 'retrieval_stage':
                        # Store the retrieval query
                        params['query'] = str(body_child)  # We'll parse this later
                    elif hasattr(body_child, 'data') and body_child.data == 'context_params':
                        # Parse context parameters
                        params['context_params'] = self._parse_context_params(body_child)
        
        # Extract refresh trigger if present
        for child in node.children:
            if hasattr(child, 'data') and child.data == 'refresh_trigger':
                params['refresh_trigger'] = self._parse_refresh_trigger(child)
        
        # Extract max_tokens from context_params
        if 'max_tokens' in params['context_params']:
            params['max_tokens'] = params['context_params']['max_tokens']
        
        return AIQLNode(
            node_type=AIQLNodeType.MATERIALIZE_VIEW,
            parameters=params
        )
    
    def _parse_refresh_materialized_view(self, node) -> AIQLNode:
        """Parse REFRESH MATERIALIZED VIEW."""
        params = {
            'operation': 'refresh',
            'view_name': None
        }
        
        # Extract view name
        for child in node.children:
            if hasattr(child, 'data') and child.data == 'identifier':
                params['view_name'] = self._get_identifier(child)
                break
        
        return AIQLNode(
            node_type=AIQLNodeType.MATERIALIZE_VIEW,
            parameters=params
        )
    
    def _parse_drop_materialized_view(self, node) -> AIQLNode:
        """Parse DROP MATERIALIZED VIEW."""
        params = {
            'operation': 'drop',
            'view_name': None
        }
        
        # Extract view name
        for child in node.children:
            if hasattr(child, 'data') and child.data == 'identifier':
                params['view_name'] = self._get_identifier(child)
                break
        
        return AIQLNode(
            node_type=AIQLNodeType.MATERIALIZE_VIEW,
            parameters=params
        )
    
    def _parse_context_params(self, node) -> Dict[str, Any]:
        """Parse context parameters like max_tokens=8000."""
        params = {}
        
        for child in node.children:
            if hasattr(child, 'data') and child.data == 'context_param':
                # context_param: identifier "=" (number | string | boolean)
                if len(child.children) >= 2:
                    key = self._get_identifier(child.children[0])
                    value_node = child.children[1]
                    
                    if hasattr(value_node, 'data'):
                        if value_node.data == 'number':
                            params[key] = self._get_number(value_node)
                        elif value_node.data == 'string':
                            params[key] = self._get_string(value_node)
                        elif value_node.data == 'boolean':
                            params[key] = self._get_boolean(value_node)
        
        return params
    
    def _parse_refresh_trigger(self, node) -> Dict[str, Any]:
        """Parse refresh trigger (ON event or EVERY interval)."""
        trigger = {
            'type': None,  # 'event' or 'interval'
            'value': None
        }
        
        for child in node.children:
            if hasattr(child, 'data'):
                if child.data == 'refresh_event':
                    trigger['type'] = 'event'
                    trigger['value'] = str(child.children[0].value) if child.children else None
                elif child.data == 'time_interval':
                    trigger['type'] = 'interval'
                    # time_interval: number time_unit
                    if len(child.children) >= 2:
                        amount = self._get_number(child.children[0])
                        unit = str(child.children[1].value) if hasattr(child.children[1], 'value') else 'SECONDS'
                        trigger['value'] = {'amount': amount, 'unit': unit}
        
        return trigger
    
    def _parse_temporal_stage(self, node) -> AIQLNode:
        """Parse AT TIME stage."""
        # temporal_stage: "AT" "TIME" string
        time_value = "unknown"
        
        # Look for string in children
        for child in node.children:
            if hasattr(child, 'data') and child.data == 'string':
                time_value = self._get_string(child)
            elif hasattr(child, 'value') and child.value not in ['AT', 'TIME']:
                time_value = str(child.value)
        
        return AIQLNode(
            node_type=AIQLNodeType.TEMPORAL,
            parameters={
                'time_value': time_value
            }
        )
    
    def _parse_temporal_query(self, node) -> AIQLNode:
        """Parse temporal query: AT TIME string stage."""
        # temporal_query: temporal_clause stage
        time_value = "unknown"
        stage_node = None
        
        # Find temporal clause and stage
        for child in node.children:
            if hasattr(child, 'data'):
                if child.data == 'temporal_clause':
                    # Extract time value from temporal clause
                    for subchild in child.children:
                        if hasattr(subchild, 'data') and subchild.data == 'string':
                            time_value = self._get_string(subchild)
                        elif hasattr(subchild, 'value') and subchild.value not in ['AT', 'TIME']:
                            time_value = str(subchild.value)
                elif child.data == 'stage':
                    stage_node = child
        
        # Parse the inner stage
        inner_stage = None
        if stage_node:
            inner_stage = self._parse_stage(stage_node)
        
        return AIQLNode(
            node_type=AIQLNodeType.TEMPORAL_QUERY,
            parameters={
                'time_value': time_value,
                'inner_stage': inner_stage
            }
        )
    
    def _extract_pipeline_definition(self, pipeline_node) -> str:
        """Extract pipeline definition as string."""
        # For now, return a simplified representation
        # In a full implementation, this would parse the entire pipeline
        return f"Pipeline definition for {pipeline_node.data}"
    
    
    def _parse_shortest_path_stage(self, node) -> AIQLNode:
        """Parse SHORTEST_PATH stage."""
        # shortest_path_stage: "SHORTEST_PATH" "FROM" identifier "TO" identifier ("VIA" "EDGES" identifier_list)? ("RETURN" return_clause)?
        # or: "SHORTEST_PATH" "SRC" identifier "DEST" identifier ("MAX_DEPTH" number)? ("WEIGHT_FIELD" identifier)?
        # or: "SHORTEST_PATH" "(" "SOURCE" "=" identifier "," "TARGET" "=" identifier ... ")"
        source = None
        target = None
        weight_attribute = None
        max_depth = None
        via_edges = []
        
        # Check if this is the parenthesized format
        if len(node.children) > 1 and hasattr(node.children[1], 'value') and node.children[1].value == '(':
            # Parse parenthesized format: SHORTEST PATH(SOURCE="Alice", TARGET="Bob")
            for i, child in enumerate(node.children):
                if hasattr(child, 'value') and child.value == 'SOURCE':
                    if i + 2 < len(node.children) and hasattr(node.children[i + 1], 'value') and node.children[i + 1].value == '=':
                        source = self._get_identifier(node.children[i + 2])
                elif hasattr(child, 'value') and child.value == 'TARGET':
                    if i + 2 < len(node.children) and hasattr(node.children[i + 1], 'value') and node.children[i + 1].value == '=':
                        target = self._get_identifier(node.children[i + 2])
        else:
            # Parse FROM/TO/VIA EDGES format or SRC/DEST format
            # Check for FROM/TO format first
            has_from_to = False
            for i, child in enumerate(node.children):
                if hasattr(child, 'value') and child.value == 'FROM':
                    has_from_to = True
                    if i + 1 < len(node.children):
                        source_child = node.children[i + 1]
                        # Handle string, identifier, or variable
                        if hasattr(source_child, 'data'):
                            if source_child.data == 'string':
                                source = self._get_string(source_child)
                            elif source_child.data == 'identifier':
                                source = self._get_identifier(source_child)
                            elif source_child.data == 'variable':
                                source = self._get_identifier(source_child.children[0]) if source_child.children else None
                            else:
                                source = self._get_identifier(source_child) if hasattr(source_child, 'children') else str(source_child)
                        elif hasattr(source_child, 'value'):
                            source = str(source_child.value).strip('"\'')
                        else:
                            source = self._get_identifier(source_child) if hasattr(source_child, 'value') else str(source_child)
                elif hasattr(child, 'value') and child.value == 'TO':
                    has_from_to = True
                    if i + 1 < len(node.children):
                        target_child = node.children[i + 1]
                        # Handle string, identifier, or variable
                        if hasattr(target_child, 'data'):
                            if target_child.data == 'string':
                                target = self._get_string(target_child)
                            elif target_child.data == 'identifier':
                                target = self._get_identifier(target_child)
                            elif target_child.data == 'variable':
                                target = self._get_identifier(target_child.children[0]) if target_child.children else None
                            else:
                                target = self._get_identifier(target_child) if hasattr(target_child, 'children') else str(target_child)
                        elif hasattr(target_child, 'value'):
                            target = str(target_child.value).strip('"\'')
                        else:
                            target = self._get_identifier(target_child) if hasattr(target_child, 'value') else str(target_child)
                elif hasattr(child, 'value') and child.value == 'VIA' and i + 1 < len(node.children):
                    next_child = node.children[i + 1]
                    if hasattr(next_child, 'value') and next_child.value == 'EDGES' and i + 2 < len(node.children):
                        # Parse edge list: ["RELATED_TO","MENTIONS"] or identifier_list
                        edges_node = node.children[i + 2]
                        if hasattr(edges_node, 'data'):
                            if edges_node.data == 'identifier_list':
                                for edge_child in edges_node.children:
                                    if hasattr(edge_child, 'data') and edge_child.data == 'identifier':
                                        via_edges.append(self._get_identifier(edge_child))
                            elif edges_node.data == 'string_list':
                                # Handle string list format
                                for edge_child in edges_node.children:
                                    if hasattr(edge_child, 'data'):
                                        if edge_child.data == 'string':
                                            edge_name = self._get_string(edge_child)
                                        elif edge_child.data == 'identifier':
                                            edge_name = self._get_identifier(edge_child)
                                        else:
                                            edge_name = str(edge_child)
                                    else:
                                        edge_name = str(edge_child)
                                    via_edges.append(edge_name.strip('"\''))
                            elif edges_node.data == 'list':
                                # Generic list format
                                for edge_child in edges_node.children:
                                    if hasattr(edge_child, 'data'):
                                        if edge_child.data == 'string':
                                            via_edges.append(self._get_string(edge_child).strip('"\''))
                                        elif edge_child.data == 'identifier':
                                            via_edges.append(self._get_identifier(edge_child))
                                    elif hasattr(edge_child, 'value'):
                                        via_edges.append(str(edge_child.value).strip('"\''))
            
            if not has_from_to:
                # Parse SRC/DEST format: SHORTEST PATH SRC "Alice" DEST "Bob" [MAX_DEPTH number] [WEIGHT_FIELD field]
                # If we have exactly 2 children and they are strings, assume they are source and target
                if len(node.children) == 2:
                    source = self._get_identifier(node.children[0])
                    target = self._get_identifier(node.children[1])
                else:
                    # Parse by looking for SRC/DEST/MAX_DEPTH/WEIGHT_FIELD tokens
                    for i, child in enumerate(node.children):
                        if hasattr(child, 'value') and child.value == 'SRC':
                            if i + 1 < len(node.children):
                                source = self._get_identifier(node.children[i + 1])
                        elif hasattr(child, 'value') and child.value == 'DEST':
                            if i + 1 < len(node.children):
                                target = self._get_identifier(node.children[i + 1])
                        elif hasattr(child, 'value') and child.value == 'WEIGHT':
                            if i + 1 < len(node.children):
                                weight_attribute = self._get_identifier(node.children[i + 1])
                        elif hasattr(child, 'value') and child.value == 'MAX_DEPTH':
                            if i + 1 < len(node.children):
                                max_depth = self._get_number(node.children[i + 1])
                        elif hasattr(child, 'value') and child.value == 'WEIGHT_FIELD':
                            if i + 1 < len(node.children):
                                weight_attribute = self._get_identifier(node.children[i + 1])
        
        params = {
            'source': source,
            'target': target,
            'weight_attribute': weight_attribute,
            'max_depth': max_depth
        }
        if via_edges:
            params['via_edges'] = via_edges
        
        return AIQLNode(
            node_type=AIQLNodeType.SHORTEST_PATH,
            parameters=params
        )
    
    def _parse_community_stage(self, node) -> AIQLNode:
        """Parse COMMUNITY_DETECTION stage."""
        # community_stage: "COMMUNITY_DETECTION" ("ALGORITHM" identifier)? ("ON" identifier)?
        algorithm = 'louvain'  # Default algorithm
        target_graph = None
        
        for i, child in enumerate(node.children):
            if hasattr(child, 'value') and child.value == 'ALGORITHM':
                if i + 1 < len(node.children):
                    algorithm = self._get_identifier(node.children[i + 1])
            elif hasattr(child, 'value') and child.value == 'ON':
                if i + 1 < len(node.children):
                    target_graph = self._get_identifier(node.children[i + 1])
        
        return AIQLNode(
            node_type=AIQLNodeType.COMMUNITY_DETECTION,
            parameters={
                'algorithm': algorithm,
                'target_graph': target_graph
            }
        )
    
    def _get_number(self, node) -> Union[int, float]:
        """Extract number from node."""
        if hasattr(node, 'value'):
            try:
                if '.' in str(node.value):
                    return float(node.value)
                else:
                    return int(node.value)
            except ValueError:
                return 0
        return 0
    
    def _parse_parameter_list(self, node) -> Dict[str, Any]:
        """Parse parameter list."""
        parameters = {}
        for child in node.children:
            if child.data == 'parameter':
                key = self._get_identifier(child.children[0])
                value = self._parse_value(child.children[1])
                parameters[key] = value
        return parameters
    
    
    def _parse_delete_stage(self, node) -> AIQLNode:
        """Parse DELETE stage (DELETE NODE or DELETE EDGE)."""
        inner_stage = node.children[0]
        stage_type = inner_stage.data
        
        if stage_type == 'delete_node':
            return self._parse_delete_node(inner_stage)
        elif stage_type == 'delete_edge':
            return self._parse_delete_edge(inner_stage)
        else:
            return None
    
    def _parse_delete_node(self, node) -> AIQLNode:
        """Parse DELETE NODE statement."""
        # Check if this is DELETE ALL NODES (no children means it's the ALL NODES case)
        if len(node.children) == 0:
            return AIQLNode(
                node_type=AIQLNodeType.DELETE_NODE,
                parameters={'node_type': 'ALL'}
            )
        
        # Check if this is DELETE ALL NODES with explicit children
        # Look for "ALL" and "NODES" tokens in children
        is_all_nodes = False
        where_conditions = None
        start_idx = 0
        
        if len(node.children) >= 2:
            first_child = node.children[0]
            second_child = node.children[1]
            
            # Check for "ALL" token followed by "NODES"
            if (hasattr(first_child, 'value') and first_child.value == 'ALL' and
                hasattr(second_child, 'value') and second_child.value == 'NODES'):
                is_all_nodes = True
                start_idx = 2  # Skip ALL and NODES tokens
        
        if is_all_nodes:
            # Parse WHERE clause if present (for DELETE ALL NODES WHERE ...)
            for i in range(start_idx, len(node.children)):
                child = node.children[i]
                if hasattr(child, 'data') and child.data == 'condition':
                    where_conditions = [self._parse_condition(child)]
                    break
            
            return AIQLNode(
                node_type=AIQLNodeType.DELETE_NODE,
                parameters={'node_type': 'ALL', 'where': where_conditions} if where_conditions else {'node_type': 'ALL'}
            )
        
        # Regular DELETE NODE with specific node type
        if len(node.children) == 0:
            # This shouldn't happen with valid grammar, but handle gracefully
            return AIQLNode(
                node_type=AIQLNodeType.DELETE_NODE,
                parameters={'node_type': 'UNKNOWN'}
            )
        
        node_type = self._get_identifier(node.children[0])
        params = {'node_type': node_type}
        
        # Parse WHERE clause if present
        if len(node.children) > 1:
            where_node = node.children[1]
            if hasattr(where_node, 'data') and where_node.data == 'condition':
                params['where'] = [self._parse_condition(where_node)]  # Wrap in list
        
        return AIQLNode(
            node_type=AIQLNodeType.DELETE_NODE,
            parameters=params
        )
    
    def _parse_delete_edge(self, node) -> AIQLNode:
        """Parse DELETE EDGE statement."""
        edge_type = self._get_identifier(node.children[0])
        params = {'edge_type': edge_type}
        
        # Check for SRC/DEST syntax (DELETE EDGE edge_type SRC source DEST target)
        source_node = None
        target_node = None
        where_conditions = []
        
        # Look for SRC and DEST tokens
        for i, child in enumerate(node.children):
            if hasattr(child, 'value'):
                if child.value == 'SRC' and i + 1 < len(node.children):
                    source_node = self._get_identifier(node.children[i + 1])
                    params['source_node'] = source_node
                elif child.value == 'DEST' and i + 1 < len(node.children):
                    target_node = self._get_identifier(node.children[i + 1])
                    params['target_node'] = target_node
        
        # If no SRC/DEST found, parse WHERE clause
        if source_node is None and target_node is None:
            if len(node.children) > 1:
                where_node = node.children[1]
                if hasattr(where_node, 'data'):
                    if where_node.data == 'condition':
                        where_conditions = [self._parse_condition(where_node)]
                    elif where_node.data == 'where_clause':
                        # Parse multiple conditions
                        for cond_node in where_node.children:
                            if hasattr(cond_node, 'data') and cond_node.data == 'condition':
                                where_conditions.append(self._parse_condition(cond_node))
                elif isinstance(where_node, list):
                    # Already a list of conditions
                    where_conditions = where_node
                else:
                    # Try to parse as condition
                    try:
                        where_conditions = [self._parse_condition(where_node)]
                    except:
                        pass
        
        if where_conditions:
            params['where'] = where_conditions
        
        return AIQLNode(
            node_type=AIQLNodeType.DELETE_EDGE,
            parameters=params
        )
    
    def _parse_update_stage(self, node) -> AIQLNode:
        """Parse UPDATE stage (UPDATE NODE or UPDATE EDGE)."""
        inner_stage = node.children[0]
        stage_type = inner_stage.data
        
        if stage_type == 'update_node':
            return self._parse_update_node(inner_stage)
        elif stage_type == 'update_edge':
            return self._parse_update_edge(inner_stage)
        else:
            return None
    
    def _parse_update_node(self, node) -> AIQLNode:
        """Parse UPDATE NODE statement."""
        node_type = self._get_identifier(node.children[0])
        params = {'node_type': node_type}
        
        # Parse SET clause (update_assignment_list)
        if len(node.children) > 1:
            set_node = node.children[1]
            if hasattr(set_node, 'data') and set_node.data == 'update_assignment_list':
                params['set'] = self._parse_update_assignment_list(set_node)
        
        # Parse WHERE clause if present
        if len(node.children) > 2:
            where_node = node.children[2]
            if hasattr(where_node, 'data') and where_node.data == 'condition':
                params['where'] = [self._parse_condition(where_node)]  # Wrap in list
        
        return AIQLNode(
            node_type=AIQLNodeType.UPDATE_NODE,
            parameters=params
        )
    
    def _parse_update_edge(self, node) -> AIQLNode:
        """Parse UPDATE EDGE statement."""
        edge_type = self._get_identifier(node.children[0])
        params = {'edge_type': edge_type}
        
        # Parse SET clause (update_assignment_list)
        if len(node.children) > 1:
            set_node = node.children[1]
            if hasattr(set_node, 'data') and set_node.data == 'update_assignment_list':
                params['set'] = self._parse_update_assignment_list(set_node)
        
        # Parse WHERE clause if present
        if len(node.children) > 2:
            where_node = node.children[2]
            if hasattr(where_node, 'data') and where_node.data == 'condition':
                params['where'] = [self._parse_condition(where_node)]  # Wrap in list
        
        return AIQLNode(
            node_type=AIQLNodeType.UPDATE_EDGE,
            parameters=params
        )
    
    def _parse_update_assignment_list(self, node) -> Dict[str, Any]:
        """Parse update_assignment_list: property = value, property = value, ..."""
        assignments = {}
        for child in node.children:
            if hasattr(child, 'data') and child.data == 'update_assignment':
                # update_assignment: identifier "=" value
                if len(child.children) >= 2:
                    prop_name = self._get_identifier(child.children[0])
                    prop_value = self._parse_value(child.children[1])
                    assignments[prop_name] = prop_value
        return assignments
    
    def _parse_generation_stage(self, node) -> AIQLNode:
        """Parse GENERATION stage."""
        inner_stage = node.children[0]
        stage_type = inner_stage.data
        
        if stage_type == 'generate_with_model':
            return self._parse_generate_with_model(inner_stage)
        elif stage_type == 'generate_with_prompt':
            return self._parse_generate_with_prompt(inner_stage)
        else:
            return None
    
    def _parse_generate_with_model(self, node) -> AIQLNode:
        """Parse GENERATE USING model PROMPT prompt_name."""
        model = self._get_identifier(node.children[0])
        prompt = self._get_identifier(node.children[1])
        params = {'model': model, 'prompt': prompt}
        
        # Parse WITH context if present
        if len(node.children) > 2:
            context_node = node.children[2]
            if hasattr(context_node, 'data') and context_node.data == 'context_spec':
                params['context'] = self._parse_context_spec(context_node)
        
        return AIQLNode(
            node_type=AIQLNodeType.GENERATE_WITH_MODEL,
            parameters=params
        )
    
    def _parse_generate_with_prompt(self, node) -> AIQLNode:
        """Parse GENERATE USING model PROMPT prompt_string."""
        model = self._get_identifier(node.children[0])
        prompt = self._get_string(node.children[1])
        params = {'model': model, 'prompt': prompt}
        
        # Parse WITH context if present
        if len(node.children) > 2:
            context_node = node.children[2]
            if hasattr(context_node, 'data') and context_node.data == 'context_spec':
                params['context'] = self._parse_context_spec(context_node)
        
        return AIQLNode(
            node_type=AIQLNodeType.GENERATE_WITH_PROMPT,
            parameters=params
        )
    
    def _parse_context_spec(self, node) -> Dict[str, Any]:
        """Parse context specification."""
        context = {}
        for child in node.children:
            if child.data == 'context_assignment':
                key = self._get_identifier(child.children[0])
                value = self._parse_value(child.children[1])
                context[key] = value
        return context
    
    def _parse_aggregation_stage(self, node) -> AIQLNode:
        """Parse AGGREGATION stage."""
        inner_stage = node.children[0]
        stage_type = inner_stage.data
        
        if stage_type == 'aggregation_query':
            return self._parse_aggregation_query(inner_stage)
        elif stage_type == 'group_by_query':
            return self._parse_group_by_query(inner_stage)
        elif stage_type == 'having_query':
            return self._parse_having_query(inner_stage)
        else:
            return None
    
    def _parse_aggregation_query(self, node) -> AIQLNode:
        """Parse aggregation query."""
        # This is similar to SELECT but focused on aggregation functions
        return self._parse_select(node)
    
    def _parse_group_by_query(self, node) -> AIQLNode:
        """Parse GROUP BY query."""
        return self._parse_select(node)
    
    def _parse_having_query(self, node) -> AIQLNode:
        """Parse HAVING query."""
        return self._parse_select(node)
    
    def _parse_count_stage(self, node) -> AIQLNode:
        """Parse COUNT stage."""
        identifier = self._get_identifier(node.children[0])
        
        # Check for WHERE clause
        where_conditions = None
        if len(node.children) > 1:
            for child in node.children[1:]:
                if hasattr(child, 'data') and child.data == 'condition':
                    parsed_condition = self._parse_condition(child)
                    # Ensure where_conditions is a list for COUNT operator
                    if isinstance(parsed_condition, list):
                        where_conditions = parsed_condition
                    elif isinstance(parsed_condition, dict):
                        where_conditions = [parsed_condition]
                    else:
                        where_conditions = []
                    break
        
        return AIQLNode(
            node_type=AIQLNodeType.COUNT,
            parameters={
                'identifier': identifier,
                'where_conditions': where_conditions
            }
        )

    def _parse_global_strategy(self, node) -> AIQLNode:
        """Parse global strategy declaration."""
        strategy = self._get_identifier(node.children[0])
        return AIQLNode(
            node_type=AIQLNodeType.GLOBAL_STRATEGY,
            parameters={'strategy': strategy}
        )

    # Enhanced RAG Pipeline Parser Methods
    def _parse_rag_pipeline_stage(self, node) -> AIQLNode:
        """Parse RAG PIPELINE stage."""
        pipeline_name = self._get_string(node.children[0])
        pipeline_body = node.children[1]
        
        # Parse pipeline body stages
        stages = []
        for child in pipeline_body.children:
            if hasattr(child, 'data') and child.data == 'rag_stage':
                stage_node = self._parse_stage(child)
                if stage_node:
                    stages.append(stage_node)
        
        return AIQLNode(
            node_type=AIQLNodeType.RAG_PIPELINE,
            parameters={
                'pipeline_name': pipeline_name,
                'stages': stages
            }
        )

    def _parse_file_reader_stage(self, node) -> AIQLNode:
        """Parse file-type-based reader stage."""
        reader_strategy = self._get_identifier(node.children[0])
        file_types = []
        
        # Parse file types if present
        if len(node.children) > 1:
            file_types_node = node.children[1]
            if hasattr(file_types_node, 'data') and file_types_node.data == 'file_types':
                for child in file_types_node.children:
                    if hasattr(child, 'data') and child.data == 'file_type':
                        file_types.append(self._get_identifier(child))
        
        return AIQLNode(
            node_type=AIQLNodeType.FILE_READER,
            parameters={
                'reader_strategy': reader_strategy,
                'file_types': file_types
            }
        )

    def _parse_multimodal_extraction_stage(self, node) -> AIQLNode:
        """Parse enhanced multimodal extraction stage."""
        extraction_types = []
        extraction_config = {}
        
        # Parse extraction types
        if len(node.children) > 0:
            types_node = node.children[0]
            if hasattr(types_node, 'data') and types_node.data == 'extraction_types':
                for child in types_node.children:
                    if hasattr(child, 'data') and child.data == 'extraction_type':
                        extraction_types.append(self._get_identifier(child))
        
        # Parse extraction config if present
        if len(node.children) > 1:
            config_node = node.children[1]
            if hasattr(config_node, 'data') and config_node.data == 'extraction_config':
                extraction_config = self._parse_parameter_list(config_node)
        
        return AIQLNode(
            node_type=AIQLNodeType.MULTIMODAL_EXTRACTION,
            parameters={
                'extraction_types': extraction_types,
                'extraction_config': extraction_config
            }
        )

    def _parse_smart_chunking_stage(self, node) -> AIQLNode:
        """Parse enhanced smart chunking stage."""
        chunking_strategy = self._get_identifier(node.children[0])
        chunking_params = {}
        chunking_config = {}
        
        # Parse chunking parameters if present
        if len(node.children) > 1:
            params_node = node.children[1]
            if hasattr(params_node, 'data') and params_node.data == 'chunking_params':
                chunking_params = self._parse_parameter_list(params_node)
        
        # Parse chunking config if present
        if len(node.children) > 2:
            config_node = node.children[2]
            if hasattr(config_node, 'data') and config_node.data == 'chunking_config':
                chunking_config = self._parse_parameter_list(config_node)
        
        return AIQLNode(
            node_type=AIQLNodeType.SMART_CHUNKING,
            parameters={
                'chunking_strategy': chunking_strategy,
                'chunking_params': chunking_params,
                'chunking_config': chunking_config
            }
        )

    def _parse_retrieval_system_stage(self, node) -> AIQLNode:
        """Parse retrieval system stage."""
        retrieval_config = {}
        
        # Parse retrieval config if present
        if len(node.children) > 0:
            config_node = node.children[0]
            if hasattr(config_node, 'data') and config_node.data == 'retrieval_config':
                retrieval_config = self._parse_parameter_list(config_node)
        
        return AIQLNode(
            node_type=AIQLNodeType.RETRIEVAL_SYSTEM,
            parameters={
                'retrieval_config': retrieval_config
            }
        )

    def _parse_evaluation_stage(self, node) -> AIQLNode:
        """Parse evaluation system stage."""
        evaluation_config = {}
        
        # Parse evaluation config if present
        if len(node.children) > 0:
            config_node = node.children[0]
            if hasattr(config_node, 'data') and config_node.data == 'evaluation_config':
                evaluation_config = self._parse_parameter_list(config_node)
        
        return AIQLNode(
            node_type=AIQLNodeType.EVALUATION_SYSTEM,
            parameters={
                'evaluation_config': evaluation_config
            }
        )

    def _parse_pipeline_management_stage(self, node) -> AIQLNode:
        """Parse pipeline management stage."""
        # Determine the type of management operation
        if len(node.children) > 0:
            first_child = node.children[0]
            if hasattr(first_child, 'value'):
                operation = first_child.value
                
                if operation == 'LOAD':
                    pipeline_name = self._get_string(node.children[1])
                    return AIQLNode(
                        node_type=AIQLNodeType.PIPELINE_MANAGEMENT,
                        parameters={
                            'operation': 'LOAD',
                            'pipeline_name': pipeline_name
                        }
                    )
                elif operation == 'REPRODUCE':
                    pipeline_name = self._get_string(node.children[1])
                    return AIQLNode(
                        node_type=AIQLNodeType.PIPELINE_MANAGEMENT,
                        parameters={
                            'operation': 'REPRODUCE',
                            'pipeline_name': pipeline_name
                        }
                    )
                elif operation == 'SHOW':
                    return AIQLNode(
                        node_type=AIQLNodeType.PIPELINE_MANAGEMENT,
                        parameters={
                            'operation': 'SHOW_METADATA'
                        }
                    )
                elif operation == 'EVALUATE':
                    pipeline_name = self._get_string(node.children[1])
                    return AIQLNode(
                        node_type=AIQLNodeType.PIPELINE_MANAGEMENT,
                        parameters={
                            'operation': 'EVALUATE',
                            'pipeline_name': pipeline_name
                        }
                    )
                elif operation == 'COMPARE':
                    pipeline_names = []
                    for i in range(1, len(node.children)):
                        pipeline_names.append(self._get_string(node.children[i]))
                    return AIQLNode(
                        node_type=AIQLNodeType.PIPELINE_MANAGEMENT,
                        parameters={
                            'operation': 'COMPARE',
                            'pipeline_names': pipeline_names
                        }
                    )
        
        return AIQLNode(
            node_type=AIQLNodeType.PIPELINE_MANAGEMENT,
            parameters={
                'operation': 'UNKNOWN'
            }
        )

    def _parse_run_pipeline_cmd(self, node) -> AIQLNode:
        """Parse RUN PIPELINE command (enhanced syntax)."""
        pipeline_name = self._get_identifier(node.children[0])
        
        # Parse enhanced options: DRY_RUN, LAZY, FROM STEP, RESTART STEP, STEPS, OVERRIDE PARAMETERS, etc.
        dry_run = False
        lazy = False
        from_step = None
        restart_step = None
        step_range = None
        override_parameters = {}
        parameters = {}
        checkpoint = None
        run_id = None
        
        # #region agent log
        import json
        try:
            with open(r'c:\qgraph\qgraph-app\.cursor\debug.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps({
                    'sessionId': 'debug-session',
                    'runId': 'parse',
                    'hypothesisId': 'G',
                    'location': 'aiql_parser.py:8704',
                    'message': '_parse_run_pipeline_cmd entry',
                    'data': {
                        'pipeline_name': pipeline_name,
                        'children_count': len(node.children),
                        'children_info': [{'idx': i, 'has_value': hasattr(c, 'value'), 'value': getattr(c, 'value', None), 'data': getattr(c, 'data', None)} for i, c in enumerate(node.children[:10])]
                    },
                    'timestamp': int(__import__('time').time() * 1000)
                }) + '\n')
        except: pass
        # #endregion
        
        # #region agent log
        import json
        try:
            with open(r'c:\qgraph\qgraph-app\.cursor\debug.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps({
                    'sessionId': 'debug-session',
                    'runId': 'parse',
                    'hypothesisId': 'G',
                    'location': 'aiql_parser.py:8719',
                    'message': '_parse_run_pipeline_cmd entry',
                    'data': {
                        'pipeline_name': pipeline_name,
                        'children_count': len(node.children),
                        'children_info': [{'idx': i, 'has_value': hasattr(c, 'value'), 'value': getattr(c, 'value', None), 'has_data': hasattr(c, 'data'), 'data': getattr(c, 'data', None), 'type': getattr(c, 'type', None)} for i, c in enumerate(node.children[:15])]
                    },
                    'timestamp': int(__import__('time').time() * 1000)
                }) + '\n')
        except: pass
        # #endregion
        
        i = 1
        while i < len(node.children):
            child = node.children[i]
            # Check if this is run_pipeline_options (Tree node with data)
            # Grammar: run_pipeline_options: run_pipeline_option*
            # run_pipeline_option can be: "FROM" "STEP" identifier
            if hasattr(child, 'data') and child.data == 'run_pipeline_options':
                # Parse options from within run_pipeline_options
                # Each option is a run_pipeline_option node
                # #region agent log
                try:
                    with open(r'c:\qgraph\qgraph-app\.cursor\debug.log', 'a', encoding='utf-8') as f:
                        f.write(json.dumps({
                            'sessionId': 'debug-session',
                            'runId': 'parse',
                            'hypothesisId': 'G',
                            'location': 'aiql_parser.py:8740',
                            'message': 'Found run_pipeline_options node',
                            'data': {
                                'options_children_count': len(child.children),
                                'options_children': [{'idx': j, 'has_value': hasattr(oc, 'value'), 'value': getattr(oc, 'value', None), 'has_data': hasattr(oc, 'data'), 'data': getattr(oc, 'data', None), 'type': getattr(oc, 'type', None)} for j, oc in enumerate(child.children[:10])]
                            },
                            'timestamp': int(__import__('time').time() * 1000)
                        }) + '\n')
                except: pass
                # #endregion
                
                # Iterate through run_pipeline_option nodes
                # Grammar: run_pipeline_options: run_pipeline_option*
                # run_pipeline_option can be a Tree node with data="run_pipeline_option" or direct tokens
                opt_idx = 0
                while opt_idx < len(child.children):
                    opt_child = child.children[opt_idx]
                    
                    # #region agent log
                    try:
                        with open(r'c:\qgraph\qgraph-app\.cursor\debug.log', 'a', encoding='utf-8') as f:
                            f.write(json.dumps({
                                'sessionId': 'debug-session',
                                'runId': 'parse',
                                'hypothesisId': 'G',
                                'location': 'aiql_parser.py:8787',
                                'message': 'Processing run_pipeline_option',
                                'data': {
                                    'opt_idx': opt_idx,
                                    'opt_child_has_value': hasattr(opt_child, 'value'),
                                    'opt_child_value': getattr(opt_child, 'value', None),
                                    'opt_child_has_data': hasattr(opt_child, 'data'),
                                    'opt_child_data': getattr(opt_child, 'data', None),
                                    'opt_child_children_count': len(opt_child.children) if hasattr(opt_child, 'children') else 0
                                },
                                'timestamp': int(__import__('time').time() * 1000)
                            }) + '\n')
                    except: pass
                    # #endregion
                    
                    # Check if this is a Tree node with data="run_pipeline_option" containing "FROM STEP identifier"
                    if hasattr(opt_child, 'data'):
                        # Could be a Tree node containing the FROM STEP sequence
                        if hasattr(opt_child, 'children') and len(opt_child.children) >= 3:
                            # Check if first child is FROM, second is STEP, third is identifier
                            first = opt_child.children[0] if len(opt_child.children) > 0 else None
                            second = opt_child.children[1] if len(opt_child.children) > 1 else None
                            third = opt_child.children[2] if len(opt_child.children) > 2 else None
                            
                            if (first and hasattr(first, 'value') and first.value == 'FROM' and
                                second and hasattr(second, 'value') and second.value == 'STEP' and
                                third):
                                from_step = self._get_identifier(third)
                                # #region agent log
                                try:
                                    with open(r'c:\qgraph\qgraph-app\.cursor\debug.log', 'a', encoding='utf-8') as f:
                                        f.write(json.dumps({
                                            'sessionId': 'debug-session',
                                            'runId': 'parse',
                                            'hypothesisId': 'G',
                                            'location': 'aiql_parser.py:8810',
                                            'message': 'Extracted from_step from Tree node',
                                            'data': {'from_step': from_step},
                                            'timestamp': int(__import__('time').time() * 1000)
                                        }) + '\n')
                                except: pass
                                # #endregion
                                opt_idx += 1
                                continue
                    
                    # Check if this is a direct "FROM STEP identifier" sequence
                    if hasattr(opt_child, 'value') and opt_child.value == 'FROM':
                        if opt_idx + 2 < len(child.children):
                            next_opt = child.children[opt_idx + 1]
                            next_next_opt = child.children[opt_idx + 2]
                            if hasattr(next_opt, 'value') and next_opt.value == 'STEP':
                                # Extract the step identifier
                                from_step = self._get_identifier(next_next_opt)
                                # #region agent log
                                try:
                                    with open(r'c:\qgraph\qgraph-app\.cursor\debug.log', 'a', encoding='utf-8') as f:
                                        f.write(json.dumps({
                                            'sessionId': 'debug-session',
                                            'runId': 'parse',
                                            'hypothesisId': 'G',
                                            'location': 'aiql_parser.py:8830',
                                            'message': 'Extracted from_step from direct sequence',
                                            'data': {'from_step': from_step},
                                            'timestamp': int(__import__('time').time() * 1000)
                                        }) + '\n')
                                except: pass
                                # #endregion
                                opt_idx += 3
                                continue
                    
                    # Check for other options
                    if hasattr(opt_child, 'value'):
                        opt_val = opt_child.value
                        if opt_val == 'DRY_RUN':
                            dry_run = True
                        elif opt_val == 'LAZY':
                            lazy = True
                    opt_idx += 1
                i += 1
            elif hasattr(child, 'value'):
                if child.value == 'DRY_RUN':
                    dry_run = True
                    i += 1
                elif child.value == 'LAZY':
                    lazy = True
                    i += 1
                elif child.value == 'FROM' and i + 2 < len(node.children):
                    # #region agent log
                    try:
                        with open(r'c:\qgraph\qgraph-app\.cursor\debug.log', 'a', encoding='utf-8') as f:
                            f.write(json.dumps({
                                'sessionId': 'debug-session',
                                'runId': 'parse',
                                'hypothesisId': 'G',
                                'location': 'aiql_parser.py:8760',
                                'message': 'Found FROM token (direct child)',
                                'data': {
                                    'i': i,
                                    'next_child_has_value': hasattr(node.children[i+1], 'value') if i+1 < len(node.children) else False,
                                    'next_child_value': node.children[i+1].value if i+1 < len(node.children) and hasattr(node.children[i+1], 'value') else None,
                                    'next_next_child': str(node.children[i+2])[:50] if i+2 < len(node.children) else None
                                },
                                'timestamp': int(__import__('time').time() * 1000)
                            }) + '\n')
                    except: pass
                    # #endregion
                    if hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'STEP':
                        from_step = self._get_identifier(node.children[i + 2])
                        # #region agent log
                        try:
                            with open(r'c:\qgraph\qgraph-app\.cursor\debug.log', 'a', encoding='utf-8') as f:
                                f.write(json.dumps({
                                    'sessionId': 'debug-session',
                                    'runId': 'parse',
                                    'hypothesisId': 'G',
                                    'location': 'aiql_parser.py:8775',
                                    'message': 'Extracted from_step (direct)',
                                    'data': {'from_step': from_step},
                                    'timestamp': int(__import__('time').time() * 1000)
                                }) + '\n')
                        except: pass
                        # #endregion
                        i += 3
                    elif hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'CHECKPOINT':
                        checkpoint = self._get_identifier(node.children[i + 2])
                        i += 3
                    else:
                        i += 1
                elif child.value == 'RESTART' and i + 2 < len(node.children):
                    if hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'STEP':
                        restart_step = self._get_identifier(node.children[i + 2])
                        i += 3
                    else:
                        i += 1
                elif child.value == 'STEPS' and i + 1 < len(node.children):
                    steps_node = node.children[i + 1]
                    if hasattr(steps_node, 'data') and steps_node.data == 'step_range':
                        # Parse step_range: identifier -> identifier
                        if len(steps_node.children) >= 1:
                            start_step = self._get_identifier(steps_node.children[0])
                            if len(steps_node.children) >= 3 and hasattr(steps_node.children[1], 'value') and steps_node.children[1].value == '->':
                                end_step = self._get_identifier(steps_node.children[2])
                                step_range = (start_step, end_step)
                            else:
                                step_range = (start_step, None)
                    i += 2
                elif child.value == 'OVERRIDE' and i + 2 < len(node.children):
                    if hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'PARAMETERS':
                        param_node = node.children[i + 2]
                        if hasattr(param_node, 'data') and param_node.data == 'parameter_dict':
                            override_parameters = self._extract_parameter_dict(param_node)
                        i += 3
                    else:
                        i += 1
                elif child.value == 'PARAMETERS' and i + 1 < len(node.children):
                    param_node = node.children[i + 1]
                    if hasattr(param_node, 'data') and param_node.data == 'parameter_dict':
                        parameters = self._extract_parameter_dict(param_node)
                    i += 2
                elif child.value == 'WITH' and i + 2 < len(node.children):
                    if hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'PARAMETERS':
                        param_node = node.children[i + 2]
                        if hasattr(param_node, 'data') and param_node.data == 'parameters_block':
                            parameters = self._parse_parameters_block(param_node)
                        i += 3
                    else:
                        i += 1
                elif child.value == 'AS' and i + 1 < len(node.children):
                    run_id = self._get_string(node.children[i + 1])
                    i += 2
                else:
                    i += 1
            elif hasattr(child, 'data'):
                if child.data == 'step_list':
                    # Legacy step_list support
                    selected_steps = []
                    for step_node in child.children:
                        if hasattr(step_node, 'value'):
                            selected_steps.append(step_node.value)
                    i += 1
                else:
                    i += 1
            else:
                i += 1
        
        return AIQLNode(
            node_type=AIQLNodeType.RUN_PIPELINE,
            parameters={
                'pipeline_name': pipeline_name,
                'dry_run': dry_run,
                'lazy': lazy,
                'from_step': from_step,
                'restart_step': restart_step,
                'step_range': step_range,
                'override_parameters': override_parameters,
                'parameters': parameters,
                'checkpoint': checkpoint,
                'run_id': run_id
            }
        )
    
    def _parse_classify_file_cmd(self, node) -> AIQLNode:
        """Parse CLASSIFY FILE command."""
        file_path = self._get_string(node.children[0]) if node.children else ""
        return AIQLNode(
            node_type=AIQLNodeType.CLASSIFY_FILE,
            parameters={"file_path": file_path}
        )

    def _parse_create_multimodal_pipeline(self, node) -> AIQLNode:
        """Parse CREATE PIPELINE for multimodal ingestion (enhanced syntax with EXECUTION_MODE support)."""
        logger.info("Parsing CREATE PIPELINE (enhanced syntax with execution planning)...")
        
        # Extract pipeline name (first identifier)
        pipeline_name = self._get_identifier(node.children[0])
        
        # Debug: Log all children to understand structure (only in debug mode)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"[PARSE DEBUG] CREATE PIPELINE node has {len(node.children)} children")
            for idx, child in enumerate(node.children):
                child_value = getattr(child, 'value', None)
                child_data = getattr(child, 'data', None)
                child_type = getattr(child, 'type', None)
                logger.debug(f"[PARSE DEBUG] Child {idx}: value={child_value}, data={child_data}, type={child_type}, class={type(child).__name__}")
        
        # Parse optional IN NAMESPACE, PARAMETERS, STAGES, EXECUTION_MODE, etc.
        namespace = None
        namespace_is_variable = False
        parameters = {}
        stages = []
        source_collection = None
        target_collection = None
        description = None
        execution_mode = 'auto'  # Default execution mode (AUTO for automatic optimization)
        checkpoint_policy = 'disabled'  # Default checkpoint policy
        
        # The grammar parses "IN NAMESPACE identifier" as optional, so the structure might be:
        # - Child 0: pipeline name (identifier)
        # - Child 1: namespace (identifier) - if "IN NAMESPACE" is present
        # - Child 2: source collection (identifier) - if "SOURCE COLLECTION" is present
        # - Child 3: target collection (identifier) - if "TARGET COLLECTION" is present
        # - Child 4: description (string) - if "DESCRIPTION" is present
        # - Child 5: stages (enhanced_stage_list) - if "STAGES" is present
        
        # Try to identify children by position and content
        # Since the grammar makes everything optional, we need to infer from position
        # and check token values to determine what each child represents
        
        i = 1
        while i < len(node.children):
            child = node.children[i]
            child_data = getattr(child, 'data', None)
            
            # Check if this child could be the namespace
            # If it's an identifier and we haven't found namespace yet, it might be it
            if child_data == 'identifier' and namespace is None:
                # Get token value to check
                if child.children and len(child.children) > 0:
                    token = child.children[0]
                    token_value = getattr(token, 'value', None)
                    # Check if this looks like a namespace (not a keyword)
                    if token_value and token_value not in ['IN', 'NAMESPACE', 'SOURCE', 'TARGET', 'COLLECTION', 'DESCRIPTION', 'STAGES', 'EXECUTION_MODE']:
                        # This could be the namespace - but we need to check if "IN NAMESPACE" precedes it
                        # Since the grammar collapses "IN NAMESPACE identifier", if this is child 1 (right after pipeline name),
                        # it's likely the namespace
                        if i == 1:  # First identifier after pipeline name is likely namespace
                            namespace = self._get_identifier(child)
                            namespace_is_variable = False
                            logger.info(f"[PARSE DEBUG] Extracted namespace from position {i}: {namespace}")
                            i += 1
                            continue
            
            # Check for SOURCE COLLECTION - if namespace is found, next identifier after it is likely source_collection
            if child_data == 'identifier' and source_collection is None and namespace is not None:
                # If we've found namespace and this is the next identifier, it's likely source_collection
                # But we need to check if "SOURCE COLLECTION" keywords are present
                # Since grammar collapses them, check position: after namespace should be source_collection
                if i == 2:  # Second identifier after pipeline name (first is namespace)
                    token_value = None
                    if child.children and len(child.children) > 0:
                        token = child.children[0]
                        token_value = getattr(token, 'value', None)
                    # If it's not a keyword, it's likely the source collection name
                    if token_value and token_value not in ['SOURCE', 'COLLECTION', 'TARGET', 'DESCRIPTION', 'STAGES', 'EXECUTION_MODE']:
                        source_collection = self._get_identifier(child)
                        logger.info(f"[PARSE DEBUG] Extracted source_collection from position {i}: {source_collection}")
                        i += 1
                        continue
            
            # Check for TARGET COLLECTION - if source_collection is found, next identifier is likely target_collection
            if child_data == 'identifier' and target_collection is None and source_collection is not None:
                # If we've found source_collection and this is the next identifier, it's likely target_collection
                if i == 3:  # Third identifier after pipeline name (second is source_collection)
                    token_value = None
                    if child.children and len(child.children) > 0:
                        token = child.children[0]
                        token_value = getattr(token, 'value', None)
                    # If it's not a keyword, it's likely the target collection name
                    if token_value and token_value not in ['SOURCE', 'COLLECTION', 'TARGET', 'DESCRIPTION', 'STAGES', 'EXECUTION_MODE']:
                        target_collection = self._get_identifier(child)
                        logger.info(f"[PARSE DEBUG] Extracted target_collection from position {i}: {target_collection}")
                        i += 1
                        continue
            
            # Original check for Token objects (backward compatibility)
            if hasattr(child, 'value') and child.value == 'IN' and i + 2 < len(node.children):
                next_child = node.children[i+1]
                next_value = getattr(next_child, 'value', None)
                if hasattr(next_child, 'value') and next_child.value == 'NAMESPACE':
                    ns_node = node.children[i + 2]
                    if hasattr(ns_node, 'data') and ns_node.data == 'variable':
                        namespace = self._get_identifier(ns_node.children[0]) if ns_node.children else None
                        namespace_is_variable = True
                    else:
                        namespace = self._get_identifier(ns_node)
                        namespace_is_variable = False
                    logger.info(f"[PARSE DEBUG] Extracted namespace: {namespace} (is_variable: {namespace_is_variable})")
                    i += 3
                else:
                    i += 1
            elif hasattr(child, 'value') and child.value == 'PARAMETERS' and i + 1 < len(node.children):
                param_node = node.children[i + 1]
                if hasattr(param_node, 'data') and param_node.data == 'parameter_dict':
                    parameters = self._extract_parameter_dict(param_node)
                i += 2
            elif hasattr(child, 'value') and child.value == 'STAGES' and i + 2 < len(node.children):
                if hasattr(node.children[i+1], 'value') and node.children[i+1].value == '=':
                    stages_node = node.children[i + 2]
                    if hasattr(stages_node, 'data') and stages_node.data == 'enhanced_stage_list':
                        for stage_child in stages_node.children:
                            if hasattr(stage_child, 'data') and stage_child.data == 'enhanced_stage':
                                stage_data = self._parse_enhanced_stage(stage_child)
                                if stage_data:
                                    stages.append(stage_data)
                i += 3
            elif hasattr(child, 'value') and child.value == 'SOURCE' and i + 2 < len(node.children):
                # Legacy syntax: SOURCE COLLECTION
                if hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'COLLECTION':
                    source_collection = self._get_identifier(node.children[i + 2])
                    i += 3
                else:
                    i += 1
            elif hasattr(child, 'value') and child.value == 'TARGET' and i + 2 < len(node.children):
                # Legacy syntax: TARGET COLLECTION
                if hasattr(node.children[i+1], 'value') and node.children[i+1].value == 'COLLECTION':
                    target_collection = self._get_identifier(node.children[i + 2])
                    i += 3
                else:
                    i += 1
            elif hasattr(child, 'value') and child.value == 'DESCRIPTION' and i + 1 < len(node.children):
                description = self._get_string(node.children[i + 1])
                i += 2
            elif hasattr(child, 'value') and child.value == 'EXECUTION_MODE' and i + 1 < len(node.children):
                # NEW: Parse EXECUTION_MODE parameter
                execution_mode_node = node.children[i + 1]
                execution_mode = self._get_identifier(execution_mode_node).upper()
                # Validate execution mode
                valid_modes = ['AUTO', 'SEQUENTIAL', 'PARALLEL', 'HYBRID']
                if execution_mode not in valid_modes:
                    logger.warning(f"Unknown execution mode '{execution_mode}', defaulting to AUTO")
                    execution_mode = 'AUTO'
                logger.info(f"Parsed EXECUTION_MODE: {execution_mode}")
                i += 2
            elif hasattr(child, 'value') and child.value == 'CHECKPOINT' and i + 1 < len(node.children):
                # Parse CHECKPOINT POLICY enabled/disabled
                policy_node = node.children[i + 1]
                if hasattr(policy_node, 'value') and policy_node.value == 'POLICY':
                    if i + 2 < len(node.children):
                        policy_value_node = node.children[i + 2]
                        checkpoint_policy = self._get_identifier(policy_value_node).lower()
                        if checkpoint_policy in ['enabled', 'disabled']:
                            pipeline_params['checkpoint_policy'] = checkpoint_policy
                            logger.info(f"Parsed CHECKPOINT POLICY: {checkpoint_policy}")
                        i += 3
                    else:
                        i += 1
                else:
                    i += 1
            else:
                i += 1
        
        # Check for USING SCHEMA at pipeline level (legacy support)
        using_schema = None
        
        # Look for USING SCHEMA pattern in children (can appear before or after stages)
        for i, child in enumerate(node.children):
            # Check for USING SCHEMA pattern: USING followed by SCHEMA followed by string
            if hasattr(child, 'type'):
                child_val = ""
                if not hasattr(child, 'data') and hasattr(child, 'value'):
                    child_val = str(child.value).upper()
                elif hasattr(child, 'value'):
                    child_val = str(child.value).upper()
                if child_val == 'SCHEMA' and i > 0:
                    # Check previous for USING
                    prev_child = node.children[i-1]
                    prev_val = ""
                    if not hasattr(prev_child, 'data') and hasattr(prev_child, 'value'):
                        prev_val = str(prev_child.value).upper()
                    elif hasattr(prev_child, 'value'):
                        prev_val = str(prev_child.value).upper()
                    if prev_val == 'USING' and i + 1 < len(node.children):
                        schema_child = node.children[i + 1]
                        if hasattr(schema_child, 'type') and schema_child.type == 'STRING':
                            using_schema = self._get_string_value(schema_child)
                        elif hasattr(schema_child, 'value'):
                            using_schema = str(schema_child.value).strip('"').strip("'")
            elif hasattr(child, 'data') and child.data == 'enhanced_stage_list':
                for stage_child in child.children:
                    if hasattr(stage_child, 'data') and stage_child.data == 'enhanced_stage':
                        stage_data = self._parse_enhanced_stage(stage_child)
                        if stage_data:
                            stages.append(stage_data)
        
        # Also check parameters for schema
        params = {}
        for child in node.children:
            if hasattr(child, 'data') and child.data in ['parameter_dict', 'parameter_list']:
                params = self._parse_parameter_dict(child)
                if 'schema' in params or 'using_schema' in params:
                    using_schema = params.get('schema') or params.get('using_schema')
                    if isinstance(using_schema, str):
                        using_schema = using_schema.strip('"').strip("'")
        
        # Initialize checkpoint_policy variable
        checkpoint_policy = 'disabled'  # Default
        
        pipeline_params = {
            'pipeline_name': pipeline_name,
            'stages': stages,
            'pipeline_type': 'enhanced'
        }
        
        # Add optional fields
        if namespace:
            pipeline_params['namespace'] = namespace
            pipeline_params['namespace_is_variable'] = namespace_is_variable
        if parameters:
            pipeline_params['parameters'] = parameters
        if source_collection:
            pipeline_params['source_collection'] = source_collection
        if target_collection:
            pipeline_params['target_collection'] = target_collection
        if description:
            pipeline_params['description'] = description
        
        # Set checkpoint_policy (will be updated if found in parsing loop)
        pipeline_params['checkpoint_policy'] = checkpoint_policy
        
        # Add schema if found
        if using_schema:
            pipeline_params['using_schema'] = using_schema
            logger.info(f"Found pipeline-level schema: {using_schema}")
        
        # Add execution mode (always add, defaults to 'auto' for automatic optimization)
        pipeline_params['execution_mode'] = execution_mode.lower()
        logger.info(f"Pipeline execution mode: {execution_mode} (default: AUTO if not specified)")
        
        logger.info(f"Parsed multimodal pipeline: {pipeline_name} with {len(stages)} stages, execution_mode={execution_mode}")
        
        return AIQLNode(
            node_type=AIQLNodeType.CREATE_PIPELINE,
            parameters=pipeline_params
        )
    
    def _parse_enhanced_stage(self, node) -> Dict:
        """Parse an enhanced stage (with STEP prefix)."""
        stage_data = {}
        
        # Look for step_stage vs standalone_stage
        step_stage_node = None
        standalone_stage_node = None
        
        for child in node.children:
            if hasattr(child, 'data'):
                if child.data == 'step_stage':
                    step_stage_node = child
                elif child.data in ['enhanced_extract_stage', 'enhanced_chunk_stage', 'enhanced_embed_stage', 
                                    'enhanced_index_stage', 'enhanced_entity_extract_stage']:
                    standalone_stage_node = child
        
        # If we have a step_stage, parse it (it may contain the stage action as a child)
        if step_stage_node:
            stage_data.update(self._parse_step_stage(step_stage_node))
        elif standalone_stage_node:
            stage_data.update(self._parse_standalone_stage(standalone_stage_node))
        else:
            logger.warning(f"[WARN] No step_stage or standalone_stage found in enhanced_stage")
            logger.warning(f"   Children data: {[c.data if hasattr(c, 'data') else str(c) for c in node.children]}")
        
        return stage_data
    
    def _parse_step_stage(self, node) -> Dict:
        """Parse a STEP-prefixed stage."""
        stage_data = {}
        
        # Extract step name
        step_name = self._get_identifier(node.children[0])
        stage_data['step_name'] = step_name
        
        # Look for description
        for child in node.children:
            if hasattr(child, 'type') and child.type == 'STRING':
                stage_data['description'] = self._get_string_value(child)
            elif hasattr(child, 'data') and 'DESCRIPTION' in str(child):
                if len(child.children) > 0:
                    stage_data['description'] = self._get_string_value(child.children[0])
        
        # Parse stage action - IMPORTANT: This must come BEFORE setting defaults
        stage_found = False
        for child in node.children:
            if hasattr(child, 'data'):
                if child.data == 'function_stage':
                    action_data = self._parse_function_stage(child)
                    stage_data.update(action_data)
                    stage_found = True
                    break
                elif child.data == 'pipeline_reference_stage':
                    # Handle pipeline reference stage: RUN PIPELINE identifier ...
                    action_data = self._parse_pipeline_reference_stage(child)
                    stage_data.update(action_data)
                    stage_found = True
                    break
                elif child.data in ['enhanced_extract_stage', 'enhanced_chunk_stage',
                    'enhanced_embed_stage', 'enhanced_index_stage', 'enhanced_entity_extract_stage']:
                    action_data = self._parse_standalone_stage(child)
                    stage_data.update(action_data)
                    stage_found = True
                    break
                elif child.data == 'extract_from_folder_stage':
                    parsed_node = self._parse_extract_from_folder_stage(child)
                    if parsed_node and hasattr(parsed_node, 'parameters'):
                        stage_data.update(parsed_node.parameters)
                    stage_found = True
                    break
                elif child.data == 'chunk_by_semantic_stage':
                    parsed_node = self._parse_chunk_by_semantic_stage(child)
                    if parsed_node and hasattr(parsed_node, 'parameters'):
                        stage_data.update(parsed_node.parameters)
                    stage_found = True
                    break
                elif child.data == 'embed_into_stage':
                    parsed_node = self._parse_embed_into_stage(child)
                    if parsed_node and hasattr(parsed_node, 'parameters'):
                        stage_data.update(parsed_node.parameters)
                    stage_found = True
                    break
                elif child.data == 'hybrid_search_stage':
                    parsed_node = self._parse_hybrid_search_stage(child)
                    if parsed_node and hasattr(parsed_node, 'parameters'):
                        stage_data.update(parsed_node.parameters)
                    stage_found = True
                    break
                elif child.data == 'traverse_stage_pipeline':
                    parsed_node = self._parse_traverse_stage_pipeline(child)
                    if parsed_node and hasattr(parsed_node, 'parameters'):
                        stage_data.update(parsed_node.parameters)
                    stage_found = True
                    break
                elif child.data == 'generate_stage':
                    parsed_node = self._parse_generate_stage_pipeline(child)
                    if parsed_node and hasattr(parsed_node, 'parameters'):
                        stage_data.update(parsed_node.parameters)
                    stage_found = True
                    break
                elif child.data == 'evaluate_rag_stage':
                    parsed_node = self._parse_evaluate_rag_stage_pipeline(child)
                    if parsed_node and hasattr(parsed_node, 'parameters'):
                        stage_data.update(parsed_node.parameters)
                    stage_found = True
                    break
                elif child.data == 'insert_into_stage':
                    parsed_node = self._parse_insert_into_stage_pipeline(child)
                    if parsed_node and hasattr(parsed_node, 'parameters'):
                        stage_data.update(parsed_node.parameters)
                    stage_found = True
                    break
        
        # If no stage action was found, check if it's in a stage_action wrapper
        if not stage_found:
            for child in node.children:
                if hasattr(child, 'data') and child.data == 'stage_action':
                    # stage_action wraps the actual stage type
                    for subchild in child.children:
                        if hasattr(subchild, 'data'):
                            if subchild.data == 'pipeline_reference_stage':
                                # Handle pipeline reference stage
                                action_data = self._parse_pipeline_reference_stage(subchild)
                                stage_data.update(action_data)
                                stage_found = True
                                break
                            elif subchild.data in ['enhanced_extract_stage', 'enhanced_chunk_stage',
                                'enhanced_embed_stage', 'enhanced_index_stage', 'enhanced_entity_extract_stage',
                                'enhanced_preprocess_stage', 'enhanced_relationship_extract_stage', 'enhanced_connect_stage']:
                                action_data = self._parse_standalone_stage(subchild)
                                stage_data.update(action_data)
                                stage_found = True
                                break
                            elif subchild.data == 'extract_from_folder_stage':
                                parsed_node = self._parse_extract_from_folder_stage(subchild)
                                if parsed_node and hasattr(parsed_node, 'parameters'):
                                    stage_data.update(parsed_node.parameters)
                                stage_found = True
                                break
                            elif subchild.data == 'chunk_by_semantic_stage':
                                parsed_node = self._parse_chunk_by_semantic_stage(subchild)
                                if parsed_node and hasattr(parsed_node, 'parameters'):
                                    stage_data.update(parsed_node.parameters)
                                stage_found = True
                                break
                            elif subchild.data == 'embed_into_stage':
                                parsed_node = self._parse_embed_into_stage(subchild)
                                if parsed_node and hasattr(parsed_node, 'parameters'):
                                    stage_data.update(parsed_node.parameters)
                                stage_found = True
                                break
                            elif subchild.data == 'hybrid_search_stage':
                                parsed_node = self._parse_hybrid_search_stage(subchild)
                                if parsed_node and hasattr(parsed_node, 'parameters'):
                                    stage_data.update(parsed_node.parameters)
                                stage_found = True
                                break
                            elif subchild.data == 'traverse_stage_pipeline':
                                parsed_node = self._parse_traverse_stage_pipeline(subchild)
                                if parsed_node and hasattr(parsed_node, 'parameters'):
                                    stage_data.update(parsed_node.parameters)
                                stage_found = True
                                break
                            elif subchild.data == 'generate_stage':
                                parsed_node = self._parse_generate_stage_pipeline(subchild)
                                if parsed_node and hasattr(parsed_node, 'parameters'):
                                    stage_data.update(parsed_node.parameters)
                                stage_found = True
                                break
                            elif subchild.data == 'evaluate_rag_stage':
                                parsed_node = self._parse_evaluate_rag_stage_pipeline(subchild)
                                if parsed_node and hasattr(parsed_node, 'parameters'):
                                    stage_data.update(parsed_node.parameters)
                                stage_found = True
                                break
                            elif subchild.data == 'insert_into_stage':
                                parsed_node = self._parse_insert_into_stage_pipeline(subchild)
                                if parsed_node and hasattr(parsed_node, 'parameters'):
                                    stage_data.update(parsed_node.parameters)
                                stage_found = True
                                break
                    if stage_found:
                        break
        
        # If still no stage action found, log details for debugging
        if not stage_found:
            logger.warning(f"[WARN] No stage action found in step '{step_name}'")
            logger.warning(f"   Children count: {len(node.children)}")
            for i, child in enumerate(node.children):
                child_info = f"  [{i}] "
                if hasattr(child, 'data'):
                    child_info += f"data={child.data}"
                elif hasattr(child, 'type'):
                    child_info += f"type={child.type}"
                else:
                    child_info += f"value={str(child)[:50]}"
                logger.warning(child_info)
        
        return stage_data
    
    def _parse_pipeline_reference_stage(self, node) -> Dict:
        """Parse a pipeline reference stage: RUN PIPELINE identifier (WITH PARAMETERS ...)?"""
        stage_data = {
            'type': 'PIPELINE_REFERENCE'
        }
        
        # Extract pipeline name (first identifier after RUN PIPELINE)
        pipeline_name = None
        parameters = {}
        checkpoint = None
        
        # Look for identifier (pipeline name)
        for child in node.children:
            if hasattr(child, 'data') and child.data == 'identifier':
                pipeline_name = self._get_identifier(child)
                break
            elif hasattr(child, 'type') and child.type == 'IDENTIFIER':
                # Direct token
                if hasattr(child, 'value'):
                    pipeline_name = str(child.value)
                break
        
        if not pipeline_name:
            # Try to find identifier in any child
            for child in node.children:
                if hasattr(child, 'type') and child.type == 'IDENTIFIER':
                    pipeline_name = str(child.value) if hasattr(child, 'value') else str(child)
                    break
        
        stage_data['pipeline_name'] = pipeline_name
        
        # Look for WITH PARAMETERS
        for i, child in enumerate(node.children):
            if hasattr(child, 'data') and child.data == 'parameters_block':
                parameters = self._parse_parameter_dict(child)
                stage_data['parameters'] = parameters
            elif hasattr(child, 'type') and str(child).upper() == 'PARAMETERS':
                # Check next child for parameters_block
                if i + 1 < len(node.children):
                    next_child = node.children[i + 1]
                    if hasattr(next_child, 'data') and next_child.data == 'parameters_block':
                        parameters = self._parse_parameter_dict(next_child)
                        stage_data['parameters'] = parameters
        
        # Look for FROM CHECKPOINT
        for i, child in enumerate(node.children):
            if hasattr(child, 'type') and str(child).upper() == 'CHECKPOINT':
                # Check previous for FROM
                if i > 0:
                    prev_child = node.children[i - 1]
                    if hasattr(prev_child, 'type') and str(prev_child).upper() == 'FROM':
                        # Next should be checkpoint identifier
                        if i + 1 < len(node.children):
                            checkpoint_node = node.children[i + 1]
                            checkpoint = self._get_identifier(checkpoint_node)
                            stage_data['checkpoint'] = checkpoint
        
        return stage_data
    
    def _parse_function_stage(self, node) -> Dict:
        """Parse a function-based stage."""
        stage_data = {}
        
        function_name = None
        parameters = {}
        outputs = []
        
        for child in node.children:
            if hasattr(child, 'data'):
                if child.data == 'function_stage_input':
                    # Parse FROM FILE "path" FORMAT "format"
                    stage_data['input_file'] = self._get_string_value(child.children[0]) if len(child.children) > 0 else None
                    if len(child.children) > 2:
                        stage_data['input_format'] = self._get_string_value(child.children[2])
                elif hasattr(child, 'type'):
                    # Token: could be identifier or FUNCTION_NAME
                    if child.type in ['IDENTIFIER', 'FUNCTION_NAME']:
                        function_name = self._get_identifier(child)
                elif child.data == 'parameter_dict':
                    parameters = self._parse_parameter_dict(child)
                elif child.data == 'function_stage_output':
                    outputs.append(self._parse_function_stage_output(child))
        
        stage_data['type'] = 'function'
        stage_data['function'] = function_name
        stage_data['parameters'] = parameters
        stage_data['outputs'] = outputs
        
        return stage_data
    
    def _parse_function_stage_output(self, node) -> Dict:
        """Parse function stage output clauses."""
        output = {}
        # Extract the type of output (STORE AS NODE TYPES, LINK, etc.)
        for child in node.children:
            if hasattr(child, 'data'):
                output['type'] = child.data.lower()
        return output
    
    def _parse_standalone_stage(self, node) -> Dict:
        """Parse a standalone stage action."""
        # Handle new pipeline stage types directly
        if node.data == 'extract_from_folder_stage':
            parsed_node = self._parse_extract_from_folder_stage(node)
            if parsed_node and hasattr(parsed_node, 'parameters'):
                return parsed_node.parameters
        elif node.data == 'chunk_by_semantic_stage':
            parsed_node = self._parse_chunk_by_semantic_stage(node)
            if parsed_node and hasattr(parsed_node, 'parameters'):
                return parsed_node.parameters
        elif node.data == 'embed_into_stage':
            parsed_node = self._parse_embed_into_stage(node)
            if parsed_node and hasattr(parsed_node, 'parameters'):
                return parsed_node.parameters
        elif node.data == 'hybrid_search_stage':
            parsed_node = self._parse_hybrid_search_stage(node)
            if parsed_node and hasattr(parsed_node, 'parameters'):
                return parsed_node.parameters
        elif node.data == 'traverse_stage_pipeline':
            parsed_node = self._parse_traverse_stage_pipeline(node)
            if parsed_node and hasattr(parsed_node, 'parameters'):
                return parsed_node.parameters
        elif node.data == 'generate_stage':
            parsed_node = self._parse_generate_stage_pipeline(node)
            if parsed_node and hasattr(parsed_node, 'parameters'):
                return parsed_node.parameters
        elif node.data == 'evaluate_rag_stage':
            parsed_node = self._parse_evaluate_rag_stage_pipeline(node)
            if parsed_node and hasattr(parsed_node, 'parameters'):
                return parsed_node.parameters
        elif node.data == 'insert_into_stage':
            parsed_node = self._parse_insert_into_stage_pipeline(node)
            if parsed_node and hasattr(parsed_node, 'parameters'):
                return parsed_node.parameters
        
        # Convert node.data to stage type, handling special cases
        stage_type = node.data.upper().replace('ENHANCED_', '').replace('_STAGE', '')
        
        # Map special stage types to executor expectations
        if stage_type == 'ENTITY_EXTRACT':
            stage_type = 'EXTRACT_ENTITIES'
        elif stage_type == 'RELATIONSHIP_EXTRACT':
            stage_type = 'EXTRACT_RELATIONSHIPS'
        
        stage_data = {'type': stage_type}
        
        # Parse stage-specific parameters
        if node.data == 'enhanced_extract_stage':
            # Extract FROM FILE "path"
            # Grammar: "EXTRACT" ("FROM" "FILE" string ...)? ...
            # Look through all children for string tokens and check context
            logger.debug(f"[DEBUG] Parsing enhanced_extract_stage with {len(node.children)} children")
            logger.debug("enhanced_extract_stage has {len(node.children)} children")
            found_from_file = False
            
            # Parse extract_mode (PAGES or MODE clause) FIRST
            for idx, ch in enumerate(node.children):
                if hasattr(ch, 'data') and ch.data == 'extract_mode':
                    # Found extract_mode - can be either "MODE extraction_mode" or "PAGES page_spec"
                    logger.debug(f"[PARSE] Found extract_mode node with {len(ch.children)} children")
                    for mode_child in ch.children:
                        if hasattr(mode_child, 'data'):
                            if mode_child.data == 'extraction_mode':
                                # MODE FULL | RANGE | LIST
                                if mode_child.children:
                                    mode_val = str(mode_child.children[0]).upper() if hasattr(mode_child.children[0], 'value') else str(mode_child.children[0]).upper()
                                    stage_data['mode'] = mode_val
                                    logger.debug(f"[PARSE] Extracted mode: {mode_val}")
                            elif mode_child.data == 'page_spec':
                                # PAGES clause - can be page_range or page_list
                                logger.debug(f"[PARSE] Found page_spec with {len(mode_child.children)} children")
                                for spec_child in mode_child.children:
                                    if hasattr(spec_child, 'data'):
                                        if spec_child.data == 'page_range':
                                            # number ".." number or number "-" number
                                            numbers = []
                                            for num_child in spec_child.children:
                                                if hasattr(num_child, 'data') and num_child.data == 'number':
                                                    num_val = self._get_number(num_child.children[0]) if num_child.children else None
                                                    if num_val is not None:
                                                        numbers.append(num_val)
                                                elif hasattr(num_child, 'type') and num_child.type == 'NUMBER':
                                                    num_val = self._get_number(num_child)
                                                    if num_val is not None:
                                                        numbers.append(num_val)
                                            if len(numbers) >= 2:
                                                stage_data['pages_range'] = (numbers[0], numbers[1])
                                                logger.debug(f"[PARSE] Extracted pages_range: {numbers[0]}..{numbers[1]}")
                                                logger.debug("Extracted pages_range: {numbers[0]}..{numbers[1]}")
                                        elif spec_child.data == 'page_list':
                                            # "[" number ("," number)* "]"
                                            numbers = []
                                            for num_child in spec_child.children:
                                                if hasattr(num_child, 'data') and num_child.data == 'number':
                                                    num_val = self._get_number(num_child.children[0]) if num_child.children else None
                                                    if num_val is not None:
                                                        numbers.append(num_val)
                                                elif hasattr(num_child, 'type') and num_child.type == 'NUMBER':
                                                    num_val = self._get_number(num_child)
                                                    if num_val is not None:
                                                        numbers.append(num_val)
                                            if numbers:
                                                stage_data['pages_list'] = numbers
                                                logger.debug(f"[PARSE] Extracted pages_list: {numbers}")
                                                logger.debug("Extracted pages_list: {numbers}")
                        # Also check for direct "PAGES" token followed by numbers
                        elif hasattr(mode_child, 'type') or (hasattr(mode_child, 'value') and 'PAGES' in str(mode_child.value).upper()):
                            # Look ahead for page numbers
                            if idx + 1 < len(node.children):
                                next_child = node.children[idx + 1]
                                # Try to extract page range or list from next tokens
                                pass  # Will be handled by page_spec parsing above
            
            # First, check all children and their types/values for debugging
            for idx, ch in enumerate(node.children):
                child_type = 'N/A'
                child_value = 'N/A'
                if hasattr(ch, 'type'):
                    child_type = ch.type
                if hasattr(ch, 'value'):
                    child_value = str(ch.value)[:50]
                elif hasattr(ch, 'data'):
                    child_type = ch.data
                logger.debug(f"[DEBUG] Child {idx}: type={child_type}, value={child_value}")
                logger.debug("Child {idx}: data={child_type}, value={child_value}, type={type(ch)}")
                # Check if this is storage_clauses (plural) - DETECT clause is inside here
                if hasattr(ch, 'data') and ch.data == 'storage_clauses':
                    logger.debug("Found storage_clauses! Has {len(ch.children)} children")
                    # Iterate through storage_clause children
                    for sc_idx, sc_child in enumerate(ch.children):
                        sc_info = f"[PARSE DEBUG] storage_clauses child[{sc_idx}]: "
                        if hasattr(sc_child, 'data'):
                            sc_info += f"data={sc_child.data}, "
                        if hasattr(sc_child, 'type'):
                            sc_info += f"type={sc_child.type}, "
                        if hasattr(sc_child, 'value'):
                            sc_info += f"value={sc_child.value}, "
                        logger.debug("storage_clause info: %s", sc_info)
                        # Check if this storage_clause contains detect_types
                        if hasattr(sc_child, 'data') and sc_child.data == 'storage_clause':
                            logger.debug("Found storage_clause inside storage_clauses! Has {len(sc_child.children)} children")
                            for storage_child in sc_child.children:
                                if hasattr(storage_child, 'data') and storage_child.data == 'detect_types':
                                    # Found detect_types!
                                    detect_list = []
                                    logger.debug("detect_types found in storage_clause! Has {len(storage_child.children)} children")
                                    for idx, detect_child in enumerate(storage_child.children):
                                        logger.debug("Processing detect_child[{idx}]: {type(detect_child)}, has_data={hasattr(detect_child, 'data')}, data={detect_child.data if hasattr(detect_child, 'data') else 'N/A'}")
                                        # Skip commas
                                        if hasattr(detect_child, 'value') and str(detect_child.value) == ',':
                                            continue
                                        if hasattr(detect_child, 'type') and str(detect_child).strip() == ',':
                                            continue
                                        
                                        # Process detect_type nodes - detect_child IS a detect_type Tree
                                        if hasattr(detect_child, 'data') and detect_child.data == 'detect_type':
                                            logger.debug("detect_type node has {len(detect_child.children)} children")
                                            # detect_type now matches IDENTIFIER, so it should have a child IDENTIFIER token
                                            if len(detect_child.children) > 0:
                                                # Has children - extract IDENTIFIER from children
                                                for subchild in detect_child.children:
                                                    logger.debug("detect_type subchild: type={subchild.type if hasattr(subchild, 'type') else 'N/A'}, value={subchild.value if hasattr(subchild, 'value') else 'N/A'}")
                                                    if hasattr(subchild, 'type') and subchild.type == 'IDENTIFIER':
                                                        if hasattr(subchild, 'value'):
                                                            detect_val = str(subchild.value).strip()
                                                            logger.debug("[EMOJI] Extracted detect_val: {detect_val}")
                                                            if detect_val and detect_val not in detect_list:
                                                                detect_list.append(detect_val)
                                                    # Also check if subchild is direct IDENTIFIER token
                                                    elif not hasattr(subchild, 'data') and hasattr(subchild, 'type') and subchild.type == 'IDENTIFIER':
                                                        if hasattr(subchild, 'value'):
                                                            detect_val = str(subchild.value).strip()
                                                            logger.debug("[EMOJI] Extracted detect_val (direct): {detect_val}")
                                                            if detect_val and detect_val not in detect_list:
                                                                detect_list.append(detect_val)
                                            else:
                                                # No children - this shouldn't happen with IDENTIFIER rule, but handle gracefully
                                                logger.debug("WARNING: detect_type has no children!")
                                                # Try to get from tree string representation as fallback
                                                tree_str = str(detect_child)
                                                import re
                                                value_match = re.search(r"['\"]([A-Z_]+)['\"]", tree_str)
                                                if value_match:
                                                    val = value_match.group(1)
                                                    if val in ['TEXT', 'TABLES', 'IMAGES', 'AUDIO', 'VIDEO'] and val not in detect_list:
                                                        detect_list.append(val)
                                                        logger.debug("[EMOJI] Extracted detect_val (fallback): {val}")
                                        # If detect_child is a direct IDENTIFIER token (not wrapped in detect_type)
                                        elif hasattr(detect_child, 'type') and detect_child.type == 'IDENTIFIER':
                                            if not hasattr(detect_child, 'data') and hasattr(detect_child, 'value'):
                                                detect_val = str(detect_child.value).strip()
                                                logger.debug("Extracted detect_val (direct IDENTIFIER): {detect_val}")
                                                if detect_val and detect_val not in detect_list:
                                                    detect_list.append(detect_val)
                                        elif not hasattr(detect_child, 'data') and hasattr(detect_child, 'value'):
                                            detect_val = str(detect_child.value).strip('"').strip("'")
                                            if detect_val and detect_val != ',' and detect_val not in detect_list:
                                                logger.debug("Extracted detect_val (from value): {detect_val}")
                                                detect_list.append(detect_val)
                                    logger.debug("Final detect_list from storage_clause: {detect_list}")
                                    if detect_list:
                                        stage_data['detect_types'] = detect_list
                                        logger.debug("[EMOJI] Saved detect_types to stage_data: {stage_data.get('detect_types')}")
                                    break
            
            # Parse USING clause if present
            for i, child in enumerate(node.children):
                # Check for USING READER, USING MODEL, USING LLM
                if hasattr(child, 'type') or hasattr(child, 'data'):
                    child_str = str(child).upper()
                    if hasattr(child, 'value'):
                        child_str = str(child.value).upper()
                    
                    if 'USING' in child_str or (hasattr(child, 'data') and child.data == 'using_clause'):
                        # Found USING - look for next tokens
                        if i + 1 < len(node.children):
                            next_child = node.children[i + 1]
                            next_str = ""
                            if hasattr(next_child, 'type'):
                                if next_child.type == 'IDENTIFIER' and hasattr(next_child, 'value'):
                                    next_str = str(next_child.value).upper()
                                elif next_child.type == 'STRING':
                                    # This is USING <string> (legacy)
                                    stage_data['using_reader'] = self._get_string_value(next_child)
                            elif hasattr(next_child, 'data'):
                                if next_child.data == 'string':
                                    # Extract string value
                                    for subchild in next_child.children:
                                        if hasattr(subchild, 'type') and subchild.type == 'STRING':
                                            stage_data['using_reader'] = self._get_string_value(subchild)
                                            break
                                elif 'READER' in str(next_child).upper():
                                    # USING READER pattern
                                    if i + 2 < len(node.children):
                                        reader_str_node = node.children[i + 2]
                                        if hasattr(reader_str_node, 'type') and reader_str_node.type == 'STRING':
                                            stage_data['using_reader'] = self._get_string_value(reader_str_node)
                                elif 'MODEL' in str(next_child).upper():
                                    # USING MODEL pattern
                                    if i + 2 < len(node.children):
                                        model_str_node = node.children[i + 2]
                                        if hasattr(model_str_node, 'type') and model_str_node.type == 'STRING':
                                            stage_data['using_model'] = self._get_string_value(model_str_node)
                                        elif hasattr(model_str_node, 'value'):
                                            stage_data['using_model'] = str(model_str_node.value).strip('"').strip("'")
                                elif 'PROFILE' in str(next_child).upper():
                                    # USING PROFILE pattern - can be string or identifier
                                    if i + 2 < len(node.children):
                                        profile_node = node.children[i + 2]
                                        if hasattr(profile_node, 'type'):
                                            if profile_node.type == 'STRING':
                                                stage_data['using_profile'] = self._get_string_value(profile_node)
                                            elif profile_node.type == 'IDENTIFIER' and hasattr(profile_node, 'value'):
                                                stage_data['using_profile'] = str(profile_node.value)
                                        elif hasattr(profile_node, 'value'):
                                            stage_data['using_profile'] = str(profile_node.value).strip('"').strip("'")
            
            # More robust FROM FILE extraction
            # Handle new grammar: extract_source -> "FROM" "FILE" string
            # First check for extract_source Tree node
            found_from_file = False
            for child in node.children:
                if hasattr(child, 'data') and child.data == 'extract_source':
                    # New grammar: extract_source contains FROM FILE string
                    for subchild in child.children:
                        if hasattr(subchild, 'data') and subchild.data == 'string':
                            if subchild.children:
                                for str_child in subchild.children:
                                    if hasattr(str_child, 'type') and str_child.type == 'STRING':
                                        file_path = self._get_string_value(str_child)
                                        if file_path.startswith('"') and file_path.endswith('"'):
                                            file_path = file_path[1:-1]
                                        elif file_path.startswith("'") and file_path.endswith("'"):
                                            file_path = file_path[1:-1]
                                        stage_data['from_file'] = file_path
                                        found_from_file = True
                                        logger.debug(f"[DEBUG] Extracted from_file from extract_source: {stage_data['from_file']}")
                                        break
                                if found_from_file:
                                    break
                        elif hasattr(subchild, 'type') and subchild.type == 'STRING':
                            file_path = self._get_string_value(subchild)
                            if file_path.startswith('"') and file_path.endswith('"'):
                                file_path = file_path[1:-1]
                            elif file_path.startswith("'") and file_path.endswith("'"):
                                file_path = file_path[1:-1]
                            stage_data['from_file'] = file_path
                            found_from_file = True
                            logger.debug(f"[DEBUG] Extracted from_file from extract_source (direct): {stage_data['from_file']}")
                            break
                    if found_from_file:
                        break
            
            # Fallback to old grammar extraction
            if not found_from_file:
                # Look for pattern: EXTRACT FROM FILE "<path>"
                # The grammar shows: ("FROM" "FILE" string ...)?
                # So the string might be a direct STRING token or wrapped in a 'string' Tree
                for i, child in enumerate(node.children):
                    # Strategy 1: Handle 'string' Tree node (grammar wraps STRING in string Tree)
                    if hasattr(child, 'data') and child.data == 'string':
                        # This is the file path (first string in EXTRACT FROM FILE)
                        if child.children:
                            for subchild in child.children:
                                if hasattr(subchild, 'type') and subchild.type == 'STRING':
                                    file_path = self._get_string_value(subchild)
                                    # _get_string_value returns with quotes - strip them
                                    if file_path.startswith('"') and file_path.endswith('"'):
                                        file_path = file_path[1:-1]
                                    elif file_path.startswith("'") and file_path.endswith("'"):
                                        file_path = file_path[1:-1]
                                    stage_data['from_file'] = file_path
                                    found_from_file = True
                                    logger.debug(f"[DEBUG] Extracted from_file from string Tree: {stage_data['from_file']}")
                                    break
                            if found_from_file:
                                break
                    
                    # Strategy 2: Look for STRING token after "FROM" and "FILE"
                    elif hasattr(child, 'type') and child.type == 'STRING':
                        string_value = self._get_string_value(child)
                        
                        # Check previous tokens for "FROM" and "FILE"
                        lookback_start = max(0, i - 5)
                        from_found = False
                        file_found = False
                        
                        for j in range(lookback_start, i):
                            prev_child = node.children[j]
                            prev_text = ""
                            
                            # Get text value from token - handle both Token and Tree
                            if hasattr(prev_child, 'value') and not hasattr(prev_child, 'data'):
                                # Token with value
                                prev_text = str(prev_child.value).upper().strip()
                            elif hasattr(prev_child, 'type'):
                                # Token - try to get value from type or string representation
                                if hasattr(prev_child, 'value'):
                                    prev_text = str(prev_child.value).upper().strip()
                                else:
                                    prev_text = str(prev_child).upper().strip()
                            elif hasattr(prev_child, 'data'):
                                # Tree node - check if it's a keyword
                                prev_text = str(prev_child).upper().strip()
                            
                            if 'FROM' in prev_text:
                                from_found = True
                            elif 'FILE' in prev_text:
                                file_found = True
                        
                        # If we found both FROM and FILE before this string, it's the file path
                        if from_found and file_found:
                            stage_data['from_file'] = string_value
                            found_from_file = True
                            logger.debug(f"[DEBUG] Extracted from_file: {stage_data['from_file']}")
                            break
                        
                        # Strategy 2b: Check immediate previous token for "FILE"
                        if i > 0 and not found_from_file:
                            prev_token = node.children[i-1]
                            prev_text = ""
                            if hasattr(prev_token, 'value') and not hasattr(prev_token, 'data'):
                                prev_text = str(prev_token.value).upper().strip()
                            elif hasattr(prev_token, 'type'):
                                if hasattr(prev_token, 'value'):
                                    prev_text = str(prev_token.value).upper().strip()
                                else:
                                    prev_text = str(prev_token).upper().strip()
                            elif hasattr(prev_token, 'data'):
                                prev_text = str(prev_token).upper().strip()
                            
                            if 'FILE' in prev_text:
                                stage_data['from_file'] = string_value
                                found_from_file = True
                                logger.debug(f"[DEBUG] Extracted from_file (direct FILE): {stage_data['from_file']}")
                                break
                        
                        if found_from_file:
                            break
                    
                    # Strategy 3: Look for Tree nodes that might contain "FROM FILE"
                    elif hasattr(child, 'data'):
                        child_str = str(child).upper()
                        # Check if this tree contains "FROM" and "FILE"
                        if 'FROM' in child_str and 'FILE' in child_str:
                            # Look for STRING token in children
                            for subchild in child.children:
                                if hasattr(subchild, 'type') and subchild.type == 'STRING':
                                    stage_data['from_file'] = self._get_string_value(subchild)
                                    found_from_file = True
                                    logger.debug(f"[DEBUG] Extracted from_file from tree: {stage_data['from_file']}")
                                    break
                            if found_from_file:
                                break
                
                # Handle Tree nodes with data (for detect_types, etc.)
                # DETECT clause is parsed as storage_clause, so check for that too
                if hasattr(child, 'data'):
                    # Check if this is a storage_clause containing DETECT
                    if child.data == 'storage_clause':
                        # Look for detect_types within storage_clause
                        for storage_child in child.children:
                            if hasattr(storage_child, 'data') and storage_child.data == 'detect_types':
                                # Found detect_types within storage_clause
                                detect_list = []
                                logger.debug("detect_types found in storage_clause! Has {len(storage_child.children)} children")
                                for idx, detect_child in enumerate(storage_child.children):
                                    logger.debug("Processing detect_child[{idx}]: {type(detect_child)}, has_data={hasattr(detect_child, 'data')}, has_type={hasattr(detect_child, 'type')}, has_value={hasattr(detect_child, 'value')}")
                                    # Skip commas
                                    if hasattr(detect_child, 'value') and str(detect_child.value) == ',':
                                        continue
                                    if hasattr(detect_child, 'type') and str(detect_child).strip() == ',':
                                        continue
                                    
                                    # Process detect_type nodes
                                    if hasattr(detect_child, 'data') and detect_child.data == 'detect_type':
                                        for subchild in detect_child.children:
                                            if hasattr(subchild, 'type') and subchild.type == 'IDENTIFIER':
                                                if hasattr(subchild, 'value'):
                                                    detect_val = str(subchild.value).strip()
                                                    if detect_val and detect_val not in detect_list:
                                                        detect_list.append(detect_val)
                                    elif hasattr(detect_child, 'type') and detect_child.type == 'IDENTIFIER':
                                        if not hasattr(detect_child, 'data') and hasattr(detect_child, 'value'):
                                            detect_val = str(detect_child.value).strip()
                                            if detect_val and detect_val not in detect_list:
                                                detect_list.append(detect_val)
                                    elif not hasattr(detect_child, 'data') and hasattr(detect_child, 'value'):
                                        detect_val = str(detect_child.value).strip('"').strip("'")
                                        if detect_val and detect_val != ',' and detect_val not in detect_list:
                                            detect_list.append(detect_val)
                                logger.debug("Final detect_list from storage_clause: {detect_list}")
                                if detect_list:
                                    stage_data['detect_types'] = detect_list
                                    logger.debug("Saved detect_types to stage_data: {stage_data.get('detect_types')}")
                                break
                    elif child.data == 'detect_types':
                        detect_list = []
                        logger.debug("detect_types node found! Has {len(child.children)} children")
                        logger.debug(f"[PARSE] detect_types node has {len(child.children)} children")
                        for idx, detect_child in enumerate(child.children):
                            logger.debug("Processing child[{idx}]: {type(detect_child)}, has_data={hasattr(detect_child, 'data')}, has_type={hasattr(detect_child, 'type')}, has_value={hasattr(detect_child, 'value')}")
                            try:
                                logger.debug(f"[PARSE] detect_child[{idx}]: data={detect_child.data if hasattr(detect_child, 'data') else 'N/A'}, type={detect_child.type if hasattr(detect_child, 'type') else 'N/A'}, value={detect_child.value if hasattr(detect_child, 'value') else 'N/A'}")
                                # detect_child can be a detect_type Tree node or a token
                                if hasattr(detect_child, 'data') and detect_child.data == 'detect_type':
                                    # detect_type node - extract from children
                                    # Grammar: detect_type: "TEXT" | "TABLES" | "IMAGES" | "AUDIO" | "VIDEO"
                                    # These are IDENTIFIER tokens
                                    for subchild in detect_child.children:
                                        if hasattr(subchild, 'type') and subchild.type == 'IDENTIFIER':
                                            if hasattr(subchild, 'value'):
                                                detect_list.append(str(subchild.value))
                                            else:
                                                # Try string representation
                                                subchild_str = str(subchild).strip()
                                                if subchild_str and not subchild_str.startswith('Tree'):
                                                    detect_list.append(subchild_str)
                                        # Safe value extraction - only if not a Tree
                                        elif not hasattr(subchild, 'data'):
                                            try:
                                                if hasattr(subchild, 'value'):
                                                    detect_list.append(str(subchild.value).strip())
                                            except (AttributeError, TypeError):
                                                pass
                                elif hasattr(detect_child, 'type'):
                                    # Direct token (IDENTIFIER for TEXT, TABLES, etc.)
                                    if detect_child.type == 'IDENTIFIER':
                                        # For IDENTIFIER tokens, value should exist
                                        try:
                                            # Only access value if it's a Token (no 'data' attr)
                                            if not hasattr(detect_child, 'data') and hasattr(detect_child, 'value'):
                                                detect_val = str(detect_child.value).strip()
                                                if detect_val and detect_val not in detect_list:
                                                    detect_list.append(detect_val)
                                            else:
                                                # Fallback to string representation
                                                detect_str = str(detect_child).strip()
                                                if detect_str and not detect_str.startswith('Tree') and detect_str and detect_str not in detect_list:
                                                    detect_list.append(detect_str)
                                        except (AttributeError, TypeError):
                                            # Fallback to string representation
                                            try:
                                                detect_str = str(detect_child).strip()
                                                if detect_str and not detect_str.startswith('Tree') and detect_str and detect_str not in detect_list:
                                                    detect_list.append(detect_str)
                                            except:
                                                pass
                                    elif detect_child.type == 'STRING':
                                        try:
                                            detect_val = self._get_string_value(detect_child)
                                            if detect_val and detect_val not in detect_list:
                                                detect_list.append(detect_val)
                                        except:
                                            pass
                                elif not hasattr(detect_child, 'data'):  # Only check value if not a Tree node
                                    # Direct value (Token, not Tree)
                                    try:
                                        if hasattr(detect_child, 'value'):
                                            detect_val = str(detect_child.value).strip('"').strip("'")
                                            # Skip commas
                                            if detect_val and detect_val != ',' and detect_val not in detect_list:
                                                detect_list.append(detect_val)
                                    except (AttributeError, TypeError):
                                        # Skip - it's a Tree without value
                                        pass
                                else:
                                    # Last resort - try to get string representation
                                    try:
                                        detect_str = str(detect_child).strip('"').strip("'")
                                        # Skip commas and duplicates
                                        if detect_str and not detect_str.startswith('Tree') and not detect_str.startswith('Token') and detect_str != ',' and detect_str not in detect_list:
                                            detect_list.append(detect_str)
                                    except:
                                        pass
                            except (AttributeError, KeyError, TypeError) as e:
                                logger.warning(f"[WARN] Error extracting detect_type value: {e}, skipping")
                                continue
                        logger.debug("Final detect_list: {detect_list}")
                        logger.debug(f"[PARSE] Final detect_list: {detect_list}")
                        stage_data['detect_types'] = detect_list
                        logger.debug("Saved detect_types to stage_data: {stage_data.get('detect_types')}")
                    elif child.data == 'node_types':
                        node_list = []
                        for node_child in child.children:
                            try:
                                # Only access value if it's a Token (not a Tree)
                                if not hasattr(node_child, 'data') and hasattr(node_child, 'value'):
                                    node_list.append(str(node_child.value).strip('"').strip("'"))
                                else:
                                    # Fallback to string representation
                                    node_str = str(node_child).strip('"').strip("'")
                                    if node_str and not node_str.startswith('Tree'):
                                        node_list.append(node_str)
                            except (AttributeError, TypeError):
                                pass
                        stage_data['node_types'] = node_list
                    elif child.data in ['parameter_dict', 'parameter_list']:
                        stage_data['parameters'] = self._parse_parameter_dict(child)
        elif node.data == 'enhanced_chunk_stage':
            # Extract CHUNK parameters
            # Grammar: "CHUNK" (("BY" chunk_method) | ("USING" "MODEL" string) | ("PARAMETERS" "(" parameter_list ")") | ("STORE" "AS" "NODE" "TYPE" identifier) | ("LINK" "TO" identifier "ON" identifier) | ...)*
            chunk_method_str = ""
            params = {}
            
            for i, child in enumerate(node.children):
                if hasattr(child, 'data'):
                    if child.data == 'chunk_method':
                        # Extract chunk method value
                        if child.children:
                            chunk_val = str(child.children[0])
                            if hasattr(child.children[0], 'value'):
                                chunk_val = str(child.children[0].value)
                            chunk_method_str = chunk_val.strip('"').strip("'")
                            stage_data['chunk_method'] = chunk_method_str
                    elif 'BY' in str(child).upper():
                        # Look for BY token followed by chunk_method
                        # Check next child for chunk method
                        if i + 1 < len(node.children):
                            next_child = node.children[i + 1]
                            if hasattr(next_child, 'data') and next_child.data == 'chunk_method':
                                chunk_val = str(next_child.children[0]) if next_child.children else str(next_child)
                                if hasattr(next_child, 'children') and len(next_child.children) > 0:
                                    chunk_val = str(next_child.children[0])
                                    if hasattr(next_child.children[0], 'value'):
                                        chunk_val = str(next_child.children[0].value)
                                chunk_method_str = chunk_val.strip('"').strip("'")
                                stage_data['chunk_method'] = chunk_method_str
                # Check for USING MODEL
                if hasattr(child, 'type') or hasattr(child, 'data'):
                    child_str = str(child).upper()
                    if hasattr(child, 'value'):
                        child_str = str(child.value).upper()
                    if 'MODEL' in child_str and i > 0:
                        # Previous token should be USING, next should be the model string
                        if i + 1 < len(node.children):
                            model_child = node.children[i + 1]
                            if hasattr(model_child, 'type') and model_child.type == 'STRING':
                                stage_data['using_model'] = self._get_string_value(model_child)
                            elif hasattr(model_child, 'value'):
                                stage_data['using_model'] = str(model_child.value).strip('"').strip("'")
                    # Check for USING SCHEMA
                    elif 'SCHEMA' in child_str and i > 0:
                        # Previous token should be USING, next should be the schema file path
                        prev_node = node.children[i-1]
                        prev_val = ""
                        if not hasattr(prev_node, 'data') and hasattr(prev_node, 'value'):
                            prev_val = str(prev_node.value).upper()
                        elif hasattr(prev_node, 'value'):
                            prev_val = str(prev_node.value).upper()
                        if prev_val == 'USING' and i + 1 < len(node.children):
                            schema_child = node.children[i + 1]
                            if hasattr(schema_child, 'type') and schema_child.type == 'STRING':
                                stage_data['using_schema'] = self._get_string_value(schema_child)
                            elif hasattr(schema_child, 'value'):
                                stage_data['using_schema'] = str(schema_child.value).strip('"').strip("'")
                
                # Handle Tree nodes with data
                if hasattr(child, 'data'):
                    if child.data == 'chunk_method':
                        chunk_val = str(child.children[0]) if child.children else ""
                        if child.children and hasattr(child.children[0], 'value'):
                            chunk_val = str(child.children[0].value)
                        stage_data['chunk_method'] = chunk_val.strip('"').strip("'")
                    elif child.data in ['parameter_dict', 'parameter_list']:
                        params = self._parse_parameter_dict(child) if hasattr(self, '_parse_parameter_dict') else {}
                        stage_data['parameters'] = params
                    elif child.data == 'parameter':
                        # Handle direct parameter: identifier "=" value
                        if len(child.children) >= 2:
                            param_name = self._get_identifier(child.children[0])
                            param_value = self._extract_parameter_value(child.children[1])
                            if 'parameters' not in stage_data:
                                stage_data['parameters'] = {}
                            stage_data['parameters'][param_name] = param_value
                    # Also check for direct token sequence: PARAMETERS (identifier "=" value)+
                    elif hasattr(child, 'type') and child.type == 'IDENTIFIER' and str(child).upper() == 'PARAMETERS':
                        # Look for parameter list after PARAMETERS keyword
                        if i + 1 < len(node.children):
                            next_child = node.children[i + 1]
                            if hasattr(next_child, 'data') and next_child.data in ['parameter_list', 'parameter_dict']:
                                params = self._parse_parameter_dict(next_child)
                                if params:
                                    if 'parameters' not in stage_data:
                                        stage_data['parameters'] = {}
                                    stage_data['parameters'].update(params)
                    elif child.data and 'STORE' in child.data.upper():
                        # Look for STORE AS NODE TYPE identifier in children
                        for subchild in child.children:
                            subchild_str = str(subchild)
                            if hasattr(subchild, 'data'):
                                subchild_str = subchild.data
                            if 'NODE' in subchild_str.upper() and 'TYPE' in subchild_str.upper():
                                # Find identifier after NODE TYPE
                                for j, typechild in enumerate(subchild.children):
                                    if hasattr(typechild, 'type') and typechild.type == 'IDENTIFIER':
                                        # Only access .value on Tokens, not Trees
                                        if not hasattr(typechild, 'data') and hasattr(typechild, 'value'):
                                            stage_data['store_as_node_type'] = str(typechild.value)
                                        else:
                                            stage_data['store_as_node_type'] = str(typechild)
                                    elif hasattr(typechild, 'value') and not hasattr(typechild, 'data') and typechild.type != 'STRING':
                                        stage_data['store_as_node_type'] = str(typechild.value)
                            # Also check if this child has identifier children
                            if hasattr(subchild, 'type') and subchild.type == 'IDENTIFIER':
                                # Only access .value on Tokens
                                if not hasattr(subchild, 'data') and hasattr(subchild, 'value'):
                                    stage_data['store_as_node_type'] = str(subchild.value)
                                else:
                                    stage_data['store_as_node_type'] = str(subchild)
                    elif child.data and 'LINK' in child.data.upper():
                        # Extract LINK TO identifier ON identifier
                        link_parts = []
                        for subchild in child.children:
                            if hasattr(subchild, 'type') and subchild.type == 'IDENTIFIER':
                                # Only access .value on Tokens, not Trees
                                if not hasattr(subchild, 'data') and hasattr(subchild, 'value'):
                                    link_parts.append(str(subchild.value))
                                else:
                                    link_parts.append(str(subchild))
                            elif hasattr(subchild, 'value') and not hasattr(subchild, 'data'):
                                # Only access .value on Tokens
                                link_parts.append(str(subchild.value))
                        
                        # Look for TO and ON patterns
                        for j, part in enumerate(link_parts):
                            if part.upper() == 'TO' and j + 1 < len(link_parts):
                                stage_data['link_to'] = link_parts[j + 1]
                            elif part.upper() == 'ON' and j + 1 < len(link_parts):
                                stage_data['link_on'] = link_parts[j + 1]
                    
                    # Also check for PARAMETERS clause
                    if child.data in ['parameter_dict', 'parameter_list']:
                        params = self._parse_parameter_dict(child) if hasattr(self, '_parse_parameter_dict') else {}
                        if params:
                            if isinstance(stage_data.get('parameters'), dict):
                                stage_data['parameters'].update(params)
                            else:
                                stage_data['parameters'] = params
            
            # Also check token sequence for CHUNK BY semantic+layout pattern
            for i, child in enumerate(node.children):
                if hasattr(child, 'type'):
                    # Only access .value on Tokens (not Trees) - Trees don't have .value
                    try:
                        if not hasattr(child, 'data') and hasattr(child, 'value'):
                            child_val = str(child.value)
                        else:
                            child_val = str(child)
                    except (AttributeError, TypeError):
                        child_val = str(child)
                    if child_val.upper() == 'BY' and i + 1 < len(node.children):
                        next_child = node.children[i + 1]
                        # Check if next is chunk_method token or Tree
                        if hasattr(next_child, 'data') and next_child.data == 'chunk_method':
                            chunk_val = str(next_child.children[0]) if next_child.children else ""
                            if next_child.children:
                                first_grandchild = next_child.children[0]
                                # Only access .value on Tokens, not Trees
                                if not hasattr(first_grandchild, 'data') and hasattr(first_grandchild, 'value'):
                                    chunk_val = str(first_grandchild.value)
                                else:
                                    chunk_val = str(first_grandchild)
                            stage_data['chunk_method'] = chunk_val.strip('"').strip("'")
                        elif hasattr(next_child, 'type') or hasattr(next_child, 'value'):
                            # Only access .value on Tokens, not Trees
                            if not hasattr(next_child, 'data') and hasattr(next_child, 'value'):
                                chunk_val = str(next_child.value)
                            else:
                                chunk_val = str(next_child)
                            stage_data['chunk_method'] = chunk_val.strip('"').strip("'")
                    elif child_val.upper() == 'MODEL' and i > 0:
                        # Only access .value on Tokens, not Trees
                        prev_node = node.children[i-1]
                        if not hasattr(prev_node, 'data') and hasattr(prev_node, 'value'):
                            prev_val = str(prev_node.value)
                        else:
                            prev_val = str(prev_node)
                        if prev_val.upper() == 'USING' and i + 1 < len(node.children):
                            model_child = node.children[i + 1]
                            if hasattr(model_child, 'type') and model_child.type == 'STRING':
                                stage_data['using_model'] = self._get_string_value(model_child)
                            elif hasattr(model_child, 'value') and not hasattr(model_child, 'data'):
                                # Only access .value on Tokens
                                stage_data['using_model'] = str(model_child.value).strip('"').strip("'")
                    elif child_val.upper() == 'SCHEMA' and i > 0:
                        # Check for USING SCHEMA pattern
                        prev_node = node.children[i-1]
                        prev_val = ""
                        if not hasattr(prev_node, 'data') and hasattr(prev_node, 'value'):
                            prev_val = str(prev_node.value).upper()
                        elif hasattr(prev_node, 'value'):
                            prev_val = str(prev_node.value).upper()
                        if prev_val == 'USING' and i + 1 < len(node.children):
                            schema_child = node.children[i + 1]
                            if hasattr(schema_child, 'type') and schema_child.type == 'STRING':
                                stage_data['using_schema'] = self._get_string_value(schema_child)
                            elif hasattr(schema_child, 'value') and not hasattr(schema_child, 'data'):
                                stage_data['using_schema'] = str(schema_child.value).strip('"').strip("'")
        elif node.data == 'enhanced_embed_stage':
            # Extract EMBED parameters
            # First, check token sequence for USING PROFILE
            for i, child in enumerate(node.children):
                if hasattr(child, 'type') or hasattr(child, 'data'):
                    child_val = ""
                    if hasattr(child, 'value') and not hasattr(child, 'data'):
                        child_val = str(child.value).upper()
                    elif hasattr(child, 'type'):
                        child_val = str(child).upper()
                    elif hasattr(child, 'data'):
                        child_val = child.data.upper()
                    
                    if child_val == 'PROFILE' and i > 0:
                        # Check if previous is USING
                        prev_node = node.children[i-1]
                        prev_val = ""
                        if hasattr(prev_node, 'value') and not hasattr(prev_node, 'data'):
                            prev_val = str(prev_node.value).upper()
                        elif hasattr(prev_node, 'type'):
                            prev_val = str(prev_node).upper()
                        
                        if prev_val == 'USING' and i + 1 < len(node.children):
                            profile_node = node.children[i + 1]
                            if hasattr(profile_node, 'type'):
                                if profile_node.type == 'STRING':
                                    stage_data['using_profile'] = self._get_string_value(profile_node)
                                elif profile_node.type == 'IDENTIFIER' and hasattr(profile_node, 'value'):
                                    stage_data['using_profile'] = str(profile_node.value)
                            elif hasattr(profile_node, 'value'):
                                stage_data['using_profile'] = str(profile_node.value).strip('"').strip("'")
                    elif child_val == 'USING' and i + 1 < len(node.children):
                        next_child = node.children[i + 1]
                        if hasattr(next_child, 'type') or hasattr(next_child, 'data'):
                            next_str = ""
                            if hasattr(next_child, 'value'):
                                next_str = str(next_child.value).upper()
                            elif hasattr(next_child, 'type'):
                                next_str = str(next_child).upper()
                            
                            if next_str == 'PROFILE' and i + 2 < len(node.children):
                                profile_node = node.children[i + 2]
                                if hasattr(profile_node, 'type'):
                                    if profile_node.type == 'STRING':
                                        stage_data['using_profile'] = self._get_string_value(profile_node)
                                    elif profile_node.type == 'IDENTIFIER' and hasattr(profile_node, 'value'):
                                        stage_data['using_profile'] = str(profile_node.value)
                                elif hasattr(profile_node, 'value'):
                                    stage_data['using_profile'] = str(profile_node.value).strip('"').strip("'")
            
            # Then check data nodes
            for child in node.children:
                if hasattr(child, 'data'):
                    if child.data == 'using_clause':
                        # Parse USING clause tree
                        for subchild in child.children:
                            if hasattr(subchild, 'data'):
                                if 'PROFILE' in str(subchild).upper():
                                    # Look for profile value in subchildren
                                    for grandchild in subchild.children:
                                        if hasattr(grandchild, 'type') and grandchild.type == 'STRING':
                                            stage_data['using_profile'] = self._get_string_value(grandchild)
                                        elif hasattr(grandchild, 'type') and grandchild.type == 'IDENTIFIER' and hasattr(grandchild, 'value'):
                                            stage_data['using_profile'] = str(grandchild.value)
                    elif 'PROFILE' in str(child):
                        # Extract USING PROFILE "name"
                        for subchild in child.children:
                            if hasattr(subchild, 'type') and subchild.type == 'STRING':
                                stage_data['using_profile'] = self._get_string_value(subchild)
                            elif hasattr(subchild, 'type') and subchild.type == 'IDENTIFIER':
                                # Only access .value on Tokens, not Trees
                                if not hasattr(subchild, 'data') and hasattr(subchild, 'value'):
                                    stage_data['using_profile'] = str(subchild.value)
                                else:
                                    stage_data['using_profile'] = str(subchild)
                    elif child.data in ['parameter_dict', 'parameter_list']:
                        stage_data['parameters'] = self._parse_parameter_dict(child)
                    elif 'STORE' in str(child) and 'EMBEDDING' in str(child):
                        # Extract STORE EMBEDDING IN Chunk.embedding
                        parts = str(child).split()
                        if 'IN' in parts:
                            idx = parts.index('IN')
                            if idx + 1 < len(parts):
                                stage_data['store_embedding_in'] = parts[idx + 1]
        elif node.data == 'enhanced_entity_extract_stage':
            # Extract EXTRACT ENTITIES parameters
            for child in node.children:
                if hasattr(child, 'data'):
                    if 'LLM' in str(child) or 'USING' in str(child):
                        # Extract USING LLM "model"
                        for i, subchild in enumerate(child.children):
                            if hasattr(subchild, 'type') and subchild.type == 'STRING':
                                stage_data['using_llm'] = self._get_string_value(subchild)
                            elif hasattr(subchild, 'value'):
                                if 'LLM' in str(subchild.value).upper() and i + 1 < len(child.children):
                                    next_child = child.children[i + 1]
                                    if hasattr(next_child, 'type') and next_child.type == 'STRING':
                                        stage_data['using_llm'] = self._get_string_value(next_child)
                    elif child.data in ['parameter_dict', 'parameter_list']:
                        stage_data['parameters'] = self._parse_parameter_dict(child)
                    elif 'STORE' in str(child) and 'NODE' in str(child) and 'TYPE' in str(child):
                        # Extract STORE AS NODE TYPE Entity
                        for subchild in child.children:
                            if hasattr(subchild, 'type') and subchild.type == 'IDENTIFIER':
                                if not hasattr(subchild, 'data') and hasattr(subchild, 'value'):
                                    stage_data['store_as_node_type'] = str(subchild.value)
                                else:
                                    stage_data['store_as_node_type'] = str(subchild)
            # Also check token sequence for USING LLM
            for i, child in enumerate(node.children):
                if hasattr(child, 'type'):
                    child_val = str(child.value) if hasattr(child, 'value') else str(child)
                    if child_val.upper() == 'LLM' and i > 0:
                        prev_node = node.children[i-1]
                        prev_val = str(prev_node.value) if hasattr(prev_node, 'value') else str(prev_node)
                        if prev_val.upper() == 'USING' and i + 1 < len(node.children):
                            model_child = node.children[i + 1]
                            if hasattr(model_child, 'type') and model_child.type == 'STRING':
                                stage_data['using_llm'] = self._get_string_value(model_child)
                            elif hasattr(model_child, 'value'):
                                stage_data['using_llm'] = str(model_child.value).strip('"').strip("'")
        elif node.data == 'enhanced_relationship_extract_stage':
            # Extract EXTRACT RELATIONSHIPS parameters
            for child in node.children:
                if hasattr(child, 'data'):
                    if 'LLM' in str(child) or 'USING' in str(child):
                        # Extract USING LLM "model"
                        for i, subchild in enumerate(child.children):
                            if hasattr(subchild, 'type') and subchild.type == 'STRING':
                                stage_data['using_llm'] = self._get_string_value(subchild)
                            elif hasattr(subchild, 'value'):
                                if 'LLM' in str(subchild.value).upper() and i + 1 < len(child.children):
                                    next_child = child.children[i + 1]
                                    if hasattr(next_child, 'type') and next_child.type == 'STRING':
                                        stage_data['using_llm'] = self._get_string_value(next_child)
                    elif child.data in ['parameter_dict', 'parameter_list']:
                        stage_data['parameters'] = self._parse_parameter_dict(child)
                    elif 'STORE' in str(child) and 'EDGE' in str(child) and 'TYPE' in str(child):
                        # Extract STORE AS EDGE TYPE Relationship
                        for subchild in child.children:
                            if hasattr(subchild, 'type') and subchild.type == 'IDENTIFIER':
                                if not hasattr(subchild, 'data') and hasattr(subchild, 'value'):
                                    stage_data['store_as_edge_type'] = str(subchild.value)
                                else:
                                    stage_data['store_as_edge_type'] = str(subchild)
            # Also check token sequence for USING LLM
            for i, child in enumerate(node.children):
                if hasattr(child, 'type'):
                    child_val = str(child.value) if hasattr(child, 'value') else str(child)
                    if child_val.upper() == 'LLM' and i > 0:
                        prev_node = node.children[i-1]
                        prev_val = str(prev_node.value) if hasattr(prev_node, 'value') else str(prev_node)
                        if prev_val.upper() == 'USING' and i + 1 < len(node.children):
                            model_child = node.children[i + 1]
                            if hasattr(model_child, 'type') and model_child.type == 'STRING':
                                stage_data['using_llm'] = self._get_string_value(model_child)
                            elif hasattr(model_child, 'value'):
                                stage_data['using_llm'] = str(model_child.value).strip('"').strip("'")
        elif node.data == 'enhanced_connect_stage':
            # Extract CONNECT stage parameters
            # Support both: CONNECT AUTO and CONNECT FROM NORMALIZED
            connect_mode = 'AUTO'  # Default
            create_nodes = []
            link_specs = {}
            
            # #region agent log
            import json
            try:
                with open(r'c:\qgraph\qgraph-app\.cursor\debug.log', 'a', encoding='utf-8') as f:
                    f.write(json.dumps({
                        'sessionId': 'debug-session',
                        'runId': 'parse',
                        'hypothesisId': 'H',
                        'location': 'aiql_parser.py:10324',
                        'message': 'Parsing enhanced_connect_stage',
                        'data': {
                            'children_count': len(node.children),
                            'children_info': [{'idx': i, 'has_value': hasattr(c, 'value'), 'value': getattr(c, 'value', None), 'has_data': hasattr(c, 'data'), 'data': getattr(c, 'data', None)} for i, c in enumerate(node.children[:15])]
                        },
                        'timestamp': int(__import__('time').time() * 1000)
                    }) + '\n')
            except: pass
            # #endregion
            
            # Check for CONNECT FROM NORMALIZED
            for i, child in enumerate(node.children):
                if hasattr(child, 'value'):
                    child_val = str(child.value).upper() if hasattr(child, 'value') else ''
                    if child_val == 'FROM' and i + 1 < len(node.children):
                        next_child = node.children[i + 1]
                        next_val = str(next_child.value).upper() if hasattr(next_child, 'value') else ''
                        if next_val == 'NORMALIZED':
                            connect_mode = 'FROM_NORMALIZED'
                            # #region agent log
                            try:
                                with open(r'c:\qgraph\qgraph-app\.cursor\debug.log', 'a', encoding='utf-8') as f:
                                    f.write(json.dumps({
                                        'sessionId': 'debug-session',
                                        'runId': 'parse',
                                        'hypothesisId': 'H',
                                        'location': 'aiql_parser.py:10338',
                                        'message': 'Found CONNECT FROM NORMALIZED',
                                        'data': {'connect_mode': connect_mode},
                                        'timestamp': int(__import__('time').time() * 1000)
                                    }) + '\n')
                            except: pass
                            # #endregion
                            # Look for CREATE NODES and LINK clauses after NORMALIZED
                            # These should be in storage_clauses or as separate children
                            break
            
            # Parse CREATE NODES and LINK from storage_clauses or children
            for child in node.children:
                if hasattr(child, 'data'):
                    if child.data == 'storage_clauses':
                        # Parse storage clauses for CREATE NODES and LINK
                        for sc_child in child.children:
                            if hasattr(sc_child, 'data'):
                                # Check for STORE AS NODE TYPES (for CREATE NODES)
                                if 'NODE' in str(sc_child).upper() and 'TYPE' in str(sc_child).upper():
                                    # Extract node types from children
                                    node_types = []
                                    for subchild in sc_child.children:
                                        if hasattr(subchild, 'data') and subchild.data == 'node_types':
                                            for nt_child in subchild.children:
                                                if hasattr(nt_child, 'type') and nt_child.type == 'IDENTIFIER':
                                                    if not hasattr(nt_child, 'data') and hasattr(nt_child, 'value'):
                                                        node_types.append(str(nt_child.value))
                                                    else:
                                                        node_types.append(str(nt_child))
                                    if node_types:
                                        create_nodes = node_types
                                # Check for LINK clause
                                elif 'LINK' in str(sc_child).upper():
                                    # Extract LINK specifications
                                    if hasattr(sc_child, 'data') and sc_child.data == 'link_specs':
                                        # Parse link_specs: link_item ("," link_item)*
                                        # link_item: IDENTIFIER "TO" IDENTIFIER
                                        for link_item in sc_child.children:
                                            if hasattr(link_item, 'data') and link_item.data == 'link_item':
                                                source = None
                                                target = None
                                                for li_child in link_item.children:
                                                    if hasattr(li_child, 'type') and li_child.type == 'IDENTIFIER':
                                                        if not hasattr(li_child, 'data') and hasattr(li_child, 'value'):
                                                            val = str(li_child.value)
                                                        else:
                                                            val = str(li_child)
                                                        if source is None:
                                                            source = val
                                                        elif target is None:
                                                            target = val
                                                if source and target:
                                                    if source not in link_specs:
                                                        link_specs[source] = []
                                                    link_specs[source].append(target)
            
            # Also check token sequence for CREATE NODES and LINK
            for i, child in enumerate(node.children):
                if hasattr(child, 'value'):
                    child_val = str(child.value).upper()
                    # Look for CREATE NODES (Document, Table, Image)
                    if child_val == 'CREATE' and i + 1 < len(node.children):
                        next_child = node.children[i + 1]
                        next_val = str(next_child.value).upper() if hasattr(next_child, 'value') else ''
                        if next_val == 'NODES' and i + 2 < len(node.children):
                            # Next should be node_types list
                            node_types_node = node.children[i + 2]
                            if hasattr(node_types_node, 'data') and node_types_node.data == 'node_types':
                                create_nodes = []
                                for nt_child in node_types_node.children:
                                    if hasattr(nt_child, 'type') and nt_child.type == 'IDENTIFIER':
                                        if not hasattr(nt_child, 'data') and hasattr(nt_child, 'value'):
                                            create_nodes.append(str(nt_child.value))
                                        else:
                                            create_nodes.append(str(nt_child))
                    # Look for LINK (Document TO Table, Document TO Image)
                    elif child_val == 'LINK' and i + 1 < len(node.children):
                        link_node = node.children[i + 1]
                        if hasattr(link_node, 'data') and link_node.data == 'link_specs':
                            for link_item in link_node.children:
                                if hasattr(link_item, 'data') and link_item.data == 'link_item':
                                    source = None
                                    target = None
                                    for li_child in link_item.children:
                                        if hasattr(li_child, 'type') and li_child.type == 'IDENTIFIER':
                                            if not hasattr(li_child, 'data') and hasattr(li_child, 'value'):
                                                val = str(li_child.value)
                                            else:
                                                val = str(li_child)
                                            if source is None:
                                                source = val
                                            elif target is None:
                                                target = val
                                    if source and target:
                                        if source not in link_specs:
                                            link_specs[source] = []
                                        link_specs[source].append(target)
            
            if connect_mode == 'FROM_NORMALIZED':
                stage_data['connect_from_normalized'] = True
            if create_nodes:
                stage_data['create_nodes'] = create_nodes
            if link_specs:
                # Convert to format expected by executor: {'Document': 'Table', 'Document': 'Image'}
                # Executor expects: edge_types = {'Document': 'Table', 'Document': 'Image'}
                edge_types = {}
                for source, targets in link_specs.items():
                    for target in targets:
                        edge_types[source] = target
                stage_data['link'] = edge_types
        elif node.data == 'enhanced_index_stage':
            # Extract INDEX parameters
            for child in node.children:
                if hasattr(child, 'data'):
                    if child.data == 'index_options':
                        # Grammar: index_options: "{" index_option ("," index_option)* "}"
                        # index_option: identifier ":" (boolean | number | string)
                        options = {}
                        for opt_child in child.children:
                            if hasattr(opt_child, 'data') and opt_child.data == 'index_option':
                                # Extract key (identifier) and value (boolean/number/string)
                                key = None
                                val = None
                                if len(opt_child.children) >= 2:
                                    # First child is identifier (key)
                                    key_child = opt_child.children[0]
                                    if hasattr(key_child, 'type') and key_child.type == 'IDENTIFIER':
                                        if not hasattr(key_child, 'data') and hasattr(key_child, 'value'):
                                            key = str(key_child.value)
                                        else:
                                            key = str(key_child).strip()
                                    elif not hasattr(key_child, 'data') and hasattr(key_child, 'value'):
                                        key = str(key_child.value)
                                    else:
                                        # Try to extract identifier from children
                                        for subchild in opt_child.children:
                                            if hasattr(subchild, 'type') and subchild.type == 'IDENTIFIER':
                                                if not hasattr(subchild, 'data') and hasattr(subchild, 'value'):
                                                    key = str(subchild.value)
                                                    break
                                    
                                    # Second child is value (boolean/number/string)
                                    val_child = opt_child.children[1]
                                    val = self._extract_parameter_value(val_child)
                                
                                if key and val is not None:
                                    options[key] = val
                        stage_data['using_options'] = options
                    elif 'BUILD_INDEX' in str(child):
                        # Extract BUILD_INDEX "name"
                        for subchild in child.children:
                            if hasattr(subchild, 'type') and subchild.type == 'STRING':
                                stage_data['build_index'] = self._get_string_value(subchild)
                    elif 'STORE' in str(child) and 'COLLECTION' in str(child):
                        # Extract STORE IN COLLECTION
                        parts = str(child).split()
                        if 'COLLECTION' in parts:
                            idx = parts.index('COLLECTION')
                            if idx + 1 < len(parts):
                                stage_data['store_in_collection'] = parts[idx + 1]
        
        return stage_data
    
    def _parse_parameter_dict(self, node) -> Dict:
        """Parse parameter dictionary or parameter list."""
        params = {}
        
        # Handle both parameter_dict and parameter_list
        for child in node.children:
            if hasattr(child, 'data'):
                # parameter_dict has parameter_pair children: identifier ":" value
                if child.data == 'parameter_pair':
                    if len(child.children) >= 2:
                        param_name = self._get_identifier(child.children[0])
                        param_value = self._extract_parameter_value(child.children[1])
                        params[param_name] = param_value
                # parameter_list has parameter items separated by commas
                elif child.data == 'parameter_list' or child.data == 'parameter':
                    # Recurse into parameter_list
                    sub_params = self._parse_parameter_dict(child)
                    params.update(sub_params)
            # Also handle direct parameter items (from parameter_list)
            elif hasattr(child, 'type'):
                # Might be a direct parameter item
                pass
        
        # Also look for parameter_list with comma-separated items
        # parameter_list: parameter ("," parameter)*
        # parameter: identifier "=" value
        for child in node.children:
            if hasattr(child, 'data') and child.data == 'parameter':
                if len(child.children) >= 2:
                    param_name = self._get_identifier(child.children[0])
                    param_value = self._extract_parameter_value(child.children[1])
                    params[param_name] = param_value
        
        return params
    
    def _extract_parameter_dict(self, node) -> Dict:
        """Alias for _parse_parameter_dict for consistency with RAG parser methods."""
        return self._parse_parameter_dict(node)
    
    def _extract_parameter_value(self, value_node) -> Any:
        """Extract actual value from a parameter value node (handles Tree objects)."""
        # If it's a Tree, recursively extract the value
        if hasattr(value_node, 'data'):
            # Tree node - extract from children
            if value_node.data == 'value':
                # value node - get first child
                if value_node.children:
                    return self._extract_parameter_value(value_node.children[0])
                else:
                    return None
            elif value_node.data == 'parameter_value':
                # parameter_value node - extract from children
                if value_node.children:
                    return self._extract_parameter_value(value_node.children[0])
                else:
                    return None
            elif value_node.data == 'list':
                # List node - extract all children as list
                result_list = []
                for child in value_node.children:
                    extracted = self._extract_parameter_value(child)
                    result_list.append(extracted)
                return result_list
            elif value_node.data == 'number':
                # number tree - extract NUMBER token
                for child in value_node.children:
                    if hasattr(child, 'type') and child.type == 'NUMBER':
                        if not hasattr(child, 'data') and hasattr(child, 'value'):
                            try:
                                # Try to convert to int or float
                                val_str = str(child.value)
                                if '.' in val_str:
                                    return float(val_str)
                                else:
                                    return int(val_str)
                            except:
                                return val_str
                        else:
                            val_str = str(child).strip()
                            try:
                                if '.' in val_str:
                                    return float(val_str)
                                else:
                                    return int(val_str)
                            except:
                                return val_str
            elif value_node.data == 'string':
                # string tree - extract STRING token
                for child in value_node.children:
                    if hasattr(child, 'type') and child.type == 'STRING':
                        if not hasattr(child, 'data') and hasattr(child, 'value'):
                            val_str = str(child.value)
                            # Remove quotes
                            if (val_str.startswith('"') and val_str.endswith('"')) or \
                               (val_str.startswith("'") and val_str.endswith("'")):
                                return val_str[1:-1]
                            return val_str
                        else:
                            val_str = str(child).strip()
                            if (val_str.startswith('"') and val_str.endswith('"')) or \
                               (val_str.startswith("'") and val_str.endswith("'")):
                                return val_str[1:-1]
                            return val_str
                # If no STRING token found, try to extract from first child
                if value_node.children:
                    return self._extract_parameter_value(value_node.children[0])
            elif value_node.data == 'identifier':
                # identifier tree - extract identifier
                if value_node.children:
                    return self._get_identifier(value_node.children[0]) if hasattr(value_node.children[0], 'data') else str(value_node.children[0])
                else:
                    return self._get_identifier(value_node) if hasattr(value_node, 'children') else str(value_node)
            elif value_node.data == 'boolean':
                # boolean tree
                if value_node.children:
                    bool_val = value_node.children[0]
                    if hasattr(bool_val, 'value'):
                        return str(bool_val.value).upper() in ['TRUE', '1', 'YES']
                    return str(bool_val).upper() in ['TRUE', '1', 'YES']
                return False
        
        # If it's a token, extract the value directly
        if hasattr(value_node, 'type'):
            if value_node.type == 'NUMBER':
                if hasattr(value_node, 'value'):
                    try:
                        val_str = str(value_node.value)
                        if '.' in val_str:
                            return float(val_str)
                        else:
                            return int(val_str)
                    except:
                        return str(value_node.value)
                else:
                    try:
                        val_str = str(value_node).strip()
                        if '.' in val_str:
                            return float(val_str)
                        else:
                            return int(val_str)
                    except:
                        return str(value_node)
            elif value_node.type == 'STRING':
                if hasattr(value_node, 'value'):
                    val_str = str(value_node.value)
                    # Remove quotes
                    if (val_str.startswith('"') and val_str.endswith('"')) or \
                       (val_str.startswith("'") and val_str.endswith("'")):
                        return val_str[1:-1]
                    return val_str
                else:
                    val_str = str(value_node).strip()
                    if (val_str.startswith('"') and val_str.endswith('"')) or \
                       (val_str.startswith("'") and val_str.endswith("'")):
                        return val_str[1:-1]
                    return val_str
        
        # Fallback: return as string
        return str(value_node)
    
    def _parse_create_retrieval_pipeline(self, node) -> AIQLNode:
        """Parse CREATE PIPELINE for retrieval."""
        logger.info("Parsing CREATE RETRIEVAL PIPELINE...")
        
        # Expected structure: CREATE PIPELINE identifier IN NAMESPACE identifier SOURCE COLLECTION identifier DESCRIPTION string STAGES = [...]
        pipeline_name = self._get_identifier(node.children[0])  # identifier at position 0
        namespace = self._get_identifier(node.children[1])  # namespace identifier at position 1
        source_collection = self._get_identifier(node.children[2])  # collection at position 2
        description = self._get_string_value(node.children[3])  # string at position 3
        
        # Find stages
        stages = []
        stages_index = None
        for i, child in enumerate(node.children):
            if hasattr(child, 'data') and child.data == 'retrieval_stage_list':
                stages_index = i
                break
        
        if stages_index is not None:
            stages_node = node.children[stages_index]
            if hasattr(stages_node, 'children'):
                for stage_child in stages_node.children:
                    if hasattr(stage_child, 'data') and stage_child.data == 'retrieval_stage':
                        stage_data = self._parse_retrieval_stage(stage_child)
                        if stage_data:
                            stages.append(stage_data)
        
        logger.info(f"Parsed retrieval pipeline: {pipeline_name} with {len(stages)} stages")
        
        return AIQLNode(
            node_type=AIQLNodeType.CREATE_PIPELINE,
            parameters={
                'pipeline_name': pipeline_name,
                'namespace': namespace,
                'source_collection': source_collection,
                'description': description,
                'stages': stages,
                'pipeline_type': 'retrieval'
            }
        )
    
    def _parse_retrieval_stage(self, node) -> Dict:
        """Parse a retrieval stage."""
        stage_data = {}
        
        # Look for step_name
        for child in node.children:
            if hasattr(child, 'data'):
                if child.data == 'retrieval_function_stage':
                    # Extract step name
                    for subchild in child.children:
                        if hasattr(subchild, 'type') and subchild.type == 'IDENTIFIER':
                            stage_data['step_name'] = subchild.value
                        elif hasattr(subchild, 'data') and subchild.data == 'retrieval_function_call':
                            func_data = self._parse_retrieval_function_call(subchild)
                            stage_data.update(func_data)
        
        return stage_data
    
    def _parse_retrieval_function_call(self, node) -> Dict:
        """Parse retrieval function call."""
        func_data = {}
        
        # Extract USING FUNCTION function_name
        for i, child in enumerate(node.children):
            if hasattr(child, 'data') and child.data == 'retrieval_function_name':
                func_name = ''
                for subchild in child.children:
                    if hasattr(subchild, 'value'):
                        func_name += ('.' if func_name else '') + subchild.value
                func_data['function'] = func_name
            elif hasattr(child, 'data') and child.data == 'parameters_block':
                params = self._parse_parameters_block(child)
                func_data['parameters'] = params
            elif hasattr(child, 'data') and child.data == 'input_output_clauses':
                io_clauses = self._parse_input_output_clauses(child)
                func_data.update(io_clauses)
        
        return func_data
    
    def _parse_parameters_block(self, node) -> Dict:
        """Parse parameters block."""
        params = {}
        for child in node.children:
            if hasattr(child, 'data'):
                # Handle different parameter formats
                if child.data in ['retrieval_parameter', 'parameter']:
                    # Extract parameter name and value
                    if len(child.children) >= 2:
                        param_name = self._get_identifier(child.children[0])
                        # Parse value node - might be a tree
                        value_node = child.children[1]
                        if hasattr(value_node, 'children') and len(value_node.children) > 0:
                            # Value is nested, get the actual value
                            actual_value_node = value_node.children[0]
                            if hasattr(actual_value_node, 'children') and len(actual_value_node.children) > 0:
                                # Extract from token
                                param_value = actual_value_node.children[0].value if hasattr(actual_value_node.children[0], 'value') else str(actual_value_node.children[0])
                            else:
                                param_value = actual_value_node.value if hasattr(actual_value_node, 'value') else str(actual_value_node)
                        else:
                            param_value = value_node.value if hasattr(value_node, 'value') else str(value_node)
                        
                        params[param_name] = param_value
        return params
    
    def _parse_input_output_clauses(self, node) -> Dict:
        """Parse input/output clauses."""
        clauses = {}
        for child in node.children:
            if hasattr(child, 'data'):
                if 'INPUT' in str(child.data):
                    if len(child.children) > 0:
                        input_collection = self._get_identifier(child.children[-1])
                        clauses['input_collection'] = input_collection
                elif 'OUTPUT' in str(child.data):
                    if len(child.children) > 0:
                        output_collection = self._get_identifier(child.children[-1])
                        clauses['output_collection'] = output_collection
        return clauses
    
    def _get_value(self, node) -> Any:
        """Get value from a node (string, number, boolean, etc.)."""
        if hasattr(node, 'type'):
            if node.type == 'STRING':
                return self._get_string_value(node)
            elif node.type == 'NUMBER':
                return self._get_number(node)
            elif node.type == 'BOOLEAN':
                return self._get_boolean_value(node)
            elif node.type == 'IDENTIFIER':
                return self._get_identifier(node)
        
        # Fallback: try to extract value
        if hasattr(node, 'value'):
            return node.value
        
        return str(node)

# Unit tests
def test_parser():
    """Test the AIQL parser with example queries."""
    parser = AIQLParser()
    
    test_queries = [
        # Variable declaration
        "LET $author = \"Alice\"",
        
        # Graph creation
        "CREATE GRAPH social_graph",
        
        # Node and edge creation with properties
        "CREATE NODE Person AS a { name: \"Alice\", age: 30 } UNIQUE KEY(name)",
        "CREATE NODE Person AS b { name: \"Bob\", age: 25 } UNIQUE KEY(name)",
        "CREATE EDGE (a) KNOW { since: 2015 } UNIQUE KEY(source, target, type) (b)",
        
        # DELETE operations
        "DELETE NODE Person WHERE name = \"Bob\"",
        "DELETE EDGE KNOW WHERE source = \"Alice\" AND target = \"Bob\"",
        
        # UPDATE operations
        "UPDATE NODE Person SET { age: 31 } WHERE name = \"Alice\"",
        
        # Retrieval operations
        "SPARSE MATCH($query) AS p LIMIT 10",
        "DENSE MATCH($query, MODEL=emb_model) AS p LIMIT 10",
        "HYBRID MATCH($query, SPARSE_WEIGHT=0.5, DENSE_WEIGHT=0.5, DENSE_MODEL=emb_model) AS p LIMIT 20",
        
        # Traversal with strategies
        "TRAVERSE p VIA EDGE CITE TO p2 STRATEGY BFS",
        "TRAVERSE p VIA EDGE CITE+ TO p2",
        "TRAVERSE p VIA EDGE CITE{2..5} TO p3",
        
        # Generation
        "GENERATE USING gen_model PROMPT summarize_citations WITH context=p2.abstract",
        
        # Analytics
        "PAGERANK()",
        "SHORTEST_PATH(SOURCE=$source, TARGET=$target, EDGE_TYPES=[\"KNOW\"])",
        "COMMUNITY_DETECTION(METHOD=\"LOUVAIN\")",
        
        # Complex pipeline
        """
        LET $author = "Alice"
        THEN TRAVERSE FROM Person AS p OUTGOING KNOWS EDGE TO Person AS f
        WHERE p.name = $author
        SELECT f.name
        """
    ]
    
    for i, query in enumerate(test_queries):
        print(f"\nTest {i+1}: {query.strip()}")
        result = parser.parse(query)
        
        if result['success']:
            print("[OK] Parsed successfully")
            print(f"Variables: {result['variables']}")
            print(f"AST nodes: {len(result['ast'])}")
        else:
            logger.error("Parse failed: %s", result.get("error", "unknown"))

if __name__ == "__main__":
    test_parser()
