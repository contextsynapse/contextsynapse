"""AIContextDB SDK — Context-as-a-Service for AI agents."""

from .client import AIContextDB
from .session import Session, Boundary
from .agent import AIContextDBAgent
from .exceptions import AIContextDBError, AuthError, NotFoundError
from .tracked import tracked, tracked_call, TrackedClient
from .worker import AgentWorker

__all__ = [
    "AIContextDB", "Session", "Boundary", "AIContextDBAgent", "AgentWorker",
    "AIContextDBError", "AuthError", "NotFoundError",
    "connect", "tracked", "tracked_call", "TrackedClient",
    # Backward compat aliases
    "QGraph", "QGraphError", "AINatDBAgent",
]
__version__ = "0.1.0"

# Backward compatibility aliases
QGraph = AIContextDB
QGraphError = AIContextDBError
AINatDBAgent = AIContextDBAgent


def connect(
    url: str = "http://localhost:8000",
    api_key: str | None = None,
    agent_name: str | None = None,
    platform: str = "app",
    capabilities: list[str] | None = None,
) -> AIContextDB:
    """Connect to AIContextDB — the simplest entry point for any agent.

    If ``api_key`` is provided, connects with existing credentials.
    If ``agent_name`` is provided instead, registers a new agent and connects.

    Examples::

        # Existing agent
        ctx = connect(url="https://abc.ngrok-free.app", api_key="agent_id:secret")

        # New agent (auto-registers)
        ctx = connect(url="https://abc.ngrok-free.app", agent_name="my-bot")
        print(ctx._api_key)  # save this for next time

    Returns:
        A connected AIContextDB client ready to use.
    """
    if api_key:
        return AIContextDB(base_url=url, api_key=api_key)

    if agent_name:
        # Register first (no auth needed), then reconnect with the key
        tmp = AIContextDB(base_url=url)
        agent = tmp.agents.register(
            name=agent_name,
            platform=platform,
            capabilities=capabilities or ["read", "write"],
        )
        tmp.close()
        return AIContextDB(base_url=url, api_key=agent.api_key)

    return AIContextDB(base_url=url)
