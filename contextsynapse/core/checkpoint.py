"""
Checkpoint System for AIContextDB
Git-like versioning and rollback capabilities for graph data
"""

import json
import os
import shutil
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class Checkpoint:
    """Represents a checkpoint (like a git commit)."""
    
    def __init__(self, checkpoint_id: str, namespace: str, timestamp: datetime, 
                 message: str = "", parent_id: Optional[str] = None):
        self.checkpoint_id = checkpoint_id
        self.namespace = namespace
        self.timestamp = timestamp
        self.message = message
        self.parent_id = parent_id
        self.node_count = 0
        self.edge_count = 0
        self.size_bytes = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert checkpoint to dictionary."""
        return {
            'checkpoint_id': self.checkpoint_id,
            'namespace': self.namespace,
            'timestamp': self.timestamp.isoformat(),
            'message': self.message,
            'parent_id': self.parent_id,
            'node_count': self.node_count,
            'edge_count': self.edge_count,
            'size_bytes': self.size_bytes
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Checkpoint':
        """Create checkpoint from dictionary."""
        checkpoint = cls(
            checkpoint_id=data['checkpoint_id'],
            namespace=data['namespace'],
            timestamp=datetime.fromisoformat(data['timestamp']),
            message=data.get('message', ''),
            parent_id=data.get('parent_id')
        )
        checkpoint.node_count = data.get('node_count', 0)
        checkpoint.edge_count = data.get('edge_count', 0)
        checkpoint.size_bytes = data.get('size_bytes', 0)
        return checkpoint

class CheckpointManager:
    """Manages checkpoints for namespaces (Git-like versioning)."""
    
    def __init__(self, base_dir: str = "contextcore_data"):
        """
        Initialize checkpoint manager with namespace-centric structure.
        
        Args:
            base_dir: Base directory (default: "contextcore_data")
                     Checkpoints will be stored in: {base_dir}/namespaces/{namespace}/checkpoints/
        """
        self.base_dir = Path(base_dir)
        self._checkpoints: Dict[str, List[Checkpoint]] = {}  # namespace -> checkpoints
    
    def _get_checkpoint_dir(self, namespace: str) -> Path:
        """
        Get checkpoint directory for a namespace (namespace-centric).
        
        Returns:
            Path to checkpoints directory: {base_dir}/namespaces/{namespace}/checkpoints/
        """
        checkpoint_dir = self.base_dir / "namespaces" / namespace / "checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        return checkpoint_dir
    
    def create_checkpoint(self, namespace: str, graph_file: str, 
                         message: str = "Auto-save", parent_id: Optional[str] = None,
                         stage_data: Optional[Dict[str, Any]] = None) -> str:
        """
        Create a checkpoint for a namespace.
        
        Args:
            namespace: Namespace name
            graph_file: Path to the graph.json file to checkpoint
            message: Checkpoint message
            parent_id: Parent checkpoint ID (for rollback history)
            stage_data: Optional stage data to save with checkpoint (for pipeline resumption)
            
        Returns:
            Checkpoint ID
        """
        try:
            # Generate checkpoint ID (like git commit hash)
            timestamp = datetime.now()
            content_hash = self._generate_hash(namespace, graph_file, timestamp)
            checkpoint_id = content_hash[:12]  # Use first 12 chars (like git short hash)
            
            # Load parent checkpoint if provided
            if parent_id is None:
                # Get latest checkpoint as parent
                checkpoints = self.get_checkpoints(namespace)
                if checkpoints:
                    parent_id = checkpoints[0].checkpoint_id
            
            # Create checkpoint object
            checkpoint = Checkpoint(
                checkpoint_id=checkpoint_id,
                namespace=namespace,
                timestamp=timestamp,
                message=message,
                parent_id=parent_id
            )
            
            # Copy graph file to checkpoint storage (namespace-centric)
            checkpoint_base_dir = self._get_checkpoint_dir(namespace)
            checkpoint_dir = checkpoint_base_dir / checkpoint_id
            checkpoint_dir.mkdir(parents=True, exist_ok=True)
            
            checkpoint_file = checkpoint_dir / "graph.json"
            if os.path.exists(graph_file):
                shutil.copy2(graph_file, checkpoint_file)
            else:
                # Create empty graph file if it doesn't exist yet (for early stage checkpoints)
                with open(checkpoint_file, 'w') as f:
                    json.dump({"nodes": [], "edges": []}, f, indent=2)
                logger.debug(f"Created empty graph file for checkpoint {checkpoint_id} (graph_file doesn't exist yet)")
            
            # Save stage_data if provided (for pipeline resumption)
            if stage_data is not None:
                stage_data_file = checkpoint_dir / "stage_data.json"
                with open(stage_data_file, 'w') as f:
                    json.dump(stage_data, f, indent=2, default=str)
                logger.debug(f"Saved stage_data to checkpoint {checkpoint_id}")
            
            # Get file stats
            if os.path.exists(graph_file):
                stat = os.stat(graph_file)
                checkpoint.size_bytes = stat.st_size
            
            # Load graph to count nodes/edges
            try:
                with open(graph_file, 'r') as f:
                    graph_data = json.load(f)
                    checkpoint.node_count = len(graph_data.get('nodes', []))
                    checkpoint.edge_count = len(graph_data.get('edges', []))
            except:
                pass
            
            # Save checkpoint metadata
            metadata_file = checkpoint_dir / "checkpoint.json"
            with open(metadata_file, 'w') as f:
                json.dump(checkpoint.to_dict(), f, indent=2)
            
            # Update checkpoint list for namespace
            if namespace not in self._checkpoints:
                self._checkpoints[namespace] = []
            self._checkpoints[namespace].insert(0, checkpoint)  # Latest first
            
            # Save checkpoint index
            self._save_checkpoint_index(namespace)
            
            logger.info(f"[EMOJI] Checkpoint created: {checkpoint_id} for namespace '{namespace}' ({checkpoint.node_count} nodes, {checkpoint.edge_count} edges)")
            return checkpoint_id
            
        except Exception as e:
            logger.error(f"[EMOJI] Failed to create checkpoint: {e}")
            raise
    
    def get_checkpoint(self, namespace: str, checkpoint_id: str) -> Optional[Checkpoint]:
        """Get a specific checkpoint."""
        checkpoints = self.get_checkpoints(namespace)
        for cp in checkpoints:
            if cp.checkpoint_id == checkpoint_id or cp.checkpoint_id.startswith(checkpoint_id):
                return cp
        return None
    
    def get_checkpoints(self, namespace: str) -> List[Checkpoint]:
        """Get all checkpoints for a namespace (latest first)."""
        if namespace in self._checkpoints:
            return self._checkpoints[namespace]
        
        # Load from disk if not in memory
        self._load_checkpoint_index(namespace)
        return self._checkpoints.get(namespace, [])
    
    def restore_checkpoint(self, namespace: str, checkpoint_id: str, 
                          target_graph_file: str) -> Dict[str, Any]:
        """
        Restore a checkpoint (rollback to previous state).
        
        Args:
            namespace: Namespace name
            checkpoint_id: Checkpoint ID to restore
            target_graph_file: Path to restore the graph to
            
        Returns:
            Dictionary with 'success' (bool) and 'stage_data' (optional Dict) if successful
        """
        try:
            checkpoint = self.get_checkpoint(namespace, checkpoint_id)
            if not checkpoint:
                logger.error(f"[EMOJI] Checkpoint '{checkpoint_id}' not found for namespace '{namespace}'")
                return {"success": False, "error": f"Checkpoint '{checkpoint_id}' not found"}
            
            checkpoint_base_dir = self._get_checkpoint_dir(namespace)
            checkpoint_dir = checkpoint_base_dir / checkpoint.checkpoint_id
            checkpoint_graph_file = checkpoint_dir / "graph.json"
            
            if not checkpoint_graph_file.exists():
                logger.error(f"[EMOJI] Checkpoint graph file not found: {checkpoint_graph_file}")
                return {"success": False, "error": f"Checkpoint graph file not found"}
            
            # Restore graph file
            target_path = Path(target_graph_file)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(checkpoint_graph_file, target_graph_file)
            
            # Restore stage_data if it exists
            stage_data = None
            stage_data_file = checkpoint_dir / "stage_data.json"
            if stage_data_file.exists():
                try:
                    with open(stage_data_file, 'r') as f:
                        stage_data = json.load(f)
                    logger.info(f"Restored stage_data from checkpoint {checkpoint_id}")
                except Exception as e:
                    logger.warning(f"Failed to load stage_data from checkpoint: {e}")
            
            logger.info(f"[EMOJI] Restored checkpoint '{checkpoint_id}' for namespace '{namespace}'")
            result = {"success": True}
            if stage_data is not None:
                result["stage_data"] = stage_data
            return result
            
        except Exception as e:
            logger.error(f"[EMOJI] Failed to restore checkpoint: {e}")
            return {"success": False, "error": str(e)}
    
    def list_checkpoints(self, namespace: str, limit: int = 10) -> List[Dict[str, Any]]:
        """List checkpoints for a namespace."""
        checkpoints = self.get_checkpoints(namespace)
        return [cp.to_dict() for cp in checkpoints[:limit]]
    
    def delete_checkpoint(self, namespace: str, checkpoint_id: str) -> bool:
        """Delete a checkpoint."""
        try:
            checkpoint_base_dir = self._get_checkpoint_dir(namespace)
            checkpoint_dir = checkpoint_base_dir / checkpoint_id
            if checkpoint_dir.exists():
                shutil.rmtree(checkpoint_dir)
            
            if namespace in self._checkpoints:
                self._checkpoints[namespace] = [
                    cp for cp in self._checkpoints[namespace] 
                    if cp.checkpoint_id != checkpoint_id
                ]
                self._save_checkpoint_index(namespace)
            
            logger.info(f"[EMOJI] Deleted checkpoint '{checkpoint_id}' for namespace '{namespace}'")
            return True
        except Exception as e:
            logger.error(f"[EMOJI] Failed to delete checkpoint: {e}")
            return False
    
    def _generate_hash(self, namespace: str, graph_file: str, timestamp: datetime) -> str:
        """Generate hash for checkpoint ID."""
        content = f"{namespace}:{graph_file}:{timestamp.isoformat()}"
        return hashlib.sha256(content.encode()).hexdigest()
    
    def _save_checkpoint_index(self, namespace: str):
        """Save checkpoint index for a namespace."""
        if namespace not in self._checkpoints:
            return
        
        checkpoint_base_dir = self._get_checkpoint_dir(namespace)
        index_file = checkpoint_base_dir / "index.json"
        
        with open(index_file, 'w') as f:
            checkpoints_data = [cp.to_dict() for cp in self._checkpoints[namespace]]
            json.dump(checkpoints_data, f, indent=2)
    
    def _load_checkpoint_index(self, namespace: str):
        """Load checkpoint index for a namespace."""
        checkpoint_base_dir = self._get_checkpoint_dir(namespace)
        index_file = checkpoint_base_dir / "index.json"
        if not index_file.exists():
            self._checkpoints[namespace] = []
            return
        
        try:
            with open(index_file, 'r') as f:
                checkpoints_data = json.load(f)
                self._checkpoints[namespace] = [
                    Checkpoint.from_dict(cp_data) for cp_data in checkpoints_data
                ]
        except Exception as e:
            logger.warning(f"[EMOJI][EMOJI] Failed to load checkpoint index: {e}")
            self._checkpoints[namespace] = []

# Global checkpoint manager instance (uses namespace-centric structure)
checkpoint_manager = CheckpointManager(base_dir="contextcore_data")

