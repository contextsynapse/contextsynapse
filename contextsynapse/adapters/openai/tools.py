"""
AIContextDB OpenAI / Codex Adapter
===================================
Exposes AIContextDB operations as OpenAI function-calling tools,
compatible with the Chat Completions API and Responses API.

Thin wrapper over the Universal Tool Layer (contextsynapse.tools).
All tool logic lives in contextcore/tools/ — this file only handles
connection setup and format conversion.

Usage:
    from contextsynapse.adapters.openai import create_openai_tools, dispatch_tool_call

    tools = create_openai_tools()          # OpenAI tool schemas
    # Pass `tools` to openai.chat.completions.create(tools=tools, ...)

    # When the model returns a tool_call, dispatch it:
    result = dispatch_tool_call(tool_call.function.name, tool_call.function.arguments)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .._base import AIContextDBConnection

logger = logging.getLogger(__name__)

# ── Module-level state (lazy) ──────────────────────────────────────
_conn: Optional[AIContextDBConnection] = None
_agent_name: str = "codex"
_agent_id: Optional[str] = None
_api_key: Optional[str] = None
_registry = None  # AgentRegistry instance
_tool_context = None  # ToolContext for dispatch


def _get_conn() -> AIContextDBConnection:
    global _conn
    if _conn is None:
        _conn = AIContextDBConnection(namespace="default")
    return _conn


def _get_registry():
    global _registry
    if _registry is None:
        from contextsynapse.context.store_factory import create_agent_registry
        _registry = create_agent_registry()
    return _registry


def _ensure_registered(platform: str = "app") -> Tuple[str, str]:
    global _agent_id, _api_key
    if _agent_id and _api_key:
        return _agent_id, _api_key
    try:
        registry = _get_registry()
        agent, api_key = registry.register(
            name=_agent_name, role="agent", platform=platform,
            capabilities=["read", "write"],
            metadata={"adapter": "openai", "registered_at": datetime.now(timezone.utc).isoformat()},
        )
        _agent_id = agent.agent_id
        _api_key = api_key
    except Exception as e:
        logger.warning("Agent registration failed: %s", e)
        _agent_id = _agent_name
        _api_key = ""
    return _agent_id, _api_key


def _get_tool_context():
    """Build or return the current ToolContext."""
    global _tool_context
    from contextsynapse.tools import ToolContext
    _tool_context = ToolContext(
        conn=_get_conn(),
        agent_id=_agent_id or "unknown",
        agent_name=_agent_name,
    )
    # Attach project if available
    try:
        from contextsynapse.project.team_tools import get_project
        _tool_context.project = get_project()
    except Exception:
        pass
    return _tool_context


def configure(
    namespace: str = "default",
    agent_name: str = "codex",
    platform: str = "app",
    connection: Optional[AIContextDBConnection] = None,
    daemon_url: Optional[str] = None,
    auto_register: bool = True,
    api_key: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, str]:
    """Configure the adapter before use.

    Preferred: pass api_key + session_id for secure session-based connection.
    Legacy: pass namespace for direct graph access.
    """
    global _conn, _agent_name, _agent_id, _api_key

    # Session-based connection (preferred)
    if session_id and api_key:
        from contextsynapse.context.session_resolver import resolve_session
        resolved = resolve_session(session_id, api_key)
        _agent_name = resolved.agent_name
        _agent_id = resolved.agent_id
        _api_key = api_key.split(":", 1)[1] if ":" in api_key else ""
        _conn = AIContextDBConnection(namespace=resolved.graph_namespace)

        # Init team tools with thread
        try:
            from contextsynapse.project.team_tools import init_team
            from contextsynapse.project.graph import ProjectGraph
            pg = ProjectGraph(resolved.graph_namespace, connection=_conn)
            init_team(resolved.graph_namespace, agent_id=_agent_id,
                      agent_name=_agent_name, project=pg, thread_id=resolved.thread_id)
        except Exception:
            pass

        return {
            "agent_id": resolved.agent_id,
            "agent_name": resolved.agent_name,
            "session_id": session_id,
            "goal": resolved.goal,
            "access_level": resolved.access_level,
        }

    # Legacy: direct namespace
    _agent_name = agent_name
    if connection is not None:
        _conn = connection
    else:
        _conn = AIContextDBConnection(namespace=namespace, daemon_url=daemon_url)

    result = {}
    if auto_register:
        agent_id, api_key_val = _ensure_registered(platform=platform)
        result = {"agent_id": agent_id, "api_key": api_key_val}

    return result


# =====================================================================
# Public API — delegates to Universal Tool Layer
# =====================================================================

def create_openai_tools(include_team: bool = True) -> List[Dict[str, Any]]:
    """Return OpenAI-compatible tool schemas for AIContextDB.

    Pass the returned list directly to openai.chat.completions.create(tools=...).
    """
    from contextsynapse.tools import ToolRegistry

    categories = ["graph", "context", "gateway"]
    if include_team:
        categories.extend(["task", "workspace"])

    return ToolRegistry.to_openai_schemas(categories)


def init_project(project_name: str) -> str:
    """Initialize a project graph for team collaboration."""
    _ensure_registered()
    from contextsynapse.tools import ToolRegistry
    ctx = _get_tool_context()
    return ToolRegistry.dispatch("init_project", ctx, {"project_name": project_name})


def dispatch_tool_call(function_name: str, arguments: str) -> str:
    """Execute a tool call returned by the OpenAI API.

    Args:
        function_name: The function name from tool_call.function.name
        arguments: The JSON arguments from tool_call.function.arguments

    Returns:
        String result to send back as tool response.
    """
    from contextsynapse.tools import ToolRegistry

    ctx = _get_tool_context()

    try:
        args = json.loads(arguments) if arguments else {}
    except json.JSONDecodeError:
        return f"Error: Invalid JSON arguments: {arguments}"

    # Observe tool call for Context Relevance Gravity
    try:
        from contextsynapse.context.gravity import get_gravity
        get_gravity().observe(ctx.agent_id or "unknown", function_name, args)
    except Exception:
        pass

    return ToolRegistry.dispatch(function_name, ctx, args)
