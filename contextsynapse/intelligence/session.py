"""Session — live workspace with attach/detach contexts.

A Session is where agents and analysts work. You attach atomic contexts
to a session, and the runtime assembler provides a unified view.
Agents read from the session and write insights back.

Usage:
    from contextsynapse.intelligence.session import ContextSession

    session = ContextSession("IT Sector Analysis", graph_registry=registry)
    session.attach("tcs")
    session.attach("india_economy")
    session.attach("tcs_price")

    # Get unified view
    view = session.assemble()

    # Agent writes insight back
    session.add_insight(
        insight="TCS outperforms when USD weakens",
        produced_by="agent:claude",
        confidence=0.85,
        evidence=["TCS Q1 revenue up 4.2%", "USD/INR stable at 83.5"],
    )

    # Detach a context
    session.detach("tcs_price")

    # View updates automatically
    view = session.assemble()
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Insight:
    """An insight produced by an agent or the platform."""
    id: str = ""
    insight: str = ""
    produced_by: str = ""       # agent:claude, platform:fusion, external:model
    confidence: float = 0.0
    evidence: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    created_at: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = f"insight_{uuid.uuid4().hex[:10]}"
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "insight": self.insight,
            "produced_by": self.produced_by,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "tags": self.tags,
            "created_at": self.created_at,
        }

    def to_node_props(self) -> Dict[str, Any]:
        return {
            "name": self.insight[:80],
            "statement": self.insight,
            "produced_by": self.produced_by,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "tags": self.tags,
            "source_type": "insight",
            "_created_at": self.created_at,
        }


class ContextSession:
    """A live workspace with attach/detach contexts and insight write-back."""

    def __init__(self, name: str, graph_registry=None, context_manager=None):
        self.name = name
        self.session_id = f"session_{uuid.uuid4().hex[:10]}"
        self._registry = graph_registry
        self._context_manager = context_manager
        self._attached: List[str] = []
        self._insights: List[Insight] = []
        self._created_at = datetime.now(timezone.utc).isoformat()
        self._assembler = None

    def _get_assembler(self):
        if not self._assembler:
            from .runtime_context import RuntimeContextAssembler
            self._assembler = RuntimeContextAssembler(
                graph_registry=self._registry,
                context_manager=self._context_manager,
            )
        return self._assembler

    def attach(self, context_name: str) -> bool:
        """Attach an atomic context to this session."""
        if context_name in self._attached:
            return False
        self._attached.append(context_name)
        # Invalidate assembler cache
        self._get_assembler().invalidate_cache(context_name)
        logger.info("[SESSION] '%s' attached context '%s' (%d total)",
                     self.name, context_name, len(self._attached))
        return True

    def detach(self, context_name: str) -> bool:
        """Detach an atomic context from this session."""
        if context_name not in self._attached:
            return False
        self._attached.remove(context_name)
        self._get_assembler().invalidate_cache(context_name)
        logger.info("[SESSION] '%s' detached context '%s' (%d remaining)",
                     self.name, context_name, len(self._attached))
        return True

    def list_attached(self) -> List[str]:
        """List all attached context names."""
        return list(self._attached)

    def assemble(self, focus_entity: str = "", days: int = 30):
        """Assemble a unified view from all attached contexts."""
        if not self._attached:
            return None
        assembler = self._get_assembler()
        return assembler.assemble(
            contexts=self._attached,
            focus_entity=focus_entity,
            days=days,
        )

    def add_insight(
        self,
        insight: str,
        produced_by: str = "",
        confidence: float = 0.0,
        evidence: List[str] = None,
        tags: List[str] = None,
        context: str = "",
    ) -> Insight:
        """Write an insight back to the platform.

        The insight is stored:
        1. In-memory (this session)
        2. In the target context's graph (if context specified) — visible to
           all agents, dashboard, and future sessions
        3. In the session's insight graph (fallback if no context specified)

        Args:
            insight: The insight text
            produced_by: Who produced it (agent:claude, platform:fusion, etc.)
            confidence: 0.0-1.0
            evidence: Supporting evidence
            tags: Classification tags
            context: Which atomic context to write into (e.g., "tcs")
        """
        ins = Insight(
            insight=insight,
            produced_by=produced_by,
            confidence=confidence,
            evidence=evidence or [],
            tags=tags or [],
        )
        self._insights.append(ins)

        # Write into the target context's graph (shared, visible to all)
        if context:
            self._write_to_context(ins, context)
        else:
            # Fallback: write to session-specific insights graph
            self._write_to_session_graph(ins)

        logger.info("[SESSION] '%s' insight by '%s' → %s: %s",
                     self.name, produced_by, context or 'session', insight[:50])
        return ins

    def _write_to_context(self, ins: Insight, context_name: str):
        """Write insight into an atomic context's graph — visible to everyone."""
        if not self._registry:
            return

        db = self._registry.get_graph(context_name, load_if_missing=True)
        if not db:
            return

        from ..core.graph_structures import GraphNode
        props = ins.to_node_props()
        props["_session"] = self.name
        props["_session_id"] = self.session_id

        db.add_node(GraphNode(
            id=ins.id,
            label="Insight",
            properties=props,
        ))

    def _write_to_session_graph(self, ins: Insight):
        """Fallback: store in session-specific graph."""
        if not self._registry:
            return

        graph_name = f"insights_{self.name.lower().replace(' ', '_')}"
        db = self._registry.get_graph(graph_name, load_if_missing=True)
        if not db:
            db = self._registry.create_graph(graph_name)

        from ..core.graph_structures import GraphNode
        db.add_node(GraphNode(
            id=ins.id,
            label="Insight",
            properties=ins.to_node_props(),
        ))

    def get_insights(self, produced_by: str = "") -> List[Insight]:
        """Get insights from this session, optionally filtered by producer."""
        if produced_by:
            return [i for i in self._insights if i.produced_by == produced_by]
        return list(self._insights)

    def status(self) -> Dict[str, Any]:
        """Get session status."""
        return {
            "session_id": self.session_id,
            "name": self.name,
            "attached_contexts": self._attached,
            "context_count": len(self._attached),
            "insight_count": len(self._insights),
            "created_at": self._created_at,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            **self.status(),
            "insights": [i.to_dict() for i in self._insights],
        }


class SessionManager:
    """Manages multiple sessions."""

    def __init__(self, graph_registry=None, context_manager=None):
        self._registry = graph_registry
        self._context_manager = context_manager
        self._sessions: Dict[str, ContextSession] = {}

    def create(self, name: str) -> ContextSession:
        """Create a new session."""
        session = ContextSession(
            name=name,
            graph_registry=self._registry,
            context_manager=self._context_manager,
        )
        self._sessions[session.session_id] = session
        logger.info("[SESSION-MGR] Created session '%s' (id=%s)", name, session.session_id)
        return session

    def get(self, session_id: str) -> Optional[ContextSession]:
        return self._sessions.get(session_id)

    def list_sessions(self) -> List[Dict[str, Any]]:
        return [s.status() for s in self._sessions.values()]

    def delete(self, session_id: str) -> bool:
        if session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False
