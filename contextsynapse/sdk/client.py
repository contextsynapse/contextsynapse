"""AIContextDB SDK — main client."""

from __future__ import annotations

from typing import Any, BinaryIO, Dict, List, Optional

import httpx

from .exceptions import AuthError, NotFoundError, AIContextDBError
from .models import Agent, SessionInfo
from .session import Session


class _AgentsAPI:
    """Agent management operations."""

    def __init__(self, client: AIContextDB):
        self._client = client

    def register(
        self,
        name: str,
        role: str = "agent",
        platform: str = "app",
        capabilities: Optional[List[str]] = None,
    ) -> Agent:
        """Register a new agent and get an API key."""
        data = self._client._post("/context/agents", {
            "name": name,
            "role": role,
            "platform": platform,
            "capabilities": capabilities or ["read", "write"],
        })
        return Agent(
            agent_id=data.get("agent_id", ""),
            name=name,
            role=role,
            platform=data.get("platform", platform),
            api_key=data.get("api_key"),
            capabilities=capabilities or ["read", "write"],
        )

    def list(self, platform: Optional[str] = None) -> List[Agent]:
        """List all registered agents, optionally filtered by platform."""
        params = {}
        if platform:
            params["platform"] = platform
        data = self._client._get("/dashboard/agents", params=params)
        return [
            Agent(
                agent_id=a.get("agent_id", ""),
                name=a.get("name", ""),
                role=a.get("role", "agent"),
                platform=a.get("platform", "app"),
                status=a.get("status", "active"),
                capabilities=a.get("capabilities", []),
            )
            for a in data.get("agents", [])
        ]

    def delete(self, agent_id: str) -> None:
        """Deregister an agent."""
        self._client._delete(f"/context/agents/{agent_id}")


class _SessionsAPI:
    """Session management operations."""

    def __init__(self, client: AIContextDB):
        self._client = client

    def create(self, name: str, owner_agent_id: Optional[str] = None) -> Session:
        """Create a new context session."""
        payload: Dict[str, Any] = {"name": name}
        if owner_agent_id:
            payload["agent_id"] = owner_agent_id
        data = self._client._post("/dashboard/sessions", payload)
        return Session(
            client=self._client,
            session_id=data.get("session_id", ""),
            name=name,
            status="active",
        )

    def get(self, session_id: str) -> Session:
        """Get an existing session."""
        data = self._client._get(f"/context/sessions/{session_id}")
        return Session(
            client=self._client,
            session_id=data.get("session_id", session_id),
            name=data.get("name", ""),
            status=data.get("status", "active"),
            member_count=data.get("member_count", 0),
        )

    def join(self, session_id: str) -> Session:
        """Join an existing session (request access)."""
        self._client._post(f"/context/sessions/{session_id}/join", {})
        return self.get(session_id)

    def list(self) -> List[SessionInfo]:
        """List all sessions."""
        data = self._client._get("/dashboard/sessions")
        return [
            SessionInfo(
                session_id=s.get("session_id", ""),
                name=s.get("name", ""),
                status=s.get("status", "active"),
                member_count=s.get("member_count", 0),
                created_at=s.get("created_at"),
            )
            for s in data.get("sessions", [])
        ]

    def delete(self, session_id: str) -> None:
        """Delete a session."""
        self._client._delete(f"/dashboard/sessions/{session_id}")

    def grant_access(self, session_id: str, agent_id: str, level: str = "read") -> dict:
        """Grant an agent access to a session."""
        return self._client._post(f"/dashboard/sessions/{session_id}/members", {
            "agent_id": agent_id,
            "level": level,
        })


class _ContextsAPI:
    """Atomic context management."""

    def __init__(self, client: AIContextDB):
        self._client = client

    def create(self, name: str, type: str, metadata: Optional[Dict] = None) -> Dict:
        """Create an atomic context."""
        return self._client._post("/contexts", {
            "name": name, "type": type, "metadata": metadata or {},
        })

    def list(self, type: Optional[str] = None) -> List[Dict]:
        params = {"type": type} if type else {}
        data = self._client._get("/contexts", params=params)
        return data.get("contexts", [])

    def get(self, context_id: str) -> Dict:
        return self._client._get(f"/contexts/{context_id}")

    def add_node(self, context_id: str, label: str, properties: Optional[Dict] = None) -> Dict:
        return self._client._post(f"/contexts/{context_id}/nodes", {
            "label": label, "properties": properties or {},
        })

    def ingest(self, context_id: str, nodes: List[Dict]) -> Dict:
        return self._client._post(f"/contexts/{context_id}/ingest", {"nodes": nodes})


class _BoundariesAPI:
    """Context Runtime management."""

    def __init__(self, client: AIContextDB):
        self._client = client

    def create(self, name: str, goal: str = "", workspace: Optional[Dict] = None) -> "Session":
        from .session import Boundary
        data = self._client._post("/boundaries", {
            "name": name, "goal": goal, "workspace": workspace or {},
        })
        return Boundary(
            client=self._client,
            boundary_id=data.get("boundary_id", ""),
            name=name,
        )

    def get(self, boundary_id: str) -> "Session":
        from .session import Boundary
        data = self._client._get(f"/boundaries/{boundary_id}")
        return Boundary(
            client=self._client,
            boundary_id=data.get("boundary_id", boundary_id),
            name=data.get("name", ""),
            status=data.get("status", "active"),
        )

    def list(self) -> List[Dict]:
        data = self._client._get("/boundaries")
        return data.get("boundaries", [])


class AIContextDB:
    """AIContextDB Context-as-a-Service client.

    Usage:
        ctx = AIContextDB(base_url="http://localhost:8000", api_key="agent_id:secret")
        session = ctx.sessions.get("session-id")
        messages = session.export(format="messages", max_tokens=4000)
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: Optional[str] = None,
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._agent_id = api_key.split(":")[0] if api_key and ":" in api_key else None
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers=self._auth_headers(),
        )
        self.agents = _AgentsAPI(self)
        self.sessions = _SessionsAPI(self)
        self.boundaries = _BoundariesAPI(self)
        self.contexts = _ContextsAPI(self)

    def _auth_headers(self) -> dict:
        if self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        return {}

    def _handle_response(self, resp: httpx.Response) -> dict:
        if resp.status_code == 401:
            raise AuthError("Authentication failed", 401)
        if resp.status_code == 403:
            raise AuthError("Forbidden", 403)
        if resp.status_code == 404:
            raise NotFoundError("Resource not found", 404)
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise AIContextDBError(f"API error {resp.status_code}: {detail}", resp.status_code)
        try:
            return resp.json()
        except Exception:
            return {"text": resp.text}

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        return self._handle_response(self._http.get(path, params=params))

    def _post(self, path: str, json: Optional[dict] = None) -> dict:
        return self._handle_response(self._http.post(path, json=json or {}))

    def _delete(self, path: str) -> dict:
        return self._handle_response(self._http.delete(path))

    def _upload(self, path: str, file: BinaryIO, filename: str) -> dict:
        return self._handle_response(
            self._http.post(path, files={"file": (filename, file)})
        )

    def search(self, query: str, top_k: int = 10) -> List[dict]:
        """Semantic search across graph nodes."""
        data = self._post("/search", {"query": query, "top_k": top_k})
        return data.get("results", [])

    def close(self):
        """Close the HTTP client."""
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __repr__(self) -> str:
        return f"AIContextDB(base_url={self.base_url!r})"
