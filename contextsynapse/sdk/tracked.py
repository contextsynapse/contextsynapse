"""
Tracked LLM Client — Auto-captures agent I/O into the context graph.

Wraps OpenAI, Anthropic, or Groq clients to automatically:
1. Record Turn nodes (user → assistant) under UserStore
2. Extract signals (Decision, Action, Question, Topic)
3. Link entity references
4. Store generated artifacts under ArtifactStore

Usage::

    from contextsynapse.sdk import tracked

    # Wrap any OpenAI-compatible client
    client = tracked(
        openai.OpenAI(),
        session_id="session_abc",
        agent_id="claude-1",
        context_url="http://localhost:8000",
    )

    # Use exactly like the original client — output auto-captured
    response = client.chat.completions.create(
        model="gpt-4", messages=[{"role": "user", "content": "hello"}]
    )

    # Or wrap just one call
    from contextsynapse.sdk.tracked import tracked_call
    response = tracked_call(
        client, messages, session_id="abc", agent_id="claude-1"
    )
"""

import logging
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def tracked_call(
    client,
    messages: List[Dict[str, str]],
    session_id: str,
    agent_id: str,
    context_url: str = "http://localhost:8000",
    api_key: Optional[str] = None,
    model: str = "gpt-4",
    **kwargs,
) -> Any:
    """Make a tracked LLM call — captures input/output into the context graph.

    Args:
        client: OpenAI-compatible client (openai.OpenAI(), groq client, etc.)
        messages: Chat messages
        session_id: Session/context namespace
        agent_id: Agent identifier
        context_url: AIContextDB server URL
        api_key: AIContextDB API key (optional)
        model: Model name
        **kwargs: Extra args passed to client.chat.completions.create()

    Returns:
        The LLM response (unchanged)
    """
    # 1. Make the actual LLM call
    response = client.chat.completions.create(model=model, messages=messages, **kwargs)

    # 2. Capture async (fire-and-forget, doesn't block return)
    user_content = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            user_content = msg.get("content", "")
            break

    assistant_content = ""
    if hasattr(response, "choices") and response.choices:
        assistant_content = response.choices[0].message.content or ""

    thread = threading.Thread(
        target=_capture_interaction,
        args=(context_url, api_key, session_id, agent_id, user_content, assistant_content, model),
        daemon=True,
    )
    thread.start()

    return response


def _capture_interaction(
    context_url: str,
    api_key: Optional[str],
    session_id: str,
    agent_id: str,
    user_content: str,
    assistant_content: str,
    model: str,
):
    """Background: record Turn nodes + extract signals into the graph."""
    try:
        import requests

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # Record user turn
        if user_content:
            requests.post(
                f"{context_url}/context/sessions/{session_id}/interactions",
                headers=headers,
                json={
                    "role": "user",
                    "content": user_content,
                    "agent_id": agent_id,
                    "metadata": {"source": "tracked_sdk"},
                },
                timeout=5,
            )

        # Record assistant turn
        if assistant_content:
            requests.post(
                f"{context_url}/context/sessions/{session_id}/interactions",
                headers=headers,
                json={
                    "role": "assistant",
                    "content": assistant_content,
                    "agent_id": agent_id,
                    "metadata": {"model": model, "source": "tracked_sdk"},
                },
                timeout=5,
            )

    except Exception as e:
        logger.debug("Failed to capture interaction: %s", e)


class TrackedClient:
    """Wrapper around an OpenAI-compatible client that auto-captures interactions.

    Usage::

        client = TrackedClient(openai.OpenAI(), session_id="abc", agent_id="bot-1")
        response = client.chat.completions.create(model="gpt-4", messages=[...])
    """

    def __init__(
        self,
        client,
        session_id: str,
        agent_id: str,
        context_url: str = "http://localhost:8000",
        api_key: Optional[str] = None,
    ):
        self._client = client
        self._session_id = session_id
        self._agent_id = agent_id
        self._context_url = context_url
        self._api_key = api_key
        # Proxy chat.completions
        self.chat = _ChatProxy(self)

    def __getattr__(self, name):
        """Proxy all other attributes to the underlying client."""
        return getattr(self._client, name)


class _ChatProxy:
    def __init__(self, tracked: TrackedClient):
        self._tracked = tracked
        self.completions = _CompletionsProxy(tracked)


class _CompletionsProxy:
    def __init__(self, tracked: TrackedClient):
        self._tracked = tracked

    def create(self, messages: list, model: str = "gpt-4", **kwargs):
        return tracked_call(
            self._tracked._client,
            messages=messages,
            session_id=self._tracked._session_id,
            agent_id=self._tracked._agent_id,
            context_url=self._tracked._context_url,
            api_key=self._tracked._api_key,
            model=model,
            **kwargs,
        )


def tracked(
    client,
    session_id: str = "",
    agent_id: str = "",
    context_url: str = "http://localhost:8000",
    api_key: Optional[str] = None,
) -> TrackedClient:
    """Wrap an LLM client to auto-capture interactions.

    Args:
        client: OpenAI, Anthropic, or Groq client
        session_id: Context namespace / session ID
        agent_id: Agent identifier
        context_url: AIContextDB server URL
        api_key: AIContextDB API key

    Returns:
        TrackedClient that behaves like the original but captures I/O.
    """
    return TrackedClient(
        client,
        session_id=session_id,
        agent_id=agent_id,
        context_url=context_url,
        api_key=api_key,
    )
