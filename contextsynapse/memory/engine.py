"""Memory Engine -- extract, store, recall, consolidate, decay.

Memory items flow through the same StorageRouter as all other content:
  - Stored in DuckDB as content type "memory"
  - Linked in graph via REMEMBERS / ABOUT edges
  - Embedded in vector store for semantic recall
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_global_engine: Optional["MemoryEngine"] = None
_engine_lock = threading.Lock()

# Memory types
MEMORY_TYPES = {
    "fact",          # "TCS revenue grew 15%"
    "preference",    # "user prefers dark mode"
    "decision",      # "we decided to use PostgreSQL"
    "relationship",  # "Alice works with Bob"
    "event",         # "meeting scheduled for Friday"
    "instruction",   # "always respond in Hindi"
    "observation",   # "user seems frustrated"
    "general",       # anything else
}

# Simple fact extraction patterns (no LLM needed for common cases)
_FACT_PATTERNS = [
    # Preferences
    (r"(?:i |my |the user ?)(?:like|prefer|want|love|use|need)s?\s+(.+)", "preference"),
    # Names
    (r"(?:my name is|i am|call me|i'm)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", "fact"),
    # Decisions
    (r"(?:we |i |let's )(?:decided?|chose?|picked|selected|going with)\s+(.+)", "decision"),
    # Events
    (r"(?:meeting|call|deadline|launch|release|event)\s+(?:is |on |at |scheduled )(.+)", "event"),
    # Instructions
    (r"(?:always|never|don't|do not|please|make sure)\s+(.+)", "instruction"),
    # Numeric facts
    (r"(\w+(?:\s+\w+)?)\s+(?:is|was|are|were|grew|declined|reached)\s+([\d,.]+%?)", "fact"),
]

_COMPILED_PATTERNS = [(re.compile(p, re.IGNORECASE), t) for p, t in _FACT_PATTERNS]


class MemoryEngine:
    """Persistent, structured memory for AI agents.

    Each memory is:
      - A content item in DuckDB (type="memory", searchable)
      - An edge in the graph (agent → REMEMBERS → memory)
      - Optionally embedded for semantic recall
    """

    def __init__(self, graph=None, namespace: str = "memory"):
        self._graph = graph
        self._namespace = namespace
        self._router = None
        self._resolver = None

    @property
    def router(self):
        if self._router is None:
            from contextsynapse.storage.router import get_storage_router
            self._router = get_storage_router(graph=self._graph, namespace=self._namespace)
        return self._router

    @property
    def resolver(self):
        if self._resolver is None:
            from contextsynapse.storage.router import get_content_resolver
            self._resolver = get_content_resolver()
        return self._resolver

    @property
    def store(self):
        return self.router.content

    def set_graph(self, graph):
        self._graph = graph
        if self._router:
            self._router.set_graph(graph)

    # ── Remember ──

    def remember(
        self,
        agent_id: str,
        content: str,
        memory_type: str = "general",
        entity_id: str = None,
        confidence: float = 1.0,
        metadata: dict = None,
        ttl_hours: int = None,
    ) -> str:
        """Store a single memory.

        Args:
            agent_id: The agent storing the memory.
            content: The memory text.
            memory_type: One of MEMORY_TYPES.
            entity_id: Optional entity this memory is about.
            confidence: How certain (0-1). Decays over time.
            metadata: Extra key-value pairs.
            ttl_hours: Auto-expire after this many hours.

        Returns:
            Memory ID.
        """
        mem_id = f"mem_{hashlib.md5(f'{agent_id}:{content}'.encode()).hexdigest()[:12]}"
        now = datetime.now(timezone.utc).isoformat()

        meta = metadata or {}
        meta["agent_id"] = agent_id
        meta["created_at"] = now
        if ttl_hours:
            meta["expires_at"] = (datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).isoformat()
        if entity_id:
            meta["entity_id"] = entity_id

        # Store via router (goes to DuckDB as content type "memory")
        # Use "parent_id" directly so the router maps it correctly
        items = [
            {"id": mem_id, "type": "Fact", "properties": {
                "statement": content,
                "fact_type": memory_type,
                "confidence": confidence,
                "parent_id": agent_id,
                "passage_id": agent_id,  # router uses this for parent mapping
                **meta,
            }},
        ]

        # Link agent → memory in graph
        if self._graph:
            items.append({"id": f"agent_{agent_id}", "type": "Agent",
                           "properties": {"name": agent_id}})
            items.append({"source_id": f"agent_{agent_id}", "target_id": mem_id,
                           "edge_type": "REMEMBERS"})
            if entity_id:
                items.append({"source_id": mem_id, "target_id": entity_id,
                               "edge_type": "ABOUT"})

        self.router.ingest(items, namespace=self._namespace)
        logger.debug("Remembered [%s] %s: %s", memory_type, agent_id, content[:50])
        return mem_id

    # ── Recall ──

    def recall(
        self,
        agent_id: str = None,
        query: str = None,
        memory_type: str = None,
        entity_id: str = None,
        limit: int = 10,
        min_confidence: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """Recall memories matching criteria.

        Args:
            agent_id: Filter by agent (None = all agents).
            query: Semantic search query (searches memory text).
            memory_type: Filter by type (preference, fact, decision, etc.).
            entity_id: Filter by related entity.
            limit: Max results.
            min_confidence: Minimum confidence threshold.

        Returns:
            List of memory dicts with text, type, confidence, age.
        """
        # Build DuckDB query
        conditions = ["type = 'fact'"]  # memories are stored as facts
        params = []

        if agent_id:
            conditions.append("parent_id = ?")
            params.append(agent_id)
        if memory_type:
            conditions.append("json_extract_string(metadata, '$.fact_type') = ?")
            params.append(memory_type)
        if min_confidence > 0:
            conditions.append("CAST(json_extract_string(metadata, '$.confidence') AS REAL) >= ?")
            params.append(min_confidence)

        where = " AND ".join(conditions)
        sql = f"SELECT * FROM content WHERE {where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        try:
            rows = self.store.query(sql, params)
        except Exception as e:
            logger.warning("Memory recall query failed: %s", e)
            return []

        # Post-filter by query (simple text match — for semantic, use vector store)
        results = []
        now = datetime.now(timezone.utc)
        for row in rows:
            text = row.get("text", row.get("statement", ""))

            # Check expiry
            expires = row.get("expires_at")
            if expires:
                try:
                    exp_dt = datetime.fromisoformat(expires)
                    if now > exp_dt:
                        continue
                except (ValueError, TypeError):
                    pass

            # Text match filter
            if query and query.lower() not in text.lower():
                # Simple substring match — replace with vector similarity for production
                continue

            # Calculate age
            created = row.get("created_at", "")
            age_hours = 0
            try:
                created_dt = datetime.fromisoformat(str(created))
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=timezone.utc)
                age_hours = (now - created_dt).total_seconds() / 3600
            except (ValueError, TypeError):
                pass

            results.append({
                "id": row.get("id", ""),
                "text": text,
                "memory_type": row.get("fact_type", row.get("memory_type", "general")),
                "confidence": float(row.get("confidence", 1.0)),
                "agent_id": row.get("agent_id", row.get("parent_id", "")),
                "entity_id": row.get("entity_id"),
                "age_hours": round(age_hours, 1),
                "created_at": str(created),
            })

        return results

    # ── Extract from conversation ──

    def extract_and_store(
        self,
        agent_id: str,
        messages: List[Dict[str, str]],
        entity_id: str = None,
    ) -> List[str]:
        """Auto-extract facts from a conversation and store them.

        Scans user messages for patterns like:
          "My name is Alice" → fact
          "I prefer dark mode" → preference
          "We decided to use PostgreSQL" → decision
          "Meeting on Friday" → event

        Args:
            agent_id: Agent storing the memories.
            messages: List of {"role": "user"/"assistant", "content": "..."}
            entity_id: Optional entity context.

        Returns:
            List of memory IDs created.
        """
        mem_ids = []
        seen = set()

        for msg in messages:
            if msg.get("role") not in ("user", "human"):
                continue
            text = msg.get("content", "")
            if not text or len(text) < 5:
                continue

            for pattern, mem_type in _COMPILED_PATTERNS:
                match = pattern.search(text)
                if match:
                    fact = match.group(0).strip()
                    # Deduplicate within this batch
                    fact_key = hashlib.md5(fact.lower().encode()).hexdigest()[:8]
                    if fact_key in seen:
                        continue
                    seen.add(fact_key)

                    mid = self.remember(
                        agent_id=agent_id,
                        content=fact,
                        memory_type=mem_type,
                        entity_id=entity_id,
                        confidence=0.8,  # auto-extracted = slightly lower confidence
                    )
                    mem_ids.append(mid)

        if mem_ids:
            logger.info("Extracted %d memories from %d messages for %s",
                        len(mem_ids), len(messages), agent_id)
        return mem_ids

    # ── Consolidate ──

    def consolidate(self, agent_id: str = None) -> int:
        """Merge duplicate memories.

        Finds memories with similar text and keeps the one with highest
        confidence, updating it with the latest timestamp.

        Returns:
            Number of duplicates removed.
        """
        memories = self.recall(agent_id=agent_id, limit=1000)
        if len(memories) < 2:
            return 0

        # Group by rough text similarity (first 50 chars lowercase)
        groups: Dict[str, List[dict]] = {}
        for m in memories:
            key = m["text"][:50].lower().strip()
            groups.setdefault(key, []).append(m)

        removed = 0
        for key, group in groups.items():
            if len(group) <= 1:
                continue
            # Keep highest confidence
            group.sort(key=lambda x: x["confidence"], reverse=True)
            keep = group[0]
            for dup in group[1:]:
                # Delete duplicate from content store
                try:
                    self.store.query(f"DELETE FROM content WHERE id = ?", [dup["id"]])
                    removed += 1
                except Exception:
                    pass

        if removed:
            logger.info("Consolidated %d duplicate memories", removed)
        return removed

    # ── Decay ──

    def decay(self, decay_rate: float = 0.01, min_confidence: float = 0.1) -> int:
        """Reduce confidence of old memories.

        confidence -= decay_rate * age_in_days

        Memories below min_confidence are deleted.

        Returns:
            Number of memories decayed or deleted.
        """
        memories = self.recall(limit=5000, min_confidence=0.0)
        affected = 0

        for m in memories:
            age_days = m["age_hours"] / 24
            new_conf = max(0, m["confidence"] - (decay_rate * age_days))

            if new_conf < min_confidence:
                # Delete expired memory
                try:
                    self.store.query("DELETE FROM content WHERE id = ?", [m["id"]])
                    affected += 1
                except Exception:
                    pass
            elif new_conf < m["confidence"]:
                # Update confidence
                try:
                    self.store.query(
                        "UPDATE content SET metadata = json_set(metadata, '$.confidence', ?) WHERE id = ?",
                        [new_conf, m["id"]]
                    )
                    affected += 1
                except Exception:
                    pass

        if affected:
            logger.info("Decayed %d memories (rate=%.3f, min=%.2f)", affected, decay_rate, min_confidence)
        return affected

    # ── Context building ──

    def build_context(
        self,
        agent_id: str,
        query: str = None,
        limit: int = 10,
        include_entity_context: bool = True,
    ) -> Dict[str, Any]:
        """Build LLM-ready context from agent memories.

        Returns a dict that can be injected into ContextHub:
            {
                "memories": [...],
                "entities": [...],
                "summary": "Agent knows X, Y, Z..."
            }
        """
        memories = self.recall(agent_id=agent_id, query=query, limit=limit, min_confidence=0.1)

        # Group by type
        by_type: Dict[str, List[str]] = {}
        entities_mentioned = set()
        for m in memories:
            by_type.setdefault(m["memory_type"], []).append(m["text"])
            if m.get("entity_id"):
                entities_mentioned.add(m["entity_id"])

        # Build summary
        parts = []
        for mtype, texts in by_type.items():
            parts.append(f"{mtype.title()}s: {'; '.join(texts[:5])}")
        summary = " | ".join(parts) if parts else "No relevant memories."

        result = {
            "memories": memories,
            "memory_count": len(memories),
            "types": {k: len(v) for k, v in by_type.items()},
            "summary": summary,
        }

        # Entity context from graph
        if include_entity_context and self._graph and entities_mentioned:
            entity_info = []
            for eid in entities_mentioned:
                node = self._graph.get_node(eid)
                if node:
                    entity_info.append({
                        "id": eid,
                        "name": node.properties.get("name", eid),
                        "type": node.node_type,
                    })
            result["entities"] = entity_info

        return result

    # ── Stats ──

    def stats(self, agent_id: str = None) -> Dict[str, Any]:
        """Memory statistics."""
        try:
            if agent_id:
                rows = self.store.query(
                    "SELECT json_extract_string(metadata, '$.fact_type') as mtype, COUNT(*) as cnt "
                    "FROM content WHERE type = 'fact' AND parent_id = ? GROUP BY mtype",
                    [agent_id]
                )
            else:
                rows = self.store.query(
                    "SELECT json_extract_string(metadata, '$.fact_type') as mtype, COUNT(*) as cnt "
                    "FROM content WHERE type = 'fact' GROUP BY mtype"
                )
            return {r.get("mtype", "unknown"): r.get("cnt", 0) for r in rows}
        except Exception:
            return {}


def get_memory_engine(graph=None, namespace: str = "memory") -> MemoryEngine:
    """Get or create the global memory engine."""
    global _global_engine
    if _global_engine is None:
        with _engine_lock:
            if _global_engine is None:
                _global_engine = MemoryEngine(graph=graph, namespace=namespace)
    if graph and _global_engine._graph is None:
        _global_engine.set_graph(graph)
    return _global_engine
