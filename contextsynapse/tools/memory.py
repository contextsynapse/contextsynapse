"""
Agent Memory Tools
===================
MCP tools for persistent agent memory — remember and recall across sessions.

Agents use these to build long-term memory that persists beyond a single
experiment run. Memory is stored as graph nodes linked to the agent,
queryable with AIQL, and weighted by Gravity scoring.
"""

from __future__ import annotations

import logging
from .registry import tool, ToolParam, ToolContext

logger = logging.getLogger(__name__)


def _get_memory(ctx: ToolContext):
    """Get or create AgentMemory instance for the current graph."""
    if not hasattr(ctx, "_agent_memory") or ctx._agent_memory is None:
        try:
            from ..context.agent_memory import AgentMemory
            registry = None
            if hasattr(ctx.conn, "graph_registry"):
                registry = ctx.conn.graph_registry
            elif hasattr(ctx.conn, "_graph_registry"):
                registry = ctx.conn._graph_registry
            if registry:
                ns = getattr(ctx.conn, "namespace", "agent_memory")
                ctx._agent_memory = AgentMemory(registry, namespace=ns)
        except Exception as e:
            logger.warning("AgentMemory init failed: %s", e)
            return None
    return getattr(ctx, "_agent_memory", None)


@tool(
    "remember", "context",
    "Save a memory that persists across sessions. Use for decisions, preferences, "
    "patterns, or facts you want to recall later.",
    params=[
        ToolParam("content", "string", "What to remember"),
        ToolParam("memory_type", "string",
                  "Type: 'decision', 'preference', 'pattern', or 'fact'",
                  required=False, default="fact"),
    ],
)
def _remember(ctx: ToolContext, content: str, memory_type: str = "fact") -> str:
    mem = _get_memory(ctx)
    if not mem:
        return "Error: Memory system not available"

    agent_id = ctx.agent_id or "unknown"
    try:
        mem_id = mem.save_memory(agent_id, content, memory_type=memory_type)
        return f"Remembered [{memory_type}]: {content[:80]} (id: {mem_id})"
    except Exception as e:
        return f"Error saving memory: {e}"


@tool(
    "recall", "context",
    "Recall memories related to a query. Returns past decisions, preferences, "
    "patterns, and facts from previous sessions.",
    params=[
        ToolParam("query", "string", "What to search memories for"),
        ToolParam("k", "integer", "Number of memories to return",
                  required=False, default=5),
        ToolParam("memory_type", "string",
                  "Filter by type: 'decision', 'preference', 'pattern', 'fact', or '' for all",
                  required=False, default=""),
    ],
)
def _recall(ctx: ToolContext, query: str, k: int = 5, memory_type: str = "") -> str:
    mem = _get_memory(ctx)
    if not mem:
        return "Error: Memory system not available"

    agent_id = ctx.agent_id or "unknown"
    k = min(int(k) if str(k).isdigit() else 5, 20)
    mtype = memory_type if memory_type in ("decision", "preference", "pattern", "fact") else None

    try:
        memories = mem.recall_memories(agent_id, query=query, k=k, memory_type=mtype)
        if not memories:
            return "No memories found."
        lines = [f"Found {len(memories)} memories:"]
        for m in memories:
            mtype_display = m.get("memory_type", m.get("tags", [""])[0] if m.get("tags") else "")
            lines.append(f"  [{mtype_display}] {m.get('content', '')[:120]}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error recalling memories: {e}"


@tool(
    "update_memory", "context",
    "Update a memory with new content. Creates a new version and supersedes the old one. "
    "Use when a fact, preference, or decision has changed. Optional — the system works fine without this.",
    params=[
        ToolParam("memory_id", "string", "ID of the memory to update"),
        ToolParam("new_content", "string", "Updated content"),
        ToolParam("reason", "string", "Why: 'user_correction', 'new_evidence', 'policy_change'",
                  required=False, default="user_correction"),
    ],
)
def _update_memory(ctx: ToolContext, memory_id: str = "", new_content: str = "", reason: str = "user_correction") -> str:
    if not memory_id or not new_content:
        return "Error: memory_id and new_content are required"
    mem = _get_memory(ctx)
    if not mem:
        return "Error: Memory system not available"
    try:
        new_id = mem.update_memory(ctx.agent_id or "unknown", memory_id, new_content, reason)
        return f"Memory updated. Old: {memory_id} (superseded). New: {new_id}"
    except Exception as e:
        return f"Error updating memory: {e}"
