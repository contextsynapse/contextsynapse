"""AIContextDB Session operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, List, Optional

from .models import ContextExport, QualityReport, Task

if TYPE_CHECKING:
    from .client import AIContextDB


class _TasksAPI:
    """Task operations — the agent stays in control.

    Usage::

        tasks = session.tasks.mine()
        available = session.tasks.available()
        session.tasks.claim(task_id)
        session.tasks.complete(task_id, summary="Done", files_changed=["src/app.py"])
        session.tasks.create(title="Write tests", assigned_to="codex")
    """

    def __init__(self, client: "AIContextDB", session_id: str, prefix: str = ""):
        self._client = client
        self._prefix = prefix or f"/agent/sessions/{session_id}"

    def mine(self, status: str = "all") -> List[Task]:
        """Get tasks assigned to me."""
        data = self._client._get(f"{self._prefix}/tasks/mine", params={"status": status})
        return [Task.from_dict(t) for t in data.get("tasks", [])]

    def available(self) -> List[Task]:
        """Get open unassigned tasks I can claim."""
        data = self._client._get(f"{self._prefix}/tasks/available")
        return [Task.from_dict(t) for t in data.get("tasks", [])]

    def all(self, status: str = "") -> List[Task]:
        """List all tasks in the session."""
        data = self._client._get(f"{self._prefix}/tasks", params={"status": status})
        return [Task.from_dict(t) for t in data.get("tasks", [])]

    def claim(self, task_id: str) -> dict:
        """Claim an open task — I'm working on this."""
        return self._client._post(f"{self._prefix}/tasks/{task_id}/claim")

    def complete(self, task_id: str, summary: str = "", files_changed: Optional[List[str]] = None) -> dict:
        """Report task completion — here's what I did."""
        return self._client._post(f"{self._prefix}/tasks/{task_id}/complete", {
            "summary": summary,
            "files_changed": files_changed or [],
        })

    def create(
        self, title: str, description: str = "", priority: str = "medium",
        assigned_to: str = "", tags: str = "", depends_on: str = "",
        parent_task: str = "",
    ) -> dict:
        """Create a task. Use parent_task to break a task into subtasks.

        Example::
            # Break down a task
            sub1 = session.tasks.create(title="Design schema", parent_task=task.id)
            sub2 = session.tasks.create(title="Write API", depends_on=sub1["task_id"], parent_task=task.id)
        """
        return self._client._post(f"{self._prefix}/tasks", {
            "title": title, "description": description, "priority": priority,
            "assigned_to": assigned_to, "tags": tags, "depends_on": depends_on,
            "parent_task": parent_task,
        })

    def handoff(self, task_id: str, to_agent: str, notes: str = "") -> dict:
        """Hand off a task to another agent."""
        return self._client._post(f"{self._prefix}/tasks/{task_id}/handoff", {
            "to_agent": to_agent, "notes": notes,
        })


class _GraphAPI:
    """Graph operations — read/write shared knowledge.

    Usage::

        session.graph.add_node("Finding", {"content": "Auth uses JWT"})
        session.graph.search("Decision", where={"topic": "auth"})
        session.graph.query("SELECT * FROM Task WHERE status = 'open'")
        session.graph.add_edge(source_id, target_id, "DEPENDS_ON")
    """

    def __init__(self, client: "AIContextDB", session_id: str, prefix: str = ""):
        self._client = client
        self._prefix = prefix or f"/agent/sessions/{session_id}"

    def add_node(self, label: str, properties: Optional[Dict[str, Any]] = None) -> dict:
        """Add a knowledge node to the shared graph."""
        return self._client._post(f"{self._prefix}/graph/nodes", {
            "label": label, "properties": properties or {},
        })

    def add_edge(self, source: str, target: str, label: str, properties: Optional[Dict[str, Any]] = None) -> dict:
        """Create a relationship between two nodes."""
        return self._client._post(f"{self._prefix}/graph/edges", {
            "source": source, "target": target,
            "label": label, "properties": properties or {},
        })

    def search(self, label: str = "", where: Optional[Dict[str, Any]] = None) -> list:
        """Search nodes by label and/or property filters."""
        data = self._client._post(f"{self._prefix}/graph/search", {
            "label": label, "where": where or {},
        })
        return data.get("nodes", [])

    def query(self, aiql: str) -> dict:
        """Execute an AIQL query."""
        return self._client._post(f"{self._prefix}/graph/query", {"aiql": aiql})

    def ask(self, question: str) -> str:
        """Ask about the project in plain English. No query syntax needed.

        Example::
            session.graph.ask("What are the requirements?")
            session.graph.ask("What has codex done so far?")
        """
        data = self._client._post(f"{self._prefix}/graph/ask", {"question": question})
        return data.get("summary", data.get("answer", "No results."))


class _WorkspaceAPI:
    """Workspace operations — for agents that can't use git directly.

    Usage::

        config = session.workspace.config()
        session.workspace.upload_files([{"path": "src/app.py", "content": "..."}])
        session.workspace.notify_push(branch="agent/codex/task-123")
    """

    def __init__(self, client: "AIContextDB", session_id: str, prefix: str = ""):
        self._client = client
        self._prefix = prefix or f"/agent/sessions/{session_id}"

    def config(self) -> dict:
        """Get workspace configuration (repo URL, branch pattern, etc.)."""
        data = self._client._get(f"{self._prefix}/workspace/config")
        return data.get("workspace", {})

    def upload_files(self, files: List[Dict[str, str]], commit_message: str = "Agent upload", task_id: str = "") -> dict:
        """Upload files to the workspace (for agents without local git)."""
        return self._client._post(f"{self._prefix}/workspace/artifacts", {
            "files": files, "commit_message": commit_message, "task_id": task_id,
        })

    def notify_push(self, branch: str, commit_sha: str = "", files_changed: Optional[List[str]] = None, task_id: str = "") -> dict:
        """Notify server that you pushed to git."""
        return self._client._post(f"{self._prefix}/workspace/push-event", {
            "branch": branch, "commit_sha": commit_sha,
            "files_changed": files_changed or [], "task_id": task_id,
        })


class _ContextsAPI:
    """Manage attached contexts on a boundary.

    Usage::

        boundary.contexts.list()
        boundary.contexts.attach(context_id, role="input")
        boundary.contexts.detach(context_id)
    """

    def __init__(self, client: "AIContextDB", boundary_id: str):
        self._client = client
        self._prefix = f"/boundaries/{boundary_id}"

    def list(self) -> list:
        """List attached contexts."""
        data = self._client._get(f"{self._prefix}/contexts")
        return data.get("contexts", [])

    def attach(self, context_id: str, role: str = "input") -> dict:
        """Attach an atomic context to this boundary."""
        return self._client._post(f"{self._prefix}/contexts", {
            "context_id": context_id, "role": role,
        })

    def detach(self, context_id: str) -> dict:
        """Detach a context from this boundary."""
        return self._client._delete(f"{self._prefix}/contexts/{context_id}")


class Session:
    """Represents a Context Runtime — where agents work.

    Also known as RuntimeContextBoundary. "Session" is kept as an alias.

    Sub-APIs:
        session.tasks     — poll, claim, complete, create tasks
        session.graph     — read/write shared knowledge graph
        session.workspace — get workspace config, upload files
        session.contexts  — attach/detach atomic contexts (boundary mode)
    """

    def __init__(self, client: AIContextDB, session_id: str, name: str = "",
                 api_prefix: str = "", **kwargs):
        self._client = client
        self.session_id = session_id
        self.boundary_id = session_id  # alias
        self.name = name
        self.status = kwargs.get("status", "active")
        self.member_count = kwargs.get("member_count", 0)

        # Determine API prefix: /boundaries/{id} or /agent/sessions/{id}
        self._api_prefix = api_prefix or f"/agent/sessions/{session_id}"

        # Sub-APIs — agent calls these when it needs something
        self.tasks = _TasksAPI(client, session_id, prefix=self._api_prefix)
        self.graph = _GraphAPI(client, session_id, prefix=self._api_prefix)
        self.workspace = _WorkspaceAPI(client, session_id, prefix=self._api_prefix)
        self.contexts = _ContextsAPI(client, session_id)

        # Pre-load orientation data for quick access
        self._orient_cache: Optional[str] = None

    @property
    def orient(self) -> str:
        """Get orientation briefing — graph summary, topics, tasks, suggestions.

        Cached after first call. Use refresh_orient() to update.
        """
        if self._orient_cache is None:
            self.refresh_orient()
        return self._orient_cache or "(Orient data unavailable)"

    def refresh_orient(self):
        """Refresh the cached orientation briefing."""
        try:
            data = self._client._get(f"{self._api_prefix}/orient")
            self._orient_cache = data.get("orient", data.get("result", str(data)))
        except Exception:
            try:
                export = self.export(format="prompt", max_tokens=2000)
                self._orient_cache = str(export)
            except Exception:
                self._orient_cache = None

    def export(self, format: str = "messages", max_tokens: Optional[int] = None) -> Any:
        """Export session context in LLM-ready format.

        Args:
            format: 'messages' (OpenAI/Anthropic), 'prompt' (single string), or 'markdown'
            max_tokens: Optional token budget

        Returns:
            Formatted context (list of messages, string, etc.)
        """
        params = {"format": format}
        if max_tokens:
            params["max_tokens"] = max_tokens
        data = self._client._get(f"/context/sessions/{self.session_id}/export", params=params)
        return data.get("preview", data)

    def contribute(
        self,
        content: str,
        content_type: str = "text",
        role: str = "background",
        label: Optional[str] = None,
    ) -> dict:
        """Contribute context to this session.

        Args:
            content: The context content
            content_type: 'text', 'json', 'code', etc.
            role: Context role (background, retrieved, instruction, etc.)
            label: Optional label
        """
        payload: Dict[str, Any] = {
            "content": content,
            "content_type": content_type,
            "role": role,
        }
        if label:
            payload["label"] = label
        return self._client._post(f"/context/sessions/{self.session_id}/context", payload)

    def upload(self, filepath: str) -> dict:
        """Upload a file into this session's context.

        Args:
            filepath: Path to the file to upload
        """
        import os
        filename = os.path.basename(filepath)
        with open(filepath, "rb") as f:
            return self._client._upload(
                f"/context/sessions/{self.session_id}/documents/upload",
                f,
                filename,
            )

    def get_members(self) -> List[dict]:
        """List agents with access to this session."""
        data = self._client._get(f"/context/sessions/{self.session_id}/members")
        return data.get("members", [])

    def get_quality(self) -> QualityReport:
        """Get context quality report."""
        data = self._client._get(f"/dashboard/sessions/{self.session_id}/quality")
        return QualityReport(
            session_id=self.session_id,
            overall_score=data.get("overall_score", 0),
            grade=data.get("grade", "?"),
            total_items=data.get("total_items", 0),
            stale_items=data.get("stale_items", 0),
            fresh_items=data.get("fresh_items", 0),
        )

    def get_analytics(self) -> dict:
        """Get session usage analytics."""
        return self._client._get(f"/dashboard/sessions/{self.session_id}/analytics")

    async def subscribe(self) -> AsyncIterator:
        """Subscribe to real-time session events via WebSocket.

        Usage:
            async for event in session.subscribe():
                print(f"{event.agent_id}: {event.event_type}")
        """
        from .ws import subscribe_session
        async for event in subscribe_session(self._client, self.session_id):
            yield event

    def get_context(self, system_prompt: str = "", max_tokens: int = 6000) -> str:
        """Get full project context — requirements, tasks, decisions, code produced.

        Returns LLM-ready context string. Call this before making LLM calls.
        """
        # Try boundary briefing first, fall back to session context
        try:
            data = self._client._get(
                f"/boundaries/{self.session_id}/briefing",
                params={"max_tokens": max_tokens},
            )
            return data.get("briefing", "")
        except Exception:
            data = self._client._get(
                f"{self._api_prefix}/context",
                params={"system_prompt": system_prompt, "max_tokens": max_tokens},
            )
            return data.get("context", "")

    # ── Convenience methods (no queries needed) ──────────────────

    def briefing(self, max_tokens: int = 6000) -> str:
        """Get your full project briefing. Same as get_context but clearer name."""
        return self.get_context(max_tokens=max_tokens)

    def requirements(self) -> list:
        """All project requirements."""
        return self.graph.search("Requirement")

    def decisions(self) -> list:
        """All recorded decisions."""
        return self.graph.search("Decision")

    def what_happened(self) -> list:
        """Recent actions by all agents."""
        return self.graph.search("Action")

    def codebase(self) -> list:
        """Code files produced so far."""
        return self.graph.search("CodeFile")

    def context_for_task(self, task_id: str) -> dict:
        """Everything about a specific task: requirements, decisions, code, dependencies."""
        return self._client._get(
            f"/agent/sessions/{self.session_id}/tasks/{task_id}/context"
        )

    def heartbeat(self, status: str = "idle", current_task_id: str = "", message: str = "") -> dict:
        """Send a heartbeat — I'm alive, here's what I'm doing."""
        return self._client._post(f"/agent/sessions/{self.session_id}/heartbeat", {
            "status": status, "current_task_id": current_task_id, "message": message,
        })

    def __repr__(self) -> str:
        return f"Session(id={self.session_id!r}, name={self.name!r})"


def Boundary(client, boundary_id: str, name: str = "", **kwargs):
    """Create a Session pointing at /boundaries/* endpoints.

    Usage::

        boundary = Boundary(client, "abc123")
        boundary.tasks.mine()
        boundary.contexts.list()
        boundary.briefing()
    """
    return Session(
        client=client,
        session_id=boundary_id,
        name=name,
        api_prefix=f"/boundaries/{boundary_id}",
        **kwargs,
    )
