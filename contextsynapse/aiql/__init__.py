"""
AIQL (AI Query Language) Module
A declarative, SQL-inspired but graph-native query language designed for both:
1. Pure graph querying and analytics
2. Hybrid Graph + RAG (retrieval-augmented) search

Module Structure:
- grammar/  : Grammar definitions (EBNF)
- parser/   : Parsing (String → AST)
- compiler/ : Compilation pipeline (AST → Optimized Plan)
  - validator.py : Query validation
  - planner.py   : Execution planning
  - optimizer.py: Query optimization
- engine/   : Execution engine (Plan → Results)
  - executor.py : Main executor
  - operators/  : Operator implementations
"""

# Grammar layer
from .grammar import EXAMPLE_QUERIES, validate_grammar, AIQL_GRAMMAR

# Parser layer
from .parser import AIQLParser, AIQLNodeType, AIQLNode

# Compiler layer (Validator → Planner → Optimizer)
from .compiler import (
    AIQLValidator, ValidatorMode, ValidationError,
    AIQLPlanner, ExecutionPlan, ExecutionMode,
    QueryOptimizer, QueryPlan
)

# Engine layer
from .engine import AIQLExecutor, ExecutionResult

# Cache
from .cache import configure_cache, get_cache_stats

# Alias QGQL as AQL for compatibility

__version__ = "1.0.0"
__author__ = "AIContextDB Team"

__all__ = [
    # Grammar
    "AIQL_GRAMMAR",
    "EXAMPLE_QUERIES",
    "validate_grammar",
    
    # Parser
    "AIQLParser",
    "AIQLNodeType",
    "AIQLNode",
    
    # Compiler (Validator → Planner → Optimizer)
    "AIQLValidator",
    "ValidatorMode",
    "ValidationError",
    "AIQLPlanner",
    "ExecutionPlan",
    "ExecutionMode",
    "QueryOptimizer",
    "QueryPlan",
    
    # Engine
    "AIQLExecutor",
    "ExecutionResult",
    
    # Cache
    "configure_cache",
    "get_cache_stats",
]