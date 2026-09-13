"""
LangGraph CAS Context Tools
============================
LangGraph-compatible tools for interacting with Context-as-a-Service sessions.

These tools give myECHO / AgenticTwin agents the ability to:
- Ingest data into context sessions
- Search context semantically
- Contribute insights back to shared context
- Export context for LLM consumption
- View session state and provenance

Usage::

    from contextsynapse.adapters.langgraph import create_context_tools

    tools = create_context_tools(
        session_name="research-project",
        agent_name="myecho-analyst",
    )
    # Pass to LangGraph agent or ToolNode
"""

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def create_context_tools(
    session_name: str = "default",
    agent_name: str = "langgraph-agent",
    agent_role: str = "agent",
    agent_platform: str = "app",
) -> list:
    """
    Create LangGraph-compatible tools for CAS context operations.

    These tools operate on a shared context session, allowing agents in
    a myECHO / LangGraph workflow to read, write, and search context.

    Args:
        session_name: Name of the context session to operate on.
        agent_name:   Name to register this agent as.
        agent_role:   Role of the agent (agent, researcher, summarizer, etc.)

    Returns:
        List of @tool-decorated functions.
    """
    # Lazy-initialise CAS components
    _state = {}

    def _init():
        if _state:
            return
        from ...context.agents import AgentRegistry
        from ...context.session import ContextSessionManager
        from ...context.hub import ContextHub
        from ...context.vector_integration import SessionVectorStore
        from ...context.dedup import ContextDedup

        try:
            from ...core.registry import GraphRegistry
            graph_registry = GraphRegistry()
        except Exception:
            graph_registry = None

        agent_registry = AgentRegistry()
        session_manager = ContextSessionManager(graph_registry=graph_registry)
        vector_store = SessionVectorStore()
        dedup_engine = ContextDedup(vector_store=vector_store)

        # Register this agent
        agent, api_key = agent_registry.register(
            name=agent_name,
            role=agent_role,
            platform=agent_platform,
            capabilities=["read", "write", "search"],
        )

        # Get or create the session
        session = session_manager.get_session_by_name(session_name)
        if not session:
            session = session_manager.create_session(
                name=session_name,
                owner_agent_id=agent.agent_id,
            )

        # Hub for in-memory context
        from ...context.cli import _load_hub
        hub = _load_hub(session.session_id)

        _state.update({
            "agent": agent,
            "session": session,
            "session_manager": session_manager,
            "agent_registry": agent_registry,
            "vector_store": vector_store,
            "dedup": dedup_engine,
            "graph_registry": graph_registry,
            "hub": hub,
        })

    def _save_hub():
        from ...context.cli import _save_hub as save_fn
        save_fn(_state["session"].session_id, _state["hub"])

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    @tool
    def ingest_context(text: str, source: str = "agent", label: str = "") -> str:
        """Add text to the shared context session. Use this to store information,
        observations, document excerpts, or any data that should be available
        to other agents in the workflow.

        Args:
            text: The text content to ingest into context.
            source: Where this data came from (e.g., "web_search", "user_input", "document").
            label: Optional short label/heading for this context item.

        Returns: JSON with success status and dedup info.
        """
        _init()
        session_id = _state["session"].session_id
        agent_id = _state["agent"].agent_id

        # Dedup check
        dup = _state["dedup"].check(session_id, text)
        if dup.is_duplicate:
            return json.dumps({
                "success": False,
                "duplicate": True,
                "match_type": dup.match_type,
                "score": dup.score,
                "message": f"Already in context ({dup.match_type})",
            })

        # Add to hub
        hub = _state["hub"]
        hub.add_text(text, role="retrieved", label=label or None, source=source)

        # Embed into vector store
        node_id = str(uuid.uuid4())
        vs = _state["vector_store"]
        if vs.available:
            vs.add_text(
                session_id=session_id,
                text=text,
                node_id=node_id,
                metadata={"agent_id": agent_id, "source": source, "label": label},
            )

        # Register hash
        _state["dedup"].register(session_id, node_id, text)
        _save_hub()

        return json.dumps({
            "success": True,
            "item_count": len(hub),
            "node_id": node_id,
        })

    @tool
    def search_context(query: str, k: int = 5) -> str:
        """Semantic search across the shared context session. Use this to find
        relevant information that has been ingested by any agent.

        Args:
            query: Natural language search query.
            k: Number of results to return (default 5).

        Returns: JSON list of matching context items with scores.
        """
        _init()
        session_id = _state["session"].session_id
        vs = _state["vector_store"]

        if not vs.available:
            return json.dumps({"error": "Vector search not available"})

        results = vs.search(session_id=session_id, query=query, k=k)
        return json.dumps(results, indent=2, default=str)

    @tool
    def contribute_insight(
        content: str,
        insight_type: str = "synthesis",
        label: str = "",
        source_ids: str = "",
    ) -> str:
        """Write an agent's output (summary, analysis, decision) back into the
        shared context. Other agents can see and build on this.

        Args:
            content: The insight text (summary, analysis, decision, etc.)
            insight_type: Type of contribution: "synthesis", "decision", or "generated".
            label: Optional heading for this insight.
            source_ids: Comma-separated IDs of items this was derived from.

        Returns: JSON with insight details.
        """
        _init()
        session_id = _state["session"].session_id
        agent_id = _state["agent"].agent_id

        # Dedup
        dup = _state["dedup"].check(session_id, content)
        if dup.is_duplicate:
            return json.dumps({
                "success": False,
                "duplicate": True,
                "message": f"Duplicate insight ({dup.match_type})",
            })

        hub = _state["hub"]
        src_ids = [s.strip() for s in source_ids.split(",") if s.strip()]

        hub.contribute(
            content=content,
            agent_id=agent_id,
            role=insight_type,
            label=label or None,
            source_ids=src_ids,
        )

        # Persist as graph node if possible
        insight_node_id = str(uuid.uuid4())
        graph_registry = _state["graph_registry"]
        session = _state["session"]
        if graph_registry:
            graph = graph_registry.get_graph(session.graph_namespace, load_if_missing=True)
            if graph:
                from ...core.graph_structures import GraphNode, GraphEdge
                node = GraphNode(
                    id=insight_node_id,
                    label="Insight",
                    properties={
                        "content": content,
                        "insight_type": insight_type,
                        "agent_id": agent_id,
                        "_ctx_session_id": session.session_id,
                    },
                )
                graph.add_node(node)

                for src_id in src_ids:
                    try:
                        edge = GraphEdge(
                            id=str(uuid.uuid4()),
                            source=insight_node_id,
                            target=src_id,
                            label="DERIVED_FROM",
                            properties={"agent_id": agent_id},
                        )
                        graph.add_edge(edge)
                    except Exception:
                        pass

        # Embed
        vs = _state["vector_store"]
        if vs.available:
            vs.add_text(
                session_id=session_id,
                text=content,
                node_id=insight_node_id,
                metadata={"agent_id": agent_id, "role": insight_type, "source": "contribute"},
            )

        _state["dedup"].register(session_id, insight_node_id, content)
        _save_hub()

        return json.dumps({
            "success": True,
            "insight_node_id": insight_node_id,
            "item_count": len(hub),
        })

    @tool
    def get_context(format: str = "prompt", max_items: int = 50) -> str:
        """Export the current shared context for use in LLM prompts.
        Use this to get all accumulated context before making a decision.

        Args:
            format: Output format - "prompt" (string), "messages" (chat format), or "markdown".
            max_items: Maximum number of context items to include.

        Returns: The context in the requested format.
        """
        _init()
        hub = _state["hub"]

        if format == "messages":
            return json.dumps(hub.to_messages(), indent=2)
        elif format == "markdown":
            return hub.to_markdown()
        else:
            return hub.to_prompt()

    @tool
    def context_stats() -> str:
        """Get statistics about the current context session.
        Shows item count, token estimate, vector count, and agent access.

        Returns: JSON with session statistics.
        """
        _init()
        session = _state["session"]
        hub = _state["hub"]
        vs = _state["vector_store"]

        stats = {
            "session_name": session.name,
            "session_id": session.session_id,
            "items": len(hub),
            "estimated_tokens": hub.estimate_tokens(),
            "within_limit": hub.is_within_limit(),
        }

        if vs:
            vs_stats = vs.get_stats(session.session_id)
            stats["vectors"] = vs_stats.get("vector_count", 0)
            stats["vector_backend"] = vs_stats.get("backend", "none")

        dedup_engine = _state["dedup"]
        stats["unique_hashes"] = dedup_engine.get_hash_count(session.session_id)

        return json.dumps(stats, indent=2)

    return [
        ingest_context,
        search_context,
        contribute_insight,
        get_context,
        context_stats,
    ]
