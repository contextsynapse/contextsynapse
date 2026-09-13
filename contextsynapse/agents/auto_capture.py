"""
Conversation Auto-Capture — automatically ingests agent activity into the graph.

Every MCP tool call or API call captures:
- What the agent searched for (search queries)
- What the agent wrote (findings, decisions)
- Tool usage patterns

This runs passively — agents don't need to do anything special.
The capture happens as a side-effect of tool dispatch.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def capture_tool_call(
    agent_name: str,
    agent_id: str,
    tool_name: str,
    params: Dict[str, Any],
    result: str,
    namespace: str,
    db=None,
):
    """Capture a tool call as a lightweight Activity node.

    Only captures meaningful activity — skips orient(), my_tasks(), etc.
    """
    # Skip noise — only capture substantive tool calls
    SKIP_TOOLS = {"orient", "my_tasks", "list_tasks", "get_agent_context",
                  "my_usage", "report_tokens", "get_tool_policy"}

    if tool_name in SKIP_TOOLS:
        return

    # Skip if result is empty or error
    if not result or result.startswith("Error:"):
        return

    # Determine activity type
    if tool_name in ("search_nodes", "search", "rag_query", "rag_graph"):
        activity_type = "search"
        query = params.get("query", params.get("label", ""))
        summary = f"Searched: {query}" if query else f"Searched graph"
    elif tool_name in ("add_knowledge", "add_decision"):
        activity_type = "write"
        content = params.get("content", "")[:100]
        summary = f"Wrote: {content}"
    elif tool_name in ("add_task", "claim_task", "complete_task"):
        activity_type = "task"
        title = params.get("title", params.get("task_id", ""))
        summary = f"Task: {tool_name.replace('_', ' ')} — {title}"
    elif tool_name == "dispatch_goal":
        activity_type = "dispatch"
        summary = f"Dispatched goal: {params.get('goal', '')[:100]}"
    else:
        activity_type = "tool"
        summary = f"Used {tool_name}"

    # Store as lightweight provenance (not a full graph node — avoids noise)
    try:
        from ..context.propagation import get_propagator
        get_propagator().propagate(
            source_agent=agent_name or agent_id,
            event_type=f"agent_{activity_type}",
            content=summary,
            namespace=namespace,
            priority="info",
        )
    except Exception:
        pass

    logger.debug("[CAPTURE] %s: %s", agent_name, summary)
