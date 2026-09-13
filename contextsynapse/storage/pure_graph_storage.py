"""Pure graph storage - no collections, flat structure."""

import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime
try:
    # Try core module first (most likely location)
    from ..core.graph_structures import GraphNode, GraphEdge
except ImportError:
    try:
        # Fallback to graph module if it exists
        from ..graph.graph_node import GraphNode
        from ..graph.graph_edge import GraphEdge
    except ImportError:
        # Use duck typing - accept any object with id, label, properties
        GraphNode = None
        GraphEdge = None

logger = logging.getLogger(__name__)


class PureGraphStorage:
    """Storage for pure graph mode - no collections, flat structure."""
    
    def __init__(self, namespace: str, base_path: Path):
        """Initialize pure graph storage.
        
        Args:
            namespace: Namespace name
            base_path: Base path for namespace data
        """
        self.namespace = namespace
        self.base_path = base_path
        self.graph_path = base_path / "graph"
        self.nodes_path = self.graph_path / "nodes"
        self.edges_path = self.graph_path / "edges"
        self.indexes_path = self.graph_path / "indexes"
        
        # Create directories
        self.nodes_path.mkdir(parents=True, exist_ok=True)
        self.edges_path.mkdir(parents=True, exist_ok=True)
        self.indexes_path.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"[EMOJI] Initialized pure graph storage for namespace: {namespace}")
    
    def store_node(self, node) -> str:
        """Store node in flat structure.
        
        Args:
            node: GraphNode to store
        
        Returns:
            Path where node was stored
        """
        try:
            node_file = self.nodes_path / f"{node.id}.json"
            node_data = {
                'id': node.id,
                'label': node.label,
                'properties': node.properties if hasattr(node, 'properties') else {},
                'created_at': datetime.now().isoformat(),
                'namespace': self.namespace,
                'storage_mode': 'pure_graph'
            }
            
            with open(node_file, 'w', encoding='utf-8') as f:
                json.dump(node_data, f, indent=2, ensure_ascii=False)
            
            logger.debug(f"[EMOJI] Stored node {node.id} in pure graph mode")
            return str(node_file)
        except Exception as e:
            logger.error(f"[EMOJI] Failed to store node {node.id}: {e}")
            raise
    
    def store_edge(self, edge) -> str:
        """Store edge in flat structure.
        
        Args:
            edge: GraphEdge to store
        
        Returns:
            Path where edge was stored
        """
        try:
            edge_file = self.edges_path / f"{edge.id}.json"
            edge_data = {
                'id': edge.id,
                'source': edge.source,
                'target': edge.target,
                'label': edge.label,
                'properties': edge.properties if hasattr(edge, 'properties') else {},
                'created_at': datetime.now().isoformat(),
                'namespace': self.namespace,
                'storage_mode': 'pure_graph'
            }
            
            with open(edge_file, 'w', encoding='utf-8') as f:
                json.dump(edge_data, f, indent=2, ensure_ascii=False)
            
            logger.debug(f"[EMOJI] Stored edge {edge.id} in pure graph mode")
            return str(edge_file)
        except Exception as e:
            logger.error(f"[EMOJI] Failed to store edge {edge.id}: {e}")
            raise
    
    def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Get node by ID.
        
        Args:
            node_id: Node ID
        
        Returns:
            Node data or None if not found
        """
        try:
            node_file = self.nodes_path / f"{node_id}.json"
            if node_file.exists():
                with open(node_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return None
        except Exception as e:
            logger.error(f"[EMOJI] Failed to get node {node_id}: {e}")
            return None
    
    def get_edge(self, edge_id: str) -> Optional[Dict[str, Any]]:
        """Get edge by ID.
        
        Args:
            edge_id: Edge ID
        
        Returns:
            Edge data or None if not found
        """
        try:
            edge_file = self.edges_path / f"{edge_id}.json"
            if edge_file.exists():
                with open(edge_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return None
        except Exception as e:
            logger.error(f"[EMOJI] Failed to get edge {edge_id}: {e}")
            return None
    
    def list_nodes(self, label: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all nodes, optionally filtered by label.
        
        Args:
            label: Optional node label filter
        
        Returns:
            List of node data
        """
        nodes = []
        try:
            for node_file in self.nodes_path.glob("*.json"):
                with open(node_file, 'r', encoding='utf-8') as f:
                    node_data = json.load(f)
                    if label is None or node_data.get('label') == label:
                        nodes.append(node_data)
        except Exception as e:
            logger.error(f"[EMOJI] Failed to list nodes: {e}")
        return nodes
    
    def list_edges(self, label: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all edges, optionally filtered by label.
        
        Args:
            label: Optional edge label filter
        
        Returns:
            List of edge data
        """
        edges = []
        try:
            for edge_file in self.edges_path.glob("*.json"):
                with open(edge_file, 'r', encoding='utf-8') as f:
                    edge_data = json.load(f)
                    if label is None or edge_data.get('label') == label:
                        edges.append(edge_data)
        except Exception as e:
            logger.error(f"[EMOJI] Failed to list edges: {e}")
        return edges

