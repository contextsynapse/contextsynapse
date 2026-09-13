"""ContextSynapse Agent Wrapper — zero-config connection for any AI agent.

Handles registration, credential persistence, session management,
and context contribution in a single class.

Examples::

    # First run — registers and saves credentials
    agent = ContextSynapseAgent("my-bot", server="https://abc.ngrok-free.app")

    # Add knowledge
    agent.add("User prefers dark mode and concise answers")
    agent.add("Meeting notes from standup", label="standup-2026-03-15")

    # Get context for LLM
    messages = agent.context(max_tokens=4000)
    # → [{"role": "system", ...}, {"role": "user", ...}]

    # Search
    results = agent.search("user preferences")

    # Subsequent runs — reuses saved credentials
    agent = ContextSynapseAgent("my-bot", server="https://abc.ngrok-free.app")
    # Automatically reconnects with stored api_key
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from .client import ContextSynapseClient
from .session import Session


_CREDS_DIR = Path.home() / ".contextsynapse"


class ContextSynapseAgent:
    """Quickstart wrapper for connecting any agent to ContextSynapse.

    On first use, registers with the server and saves credentials to
    ``~/.contextsynapse/<agent_name>.json``. On subsequent uses, loads saved
    credentials automatically.

    Args:
        name: Agent name (used for registration and credential lookup).
        server: ContextSynapse server URL (e.g. ngrok public URL).
        capabilities: Agent capabilities. Default: ["read", "write"].
        session: Session name to auto-join/create. Default: "shared".
        creds_dir: Directory for credential files. Default: ~/.contextsynapse/
    """

    def __init__(
        self,
        name: str,
        server: str = "http://localhost:8000",
        platform: str = "desktop",
        capabilities: list[str] | None = None,
        session: str = "shared",
        creds_dir: str | Path | None = None,
    ):
        self.name = name
        self.server = server.rstrip("/")
        self._platform = platform
        self._capabilities = capabilities or ["read", "write"]
        self._creds_dir = Path(creds_dir) if creds_dir else _CREDS_DIR
        self._creds_file = self._creds_dir / f"{name}.json"

        # Load or register
        creds = self._load_creds()
        if creds and creds.get("server") == self.server:
            self._api_key = creds["api_key"]
            self._agent_id = creds["agent_id"]
            self._platform = creds.get("platform", platform)
        else:
            self._register()

        # Connect
        self.client = ContextSynapseClient(base_url=self.server, api_key=self._api_key)

        # Auto-join or create session
        self._session_name = session
        self._session: Session | None = None

    # ── Credential management ──────────────────────────────────────

    def _load_creds(self) -> dict | None:
        if self._creds_file.exists():
            try:
                return json.loads(self._creds_file.read_text())
            except (json.JSONDecodeError, OSError):
                return None
        return None

    def _save_creds(self):
        self._creds_dir.mkdir(parents=True, exist_ok=True)
        self._creds_file.write_text(json.dumps({
            "agent_id": self._agent_id,
            "api_key": self._api_key,
            "server": self.server,
            "name": self.name,
            "platform": self._platform,
            "capabilities": self._capabilities,
        }, indent=2))

    def _register(self):
        tmp = ContextSynapseClient(base_url=self.server)
        agent = tmp.agents.register(
            name=self.name,
            platform=self._platform,
            capabilities=self._capabilities,
        )
        tmp.close()
        self._agent_id = agent.agent_id
        self._api_key = agent.api_key
        self._save_creds()

    # ── Session ────────────────────────────────────────────────────

    @property
    def session(self) -> Session:
        """Get or create the active session."""
        if self._session is None:
            # Try to find existing session
            try:
                sessions = self.client.sessions.list()
                match = next((s for s in sessions if s.name == self._session_name), None)
                if match:
                    self._session = self.client.sessions.get(match.session_id)
                else:
                    self._session = self.client.sessions.create(
                        name=self._session_name,
                        owner_agent_id=self._agent_id,
                    )
            except Exception:
                # Fallback: create new
                self._session = self.client.sessions.create(
                    name=self._session_name,
                    owner_agent_id=self._agent_id,
                )
        return self._session

    # ── High-level API ─────────────────────────────────────────────

    def add(
        self,
        content: str,
        content_type: str = "text",
        role: str = "background",
        label: str | None = None,
    ) -> dict:
        """Add context to the shared session.

        Args:
            content: Text content to add.
            content_type: 'text', 'json', 'code', 'markdown'.
            role: Context role — 'background', 'retrieved', 'instruction'.
            label: Optional label for the context item.
        """
        return self.session.contribute(
            content=content,
            content_type=content_type,
            role=role,
            label=label,
        )

    def context(
        self,
        format: str = "messages",
        max_tokens: int | None = None,
    ) -> Any:
        """Export session context in LLM-ready format.

        Args:
            format: 'messages' (OpenAI/Anthropic), 'prompt', or 'markdown'.
            max_tokens: Optional token budget.

        Returns:
            List of messages or formatted string.
        """
        return self.session.export(format=format, max_tokens=max_tokens)

    def search(self, query: str, top_k: int = 10) -> list[dict]:
        """Semantic search across the graph."""
        return self.client.search(query=query, top_k=top_k)

    def upload(self, filepath: str) -> dict:
        """Upload a file into the session context."""
        return self.session.upload(filepath)

    # ── Info ───────────────────────────────────────────────────────

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def api_key(self) -> str:
        return self._api_key

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __repr__(self) -> str:
        return f"ContextSynapseAgent(name={self.name!r}, server={self.server!r})"


# Backward compatibility alias
AIContextDBAgent = ContextSynapseAgent
