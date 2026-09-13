"""ContextCore Engine -- unified API for agents, RAG, and applications.

The engine sits above all stores and provides a single entry point:
  - search()        -- vector + keyword + graph expansion
  - get()           -- auto-resolves from whichever store has the data
  - ingest()        -- routes to correct stores via StorageRouter
  - context()       -- builds full context for an entity/topic
  - remember/recall -- memory operations
  - query()         -- AIQL or SQL

Agents call the engine. The engine handles the stores.
"""
from contextsynapse.engine.core import ContextEngine, get_engine

__all__ = ["ContextEngine", "get_engine"]
