"""
Graph data structures for AIContextDB
"""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional
import uuid

@dataclass
class GraphNode:
    """Represents a node in the graph.
    
    The node.id is the UUID - single identifier used throughout the system.
    The uuid field is kept for backward compatibility but node.id is the source of truth.
    UUID is NOT stored in properties to avoid duplication - use node.id directly.
    """
    id: str  # This is the UUID - single identifier
    label: str
    properties: Dict[str, Any] = None
    name: Optional[str] = None
    uuid: Optional[str] = None  # Deprecated - use id instead
    alias: Optional[str] = None
    
    def __post_init__(self):
        if self.properties is None:
            self.properties = {}
        # If id is not a UUID format, generate one
        # Otherwise, id is already the UUID
        if not self.id or (not self.uuid and len(self.id) != 36):  # UUID format is 36 chars
            generated_uuid = str(uuid.uuid4())
            if not self.id:
                self.id = generated_uuid
            if not self.uuid:
                self.uuid = generated_uuid
        
        # Ensure uuid field matches id (id is the source of truth)
        if not self.uuid:
            self.uuid = self.id
        elif self.uuid != self.id:
            # If uuid doesn't match id, use id as source of truth
            self.uuid = self.id
        
        # Don't store UUID in properties - node.id is the single source of truth
        # UUID will be added explicitly in query results using node.id
        # Remove uuid from properties if it exists (to avoid duplication)
        self.properties.pop('uuid', None)
        
        # Store name in properties if provided
        if self.name and 'name' not in self.properties:
            self.properties['name'] = self.name
        # Store alias in properties if provided
        if self.alias and 'alias' not in self.properties:
            self.properties['alias'] = self.alias

@dataclass
class GraphEdge:
    """Represents an edge in the graph.
    
    The edge.id is the UUID - single identifier used throughout the system.
    The uuid field is kept for backward compatibility but edge.id is the source of truth.
    UUID is NOT stored in properties to avoid duplication - use edge.id directly.
    """
    id: str  # This is the UUID - single identifier
    source: str  # Source node UUID
    target: str  # Target node UUID
    label: str
    properties: Dict[str, Any] = None
    name: Optional[str] = None
    uuid: Optional[str] = None  # Deprecated - use id instead
    alias: Optional[str] = None
    
    def __post_init__(self):
        if self.properties is None:
            self.properties = {}
        # If id is not a UUID format, generate one
        # Otherwise, id is already the UUID
        if not self.id or (not self.uuid and len(self.id) != 36):  # UUID format is 36 chars
            generated_uuid = str(uuid.uuid4())
            if not self.id:
                self.id = generated_uuid
            if not self.uuid:
                self.uuid = generated_uuid
        
        # Ensure uuid field matches id (id is the source of truth)
        if not self.uuid:
            self.uuid = self.id
        elif self.uuid != self.id:
            # If uuid doesn't match id, use id as source of truth
            self.uuid = self.id
        
        # Don't store UUID in properties - edge.id is the single source of truth
        # UUID will be added explicitly in query results using edge.id
        # Remove uuid from properties if it exists (to avoid duplication)
        self.properties.pop('uuid', None)
        
        # Store name in properties if provided
        if self.name and 'name' not in self.properties:
            self.properties['name'] = self.name
        # Store alias in properties if provided
        if self.alias and 'alias' not in self.properties:
            self.properties['alias'] = self.alias





