"""
Context-as-a-Service — Build, share, and export structured context for LLMs.

Usage:
    from contextsynapse.context import ContextHub, AgentRegistry, ContextSessionManager

    # Register an agent
    registry = AgentRegistry()
    agent, api_key = registry.register("my-agent", role="researcher")

    # Create a shared session
    sessions = ContextSessionManager(graph_registry=my_registry)
    session = sessions.create_session("project-alpha", owner_agent_id=agent.agent_id)

    # Build context
    hub = ContextHub(system_prompt="You are a graph analyst.")
    hub.add_text("Some background", role="background")
    messages = hub.to_messages()
"""

# Only import the most commonly used items eagerly
from .hub import ContextHub, ContextItem, ContextRole, ContextFormat, Sensitivity, SlotConfig


def __getattr__(name):
    """Lazy import for less commonly used classes."""
    _lazy_imports = {
        "AgentIdentity": ".agents",
        "AgentRegistry": ".agents",
        "ProvenanceRecord": ".agents",
        "ContextSession": ".session",
        "ContextSessionManager": ".session",
        "Context": ".context_manager",
        "ContextManager": ".context_manager",
        "BlobStore": ".blob",
        "UnifiedIngestor": ".ingest",
        "DocumentProcessor": ".document_processor",
        "ContextEvent": ".pubsub",
        "PubSubHub": ".pubsub",
        "pubsub_hub": ".pubsub",
        "SessionVectorStore": ".vector_integration",
        "ContextScoper": ".scoping",
        "ScopingConfig": ".scoping",
        "ConversationStore": ".conversation",
        "Conversation": ".conversation",
        "ConversationMessage": ".conversation",
        "EmbeddingHooks": ".embedding_hooks",
        "HybridSync": ".sync",
        "CompressionConfig": ".compression",
        "CompressionTier": ".compression",
        "InteractionGraphifier": ".interaction_graphifier",
        "GraphifyResult": ".interaction_graphifier",
        "SessionGraphBuilder": ".session_graph",
        "AgentMemory": ".agent_memory",
    }
    if name in _lazy_imports:
        import importlib
        module = importlib.import_module(_lazy_imports[name], __package__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Hub (original)
    "ContextHub", "ContextItem", "ContextRole", "ContextFormat", "Sensitivity",
    # Agents
    "AgentIdentity", "AgentRegistry", "ProvenanceRecord",
    # Sessions
    "ContextSession", "ContextSessionManager",
    # Contexts (first-class)
    "Context", "ContextManager",
    # Storage
    "BlobStore",
    # Ingestion
    "UnifiedIngestor",
    # Document processing
    "DocumentProcessor",
    # PubSub
    "ContextEvent", "PubSubHub", "pubsub_hub",
    # Vector
    "SessionVectorStore",
    # Scoping (Phase 2)
    "ContextScoper", "ScopingConfig",
    # Conversations (Phase 2)
    "ConversationStore", "Conversation", "ConversationMessage",
    # Embedding hooks (Phase 2)
    "EmbeddingHooks",
    # Sync (Phase 2)
    "HybridSync",
    # Compression & Slots (Phase 3)
    "CompressionConfig", "CompressionTier", "SlotConfig",
    # Session Graph (unified knowledge + conversation layer)
    "InteractionGraphifier", "GraphifyResult", "SessionGraphBuilder",
    # Agent Memory
    "AgentMemory",
]
