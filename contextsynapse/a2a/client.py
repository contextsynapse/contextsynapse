"""
A2A Client
===========
Outbound HTTP client for calling external A2A agents.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator, Dict, Optional

from .models import Artifact, Message, Task

logger = logging.getLogger(__name__)


class A2AClient:
    """HTTP client for interacting with an external A2A agent server."""

    def __init__(self, base_url: str, auth_token: Optional[str] = None, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.auth_token:
            h["Authorization"] = f"Bearer {self.auth_token}"
        return h

    def _rpc_payload(self, method: str, params: Dict[str, Any], req_id: str = "1") -> Dict:
        return {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}

    # ── Discovery ─────────────────────────────────────────────────────

    def discover(self) -> Dict[str, Any]:
        """Fetch the remote agent's Agent Card."""
        import requests
        resp = requests.get(
            f"{self.base_url}/.well-known/agent.json",
            headers=self._headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    # ── Task Lifecycle ────────────────────────────────────────────────

    def send_task(self, message: Message, session_id: str = "", metadata: Optional[Dict] = None) -> Task:
        """Send a task to the remote agent."""
        import requests
        params: Dict[str, Any] = {"message": message.to_dict()}
        if session_id:
            params["sessionId"] = session_id
        if metadata:
            params["metadata"] = metadata

        resp = requests.post(
            f"{self.base_url}/a2a",
            json=self._rpc_payload("tasks/send", params),
            headers=self._headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"A2A error: {data['error'].get('message', data['error'])}")
        return Task.from_dict(data.get("result", {}))

    def get_task(self, task_id: str) -> Task:
        """Get task status from the remote agent."""
        import requests
        resp = requests.post(
            f"{self.base_url}/a2a",
            json=self._rpc_payload("tasks/get", {"id": task_id}),
            headers=self._headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"A2A error: {data['error'].get('message', data['error'])}")
        return Task.from_dict(data.get("result", {}))

    def cancel_task(self, task_id: str) -> Task:
        """Cancel a task on the remote agent."""
        import requests
        resp = requests.post(
            f"{self.base_url}/a2a",
            json=self._rpc_payload("tasks/cancel", {"id": task_id}),
            headers=self._headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"A2A error: {data['error'].get('message', data['error'])}")
        return Task.from_dict(data.get("result", {}))

    def send_subscribe(self, message: Message, session_id: str = "") -> str:
        """Send a task with SSE streaming — returns the full streamed response."""
        import requests
        params: Dict[str, Any] = {"message": message.to_dict()}
        if session_id:
            params["sessionId"] = session_id

        resp = requests.post(
            f"{self.base_url}/a2a",
            json=self._rpc_payload("tasks/sendSubscribe", params),
            headers=self._headers(),
            timeout=self.timeout,
            stream=True,
        )
        resp.raise_for_status()
        chunks = []
        for line in resp.iter_lines(decode_unicode=True):
            if line and line.startswith("data:"):
                chunks.append(line[5:].strip())
        return "\n".join(chunks)
