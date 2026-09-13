"""
Single-File Binary Storage Backend

Packages all namespace data into a single HDF5 file, similar to Oracle's .dbf format.
This provides portability, atomicity, and simplified backup/restore.
"""

import json
import logging
import time
from pathlib import Path
from typing import Dict, Any, Optional, List
import numpy as np

logger = logging.getLogger(__name__)

try:
    import h5py
    HDF5_AVAILABLE = True
except ImportError:
    HDF5_AVAILABLE = False
    logger.warning("h5py not available. Install with: pip install h5py")


class SingleFileStorage:
    """
    Single-file binary storage using HDF5 format.
    
    Packages all namespace data into one file:
    - Graph structure (nodes, edges)
    - Node properties (Parquet-like)
    - CSR matrices
    - Vectors
    - Images
    - Tables
    - Metadata
    """
    
    def __init__(self, file_path: str, mode: str = 'a'):
        """
        Initialize single-file storage.
        
        Args:
            file_path: Path to HDF5 file (e.g., 'namespace.h5')
            mode: File mode ('r' read, 'w' write, 'a' append)
        """
        if not HDF5_AVAILABLE:
            raise ImportError("h5py is required for single-file storage. Install with: pip install h5py")
        
        self.file_path = Path(file_path)
        self.mode = mode
        self.h5_file = None
    
    def __enter__(self):
        """Context manager entry."""
        self.open()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
    
    def open(self):
        """Open HDF5 file."""
        self.h5_file = h5py.File(str(self.file_path), self.mode)
        return self.h5_file
    
    def close(self):
        """Close HDF5 file."""
        if self.h5_file:
            self.h5_file.close()
            self.h5_file = None
    
    def save_graph(self, graph_data: Dict[str, Any]) -> bool:
        """
        Save graph structure to HDF5.
        
        Args:
            graph_data: Dictionary with 'nodes' and 'edges' lists
            
        Returns:
            True if successful
        """
        try:
            if not self.h5_file:
                self.open()
            
            # Create graph group
            if '/graph' in self.h5_file:
                del self.h5_file['/graph']
            graph_group = self.h5_file.create_group('/graph')
            
            # Save nodes
            nodes = graph_data.get('nodes', [])
            if nodes:
                # Store nodes as structured array
                node_ids = [n['id'] for n in nodes]
                labels = [n.get('label', 'Node') for n in nodes]
                
                # Create datasets
                graph_group.create_dataset('node_ids', data=[nid.encode('utf-8') for nid in node_ids], dtype=h5py.string_dtype())
                graph_group.create_dataset('labels', data=[l.encode('utf-8') for l in labels], dtype=h5py.string_dtype())
                
                # Store properties as JSON strings (one per node)
                properties_json = [json.dumps(n.get('properties', {})) for n in nodes]
                graph_group.create_dataset('properties', data=[p.encode('utf-8') for p in properties_json], dtype=h5py.string_dtype())
                
                graph_group.attrs['node_count'] = len(nodes)
            
            # Save edges
            edges = graph_data.get('edges', [])
            if edges:
                # Store edges as structured array
                sources = [e.get('src', e.get('source', '')) for e in edges]
                targets = [e.get('dst', e.get('target', '')) for e in edges]
                edge_labels = [e.get('label', 'Edge') for e in edges]
                
                graph_group.create_dataset('edge_sources', data=[s.encode('utf-8') for s in sources], dtype=h5py.string_dtype())
                graph_group.create_dataset('edge_targets', data=[t.encode('utf-8') for t in targets], dtype=h5py.string_dtype())
                graph_group.create_dataset('edge_labels', data=[l.encode('utf-8') for l in edge_labels], dtype=h5py.string_dtype())
                
                # Store edge properties
                edge_props_json = [json.dumps(e.get('properties', {})) for e in edges]
                graph_group.create_dataset('edge_properties', data=[p.encode('utf-8') for p in edge_props_json], dtype=h5py.string_dtype())
                
                graph_group.attrs['edge_count'] = len(edges)
            
            # Save aliases
            if 'alias_to_node' in graph_data:
                aliases = graph_data['alias_to_node']
                if aliases:
                    alias_keys = list(aliases.keys())
                    alias_values = [aliases[k] for k in alias_keys]
                    graph_group.create_dataset('alias_keys', data=[k.encode('utf-8') for k in alias_keys], dtype=h5py.string_dtype())
                    graph_group.create_dataset('alias_values', data=[v.encode('utf-8') for v in alias_values], dtype=h5py.string_dtype())
                    graph_group.attrs['alias_count'] = len(aliases)
            
            # Save metadata
            metadata = graph_data.get('metadata', {})
            if metadata:
                graph_group.attrs['metadata'] = json.dumps(metadata)
            
            logger.info(f"Saved graph to HDF5: {len(nodes)} nodes, {len(edges)} edges")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save graph to HDF5: {e}")
            return False
    
    def load_graph(self) -> Optional[Dict[str, Any]]:
        """
        Load graph structure from HDF5.
        
        Returns:
            Dictionary with 'nodes' and 'edges' lists, or None if error
        """
        try:
            if not self.h5_file:
                self.open()
            
            if '/graph' not in self.h5_file:
                logger.warning("No graph data found in HDF5 file")
                return None
            
            graph_group = self.h5_file['/graph']
            
            # Load nodes
            nodes = []
            if 'node_ids' in graph_group:
                node_ids = [nid.decode('utf-8') if isinstance(nid, bytes) else nid for nid in graph_group['node_ids'][:]]
                labels = [l.decode('utf-8') if isinstance(l, bytes) else l for l in graph_group['labels'][:]]
                properties_json = [p.decode('utf-8') if isinstance(p, bytes) else p for p in graph_group['properties'][:]]
                
                for node_id, label, props_json in zip(node_ids, labels, properties_json):
                    properties = json.loads(props_json) if props_json else {}
                    nodes.append({
                        'id': node_id,
                        'label': label,
                        'properties': properties
                    })
            
            # Load edges
            edges = []
            if 'edge_sources' in graph_group and 'edge_targets' in graph_group:
                sources = [s.decode('utf-8') if isinstance(s, bytes) else s for s in graph_group['edge_sources'][:]]
                targets = [t.decode('utf-8') if isinstance(t, bytes) else t for t in graph_group['edge_targets'][:]]
                edge_labels = [l.decode('utf-8') if isinstance(l, bytes) else l for l in graph_group.get('edge_labels', sources)[:]] if 'edge_labels' in graph_group else ['EDGE'] * len(sources)
                edge_props_json = [p.decode('utf-8') if isinstance(p, bytes) else p for p in graph_group['edge_properties'][:]] if 'edge_properties' in graph_group else ['{}'] * len(sources)
                
                for src, dst, label, props_json in zip(sources, targets, edge_labels, edge_props_json):
                    properties = json.loads(props_json) if props_json else {}
                    edges.append({
                        'src': src,
                        'dst': dst,
                        'source': src,
                        'target': dst,
                        'label': label,
                        'properties': properties
                    })
            
            # Load aliases
            alias_to_node = {}
            if 'alias_keys' in graph_group:
                alias_keys = [k.decode('utf-8') if isinstance(k, bytes) else k for k in graph_group['alias_keys'][:]]
                alias_values = [v.decode('utf-8') if isinstance(v, bytes) else v for v in graph_group['alias_values'][:]]
                alias_to_node = dict(zip(alias_keys, alias_values))
            
            # Load metadata
            metadata = {}
            if 'metadata' in graph_group.attrs:
                metadata = json.loads(graph_group.attrs['metadata'])
            
            logger.info(f"Loaded graph from HDF5: {len(nodes)} nodes, {len(edges)} edges")
            
            return {
                'nodes': nodes,
                'edges': edges,
                'alias_to_node': alias_to_node,
                'metadata': metadata
            }
            
        except Exception as e:
            logger.error(f"Failed to load graph from HDF5: {e}")
            return None
    
    def save_csr_matrix(self, csr_matrix, metadata: Dict[str, Any]) -> bool:
        """Save CSR matrix to HDF5."""
        try:
            if not self.h5_file:
                self.open()
            
            if '/csr' in self.h5_file:
                del self.h5_file['/csr']
            csr_group = self.h5_file.create_group('/csr')
            
            # Store CSR matrix components
            csr_group.create_dataset('indices', data=csr_matrix.indices, compression='gzip')
            csr_group.create_dataset('indptr', data=csr_matrix.indptr, compression='gzip')
            csr_group.create_dataset('data', data=csr_matrix.data, compression='gzip')
            csr_group.attrs['shape'] = csr_matrix.shape
            csr_group.attrs['metadata'] = json.dumps(metadata)
            
            logger.info(f"Saved CSR matrix to HDF5: shape {csr_matrix.shape}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save CSR matrix: {e}")
            return False
    
    def load_csr_matrix(self):
        """Load CSR matrix from HDF5."""
        try:
            if not self.h5_file:
                self.open()
            
            if '/csr' not in self.h5_file:
                return None, None
            
            csr_group = self.h5_file['/csr']
            
            from scipy.sparse import csr_matrix
            indices = csr_group['indices'][:]
            indptr = csr_group['indptr'][:]
            data = csr_group['data'][:]
            shape = tuple(csr_group.attrs['shape'])
            
            matrix = csr_matrix((data, indices, indptr), shape=shape)
            metadata = json.loads(csr_group.attrs['metadata'])
            
            return matrix, metadata
            
        except Exception as e:
            logger.error(f"Failed to load CSR matrix: {e}")
            return None, None
    
    def save_vectors(self, collection: str, vectors: np.ndarray, metadata: Optional[Dict] = None) -> bool:
        """Save vectors to HDF5."""
        try:
            if not self.h5_file:
                self.open()
            
            vec_group = self.h5_file.create_group(f'/vectors/{collection}')
            vec_group.create_dataset('vectors', data=vectors, compression='gzip')
            if metadata:
                vec_group.attrs['metadata'] = json.dumps(metadata)
            
            logger.info(f"Saved vectors to HDF5: {collection}, shape {vectors.shape}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save vectors: {e}")
            return False
    
    def load_vectors(self, collection: str):
        """Load vectors from HDF5."""
        try:
            if not self.h5_file:
                self.open()
            
            if f'/vectors/{collection}' not in self.h5_file:
                return None, None
            
            vec_group = self.h5_file[f'/vectors/{collection}']
            vectors = vec_group['vectors'][:]
            metadata = json.loads(vec_group.attrs.get('metadata', '{}'))
            
            return vectors, metadata
            
        except Exception as e:
            logger.error(f"Failed to load vectors: {e}")
            return None, None
    
    def save_image(self, collection: str, node_id: str, image_data: bytes) -> bool:
        """Save image to HDF5."""
        try:
            if not self.h5_file:
                self.open()
            
            img_group = self.h5_file.create_group(f'/images/{collection}')
            img_group.create_dataset(node_id, data=np.frombuffer(image_data, dtype=np.uint8), compression='gzip')
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to save image: {e}")
            return False
    
    def save_table(self, collection: str, table_id: str, table_data, metadata: Optional[Dict] = None) -> bool:
        """Save table (Parquet-like) to HDF5."""
        try:
            if not self.h5_file:
                self.open()
            
            try:
                import pandas as pd
                if isinstance(table_data, pd.DataFrame):
                    # Convert DataFrame to structured array
                    table_group = self.h5_file.create_group(f'/tables/{collection}')
                    table_group.create_dataset(table_id, data=table_data.to_records(), compression='gzip')
                    table_group.attrs['columns'] = json.dumps(list(table_data.columns))
                    if metadata:
                        table_group.attrs['metadata'] = json.dumps(metadata)
                    return True
            except ImportError:
                pass
            
            # Fallback: store as JSON
            table_group = self.h5_file.create_group(f'/tables/{collection}')
            table_group.create_dataset(table_id, data=json.dumps(table_data).encode('utf-8'), dtype=h5py.string_dtype())
            if metadata:
                table_group.attrs['metadata'] = json.dumps(metadata)
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to save table: {e}")
            return False
    
    def get_file_size(self) -> int:
        """Get HDF5 file size in bytes."""
        if self.file_path.exists():
            return self.file_path.stat().st_size
        return 0
    
    def get_info(self) -> Dict[str, Any]:
        """Get information about the HDF5 file."""
        info = {
            'file_path': str(self.file_path),
            'exists': self.file_path.exists(),
            'size_bytes': self.get_file_size(),
            'groups': []
        }
        
        if self.file_path.exists():
            try:
                with h5py.File(str(self.file_path), 'r') as f:
                    def visit(name, obj):
                        if isinstance(obj, h5py.Group):
                            info['groups'].append(name)
                    f.visititems(visit)
            except Exception as e:
                logger.error(f"Failed to read HDF5 info: {e}")
        
        return info







