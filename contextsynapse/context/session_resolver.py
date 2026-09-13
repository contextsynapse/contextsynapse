"""
Session Resolver
=================
Resolves a session_id + api_key into everything an agent needs:
  - graph namespace
  - thread_id
  - goal
  - access level
  - agent identity

Used by MCP server, OpenAI adapter, and any agent connector.
"""

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ResolvedSession:
    """Everything an agent needs to work on a session."""
    session_id: str
    graph_namespace: str      # internal — agent never sees this
    goal: str                 # what to work on
    access_level: str         # read | write | admin
    agent_id: str
    agent_name: str
    thread_id: str = ""       # internal — for auto-logging behind the scenes
    context_id: str = ""      # internal — which context is attached


def resolve_session(
    session_id: str,
    api_key: str,
) -> ResolvedSession:
    """Resolve session_id + api_key into a full working context.

    Args:
        session_id: Session to connect to
        api_key: Agent API key (agent_id:secret format)

    Returns:
        ResolvedSession with all resolved fields

    Raises:
        ValueError: If auth fails or session not found
    """
    from .store_factory import create_agent_registry
    from .session import ContextSessionManager

    # 1. Authenticate agent
    registry = create_agent_registry()

    if ":" not in api_key:
        raise ValueError("Invalid API key format. Expected: agent_id:secret")

    agent_id, secret = api_key.split(":", 1)

    if not registry.authenticate(agent_id, secret):
        raise ValueError("Invalid or expired API key")

    agent = registry.get(agent_id)
    if not agent:
        raise ValueError("Agent not found")

    # 2. Resolve session
    session_mgr = ContextSessionManager()
    session = session_mgr.get_session(session_id)
    if not session:
        raise ValueError(f"Session '{session_id}' not found")

    if session.status != "active":
        raise ValueError(f"Session '{session_id}' is {session.status}")

    # 3. Check access
    access_list = session_mgr.get_access_list(session_id)
    agent_access = None
    for entry in access_list:
        if isinstance(entry, dict) and entry.get("agent_id") == agent_id:
            agent_access = entry
            break

    # If no explicit access, auto-grant based on agent capabilities
    if not agent_access:
        caps = agent.capabilities or []
        if agent.role == "admin" or "admin" in caps:
            level = "admin"
        elif "write" in caps:
            level = "write"
        else:
            level = "read"
        session_mgr.grant_access(session_id, agent_id, level)
        access_level = level
    else:
        access_level = agent_access.get("access_level", "read")

    # 4. Extract config
    config = session.config or {}

    return ResolvedSession(
        session_id=session_id,
        graph_namespace=session.graph_namespace,
        thread_id=config.get("thread_id", ""),
        goal=config.get("goal", ""),
        access_level=access_level,
        agent_id=agent_id,
        agent_name=agent.name,
        context_id=config.get("context_id", ""),
    )
