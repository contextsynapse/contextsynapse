"""
Query optimizer to leverage indexes for fast property filtering.
"""

from typing import Dict, Any, List, Set, Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)

@dataclass
class QueryPlan:
    """Optimized query execution plan."""
    use_index: bool = False
    filtered_ids: Optional[Set[str]] = None
    filter_property: Optional[str] = None
    filter_operator: Optional[str] = None
    filter_value: Any = None
    select_fields: List[str] = None
    traversal_needed: bool = False
    
    def __init__(self):
        self.select_fields = []

class QueryOptimizer:
    """Automatically optimize queries using indexes."""
    
    def __init__(self, graph_storage):
        self.graph_storage = graph_storage
    
    def optimize(self, ast_node) -> QueryPlan:
        """Generate optimized query plan."""
        query_plan = QueryPlan()
        
        # Check WHERE clause
        if hasattr(ast_node, 'parameters') and 'where' in ast_node.parameters:
            where_clauses = ast_node.parameters['where']
            
            if isinstance(where_clauses, list) and len(where_clauses) > 0:
                # Find which clauses can use indexes
                for clause in where_clauses:
                    if isinstance(clause, dict):
                        prop = clause.get('property') or clause.get('field')
                        op = clause.get('operator') or clause.get('op')
                        value = clause.get('value')
                        
                        # Check if index exists for this property
                        if prop and self._has_index(prop):
                            # Use index (fast!)
                            filtered_ids = self._filter_with_index(prop, op, value)
                            query_plan.use_index = True
                            query_plan.filtered_ids = filtered_ids
                            query_plan.filter_property = prop
                            query_plan.filter_operator = op
                            query_plan.filter_value = value
                            break
        
        # Check SELECT fields
        if hasattr(ast_node, 'parameters'):
            if 'select' in ast_node.parameters:
                query_plan.select_fields = ast_node.parameters['select']
            elif 'select_items' in ast_node.parameters:
                query_plan.select_fields = [item.get('field', '*') 
                                           for item in ast_node.parameters['select_items']]
        
        # Check traversal
        if hasattr(ast_node, 'parameters') and 'traversal' in ast_node.parameters:
            query_plan.traversal_needed = True
        
        return query_plan
    
    def _has_index(self, property_name: str) -> bool:
        """Check if index exists for property."""
        if hasattr(self.graph_storage, '_property_indexes'):
            return property_name in self.graph_storage._property_indexes
        return False
    
    def _filter_with_index(self, prop: str, op: str, value: Any) -> Set[str]:
        """Filter using index."""
        if op in ['>', '<', '>=', '<=']:
            return self.graph_storage.filter_by_property_range(prop, op, value)
        else:
            return self.graph_storage.filter_by_property(prop, value)
    
    def execute_plan(self, query_plan: QueryPlan, ast_node) -> List[Dict[str, Any]]:
        """Execute optimized query plan."""
        results = []
        
        # Start with filtered node IDs
        node_ids = query_plan.filtered_ids if query_plan.filtered_ids else None
        
        # If no index filtering, need to scan
        if node_ids is None:
            # Fallback to full scan
            for node_id in self.graph_storage.nx_graph.nodes():
                node_ids = node_ids or set()
                node_ids.add(node_id)
        
        # Get nodes
        for node_id in node_ids:
            node_data = self.graph_storage.get_node(node_id)
            if node_data:
                result = {}
                
                # Select fields
                if query_plan.select_fields:
                    for field in query_plan.select_fields:
                        result[field] = node_data.properties.get(field)
                else:
                    # Return all properties
                    result = node_data.properties.copy()
                    result['id'] = node_id
                    result['label'] = node_data.label
                
                results.append(result)
        
        return results









