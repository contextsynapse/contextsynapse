"""
Graph and workspace client proxies for remote agent workers.

These wrap HTTP calls to the central AIContextDB server's /agent/* endpoints.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class GraphClient:
    """Proxy for graph operations via the remote agent API.

    Usage::

        graph = GraphClient(http_client, session_id="abc123")
        graph.add_node("Finding", {"content": "Auth module needs refactor"})
        result = graph.query("SELECT * FROM Task")
        nodes = graph.search("Task", where={"status": "open"})
    """

    def __init__(self, client, session_id: str):
        """
        Args:
            client: An AIContextDB SDK client (has _post, _get methods).
            session_id: Session to operate on.
        """
        self._client = client
        self._sid = session_id
        self._prefix = f"/agent/sessions/{session_id}"

    def query(self, aiql: str) -> dict:
        """Execute an AIQL query against the session's graph."""
        return self._client._post(f"{self._prefix}/graph/query", {"aiql": aiql})

    def add_node(self, label: str, properties: Optional[Dict[str, Any]] = None) -> dict:
        """Add a knowledge node to the session's graph."""
        return self._client._post(f"{self._prefix}/graph/nodes", {
            "label": label,
            "properties": properties or {},
        })

    def add_edge(self, source: str, target: str, label: str, properties: Optional[Dict[str, Any]] = None) -> dict:
        """Create an edge between two nodes."""
        return self._client._post(f"{self._prefix}/graph/edges", {
            "source": source, "target": target,
            "label": label, "properties": properties or {},
        })

    def search(self, label: str = "", where: Optional[Dict[str, Any]] = None) -> list:
        """Search nodes by label and/or property filters."""
        result = self._client._post(f"{self._prefix}/graph/search", {
            "label": label, "where": where or {},
        })
        return result.get("nodes", [])

    def create_task(
        self, title: str, description: str = "", priority: str = "medium",
        assigned_to: str = "", tags: str = "", depends_on: str = "",
        parent_task: str = "",
    ) -> dict:
        """Create a new task. Use parent_task to create subtasks."""
        return self._client._post(f"{self._prefix}/tasks", {
            "title": title, "description": description, "priority": priority,
            "assigned_to": assigned_to, "tags": tags, "depends_on": depends_on,
            "parent_task": parent_task,
        })

    def list_tasks(self, status: str = "") -> list:
        """List tasks in the session."""
        result = self._client._get(f"{self._prefix}/tasks", params={"status": status})
        return result.get("tasks", [])

    def upload_artifacts(
        self,
        files: List[Dict[str, str]],
        commit_message: str = "Agent upload",
        task_id: str = "",
    ) -> dict:
        """Upload files to the workspace via API (for agents without git)."""
        return self._client._post(f"{self._prefix}/workspace/artifacts", {
            "files": files,
            "commit_message": commit_message,
            "task_id": task_id,
        })

    def notify_push(self, branch: str, commit_sha: str = "", files_changed: List[str] = None, task_id: str = "") -> dict:
        """Notify server that we pushed to git."""
        return self._client._post(f"{self._prefix}/workspace/push-event", {
            "branch": branch,
            "commit_sha": commit_sha,
            "files_changed": files_changed or [],
            "task_id": task_id,
        })
