"""ContextSynapse SDK — Context-as-a-Service for AI agents."""

from .client import ContextSynapseClient, AIContextDB
from .session import Session
from .agent import ContextSynapseAgent, AIContextDBAgent
from .exceptions import ContextSynapseError, AIContextDBError, AuthError, NotFoundError

__all__ = [
    # New canonical names
    "ContextSynapseClient", "ContextSynapseAgent", "ContextSynapseError",
    "Session", "connect",
    # Backward compat aliases
    "AIContextDB", "AIContextDBAgent", "AIContextDBError",
    "QGraph", "QGraphError", "AINatDBAgent",
    "AuthError", "NotFoundError",
]
__version__ = "0.1.0"

# Additional backward compatibility aliases
QGraph = ContextSynapseClient
QGraphError = ContextSynapseError
AINatDBAgent = ContextSynapseAgent


def connect(
    url: str = "http://localhost:8000",
    api_key: str | None = None,
    agent_name: str | None = None,
    platform: str = "app",
    capabilities: list[str] | None = None,
) -> ContextSynapseClient:
    """Connect to ContextSynapse — the simplest entry point for any agent.

    If ``api_key`` is provided, connects with existing credentials.
    If ``agent_name`` is provided instead, registers a new agent and connects.

    Examples::

        # Existing agent
        ctx = connect(url="https://abc.ngrok-free.app", api_key="agent_id:secret")

        # New agent (auto-registers)
        ctx = connect(url="https://abc.ngrok-free.app", agent_name="my-bot")
        print(ctx._api_key)  # save this for next time

    Returns:
        A connected ContextSynapseClient ready to use.
    """
    if api_key:
        return ContextSynapseClient(base_url=url, api_key=api_key)

    if agent_name:
        # Register first (no auth needed), then reconnect with the key
        tmp = ContextSynapseClient(base_url=url)
        agent = tmp.agents.register(
            name=agent_name,
            platform=platform,
            capabilities=capabilities or ["read", "write"],
        )
        tmp.close()
        return ContextSynapseClient(base_url=url, api_key=agent.api_key)

    return ContextSynapseClient(base_url=url)
