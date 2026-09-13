"""
Hybrid Storage V2 Implementation
Enhanced version with better performance and advanced features
"""

import logging
from typing import Dict, List, Any, Optional, Union, Tuple
from pathlib import Path
import json
from datetime import datetime
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

from .columnar_store_v2 import ColumnarStoreV2
from .csr_store import CSREdgeStore

logger = logging.getLogger(__name__)

class HybridStoreV2:
    """Enhanced hybrid storage with async operations and advanced features."""
    
    def __init__(self, storage_path: str = "contextcore_data"):
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        
        # Initialize enhanced storage backends
        self.columnar_store = ColumnarStoreV2(str(self.storage_path / "parquet"))
        self.csr_store = CSREdgeStore()
        
        # Enhanced configuration
        self.config = {
            'use_columnar': True,
            'use_csr': True,
            'auto_optimize': True,
            'async_operations': True,
            'cache_size': 10000,
            'batch_size': 1000,
            'compression': 'snappy',
            'indexing': True
        }
        
        # Thread pool for async operations
        self.executor = ThreadPoolExecutor(max_workers=4)
        
        # Cache for frequently accessed data
        self.cache: Dict[str, Any] = {}
        
    async def store_graph_data_async(self, graph_data: Dict[str, Any], 
                                   namespace: str = "default") -> bool:
        """Asynchronously store graph data using hybrid approach."""
        try:
            tasks = []
            
            # Store nodes in columnar format (async)
            if 'nodes' in graph_data and self.config['use_columnar']:
                task = self._store_nodes_async(graph_data['nodes'], namespace)
                tasks.append(task)
            
            # Store edges in CSR format (async)
            if 'edges' in graph_data and self.config['use_csr']:
                task = self._store_edges_async(graph_data['edges'], namespace)
                tasks.append(task)
            
            # Wait for all tasks to complete
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Check for errors
            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"Async storage task failed: {result}")
                    return False
            
            logger.info(f"Async stored graph data for namespace '{namespace}'")
            return True
            
        except Exception as e:
            logger.error(f"Failed to async store graph data: {e}")
            return False
    
    async def _store_nodes_async(self, nodes: List[Dict[str, Any]], 
                               namespace: str) -> bool:
        """Asynchronously store nodes."""
        try:
            # Process nodes in batches
            batch_size = self.config['batch_size']
            for i in range(0, len(nodes), batch_size):
                batch = nodes[i:i + batch_size]
                
                # Convert to columnar format
                nodes_data = []
                for node in batch:
                    nodes_data.append({
                        'id': node.get('id', ''),
                        'label': node.get('label', ''),
                        'properties': json.dumps(node.get('properties', {})),
                        'namespace': namespace,
                        'created_at': datetime.now().isoformat()
                    })
                
                # Store batch
                table_name = f"{namespace}_nodes"
                if not self.columnar_store.create_table(table_name, {
                    'id': 'string',
                    'label': 'string',
                    'properties': 'string',
                    'namespace': 'string',
                    'created_at': 'string'
                }):
                    return False
                
                self.columnar_store.insert_data(table_name, nodes_data)
                
                # Yield control to other tasks
                await asyncio.sleep(0)
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to async store nodes: {e}")
            return False
    
    async def _store_edges_async(self, edges: List[Dict[str, Any]], 
                               namespace: str) -> bool:
        """Asynchronously store edges."""
        try:
            # Process edges in batches
            batch_size = self.config['batch_size']
            for i in range(0, len(edges), batch_size):
                batch = edges[i:i + batch_size]
                
                # Store batch
                for edge in batch:
                    self.csr_store.add_edge(
                        edge.get('source', ''),
                        edge.get('target', ''),
                        edge.get('label', ''),
                        edge.get('properties', {})
                    )
                
                # Yield control to other tasks
                await asyncio.sleep(0)
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to async store edges: {e}")
            return False
    
    def query_graph_data_optimized(self, namespace: str = "default",
                                  query_type: str = "nodes",
                                  conditions: Optional[Dict[str, Any]] = None,
                                  limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Optimized query with caching and indexing."""
        try:
            # Check cache first
            cache_key = f"{namespace}_{query_type}_{str(conditions)}"
            if cache_key in self.cache:
                logger.debug(f"Cache hit for query: {cache_key}")
                return self.cache[cache_key]
            
            results = []
            
            if query_type == "nodes" and self.config['use_columnar']:
                table_name = f"{namespace}_nodes"
                table = self.columnar_store.query(table_name, conditions)
                
                # Convert to list of dicts
                if table.num_rows > 0:
                    df = table.to_pandas()
                    results = df.to_dict('records')
                    
                    # Parse JSON properties
                    for result in results:
                        if 'properties' in result:
                            try:
                                result['properties'] = json.loads(result['properties'])
                            except:
                                result['properties'] = {}
            
            elif query_type == "edges" and self.config['use_csr']:
                edges = self.csr_store.get_all_edges()
                results = [{'source': e.source_id, 'target': e.target_id,
                           'label': e.edge_type, 'properties': e.properties}
                          for e in edges]
            
            # Apply limit
            if limit and len(results) > limit:
                results = results[:limit]
            
            # Cache results
            if len(self.cache) < self.config['cache_size']:
                self.cache[cache_key] = results
            
            return results
            
        except Exception as e:
            logger.error(f"Failed to query graph data: {e}")
            return []
    
    def optimize_storage_advanced(self) -> bool:
        """Advanced storage optimization with performance tuning."""
        try:
            if not self.config['auto_optimize']:
                return True
            
            # Get detailed statistics
            columnar_stats = self.columnar_store.get_statistics()
            csr_stats = self.csr_store.get_statistics()
            
            # Performance tuning based on data characteristics
            if columnar_stats['total_rows'] > 100000:
                logger.info("Very large dataset detected, enabling advanced optimizations")
                self.config['batch_size'] = 5000
                self.config['cache_size'] = 50000
            
            if csr_stats['total_edges'] > 1000000:
                logger.info("Very large graph detected, enabling CSR optimizations")
                self.config['use_csr'] = True
                self.config['indexing'] = True
            
            # Memory optimization
            if columnar_stats['total_size_bytes'] > 1024 * 1024 * 1024:  # 1GB
                logger.info("Large memory usage detected, enabling compression")
                self.config['compression'] = 'gzip'
            
            logger.info("Advanced storage optimization completed")
            return True
            
        except Exception as e:
            logger.error(f"Failed to optimize storage: {e}")
            return False
    
    def get_enhanced_statistics(self) -> Dict[str, Any]:
        """Get enhanced statistics with performance metrics."""
        columnar_stats = self.columnar_store.get_statistics()
        csr_stats = self.csr_store.get_statistics()
        
        return {
            'hybrid_config': self.config,
            'columnar_stats': columnar_stats,
            'csr_stats': csr_stats,
            'cache_stats': {
                'cache_size': len(self.cache),
                'max_cache_size': self.config['cache_size']
            },
            'performance_metrics': {
                'async_operations': self.config['async_operations'],
                'batch_size': self.config['batch_size'],
                'compression': self.config['compression'],
                'indexing_enabled': self.config['indexing']
            },
            'total_namespaces': len(set([name.split('_')[0] for name in columnar_stats['table_names']])),
            'storage_path': str(self.storage_path)
        }
    
    def cleanup_cache(self):
        """Clean up cache to free memory."""
        self.cache.clear()
        logger.info("Cache cleaned up")
    
    def __del__(self):
        """Cleanup resources."""
        if hasattr(self, 'executor'):
            self.executor.shutdown(wait=False)













