"""ContextCore Memory Engine -- persistent, structured memory for AI agents.

Provides:
  - Auto-extraction of facts from conversations
  - Semantic recall (find memories by meaning)
  - Temporal decay (old memories lose confidence)
  - Consolidation (merge duplicate facts)
  - Cross-agent shared memory via the graph

Uses the same StorageRouter as everything else:
  Facts/preferences → DuckDB (content store)
  Entity links → Graph
  Embeddings → Qdrant (via vector store)

Usage:
    from contextsynapse.memory import MemoryEngine, get_memory_engine

    mem = get_memory_engine(graph=db)

    # Store a memory
    mem.remember("agent-1", "User prefers dark mode", memory_type="preference")

    # Recall by meaning
    results = mem.recall("agent-1", "what theme does the user want?")

    # Auto-extract from conversation
    mem.extract_and_store("agent-1", [
        {"role": "user", "content": "I like the dark theme, and my name is Alice"},
        {"role": "assistant", "content": "Dark mode enabled, Alice!"},
    ])
"""
from contextsynapse.memory.engine import MemoryEngine, get_memory_engine

__all__ = ["MemoryEngine", "get_memory_engine"]
