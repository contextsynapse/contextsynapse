"""
Graph Registry Metadata System
Stores metadata about all namespaces for fast discovery and management
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class GraphRegistryMetadata:
    """Manages metadata for all graphs/namespaces."""
    
    def __init__(self, registry_file: str = "contextcore_data/registry.json"):
        self.registry_file = Path(registry_file)
        self.metadata: Dict[str, Dict[str, Any]] = {}
        self.load_metadata()
    
    def load_metadata(self):
        """Load metadata from registry file."""
        if self.registry_file.exists():
            try:
                with open(self.registry_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # Handle different registry file formats
                if 'namespaces' in data:
                    # Old format: {"namespaces": {...}, "total_namespaces": ...}
                    self.metadata = data['namespaces']
                else:
                    # New format: direct namespace metadata
                    self.metadata = data
                
                logger.info(f"[EMOJI] Loaded metadata for {len(self.metadata)} namespaces")
            except Exception as e:
                logger.warning(f"[EMOJI][EMOJI] Error loading registry metadata: {e}")
                self.metadata = {}
        else:
            logger.info("[EMOJI] No existing registry metadata found")
            self.metadata = {}
    
    def save_metadata(self):
        """Save metadata to registry file."""
        try:
            self.registry_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.registry_file, 'w', encoding='utf-8') as f:
                json.dump(self.metadata, f, indent=2, ensure_ascii=False)
            logger.info(f"[EMOJI] Saved metadata for {len(self.metadata)} namespaces")
        except Exception as e:
            logger.error(f"[EMOJI] Error saving registry metadata: {e}")
    
    def register_namespace(self, namespace_name: str, namespace_path: str, 
                          graph_file: str, metadata: Optional[Dict] = None):
        """Register a namespace with its metadata using namespace-centric structure."""
        namespace_info = {
            'name': namespace_name,
            'path': namespace_path,
            'graph_file': graph_file,
            'created_at': datetime.now().isoformat(),
            'last_accessed': datetime.now().isoformat(),
            'status': 'registered',
            'loaded': False,
            'node_count': 0,
            'edge_count': 0,
            'size_mb': 0.0,
            'metadata': metadata or {},
            'structure': 'namespace_centric',  # New field to indicate structure type
            'data_types': {
                'graph': True,
                'chunks': True,
                'vectors': True,
                'tables': True,
                'multimodal': True
            },
            'directories': {
                'chunks': f"{namespace_path}/chunks",
                'vector': f"{namespace_path}/vector",
                'parquet': f"{namespace_path}/parquet",
                'hdf5': f"{namespace_path}/hdf5",
                'databases': f"{namespace_path}/databases",
                'indexes': f"{namespace_path}/indexes",
                'backups': f"{namespace_path}/backups"
            }
        }
        
        # Check if namespace directory exists and calculate total size
        namespace_dir = Path(namespace_path)
        if namespace_dir.exists():
            total_size = 0
            for file_path in namespace_dir.rglob('*'):
                if file_path.is_file():
                    try:
                        total_size += file_path.stat().st_size
                    except:
                        pass
            namespace_info['size_mb'] = round(total_size / (1024 * 1024), 2)
            namespace_info['status'] = 'available'
        
        self.metadata[namespace_name] = namespace_info
        logger.info(f"[EMOJI] Registered namespace '{namespace_name}' with namespace-centric structure")
    
    def update_namespace_status(self, namespace_name: str, **updates):
        """Update namespace status and metadata."""
        if namespace_name in self.metadata:
            self.metadata[namespace_name].update(updates)
            self.metadata[namespace_name]['last_accessed'] = datetime.now().isoformat()
            logger.debug(f"[EMOJI] Updated metadata for namespace '{namespace_name}'")
    
    def get_namespace_info(self, namespace_name: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a specific namespace."""
        return self.metadata.get(namespace_name)
    
    def list_namespaces(self) -> List[Dict[str, Any]]:
        """List all registered namespaces with their metadata."""
        return list(self.metadata.values())
    
    def discover_namespaces(self, base_path: str = "contextcore_data/namespaces") -> List[str]:
        """Auto-discover namespaces from filesystem using namespace-centric structure."""
        discovered = []
        base_dir = Path(base_path)
        
        if base_dir.exists():
            for namespace_dir in base_dir.iterdir():
                if namespace_dir.is_dir():
                    namespace_name = namespace_dir.name
                    graph_file = namespace_dir / "graph.json"  # Updated path for namespace-centric
                    
                    if graph_file.exists():
                        # Register if not already registered
                        if namespace_name not in self.metadata:
                            self.register_namespace(
                                namespace_name,
                                str(namespace_dir),
                                str(graph_file)
                            )
                        discovered.append(namespace_name)
                        logger.info(f"[EMOJI] Discovered namespace: {namespace_name}")
        
        return discovered
    
    def create_namespace_centric_structure(self, namespace_name: str, base_path: str = "contextcore_data") -> Dict[str, Any]:
        """Create complete namespace-centric directory structure."""
        base_dir = Path(base_path)
        namespaces_dir = base_dir / "namespaces"
        namespace_path = namespaces_dir / namespace_name
        
        # Create main namespace directory
        namespace_path.mkdir(parents=True, exist_ok=True)
        
        # Create subdirectories
        subdirs = [
            "chunks",
            "vector/text_embeddings",
            "vector/image_embeddings", 
            "vector/audio_embeddings",
            "vector/video_embeddings",
            "vector/table_embeddings",
            "parquet",
            "hdf5",
            "databases",
            "indexes",
            "backups"
        ]
        
        for subdir in subdirs:
            (namespace_path / subdir).mkdir(parents=True, exist_ok=True)
        
        # Create namespace configuration
        config = {
            "namespace_name": namespace_name,
            "created_at": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat(),
            "status": "active",
            "storage_backend": "hybrid",
            "data_types": {
                "graph": True,
                "chunks": True,
                "vectors": True,
                "tables": True,
                "multimodal": True
            },
            "settings": {
                "chunk_size": 1000,
                "overlap": 200,
                "embedding_model": "openai_ada_002",
                "vector_dimension": 1536
            },
            "security": {
                "access_level": "private",
                "encryption": False,
                "backup_enabled": True
            }
        }
        
        config_path = namespace_path / "namespace_config.json"
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
        
        # Create empty graph.json
        graph_data = {
            "nodes": [],
            "edges": [],
            "metadata": {
                "namespace": namespace_name,
                "created_at": datetime.now().isoformat(),
                "last_updated": datetime.now().isoformat(),
                "node_count": 0,
                "edge_count": 0,
                "storage_backend": "hybrid"
            }
        }
        
        graph_path = namespace_path / "graph.json"
        with open(graph_path, 'w') as f:
            json.dump(graph_data, f, indent=2)
        
        # Create empty chunks.json
        chunks_data = {
            "chunks": [],
            "metadata": {
                "namespace": namespace_name,
                "total_chunks": 0,
                "chunking_strategy": "smart",
                "last_updated": datetime.now().isoformat()
            }
        }
        
        chunks_path = namespace_path / "chunks" / "chunks.json"
        with open(chunks_path, 'w') as f:
            json.dump(chunks_data, f, indent=2)
        
        # Create empty embeddings files
        embeddings_data = {
            "embeddings": [],
            "metadata": {
                "namespace": namespace_name,
                "model": "openai_ada_002",
                "dimension": 1536,
                "total_embeddings": 0,
                "last_updated": datetime.now().isoformat()
            }
        }
        
        for embedding_type in ["text_embeddings", "image_embeddings", "audio_embeddings", "video_embeddings", "table_embeddings"]:
            embedding_path = namespace_path / "vector" / embedding_type / "embeddings.json"
            with open(embedding_path, 'w') as f:
                json.dump(embeddings_data, f, indent=2)
        
        # Create indexes
        indexes = {
            "text_index": {"type": "full_text", "status": "empty"},
            "vector_index": {"type": "hnsw", "status": "empty"},
            "graph_index": {"type": "adjacency", "status": "empty"}
        }
        
        for index_name, index_data in indexes.items():
            index_path = namespace_path / "indexes" / f"{index_name}.json"
            with open(index_path, 'w') as f:
                json.dump(index_data, f, indent=2)
        
        # Register the namespace
        self.register_namespace(namespace_name, str(namespace_path), str(graph_path))
        
        return {
            "namespace_name": namespace_name,
            "path": str(namespace_path),
            "created": True,
            "subdirectories": subdirs,
            "config_created": True,
            "graph_created": True,
            "chunks_created": True,
            "embeddings_created": True,
            "indexes_created": True
        }
    
    def get_registry_stats(self) -> Dict[str, Any]:
        """Get registry statistics."""
        total_namespaces = len(self.metadata)
        loaded_namespaces = sum(1 for ns in self.metadata.values() if ns.get('loaded', False))
        total_size_mb = sum(ns.get('size_mb', 0) for ns in self.metadata.values())
        
        return {
            'total_namespaces': total_namespaces,
            'loaded_namespaces': loaded_namespaces,
            'total_size_mb': round(total_size_mb, 2),
            'last_updated': datetime.now().isoformat()
        }

# Global registry metadata instance
registry_metadata = GraphRegistryMetadata()
