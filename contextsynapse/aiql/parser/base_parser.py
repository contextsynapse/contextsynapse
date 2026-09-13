"""
Base Parser

Provides base functionality for parsers.
"""

from typing import List, Dict, Any
from lark import Tree
from .aiql_parser import AIQLNode, AIQLParser


class BaseParser:
    """Base parser with common functionality."""
    
    def __init__(self):
        """Initialize base parser."""
        pass
    
    def parse(self, tree: Tree, parser: AIQLParser) -> List[AIQLNode]:
        """
        Parse tree into AST nodes.
        
        Subclasses should override this method.
        """
        raise NotImplementedError(f"{self.__class__.__name__}.parse() not implemented")
    
    def _get_identifier(self, node, parser: AIQLParser) -> str:
        """Helper to extract identifier from node."""
        return parser._get_identifier(node)
    
    def _get_string(self, node) -> str:
        """Helper to extract string from node."""
        if hasattr(node, 'value'):
            return str(node.value).strip('"\'')
        return str(node)
    
    def _extract_parameters(self, tree: Tree, parser: AIQLParser) -> Dict[str, Any]:
        """Helper to extract parameters from tree."""
        # This would use parser's existing parameter extraction methods
        return {}







