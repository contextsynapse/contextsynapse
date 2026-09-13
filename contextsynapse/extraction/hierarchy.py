"""
Hierarchy Builder

Builds document hierarchy from normalized blocks.
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)


@dataclass
class HierarchyNode:
    """Represents a node in the document hierarchy."""
    id: str
    title: str
    level: int
    parent: Optional[str] = None
    children: List[str] = field(default_factory=list)
    block_type: Optional[str] = None


class HierarchyBuilder:
    """
    Builds hierarchical structure from normalized blocks.
    
    Processes heading blocks to create a tree structure,
    and attaches non-heading blocks to their parent headings.
    """
    
    def __init__(self):
        """Initialize hierarchy builder."""
        self.hierarchy_index: Dict[str, Dict[str, Any]] = {}
        self.document_tree: List[Dict[str, Any]] = []
        self.root_sections: List[str] = []
    
    def build_page_hierarchy(self, blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Build hierarchy for a single page.
        
        Args:
            blocks: List of normalized blocks for the page
            
        Returns:
            Hierarchy structure with root and structure
        """
        if not blocks:
            return {"root": None, "structure": []}
        
        # Stack to track heading hierarchy
        heading_stack: List[HierarchyNode] = []
        structure: List[Dict[str, Any]] = []
        root_id: Optional[str] = None
        
        for block in blocks:
            block_id = block.get("id")
            block_type = block.get("type")
            
            if not block_id:
                continue
            
            if block_type == "heading":
                level = block.get("level", 1)
                text = block.get("text", "")
                
                # Pop headings with level >= current level
                while heading_stack and heading_stack[-1].level >= level:
                    heading_stack.pop()
                
                # Determine parent
                parent_id = heading_stack[-1].id if heading_stack else None
                
                # Create hierarchy node
                node = HierarchyNode(
                    id=block_id,
                    title=text,
                    level=level,
                    parent=parent_id,
                    block_type="heading"
                )
                
                # Add to structure
                structure.append({
                    "id": block_id,
                    "children": []
                })
                
                # Update parent's children if parent exists
                if parent_id:
                    for item in structure:
                        if item["id"] == parent_id:
                            item["children"].append(block_id)
                            break
                else:
                    # Root level heading
                    if root_id is None:
                        root_id = block_id
                
                # Push to stack
                heading_stack.append(node)
                
                # Update hierarchy index
                self.hierarchy_index[block_id] = {
                    "level": level,
                    "parent": parent_id,
                    "children": []
                }
                
                # Update parent's children in index
                if parent_id and parent_id in self.hierarchy_index:
                    self.hierarchy_index[parent_id]["children"].append(block_id)
            
            else:
                # Non-heading block - attach to latest heading
                parent_id = heading_stack[-1].id if heading_stack else None
                
                if parent_id:
                    # Attach to parent
                    for item in structure:
                        if item["id"] == parent_id:
                            item["children"].append(block_id)
                            break
                    
                    # Update hierarchy index
                    self.hierarchy_index[block_id] = {
                        "level": None,
                        "parent": parent_id,
                        "children": []
                    }
                    
                    if parent_id in self.hierarchy_index:
                        self.hierarchy_index[parent_id]["children"].append(block_id)
                else:
                    # Orphan block (no parent heading)
                    self.hierarchy_index[block_id] = {
                        "level": None,
                        "parent": None,
                        "children": []
                    }
        
        return {
            "root": root_id,
            "structure": structure
        }
    
    def build_document_hierarchy(self, pages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Build global document hierarchy across all pages.
        
        Args:
            pages: List of page dictionaries with normalized_blocks
            
        Returns:
            Document hierarchy with root_sections and tree
        """
        # Reset state
        self.hierarchy_index = {}
        self.document_tree = []
        self.root_sections = []
        
        # Build hierarchy for each page
        for page in pages:
            blocks = page.get("normalized_blocks", [])
            page_hierarchy = self.build_page_hierarchy(blocks)
            page["hierarchy"] = page_hierarchy
        
        # Build global tree from all root sections
        all_root_ids = []
        for page in pages:
            hierarchy = page.get("hierarchy", {})
            root_id = hierarchy.get("root")
            if root_id:
                all_root_ids.append(root_id)
        
        self.root_sections = all_root_ids
        
        # Build global tree structure
        self._build_tree_structure(pages)
        
        return {
            "root_sections": self.root_sections,
            "tree": self.document_tree
        }
    
    def _build_tree_structure(self, pages: List[Dict[str, Any]]) -> None:
        """
        Build tree structure from hierarchy index.
        
        Args:
            pages: List of page dictionaries
        """
        # Create a map of all blocks
        all_blocks = {}
        for page in pages:
            for block in page.get("normalized_blocks", []):
                block_id = block.get("id")
                if block_id:
                    all_blocks[block_id] = block
        
        # Build tree starting from root sections
        def build_node(block_id: str) -> Optional[Dict[str, Any]]:
            """Recursively build tree node."""
            if block_id not in all_blocks:
                return None
            
            block = all_blocks[block_id]
            node_info = self.hierarchy_index.get(block_id, {})
            children_ids = node_info.get("children", [])
            
            # Build children
            children = []
            for child_id in children_ids:
                child_node = build_node(child_id)
                if child_node:
                    children.append(child_node)
            
            return {
                "id": block_id,
                "title": block.get("text", ""),
                "level": node_info.get("level"),
                "children": children
            }
        
        # Build tree for each root section
        for root_id in self.root_sections:
            root_node = build_node(root_id)
            if root_node:
                self.document_tree.append(root_node)
    
    def get_hierarchy_index(self) -> Dict[str, Dict[str, Any]]:
        """
        Get the hierarchy index mapping.
        
        Returns:
            Dictionary mapping block_id to hierarchy info
        """
        return self.hierarchy_index.copy()
