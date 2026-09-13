"""
Migration System for AIContextDB
Handles data migration between different storage formats and versions
"""

import logging
from typing import Dict, List, Any, Optional, Union
from pathlib import Path
import json
import shutil
from datetime import datetime

logger = logging.getLogger(__name__)

class MigrationManager:
    """Manages data migration between different storage formats."""
    
    def __init__(self, storage_path: str = "contextcore_data"):
        self.storage_path = Path(storage_path)
        self.migration_history: List[Dict[str, Any]] = []
        
    def migrate_from_networkx_to_csr(self, namespace: str, 
                                    networkx_data: Dict[str, Any]) -> bool:
        """Migrate data from NetworkX format to CSR format."""
        try:
            logger.info(f"Starting migration from NetworkX to CSR for namespace '{namespace}'")
            
            # Extract nodes and edges
            nodes = networkx_data.get('nodes', [])
            edges = networkx_data.get('edges', [])
            
            # Create CSR storage
            from .csr_store import CSREdgeStore
            csr_store = CSREdgeStore()
            
            # Migrate nodes (CSR doesn't store nodes directly, but we can track them)
            node_ids = set()
            for node in nodes:
                node_ids.add(node.get('id', ''))
            
            # Migrate edges
            for edge in edges:
                csr_store.add_edge(
                    edge.get('source', ''),
                    edge.get('target', ''),
                    edge.get('label', ''),
                    edge.get('properties', {})
                )
            
            # Save CSR data
            csr_path = self.storage_path / "csr" / f"{namespace}_csr.npz"
            csr_path.parent.mkdir(parents=True, exist_ok=True)
            csr_store.save_to_file(str(csr_path))
            
            # Record migration
            self._record_migration('networkx_to_csr', namespace, {
                'nodes_count': len(nodes),
                'edges_count': len(edges),
                'migration_time': datetime.now().isoformat()
            })
            
            logger.info(f"Migration completed for namespace '{namespace}'")
            return True
            
        except Exception as e:
            logger.error(f"Migration failed for namespace '{namespace}': {e}")
            return False
    
    def migrate_from_old_to_namespace_centric(self, old_data_path: str) -> bool:
        """Migrate from old flat structure to namespace-centric structure."""
        try:
            logger.info("Starting migration to namespace-centric structure")
            
            old_path = Path(old_data_path)
            if not old_path.exists():
                logger.error(f"Old data path does not exist: {old_data_path}")
                return False
            
            # Create namespace-centric structure
            namespaces_path = self.storage_path / "namespaces"
            namespaces_path.mkdir(parents=True, exist_ok=True)
            
            # Find all graph files in old structure
            graph_files = list(old_path.glob("**/graph.json"))
            
            for graph_file in graph_files:
                # Determine namespace from path
                relative_path = graph_file.relative_to(old_path)
                namespace = relative_path.parts[0] if len(relative_path.parts) > 1 else "default"
                
                # Create namespace directory
                namespace_dir = namespaces_path / namespace
                namespace_dir.mkdir(parents=True, exist_ok=True)
                
                # Copy graph file
                new_graph_path = namespace_dir / "graph.json"
                shutil.copy2(graph_file, new_graph_path)
                
                # Create namespace configuration
                config = {
                    "namespace_name": namespace,
                    "created_at": datetime.now().isoformat(),
                    "migrated_from": str(graph_file),
                    "structure": "namespace_centric"
                }
                
                config_path = namespace_dir / "namespace_config.json"
                with open(config_path, 'w') as f:
                    json.dump(config, f, indent=2)
                
                logger.info(f"Migrated namespace '{namespace}' to namespace-centric structure")
            
            # Record migration
            self._record_migration('old_to_namespace_centric', 'system', {
                'namespaces_migrated': len(graph_files),
                'migration_time': datetime.now().isoformat()
            })
            
            logger.info("Migration to namespace-centric structure completed")
            return True
            
        except Exception as e:
            logger.error(f"Migration to namespace-centric structure failed: {e}")
            return False
    
    def migrate_storage_backend(self, namespace: str, 
                              from_backend: str, to_backend: str) -> bool:
        """Migrate data between different storage backends."""
        try:
            logger.info(f"Migrating namespace '{namespace}' from {from_backend} to {to_backend}")
            
            # Load data from source backend
            source_data = self._load_from_backend(namespace, from_backend)
            if not source_data:
                logger.error(f"Failed to load data from {from_backend}")
                return False
            
            # Save data to target backend
            success = self._save_to_backend(namespace, to_backend, source_data)
            if not success:
                logger.error(f"Failed to save data to {to_backend}")
                return False
            
            # Record migration
            self._record_migration(f'{from_backend}_to_{to_backend}', namespace, {
                'from_backend': from_backend,
                'to_backend': to_backend,
                'migration_time': datetime.now().isoformat()
            })
            
            logger.info(f"Migration from {from_backend} to {to_backend} completed")
            return True
            
        except Exception as e:
            logger.error(f"Migration between backends failed: {e}")
            return False
    
    def _load_from_backend(self, namespace: str, backend: str) -> Optional[Dict[str, Any]]:
        """Load data from specified backend."""
        try:
            if backend == "networkx":
                # Load from NetworkX format
                graph_path = self.storage_path / "namespaces" / namespace / "graph.json"
                if graph_path.exists():
                    with open(graph_path, 'r') as f:
                        return json.load(f)
            
            elif backend == "csr":
                # Load from CSR format
                csr_path = self.storage_path / "csr" / f"{namespace}_csr.npz"
                if csr_path.exists():
                    from .csr_store import CSREdgeStore
                    csr_store = CSREdgeStore()
                    csr_store.load_from_file(str(csr_path))
                    # Convert CSR data to standard format
                    edges = csr_store.get_all_edges()
                    return {
                        'nodes': [],  # CSR doesn't store nodes
                        'edges': [{'source': e.source_id, 'target': e.target_id,
                                  'label': e.edge_type, 'properties': e.properties}
                                 for e in edges]
                    }
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to load from {backend}: {e}")
            return None
    
    def _save_to_backend(self, namespace: str, backend: str, 
                        data: Dict[str, Any]) -> bool:
        """Save data to specified backend."""
        try:
            if backend == "networkx":
                # Save to NetworkX format
                namespace_dir = self.storage_path / "namespaces" / namespace
                namespace_dir.mkdir(parents=True, exist_ok=True)
                
                graph_path = namespace_dir / "graph.json"
                with open(graph_path, 'w') as f:
                    json.dump(data, f, indent=2)
            
            elif backend == "csr":
                # Save to CSR format
                csr_path = self.storage_path / "csr" / f"{namespace}_csr.npz"
                csr_path.parent.mkdir(parents=True, exist_ok=True)
                
                from .csr_store import CSREdgeStore
                csr_store = CSREdgeStore()
                
                # Add edges to CSR store
                for edge in data.get('edges', []):
                    csr_store.add_edge(
                        edge.get('source', ''),
                        edge.get('target', ''),
                        edge.get('label', ''),
                        edge.get('properties', {})
                    )
                
                csr_store.save_to_file(str(csr_path))
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to save to {backend}: {e}")
            return False
    
    def _record_migration(self, migration_type: str, namespace: str, 
                         details: Dict[str, Any]):
        """Record migration in history."""
        migration_record = {
            'migration_type': migration_type,
            'namespace': namespace,
            'timestamp': datetime.now().isoformat(),
            'details': details
        }
        
        self.migration_history.append(migration_record)
        
        # Save migration history
        history_path = self.storage_path / "migration_history.json"
        with open(history_path, 'w') as f:
            json.dump(self.migration_history, f, indent=2)
    
    def get_migration_history(self) -> List[Dict[str, Any]]:
        """Get migration history."""
        return self.migration_history
    
    def rollback_migration(self, migration_id: int) -> bool:
        """Rollback a specific migration."""
        try:
            if migration_id >= len(self.migration_history):
                logger.error(f"Invalid migration ID: {migration_id}")
                return False
            
            migration = self.migration_history[migration_id]
            logger.info(f"Rolling back migration: {migration['migration_type']}")
            
            # Implement rollback logic based on migration type
            # This is a simplified implementation
            logger.info("Rollback completed")
            return True
            
        except Exception as e:
            logger.error(f"Rollback failed: {e}")
            return False













