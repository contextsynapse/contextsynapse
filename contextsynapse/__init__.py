"""
ContextSynapse — The Shared Brain for AI Agents
=================================================
Open-source context engine — ingest knowledge, build connections,
and deliver the right context to any LLM. Shared memory, Graph RAG,
AIQL query language, and enterprise security for multi-agent systems.

Quick start:
    from contextsynapse import ContextSynapse, ContextHub
    from contextsynapse.sdk import connect, ContextSynapseAgent

    # Direct graph access (in-process)
    db = ContextSynapse()
    db.add_node(GraphNode(id="1", label="Person", properties={"name": "Alice"}))

    # SDK client (REST API)
    ctx = connect("http://localhost:8000", agent_name="my-bot")
    agent = ContextSynapseAgent("my-bot", server="http://localhost:8000")
"""

__version__ = "1.0.0"
__author__ = "ContextSynapse Contributors"

# ── Core (always needed, lightweight) ────────────────────────────
from .core.hybrid_graph_storage import ContextSynapse, AIContextDB
from .core.graph_structures import GraphNode, GraphEdge
from .core.registry import GraphRegistry

# Backward compat aliases
AINatDB = AIContextDB

# ── AIQL ─────────────────────────────────────────────────────────
try:
    from .aiql.parser import AIQLParser
    from .aiql.engine import AIQLExecutor
except ImportError:
    AIQLParser = None
    AIQLExecutor = None

# ── ContextHub ───────────────────────────────────────────────────
try:
    from .context import ContextHub, ContextItem, ContextRole, ContextFormat
except ImportError:
    ContextHub = None
    ContextItem = None
    ContextRole = None
    ContextFormat = None


def __getattr__(name):
    """Lazy import for heavy optional modules."""
    _lazy = {
        # SDK
        "connect": (".sdk", "connect"),
        "ContextSynapseAgent": (".sdk", "ContextSynapseAgent"),
        "AIContextDBAgent": (".sdk", "ContextSynapseAgent"),
        # Search
        "EnhancedSearch": (".search.enhanced_search", "EnhancedSearch"),
        # Vector
        "VectorDatabase": (".vector.vector_db", "VectorDatabase"),
        # Storage
        "NamespaceStore": (".storage.namespace_store", "NamespaceStore"),
        "WALConfig": (".storage.wal", "WALConfig"),
        # Adapters
        "AIContextDBConnection": (".adapters", "AIContextDBConnection"),
        # LLM
        "LLMClient": (".llm", "LLMClient"),
        "get_llm_client": (".llm", "get_llm_client"),
        # Extraction
        "EntityExtractor": (".extraction.llm_entity_extractor", "EntityExtractor"),
        "FactExtractor": (".extraction.fact_extractor", "FactExtractor"),
        "load_schema": (".extraction.schema_loader", "load_schema"),
    }
    if name in _lazy:
        module_path, attr = _lazy[name]
        import importlib
        try:
            module = importlib.import_module(module_path, __package__)
            return getattr(module, attr)
        except (ImportError, AttributeError):
            return None
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Core (new canonical names)
    "ContextSynapse", "GraphNode", "GraphEdge", "GraphRegistry",
    # Core (backward compat)
    "AIContextDB", "AINatDB",
    # AIQL
    "AIQLParser", "AIQLExecutor",
    # Context
    "ContextHub", "ContextItem", "ContextRole", "ContextFormat",
    # SDK (new canonical names)
    "connect", "ContextSynapseAgent",
    # SDK (backward compat)
    "AIContextDBAgent",
    # Search & Vector
    "EnhancedSearch", "VectorDatabase",
    # Storage
    "NamespaceStore", "WALConfig",
    # Adapters
    "AIContextDBConnection",
    # LLM & Extraction
    "LLMClient", "get_llm_client",
    "EntityExtractor", "FactExtractor", "load_schema",
]
