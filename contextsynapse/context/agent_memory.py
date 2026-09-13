"""
Agent Memory
=============
Persist agent sessions, interactions, and knowledge as a memory graph.

Each agent gets a subgraph within a shared namespace. Interactions are stored
as ContextItems with provenance, and can be recalled later using graph
traversal or semantic search.

Usage:
    memory = AgentMemory(graph_registry, namespace="shared_brain")
    memory.remember(agent_id="agent-1", content="User prefers dark mode", tags=["preference"])
    memory.log_interaction(agent_id="agent-1", role="user", content="Turn on dark mode")
    memory.log_interaction(agent_id="agent-1", role="assistant", content="Done.")

    # Recall
    memories = memory.recall(agent_id="agent-1", query="dark mode", limit=5)
    hub = memory.build_context(agent_id="agent-1", system_prompt="You are helpful.")
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class AgentMemory:
    """
    Persist and recall agent memory as a graph.

    Nodes:
      - Agent (id=agent_id, label="Agent")
      - Memory (label="Memory", content=..., tags=...)
      - Session (label="Session", session_id=...)
      - Interaction (label="Interaction", role=..., content=...)

    Edges:
      - Agent -[HAS_MEMORY]-> Memory
      - Agent -[HAS_SESSION]-> Session
      - Session -[CONTAINS]-> Interaction
      - Interaction -[NEXT]-> Interaction  (ordered chain)
      - Memory -[RELATED_TO]-> Memory  (cross-references)
    """

    def __init__(self, graph_registry, namespace: str = "agent_memory"):
        self.graph_registry = graph_registry
        self.namespace = namespace
        self._ensure_graph()

    @staticmethod
    def _update_props(db, node_id: str, props: Dict[str, Any]):
        """Update node properties — works for both CSR and Redis backends."""
        # Try Redis adapter method first
        if hasattr(db, 'csr_adapter') and hasattr(db.csr_adapter, 'update_node_properties'):
            db.csr_adapter.update_node_properties(node_id, props)
            return
        # CSR fallback: update in-memory dicts directly
        node = db.get_node(node_id) if hasattr(db, 'get_node') else None
        if node:
            node.properties.update(props)
        if hasattr(db, 'node_properties'):
            db.node_properties[node_id] = props
        # Also update CSR node if accessible
        if hasattr(db, 'csr_adapter') and hasattr(db.csr_adapter, 'nodes'):
            csr_node = db.csr_adapter.nodes.get(node_id)
            if csr_node:
                csr_node.properties.update(props)

    def _ensure_graph(self):
        """Ensure the memory graph exists."""
        db = self.graph_registry.get_graph(self.namespace, load_if_missing=True)
        if not db:
            self.graph_registry.create_graph(self.namespace)

    def _get_db(self):
        return self.graph_registry.get_graph(self.namespace, load_if_missing=True)

    def _agent_node_id(self, agent_id: str) -> str:
        return f"agent_{hashlib.sha256(agent_id.encode()).hexdigest()[:12]}"

    def _ensure_agent_node(self, agent_id: str):
        """Create agent node if it doesn't exist."""
        from ..core.graph_structures import GraphNode
        db = self._get_db()
        node_id = self._agent_node_id(agent_id)
        existing = db.get_node(node_id)
        if not existing:
            db.add_node(GraphNode(
                id=node_id,
                label="Agent",
                properties={
                    "agent_id": agent_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            ))
        return node_id

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    MEMORY_TYPES = {"decision", "preference", "pattern", "fact", "observation"}
    MIN_CONFIDENCE = 0.3

    def save_memory(
        self,
        agent_id: str,
        content: str,
        memory_type: str = "fact",
        confidence: float = 1.0,
        **kwargs,
    ) -> str:
        """Store a typed memory. Convenience wrapper around remember().

        Args:
            memory_type: One of decision, preference, pattern, fact.
        """
        if memory_type not in self.MEMORY_TYPES:
            memory_type = "fact"
        return self.remember(
            agent_id, content,
            tags=[memory_type],
            confidence=confidence,
            metadata={"memory_type": memory_type, **kwargs},
        )

    def recall_memories(
        self,
        agent_id: str,
        query: str,
        k: int = 5,
        memory_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Recall top-k memories, optionally filtered by type."""
        tags = [memory_type] if memory_type else None
        return self.recall(agent_id, query=query, tags=tags, limit=k)

    def remember(
        self,
        agent_id: str,
        content: str,
        tags: Optional[List[str]] = None,
        confidence: float = 1.0,
        source: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Store a memory for an agent.

        Returns the memory node ID.
        """
        from ..core.graph_structures import GraphNode, GraphEdge

        db = self._get_db()
        agent_node_id = self._ensure_agent_node(agent_id)

        mem_id = f"mem_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()

        db.add_node(GraphNode(
            id=mem_id,
            label="Memory",
            properties={
                "content": content,
                "tags": tags or [],
                "confidence": confidence,
                "source": source or "agent",
                "created_at": now,
                "agent_id": agent_id,
                "memory_type": (metadata or {}).get("memory_type", "fact"),
                "access_count": 0,
                "last_accessed": None,
                "version": 1,
                "status": "active",
                "valid_from": now,
                "valid_to": None,
                **(metadata or {}),
            },
        ))

        db.add_edge(GraphEdge(
            id=str(uuid.uuid4()),
            source=agent_node_id,
            target=mem_id,
            label="HAS_MEMORY",
            properties={"created_at": now},
        ))

        logger.info("Agent %s stored memory %s", agent_id, mem_id)
        return mem_id

    def forget(self, agent_id: str, memory_id: str) -> bool:
        """Remove a specific memory."""
        db = self._get_db()
        try:
            db.remove_node(memory_id)
            return True
        except Exception:
            return False

    @staticmethod
    def _effective_confidence(props: Dict[str, Any]) -> float:
        """Compute effective confidence with time decay and access reinforcement."""
        base = props.get("confidence", 1.0)
        created_at = props.get("created_at", "")
        access_count = props.get("access_count", 0)

        if not created_at:
            return base

        try:
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            age_days = max(0, (datetime.now(timezone.utc) - created).total_seconds() / 86400)
        except (ValueError, TypeError):
            return base

        decay = 0.95 ** (age_days / 7)  # ~5% decay per week
        reinforcement = min(1.0, 0.1 * access_count)  # access resists decay
        effective = base * max(decay, reinforcement)
        return round(effective, 4)

    def update_memory(
        self,
        agent_id: str,
        memory_id: str,
        new_content: str,
        reason: str = "user_correction",
    ) -> str:
        """Optional agent feedback: create a new version of a memory, superseding the old.

        Returns the new memory node ID.
        """
        from ..core.graph_structures import GraphNode, GraphEdge

        db = self._get_db()
        old_node = db.get_node(memory_id)
        if not old_node:
            raise ValueError(f"Memory {memory_id} not found")

        old_props = old_node.properties or {}
        now = datetime.now(timezone.utc).isoformat()

        # Supersede old memory
        old_props["status"] = "superseded"
        old_props["valid_to"] = now
        self._update_props(db, memory_id, old_props)

        # Create new version
        new_mid = f"mem_{uuid.uuid4().hex[:12]}"
        old_version = old_props.get("version", 1)

        db.add_node(GraphNode(
            id=new_mid,
            label="Memory",
            properties={
                "content": new_content,
                "tags": old_props.get("tags", []),
                "confidence": old_props.get("confidence", 1.0),
                "source": old_props.get("source", "agent"),
                "created_at": now,
                "agent_id": agent_id,
                "memory_type": old_props.get("memory_type", "fact"),
                "access_count": 0,
                "last_accessed": None,
                "version": old_version + 1,
                "status": "active",
                "valid_from": now,
                "valid_to": None,
            },
        ))

        # SUPERSEDES edge: new → old
        db.add_edge(GraphEdge(
            id=str(uuid.uuid4()),
            source=new_mid,
            target=memory_id,
            label="SUPERSEDES",
            properties={"reason": reason, "superseded_at": now},
        ))

        # Link new memory to agent
        agent_node_id = self._agent_node_id(agent_id)
        db.add_edge(GraphEdge(
            id=str(uuid.uuid4()),
            source=agent_node_id,
            target=new_mid,
            label="HAS_MEMORY",
            properties={"created_at": now},
        ))

        return new_mid

    def auto_consolidate(
        self,
        agent_id: str,
        overlap_threshold: float = 0.4,
    ) -> Dict[str, Any]:
        """System-driven: find and merge similar memories automatically.

        Returns dict with consolidated_count and new_memory_id.
        """
        from ..core.graph_structures import GraphNode, GraphEdge

        candidates = self.recall(agent_id, limit=50)
        if len(candidates) < 2:
            return {"consolidated_count": 0, "new_memory_id": None}

        def _keywords(text):
            return set(w.lower() for w in text.split() if len(w) > 2)

        groups = []
        used = set()
        for i, c in enumerate(candidates):
            if i in used:
                continue
            group = [c]
            kw_i = _keywords(c["content"])
            for j, d in enumerate(candidates[i + 1:], i + 1):
                if j in used:
                    continue
                kw_j = _keywords(d["content"])
                if kw_i and kw_j:
                    overlap = len(kw_i & kw_j) / min(len(kw_i), len(kw_j))
                    if overlap >= overlap_threshold:
                        group.append(d)
                        used.add(j)
            if len(group) >= 2:
                groups.append(group)
                used.add(i)

        if not groups:
            return {"consolidated_count": 0, "new_memory_id": None}

        group = max(groups, key=len)
        db = self._get_db()
        now = datetime.now(timezone.utc).isoformat()

        best = max(group, key=lambda m: len(m["content"]))
        max_conf = max(m.get("confidence", 1.0) for m in group)
        new_conf = min(1.0, max_conf + 0.05 * (len(group) - 1))

        new_mid = f"mem_{uuid.uuid4().hex[:12]}"
        db.add_node(GraphNode(
            id=new_mid,
            label="Memory",
            properties={
                "content": best["content"],
                "tags": best.get("tags", []),
                "confidence": new_conf,
                "source": "system:consolidation",
                "created_at": now,
                "agent_id": agent_id,
                "memory_type": best.get("metadata", {}).get("memory_type", "fact"),
                "access_count": sum(m.get("metadata", {}).get("access_count", 0) for m in group),
                "last_accessed": now,
                "version": 1,
                "status": "active",
                "valid_from": now,
                "valid_to": None,
            },
        ))

        agent_node_id = self._agent_node_id(agent_id)
        db.add_edge(GraphEdge(
            id=str(uuid.uuid4()), source=agent_node_id, target=new_mid,
            label="HAS_MEMORY", properties={"created_at": now},
        ))

        for m in group:
            old_node = db.get_node(m["id"])
            if old_node:
                old_node.properties["status"] = "consolidated"
                old_node.properties["valid_to"] = now
                self._update_props(db, m["id"], old_node.properties)

            db.add_edge(GraphEdge(
                id=str(uuid.uuid4()), source=new_mid, target=m["id"],
                label="DERIVED_FROM", properties={"created_at": now},
            ))

        return {"consolidated_count": len(group), "new_memory_id": new_mid}

    def auto_promote(
        self,
        target_namespace: str = "default",
        graph_registry=None,
        min_access_count: int = 5,
        min_confidence: float = 0.7,
    ) -> Dict[str, Any]:
        """System-driven: promote high-value memories to the main graph.

        Returns dict with promoted_count and promoted_ids.
        """
        from ..core.graph_structures import GraphNode

        registry = graph_registry or self.graph_registry
        main_db = registry.get_graph(target_namespace, load_if_missing=True)
        if not main_db:
            return {"promoted_count": 0, "promoted_ids": []}

        db = self._get_db()
        all_nodes = db.get_all_nodes()
        now = datetime.now(timezone.utc).isoformat()
        promoted_ids = []

        for node in all_nodes:
            if getattr(node, 'label', '') != 'Memory':
                continue
            props = node.properties or {}
            if props.get("status") != "active":
                continue
            if props.get("access_count", 0) < min_access_count:
                continue
            eff_conf = self._effective_confidence(props)
            if eff_conf < min_confidence:
                continue

            new_id = f"promoted_{uuid.uuid4().hex[:12]}"
            main_db.add_node(GraphNode(
                id=new_id,
                label="Knowledge",
                properties={
                    "name": props.get("content", "")[:80],
                    "content": props.get("content", ""),
                    "source_type": "memory",
                    "source_id": "system:auto_promote",
                    "created_by": "system:auto_promote",
                    "confidence": eff_conf,
                    "promoted_from": f"{self.namespace}:{node.id}",
                    "created_at": now,
                    "memory_type": props.get("memory_type", "fact"),
                },
            ))

            props["status"] = "promoted"
            self._update_props(db, node.id, props)
            promoted_ids.append(new_id)

        return {"promoted_count": len(promoted_ids), "promoted_ids": promoted_ids}

    def recall(
        self,
        agent_id: str,
        query: Optional[str] = None,
        tags: Optional[List[str]] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Recall memories for an agent.

        Filters by tags if provided. If query is given, does substring match
        on content (semantic search would use embeddings).
        """
        db = self._get_db()
        agent_node_id = self._agent_node_id(agent_id)

        # Get memory IDs via edges (primary) or property scan (fallback)
        all_edges = db.get_all_edges()
        mem_ids = [
            e.target for e in all_edges
            if e.source == agent_node_id and e.label == "HAS_MEMORY"
        ]

        # Fallback: if no edges found, search Memory nodes by agent_id property
        if not mem_ids:
            try:
                all_nodes = db.get_all_nodes()
            except TypeError:
                all_nodes = db.get_all_nodes()
            for node in all_nodes:
                if getattr(node, "label", "") == "Memory":
                    props = getattr(node, "properties", {}) or {}
                    if props.get("agent_id") == agent_id:
                        mem_ids.append(node.id)

        memories = []
        for mid in mem_ids:
            node = db.get_node(mid)
            if not node:
                continue
            props = node.properties or {}

            # Skip non-active memories (B4: superseded/consolidated/expired excluded)
            status = props.get("status", "active")
            if status in ("superseded", "consolidated", "expired", "promoted"):
                continue

            # Decay check (B3): exclude if effective confidence below threshold
            eff_conf = self._effective_confidence(props)
            if eff_conf < self.MIN_CONFIDENCE:
                continue

            # Filter by tags
            if tags:
                node_tags = props.get("tags", [])
                if not any(t in node_tags for t in tags):
                    continue

            # Filter by query (keyword overlap — more flexible than exact substring)
            if query:
                content = props.get("content", "").lower()
                q_terms = [w for w in query.lower().split() if len(w) > 2]
                if q_terms:
                    hits = sum(1 for t in q_terms if t in content)
                    if hits == 0:
                        continue
                    # Store score for ranking
                    props["_match_score"] = hits / len(q_terms)

            # Reinforce (B2): bump access_count and last_accessed
            props["access_count"] = props.get("access_count", 0) + 1
            props["last_accessed"] = datetime.now(timezone.utc).isoformat()
            try:
                self._update_props(db, mid, props)
            except Exception:
                pass  # reinforcement failure should not break recall

            memories.append({
                "id": mid,
                "content": props.get("content", ""),
                "tags": props.get("tags", []),
                "confidence": props.get("confidence", 1.0),
                "effective_confidence": eff_conf,
                "source": props.get("source", ""),
                "created_at": props.get("created_at", ""),
                "metadata": {k: v for k, v in props.items()
                             if k not in ("content", "tags", "confidence", "source", "created_at", "agent_id")},
            })

        # Sort by match score (if query) then by created_at descending
        if query:
            memories.sort(key=lambda m: m.get("metadata", {}).get("_match_score", 0), reverse=True)
        else:
            memories.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return memories[:limit]

    # ------------------------------------------------------------------
    # Session / Interaction logging
    # ------------------------------------------------------------------

    def start_session(self, agent_id: str, session_metadata: Optional[Dict] = None) -> str:
        """Start a new interaction session for an agent."""
        from ..core.graph_structures import GraphNode, GraphEdge

        db = self._get_db()
        agent_node_id = self._ensure_agent_node(agent_id)
        session_id = f"session_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()

        db.add_node(GraphNode(
            id=session_id,
            label="Session",
            properties={
                "session_id": session_id,
                "agent_id": agent_id,
                "started_at": now,
                "status": "active",
                **(session_metadata or {}),
            },
        ))

        db.add_edge(GraphEdge(
            id=str(uuid.uuid4()),
            source=agent_node_id,
            target=session_id,
            label="HAS_SESSION",
            properties={"started_at": now},
        ))

        return session_id

    def log_interaction(
        self,
        agent_id: str,
        role: str,
        content: str,
        session_id: Optional[str] = None,
        session_builder=None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Log a single interaction turn within a session.

        If ``session_builder`` (a SessionGraphBuilder) is provided, the
        interaction is graphified into the session's unified graph instead
        of the standalone agent_memory namespace.  This creates Turn,
        Topic, Decision, Question, and Action nodes with cross-layer
        REFERENCES edges to knowledge entities.

        If no session_builder is given, falls back to the original
        agent_memory namespace behavior for backward compatibility.

        If no session_id is provided (and no builder), creates a new session.
        """
        # ── Session-graph path (unified graph) ──
        if session_builder is not None:
            import asyncio

            async def _graphify():
                return await session_builder.graphify_interaction(
                    role=role,
                    content=content,
                    agent_id=agent_id,
                )

            # Run the async graphify — works whether called from sync or async context
            try:
                loop = asyncio.get_running_loop()
                # Already in async context — schedule as a task
                future = asyncio.ensure_future(_graphify())
                # Return a placeholder ID; the real Turn node ID is in the future
                return f"session_graph_turn_{uuid.uuid4().hex[:12]}"
            except RuntimeError:
                # No running loop — run synchronously
                result = asyncio.run(_graphify())
                return result.turn_node_id

        # ── Legacy agent_memory namespace path ──
        from ..core.graph_structures import GraphNode, GraphEdge

        db = self._get_db()

        if not session_id:
            session_id = self.start_session(agent_id)

        interaction_id = f"interaction_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()

        db.add_node(GraphNode(
            id=interaction_id,
            label="Interaction",
            properties={
                "role": role,
                "content": content,
                "agent_id": agent_id,
                "session_id": session_id,
                "timestamp": now,
                **(metadata or {}),
            },
        ))

        db.add_edge(GraphEdge(
            id=str(uuid.uuid4()),
            source=session_id,
            target=interaction_id,
            label="CONTAINS",
            properties={"timestamp": now},
        ))

        # Link to previous interaction in same session (ordered chain)
        all_edges = db.get_all_edges()
        session_interactions = [
            e.target for e in all_edges
            if e.source == session_id and e.label == "CONTAINS"
        ]
        if len(session_interactions) > 1:
            prev_id = session_interactions[-2]  # The one before the current
            db.add_edge(GraphEdge(
                id=str(uuid.uuid4()),
                source=prev_id,
                target=interaction_id,
                label="NEXT",
                properties={},
            ))

        return interaction_id

    def end_session(self, session_id: str):
        """Mark a session as ended."""
        db = self._get_db()
        node = db.get_node(session_id)
        if node:
            node.properties["status"] = "ended"
            node.properties["ended_at"] = datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # Context building
    # ------------------------------------------------------------------

    def build_context(
        self,
        agent_id: str,
        system_prompt: Optional[str] = None,
        include_memories: bool = True,
        include_recent_interactions: int = 10,
        memory_tags: Optional[List[str]] = None,
        memory_limit: int = 20,
        max_tokens: int = 8000,
    ):
        """
        Build a ContextHub pre-loaded with agent memories and recent interactions.

        Returns a ContextHub ready for LLM API calls.
        """
        from .hub import ContextHub, ContextRole

        hub = ContextHub(system_prompt=system_prompt, max_tokens=max_tokens)

        # Add memories as background context
        if include_memories:
            memories = self.recall(agent_id, tags=memory_tags, limit=memory_limit)
            if memories:
                mem_text = "\n".join(
                    f"- [{m.get('created_at', '?')[:10]}] {m['content']}"
                    + (f" (tags: {', '.join(m['tags'])})" if m.get("tags") else "")
                    for m in memories
                )
                hub.add_text(
                    f"## Agent Memories\n{mem_text}",
                    role=ContextRole.BACKGROUND,
                    label="Agent Memories",
                    source="agent_memory_graph",
                )

        # Add recent interactions
        if include_recent_interactions > 0:
            interactions = self._get_recent_interactions(agent_id, include_recent_interactions)
            for inter in interactions:
                role_map = {
                    "user": ContextRole.USER,
                    "assistant": ContextRole.ASSISTANT,
                    "system": ContextRole.SYSTEM,
                    "tool": ContextRole.TOOL,
                }
                ctx_role = role_map.get(inter.get("role", ""), ContextRole.INTERACTION)
                hub.add_text(
                    inter.get("content", ""),
                    role=ctx_role,
                    label=f"Interaction ({inter.get('role', 'unknown')})",
                    source=inter.get("session_id", ""),
                )

        return hub

    def _get_recent_interactions(self, agent_id: str, limit: int) -> List[Dict]:
        """Get recent interactions across all sessions for an agent."""
        db = self._get_db()
        all_nodes = db.get_all_nodes()

        interactions = []
        for node in all_nodes:
            props = node.properties or {}
            if node.label == "Interaction" and props.get("agent_id") == agent_id:
                interactions.append({
                    "id": node.id,
                    "role": props.get("role", ""),
                    "content": props.get("content", ""),
                    "session_id": props.get("session_id", ""),
                    "timestamp": props.get("timestamp", ""),
                })

        interactions.sort(key=lambda i: i.get("timestamp", ""), reverse=True)
        return interactions[:limit]

    # ------------------------------------------------------------------
    # Cross-agent knowledge sharing
    # ------------------------------------------------------------------

    def share_memory(
        self,
        from_agent_id: str,
        to_agent_id: str,
        memory_id: str,
    ) -> bool:
        """Share a memory from one agent to another by creating a reference edge."""
        from ..core.graph_structures import GraphEdge

        db = self._get_db()
        to_node_id = self._ensure_agent_node(to_agent_id)

        # Verify memory exists
        mem_node = db.get_node(memory_id)
        if not mem_node:
            return False

        db.add_edge(GraphEdge(
            id=str(uuid.uuid4()),
            source=to_node_id,
            target=memory_id,
            label="HAS_MEMORY",
            properties={
                "shared_from": from_agent_id,
                "shared_at": datetime.now(timezone.utc).isoformat(),
            },
        ))

        return True

    def get_agent_summary(self, agent_id: str) -> Dict[str, Any]:
        """Get a summary of an agent's memory state."""
        db = self._get_db()
        agent_node_id = self._agent_node_id(agent_id)

        all_edges = db.get_all_edges()
        memory_count = sum(
            1 for e in all_edges
            if e.source == agent_node_id and e.label == "HAS_MEMORY"
        )
        session_count = sum(
            1 for e in all_edges
            if e.source == agent_node_id and e.label == "HAS_SESSION"
        )

        return {
            "agent_id": agent_id,
            "agent_node_id": agent_node_id,
            "memory_count": memory_count,
            "session_count": session_count,
        }
