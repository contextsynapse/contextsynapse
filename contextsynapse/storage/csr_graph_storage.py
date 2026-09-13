"""
CSR (Compressed Sparse Row) Graph Storage Implementation
High-performance graph storage format for efficient traversal operations
"""

import collections
import numpy as np
from typing import Dict, List, Set, Tuple, Optional, Any
import logging
from dataclasses import dataclass
import uuid
import time

logger = logging.getLogger(__name__)

@dataclass
class CSRNode:
    """Node in CSR format."""
    id: str
    node_type: str
    properties: Dict[str, Any]
    index: int  # Index in CSR arrays

@dataclass
class CSREdge:
    """Edge in CSR format."""
    source_id: str
    target_id: str
    edge_type: str
    properties: Dict[str, Any]
    weight: float = 1.0

class CSRGraphStorage:
    """Compressed Sparse Row graph storage for high-performance operations."""
    
    def __init__(self):
        self.nodes: Dict[str, CSRNode] = {}
        self.node_id_to_index: Dict[str, int] = {}
        self.index_to_node_id: Dict[int, str] = {}
        
        # CSR arrays for edges
        self.row_ptr: List[int] = [0]  # Row pointers
        self.col_indices: List[int] = []  # Column indices
        self.edge_data: List[CSREdge] = []  # Edge data
        
        # Edge type mapping
        self.edge_types: Dict[str, Set[str]] = {}  # edge_type -> set of edge indices
        
        # Node type mapping
        self.node_types: Dict[str, Set[str]] = {}  # node_type -> set of node_ids
        
        # Properties index for fast filtering
        self.property_index: Dict[str, Dict[Any, Set[str]]] = {}  # property_name -> value -> set of node_ids

        # Incoming edge index: target_node_id → [edge_index, ...]
        # Makes INCOMING neighbor lookup O(degree) instead of O(E)
        self.incoming_edges: Dict[str, List[int]] = {}

        # Adjacency dicts for O(1) neighbor lookup
        self._adj_out: Dict[str, List] = {}
        self._adj_in: Dict[str, List] = {}
        self._row_ptr_dirty = False

        self._next_index = 0
        
    def add_node(self, node_id: str, node_type: str, properties: Dict[str, Any] = None) -> bool:
        """Add a node to the CSR graph."""
        try:
            if node_id in self.nodes:
                logger.debug(f"Node {node_id} already exists in CSR storage")
                return False  # Node already exists
            
            if properties is None:
                properties = {}
            
            # Create node
            node = CSRNode(
                id=node_id,
                node_type=node_type,
                properties=properties,
                index=self._next_index
            )
            
            # Add to mappings
            self.nodes[node_id] = node
            self.node_id_to_index[node_id] = self._next_index
            self.index_to_node_id[self._next_index] = node_id
            
            # Update node type mapping
            if node_type not in self.node_types:
                self.node_types[node_type] = set()
            self.node_types[node_type].add(node_id)
            
            # Update property index (only for hashable values)
            for prop_name, prop_value in properties.items():
                # Skip unhashable types (lists, dicts) for indexing
                if isinstance(prop_value, (list, dict)):
                    continue  # Can't use as dictionary key
                try:
                    if prop_name not in self.property_index:
                        self.property_index[prop_name] = {}
                    if prop_value not in self.property_index[prop_name]:
                        self.property_index[prop_name][prop_value] = set()
                    self.property_index[prop_name][prop_value].add(node_id)
                except TypeError as e:
                    # Value is unhashable, skip indexing but keep in properties
                    logger.debug(f"Skipping unhashable property {prop_name}: {e}")
                    continue
                except Exception as e:
                    logger.warning(f"Error indexing property {prop_name}: {e}")
                    continue
            
            # Expand CSR arrays
            self.row_ptr.append(self.row_ptr[-1])  # New row starts where previous ended
            
            self._next_index += 1
            return True
        except Exception as e:
            logger.error(f"Error adding node {node_id} to CSR storage: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    def add_edge(self, source_id: str, target_id: str, edge_type: str, 
                 properties: Dict[str, Any] = None, weight: float = 1.0) -> bool:
        """Add an edge to the CSR graph."""
        if source_id not in self.nodes or target_id not in self.nodes:
            return False  # Source or target node doesn't exist
        
        if properties is None:
            properties = {}
        
        # Create edge
        edge = CSREdge(
            source_id=source_id,
            target_id=target_id,
            edge_type=edge_type,
            properties=properties,
            weight=weight
        )
        
        # Get source node index
        source_index = self.node_id_to_index[source_id]
        
        # Ensure row_ptr is long enough
        while len(self.row_ptr) <= source_index + 1:
            self.row_ptr.append(self.row_ptr[-1] if self.row_ptr else 0)
        
        # O(1) append to edge buffer (Netflix-style: append-only, lazy CSR rebuild)
        # Instead of O(E) list.insert + shift, we append to end and maintain
        # an adjacency dict for O(1) neighbor lookup. CSR is rebuilt on save.
        edge_index = len(self.edge_data)
        self.col_indices.append(self.node_id_to_index[target_id])
        self.edge_data.append(edge)

        # Update edge type mapping — O(1) set add
        if edge_type not in self.edge_types:
            self.edge_types[edge_type] = set()
        self.edge_types[edge_type].add(edge_index)

        # Mark CSR row_ptr as stale — will be rebuilt on next CSR-based access.
        # Primary neighbor lookup uses _adj_out/_adj_in dicts (O(1)).
        self._row_ptr_dirty = True

        # Maintain adjacency dict for O(1) neighbor lookups
        self._adj_out.setdefault(source_id, []).append((edge_index, target_id, edge))
        self._adj_in.setdefault(target_id, []).append((edge_index, source_id, edge))

        # Update incoming edge index — O(1) append
        if target_id not in self.incoming_edges:
            self.incoming_edges[target_id] = []
        self.incoming_edges[target_id].append(edge_index)

        return True
    
    def get_node(self, node_id: str) -> Optional[CSRNode]:
        """Get a node by ID."""
        return self.nodes.get(node_id)

    def remove_node(self, node_id: str) -> bool:
        """Remove a node and its connected edges."""
        if node_id not in self.nodes:
            return False

        # Remove from node indexes
        node = self.nodes.pop(node_id)
        idx = self.node_id_to_index.pop(node_id, None)
        if idx is not None:
            self.index_to_node_id.pop(idx, None)

        # Remove from type index
        for type_set in self.node_types.values():
            type_set.discard(node_id)

        # Remove from property index
        for prop_name, val_map in self.property_index.items():
            for val_set in val_map.values():
                val_set.discard(node_id)

        # Remove connected edges from adjacency dicts
        if node_id in self._adj_out:
            del self._adj_out[node_id]
        if node_id in self._adj_in:
            del self._adj_in[node_id]
        # Also remove this node from other nodes' adjacency lists
        for src, edges in list(self._adj_out.items()):
            self._adj_out[src] = [(ei, tid, e) for ei, tid, e in edges if tid != node_id]
        for tgt, edges in list(self._adj_in.items()):
            self._adj_in[tgt] = [(ei, sid, e) for ei, sid, e in edges if sid != node_id]

        # Remove from incoming_edges
        self.incoming_edges.pop(node_id, None)

        # Mark CSR as dirty
        self._row_ptr_dirty = True

        return True

    def get_neighbors(self, node_id: str, edge_type: str = None) -> List[Tuple[str, CSREdge]]:
        """Get neighbors of a node, optionally filtered by edge type."""
        if node_id not in self.nodes:
            return []

        # Fast path: use adjacency dict (O(degree))
        if node_id in self._adj_out:
            neighbors = []
            for edge_index, target_id, edge in self._adj_out[node_id]:
                if edge_type is None or edge.edge_type == edge_type:
                    neighbors.append((target_id, edge))
            return neighbors

        # Fallback: CSR arrays (only if adj dict not available)
        if self._row_ptr_dirty:
            self._rebuild_row_ptr()

        node_index = self.node_id_to_index[node_id]
        if node_index + 1 >= len(self.row_ptr):
            return []
        start_ptr = self.row_ptr[node_index]
        end_ptr = self.row_ptr[node_index + 1]

        neighbors = []
        for i in range(start_ptr, end_ptr):
            if i < len(self.col_indices) and i < len(self.edge_data):
                target_index = self.col_indices[i]
                edge = self.edge_data[i]
                target_id_found = self.index_to_node_id.get(target_index, "")
                if edge_type is None or edge.edge_type == edge_type:
                    neighbors.append((target_id_found, edge))
        return neighbors

    def get_incoming_neighbors(self, node_id: str, edge_type: str = None) -> List[Tuple[str, CSREdge]]:
        """Get incoming neighbors using the incoming edge index — O(degree) not O(E)."""
        if node_id not in self.nodes:
            return []
        indices = self.incoming_edges.get(node_id, [])
        neighbors = []
        for i in indices:
            if i < len(self.edge_data):
                edge = self.edge_data[i]
                if edge_type is None or edge.edge_type == edge_type:
                    neighbors.append((edge.source_id, edge))
        return neighbors

    def _rebuild_row_ptr(self):
        """Rebuild CSR row_ptr from adjacency data. Called lazily before CSR-based access."""
        n = len(self.nodes)
        self.row_ptr = [0] * (n + 1)

        # Count edges per source node
        for edge in self.edge_data:
            src_idx = self.node_id_to_index.get(edge.source_id)
            if src_idx is not None and src_idx < n:
                self.row_ptr[src_idx + 1] += 1

        # Cumulative sum
        for i in range(1, len(self.row_ptr)):
            self.row_ptr[i] += self.row_ptr[i - 1]

        self._row_ptr_dirty = False

    def get_nodes_by_type(self, node_type: str) -> List[str]:
        """Get all node IDs of a specific type. O(1) set lookup."""
        return list(self.node_types.get(node_type, set()))
    
    def get_nodes_by_property(self, property_name: str, property_value: Any) -> List[str]:
        """Get all node IDs with a specific property value."""
        if property_name not in self.property_index:
            return []
        return list(self.property_index[property_name].get(property_value, set()))
    
    def get_edge_count(self) -> int:
        """Get total number of edges."""
        return len(self.edge_data)

    def get_node_count(self) -> int:
        """Get total number of nodes."""
        return len(self.nodes)

    def get_node_types(self) -> Set[str]:
        """Get all unique node types (labels) in the graph."""
        return set(self.node_types.keys())

    def get_edge_types(self) -> Set[str]:
        """Get all unique edge types (labels) in the graph."""
        return set(self.edge_types.keys())

    # NOTE: get_nodes_by_property defined above (line ~252) uses O(1) property_index.
    # A duplicate O(N) scan version was here — removed to use the indexed version.

    def traverse(self, start_node_id: str, edge_label: Optional[str] = None,
                 max_depth: int = 1, direction: str = "OUTGOING") -> List[str]:
        """BFS traversal from a start node."""
        if start_node_id not in self.nodes:
            return []

        visited = set()
        visited.add(start_node_id)
        queue = collections.deque([(start_node_id, 0)])
        result = []

        while queue:
            current_id, depth = queue.popleft()
            if depth >= max_depth:
                continue

            if direction == "OUTGOING" or direction == "BOTH":
                neighbors = self.get_neighbors(current_id, edge_label)
                for neighbor_id, edge in neighbors:
                    if neighbor_id not in visited:
                        visited.add(neighbor_id)
                        result.append(neighbor_id)
                        queue.append((neighbor_id, depth + 1))

            if direction == "INCOMING" or direction == "BOTH":
                incoming = self.get_incoming_neighbors(current_id, edge_label)
                for neighbor_id, edge in incoming:
                    if neighbor_id not in visited:
                        visited.add(neighbor_id)
                        result.append(neighbor_id)
                        queue.append((neighbor_id, depth + 1))

        return result
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get graph statistics."""
        return {
            'node_count': self.get_node_count(),
            'edge_count': self.get_edge_count(),
            'node_types': {nt: len(nodes) for nt, nodes in self.node_types.items()},
            'edge_types': {et: len(edges) for et, edges in self.edge_types.items()},
            'avg_degree': self.get_edge_count() / max(1, self.get_node_count()),
            'csr_size': len(self.row_ptr) - 1,  # Number of rows
            'csr_edges': len(self.col_indices)  # Number of edges in CSR
        }
    
    def optimize(self):
        """Optimize the CSR structure (currently a no-op, but could include sorting, compression, etc.)."""
        # Future: Could sort edges by target for better cache locality
        # Future: Could compress repeated edge types
        pass
    
    def clear(self):
        """Clear all data."""
        self.nodes.clear()
        self.node_id_to_index.clear()
        self.index_to_node_id.clear()
        self.row_ptr = [0]
        self.col_indices.clear()
        self.edge_data.clear()
        self.edge_types.clear()
        self.node_types.clear()
        self.property_index.clear()
        self._adj_out.clear()
        self._adj_in.clear()
        self.incoming_edges.clear()
        self._row_ptr_dirty = False
        self._next_index = 0

class CSRGraphStorageAdapter:
    """Adapter to integrate CSR storage with existing AIContextDB interface."""
    
    def __init__(self, csr_storage: CSRGraphStorage):
        self.csr_storage = csr_storage
        self.alias_mapping: Dict[str, str] = {}  # alias -> node_id
    
    def add_node(self, node_id: str, node_type: str, properties: Dict[str, Any] = None, 
                 alias: str = None) -> bool:
        """Add a node with optional alias.
        
        NOTE: alias parameter is accepted for compatibility but NOT stored.
        Aliases are query-scoped only (stored in executor.node_aliases, not here).
        """
        success = self.csr_storage.add_node(node_id, node_type, properties)
        # NOTE: alias is NOT stored - aliases are query-scoped only (stored in executor)
        return success
    
    def add_edge(self, source_id: str, target_id: str, edge_type: str, 
                 properties: Dict[str, Any] = None, weight: float = 1.0) -> bool:
        """Add an edge."""
        return self.csr_storage.add_edge(source_id, target_id, edge_type, properties, weight)
    
    def get_node_by_alias(self, alias: str) -> Optional[str]:
        """Get node ID by alias.
        
        NOTE: Aliases are query-scoped only (not persisted).
        This method returns None for query-scoped aliases.
        Use executor.node_aliases during query execution instead.
        """
        # Aliases are query-scoped only - not persisted
        return None
    
    def get_node(self, node_id: str) -> Optional[CSRNode]:
        """Get a node."""
        return self.csr_storage.get_node(node_id)
    
    def get_neighbors(self, node_id: str, edge_type: str = None) -> List[Tuple[str, CSREdge]]:
        """Get neighbors."""
        return self.csr_storage.get_neighbors(node_id, edge_type)
    
    def traverse(self, start_node_id: str, edge_type: str = None, 
                max_depth: int = 1, direction: str = 'OUTGOING') -> List[str]:
        """Traverse the graph."""
        return self.csr_storage.traverse(start_node_id, edge_type, max_depth, direction)
    
    def get_nodes_by_type(self, node_type: str) -> List[str]:
        """Get nodes by type."""
        return self.csr_storage.get_nodes_by_type(node_type)
    
    def get_nodes_by_property(self, property_name: str, property_value: Any) -> List[str]:
        """Get nodes by property."""
        return self.csr_storage.get_nodes_by_property(property_name, property_value)
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get statistics."""
        return self.csr_storage.get_statistics()

    def get_all_nodes(self, node_type: str = None) -> list:
        """Get all nodes, optionally filtered by type — Redis-compatible API."""
        if node_type:
            ids = self.csr_storage.node_types.get(node_type, set())
            return [self.csr_storage.nodes[nid] for nid in ids if nid in self.csr_storage.nodes]
        return list(self.csr_storage.nodes.values())

    def get_all_edges(self) -> list:
        """Get all edges — Redis-compatible API."""
        return list(self.csr_storage.edge_data)

    def update_node_properties(self, node_id: str, properties: Dict[str, Any]) -> bool:
        """Update node properties in-place — Redis-compatible API."""
        from datetime import datetime, timezone
        node = self.csr_storage.nodes.get(node_id)
        if not node:
            return False
        properties["_updated_at"] = datetime.now(timezone.utc).isoformat()
        node.properties.update(properties)
        return True

    def get_node_count(self) -> int:
        """Get total node count."""
        return self.csr_storage.get_node_count()

    def get_edge_count(self) -> int:
        """Get total edge count."""
        return self.csr_storage.get_edge_count()

    def remove_node(self, node_id: str) -> bool:
        """Remove a node."""
        return self.csr_storage.remove_node(node_id)

    def get_incoming_neighbors(self, node_id: str, edge_type: str = None):
        """Get incoming neighbors."""
        return self.csr_storage.get_incoming_neighbors(node_id, edge_type)

# Performance testing utilities
def benchmark_csr_vs_networkx():
    """Benchmark CSR storage against NetworkX for various operations."""
    import networkx as nx
    import time
    
    # Test parameters
    num_nodes = 1000
    num_edges = 5000
    
    print("CSR vs NetworkX Performance Benchmark")
    print("=" * 50)
    
    # Generate test data
    nodes = []
    edges = []
    
    for i in range(num_nodes):
        nodes.append((f"node_{i}", "Person", {"id": i, "name": f"Person_{i}"}))
    
    import random
    for i in range(num_edges):
        source = f"node_{random.randint(0, num_nodes-1)}"
        target = f"node_{random.randint(0, num_nodes-1)}"
        edges.append((source, target, "KNOWS", {"weight": random.random()}))
    
    # Test CSR Storage
    print("\nTesting CSR Storage:")
    csr_storage = CSRGraphStorage()
    
    # Add nodes
    start_time = time.time()
    for node_id, node_type, properties in nodes:
        csr_storage.add_node(node_id, node_type, properties)
    csr_node_time = time.time() - start_time
    
    # Add edges
    start_time = time.time()
    for source, target, edge_type, properties in edges:
        csr_storage.add_edge(source, target, edge_type, properties)
    csr_edge_time = time.time() - start_time
    
    # Traverse
    start_time = time.time()
    for i in range(100):
        start_node = f"node_{random.randint(0, num_nodes-1)}"
        csr_storage.traverse(start_node, max_depth=2)
    csr_traverse_time = time.time() - start_time
    
    # Test NetworkX
    print("\nTesting NetworkX:")
    nx_graph = nx.DiGraph()
    
    # Add nodes
    start_time = time.time()
    for node_id, node_type, properties in nodes:
        nx_graph.add_node(node_id, node_type=node_type, **properties)
    nx_node_time = time.time() - start_time
    
    # Add edges
    start_time = time.time()
    for source, target, edge_type, properties in edges:
        nx_graph.add_edge(source, target, edge_type=edge_type, **properties)
    nx_edge_time = time.time() - start_time
    
    # Traverse (using BFS)
    start_time = time.time()
    for i in range(100):
        start_node = f"node_{random.randint(0, num_nodes-1)}"
        try:
            # Simple BFS traversal
            visited = set()
            queue = [start_node]
            depth = 0
            max_depth = 2
            
            while queue and depth < max_depth:
                next_queue = []
                for node in queue:
                    if node not in visited:
                        visited.add(node)
                        next_queue.extend(nx_graph.neighbors(node))
                queue = next_queue
                depth += 1
        except:
            pass
    nx_traverse_time = time.time() - start_time
    
    # Results
    print(f"\nResults ({num_nodes} nodes, {num_edges} edges):")
    print(f"Add Nodes:")
    print(f"  CSR: {csr_node_time:.4f}s")
    print(f"  NetworkX: {nx_node_time:.4f}s")
    print(f"  Speedup: {nx_node_time/csr_node_time:.2f}x")
    
    print(f"\nAdd Edges:")
    print(f"  CSR: {csr_edge_time:.4f}s")
    print(f"  NetworkX: {nx_edge_time:.4f}s")
    print(f"  Speedup: {nx_edge_time/csr_edge_time:.2f}x")
    
    print(f"\nTraverse (100 iterations):")
    print(f"  CSR: {csr_traverse_time:.4f}s")
    print(f"  NetworkX: {nx_traverse_time:.4f}s")
    print(f"  Speedup: {nx_traverse_time/csr_traverse_time:.2f}x")
    
    print(f"\nCSR Statistics:")
    stats = csr_storage.get_statistics()
    for key, value in stats.items():
        print(f"  {key}: {value}")

if __name__ == "__main__":
    # Run benchmark
    benchmark_csr_vs_networkx()
