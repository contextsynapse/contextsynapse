"""
Query Optimizer for AIContextDB
Optimizes queries for better performance across different storage backends
"""

import logging
from typing import Dict, List, Any, Optional, Union, Tuple
from dataclasses import dataclass
from enum import Enum
import time

logger = logging.getLogger(__name__)

class QueryType(Enum):
    """Types of queries."""
    SELECT = "select"
    COUNT = "count"
    TRAVERSE = "traverse"
    AGGREGATE = "aggregate"
    SEARCH = "search"

class StorageBackend(Enum):
    """Available storage backends."""
    NETWORKX = "networkx"
    CSR = "csr"
    COLUMNAR = "columnar"
    HYBRID = "hybrid"

@dataclass
class QueryPlan:
    """Represents an optimized query plan."""
    query_type: QueryType
    storage_backend: StorageBackend
    estimated_cost: float
    estimated_time: float
    operations: List[str]
    indexes_used: List[str]
    optimization_applied: List[str]

class QueryOptimizer:
    """Optimizes queries for different storage backends."""
    
    def __init__(self):
        self.optimization_rules = {
            QueryType.SELECT: self._optimize_select_query,
            QueryType.COUNT: self._optimize_count_query,
            QueryType.TRAVERSE: self._optimize_traverse_query,
            QueryType.AGGREGATE: self._optimize_aggregate_query,
            QueryType.SEARCH: self._optimize_search_query
        }
        
        # Performance statistics
        self.stats = {
            'total_queries': 0,
            'optimized_queries': 0,
            'cache_hits': 0,
            'average_optimization_time': 0.0
        }
    
    def optimize_query(self, query: str, query_type: QueryType,
                      data_stats: Dict[str, Any]) -> QueryPlan:
        """Optimize a query based on its type and data characteristics."""
        start_time = time.time()
        
        try:
            # Determine best storage backend
            backend = self._select_optimal_backend(query_type, data_stats)
            
            # Apply query-specific optimizations
            optimization_func = self.optimization_rules.get(query_type)
            if optimization_func:
                operations, indexes, optimizations = optimization_func(query, data_stats)
            else:
                operations, indexes, optimizations = [query], [], []
            
            # Estimate cost and time
            cost = self._estimate_cost(query_type, data_stats, backend)
            estimated_time = self._estimate_time(query_type, data_stats, backend)
            
            # Create query plan
            plan = QueryPlan(
                query_type=query_type,
                storage_backend=backend,
                estimated_cost=cost,
                estimated_time=estimated_time,
                operations=operations,
                indexes_used=indexes,
                optimization_applied=optimizations
            )
            
            # Update statistics
            self._update_stats(time.time() - start_time)
            
            logger.info(f"Query optimized: {query_type.value} -> {backend.value}")
            return plan
            
        except Exception as e:
            logger.error(f"Query optimization failed: {e}")
            # Return default plan
            return QueryPlan(
                query_type=query_type,
                storage_backend=StorageBackend.NETWORKX,
                estimated_cost=1.0,
                estimated_time=0.1,
                operations=[query],
                indexes_used=[],
                optimization_applied=[]
            )
    
    def _select_optimal_backend(self, query_type: QueryType, 
                              data_stats: Dict[str, Any]) -> StorageBackend:
        """Select the optimal storage backend for the query."""
        node_count = data_stats.get('node_count', 0)
        edge_count = data_stats.get('edge_count', 0)
        
        if query_type == QueryType.TRAVERSE:
            # CSR is optimal for traversal queries
            if edge_count > 1000:
                return StorageBackend.CSR
            else:
                return StorageBackend.NETWORKX
        
        elif query_type == QueryType.COUNT:
            # Columnar is optimal for count queries
            if node_count > 10000:
                return StorageBackend.COLUMNAR
            else:
                return StorageBackend.NETWORKX
        
        elif query_type == QueryType.AGGREGATE:
            # Columnar is optimal for aggregations
            return StorageBackend.COLUMNAR
        
        elif query_type == QueryType.SEARCH:
            # Hybrid is optimal for search queries
            return StorageBackend.HYBRID
        
        else:
            # Default to NetworkX for simple queries
            return StorageBackend.NETWORKX
    
    def _optimize_select_query(self, query: str, data_stats: Dict[str, Any]) -> Tuple[List[str], List[str], List[str]]:
        """Optimize SELECT queries."""
        operations = [query]
        indexes = []
        optimizations = []
        
        # Add index usage if available
        if 'text_index' in data_stats.get('available_indexes', []):
            indexes.append('text_index')
            optimizations.append('text_index_usage')
        
        # Add projection optimization
        if 'WHERE' in query.upper():
            optimizations.append('predicate_pushdown')
        
        return operations, indexes, optimizations
    
    def _optimize_count_query(self, query: str, data_stats: Dict[str, Any]) -> Tuple[List[str], List[str], List[str]]:
        """Optimize COUNT queries."""
        operations = [query]
        indexes = []
        optimizations = []
        
        # Use columnar storage for count queries
        optimizations.append('columnar_count')
        
        # Add index usage if available
        if 'count_index' in data_stats.get('available_indexes', []):
            indexes.append('count_index')
            optimizations.append('count_index_usage')
        
        return operations, indexes, optimizations
    
    def _optimize_traverse_query(self, query: str, data_stats: Dict[str, Any]) -> Tuple[List[str], List[str], List[str]]:
        """Optimize TRAVERSE queries."""
        operations = [query]
        indexes = []
        optimizations = []
        
        # Use CSR for traversal
        optimizations.append('csr_traversal')
        
        # Add graph index usage
        if 'graph_index' in data_stats.get('available_indexes', []):
            indexes.append('graph_index')
            optimizations.append('graph_index_usage')
        
        # Add depth limiting
        if 'DEPTH' not in query.upper():
            optimizations.append('depth_limiting')
        
        return operations, indexes, optimizations
    
    def _optimize_aggregate_query(self, query: str, data_stats: Dict[str, Any]) -> Tuple[List[str], List[str], List[str]]:
        """Optimize AGGREGATE queries."""
        operations = [query]
        indexes = []
        optimizations = []
        
        # Use columnar storage for aggregations
        optimizations.append('columnar_aggregation')
        
        # Add grouping optimization
        if 'GROUP BY' in query.upper():
            optimizations.append('grouping_optimization')
        
        return operations, indexes, optimizations
    
    def _optimize_search_query(self, query: str, data_stats: Dict[str, Any]) -> Tuple[List[str], List[str], List[str]]:
        """Optimize SEARCH queries."""
        operations = [query]
        indexes = []
        optimizations = []
        
        # Use hybrid search
        optimizations.append('hybrid_search')
        
        # Add vector index usage
        if 'vector_index' in data_stats.get('available_indexes', []):
            indexes.append('vector_index')
            optimizations.append('vector_index_usage')
        
        # Add text index usage
        if 'text_index' in data_stats.get('available_indexes', []):
            indexes.append('text_index')
            optimizations.append('text_index_usage')
        
        return operations, indexes, optimizations
    
    def _estimate_cost(self, query_type: QueryType, data_stats: Dict[str, Any], 
                      backend: StorageBackend) -> float:
        """Estimate the cost of executing the query."""
        node_count = data_stats.get('node_count', 0)
        edge_count = data_stats.get('edge_count', 0)
        
        # Base cost by query type
        base_costs = {
            QueryType.SELECT: 1.0,
            QueryType.COUNT: 0.5,
            QueryType.TRAVERSE: 2.0,
            QueryType.AGGREGATE: 1.5,
            QueryType.SEARCH: 3.0
        }
        
        base_cost = base_costs.get(query_type, 1.0)
        
        # Scale by data size
        if backend == StorageBackend.CSR:
            scale_factor = edge_count / 1000.0
        elif backend == StorageBackend.COLUMNAR:
            scale_factor = node_count / 10000.0
        else:
            scale_factor = (node_count + edge_count) / 1000.0
        
        return base_cost * max(1.0, scale_factor)
    
    def _estimate_time(self, query_type: QueryType, data_stats: Dict[str, Any], 
                      backend: StorageBackend) -> float:
        """Estimate the execution time of the query."""
        cost = self._estimate_cost(query_type, data_stats, backend)
        
        # Convert cost to estimated time (milliseconds)
        time_multipliers = {
            StorageBackend.NETWORKX: 10.0,
            StorageBackend.CSR: 1.0,
            StorageBackend.COLUMNAR: 2.0,
            StorageBackend.HYBRID: 5.0
        }
        
        multiplier = time_multipliers.get(backend, 10.0)
        return cost * multiplier
    
    def _update_stats(self, optimization_time: float):
        """Update optimization statistics."""
        self.stats['total_queries'] += 1
        self.stats['optimized_queries'] += 1
        
        # Update average optimization time
        total_time = self.stats['average_optimization_time'] * (self.stats['optimized_queries'] - 1)
        self.stats['average_optimization_time'] = (total_time + optimization_time) / self.stats['optimized_queries']
    
    def get_optimization_stats(self) -> Dict[str, Any]:
        """Get optimization statistics."""
        return self.stats.copy()
    
    def suggest_indexes(self, query_patterns: List[str]) -> List[str]:
        """Suggest indexes based on query patterns."""
        suggestions = []
        
        for pattern in query_patterns:
            if 'WHERE' in pattern.upper():
                suggestions.append('text_index')
            if 'TRAVERSE' in pattern.upper():
                suggestions.append('graph_index')
            if 'SEARCH' in pattern.upper():
                suggestions.append('vector_index')
            if 'COUNT' in pattern.upper():
                suggestions.append('count_index')
        
        return list(set(suggestions))













