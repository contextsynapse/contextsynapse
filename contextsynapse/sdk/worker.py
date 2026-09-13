"""
Agent Worker — distributed task execution for AIContextDB.

An AgentWorker runs on the agent's machine, connects to a central
AIContextDB server, polls for tasks, executes them locally with
a git workspace, and pushes results back.

Usage::

    from contextsynapse.sdk import AgentWorker

    worker = AgentWorker(
        name="codex-dev",
        server="http://localhost:8000",
        session_id="abc123",
        workspace_dir="~/workspaces",
    )

    @worker.on_task
    def handle(task, workspace, graph):
        workspace.write_file("src/app.py", "print('hello')")
        workspace.commit(f"Implement: {task.title}")
        workspace.push()
        graph.add_node("CodeFile", {"path": "src/app.py"})
        return {"summary": "Created app.py", "files_changed": ["src/app.py"]}

    worker.run()  # blocking poll loop
"""

from __future__ import annotations

import logging
import signal
import time
from typing import Any, Callable, Dict, List, Optional

from .client import AIContextDB
from .models import Task
from .workspace_client import GraphClient

logger = logging.getLogger(__name__)


class AgentWorker:
    """Remote agent worker that polls AIContextDB for tasks and executes them."""

    def __init__(
        self,
        name: str,
        server: str = "http://localhost:8000",
        api_key: Optional[str] = None,
        session_id: Optional[str] = None,
        platform: str = "desktop",
        capabilities: Optional[List[str]] = None,
        workspace_dir: str = "workspaces",
        poll_interval: int = 10,
        auto_claim: bool = True,
        max_tasks: int = 1,
    ):
        """
        Args:
            name: Agent name (used for registration and branch naming).
            server: AIContextDB server URL.
            api_key: Existing agent_id:secret. If None, auto-registers.
            session_id: Session to work on. Required.
            platform: Agent platform (desktop, app, etc.).
            capabilities: Agent capabilities.
            workspace_dir: Base directory for git workspaces.
            poll_interval: Seconds between polls.
            auto_claim: Auto-claim available tasks if none assigned.
            max_tasks: Max tasks to process per poll cycle.
        """
        self.name = name
        self.server = server
        self.session_id = session_id
        self.workspace_dir = workspace_dir
        self.poll_interval = poll_interval
        self.auto_claim = auto_claim
        self.max_tasks = max_tasks
        self._handler: Optional[Callable] = None
        self._running = False
        self._current_task: Optional[str] = None
        self._status = "idle"
        self._workspace = None

        # Connect to server
        if api_key:
            self._client = AIContextDB(base_url=server, api_key=api_key)
        else:
            # Auto-register
            tmp = AIContextDB(base_url=server)
            agent = tmp.agents.register(
                name=name, platform=platform,
                capabilities=capabilities or ["read", "write"],
            )
            tmp.close()
            self._api_key = agent.api_key
            self._client = AIContextDB(base_url=server, api_key=agent.api_key)
            logger.info("Registered as %s (key: %s...)", name, agent.api_key[:12])

        # Graph client proxy
        self.graph = GraphClient(self._client, session_id) if session_id else None

    def on_task(self, handler: Callable) -> Callable:
        """Decorator to register a task handler.

        The handler receives (task, workspace, graph) and should return
        a dict with 'summary' and optionally 'files_changed'.
        """
        self._handler = handler
        return handler

    def _setup_workspace(self):
        """Initialize workspace from session config."""
        if self._workspace:
            return

        try:
            config = self._client._get(f"/agent/sessions/{self.session_id}/workspace/config")
            ws_config = config.get("workspace", {})
            session_name = config.get("session_name", "project")
        except Exception as e:
            logger.warning("Could not get workspace config: %s — using local", e)
            ws_config = {"type": "local"}
            session_name = "project"

        import os
        ws_type = ws_config.get("type", "local")
        project_dir = os.path.join(
            os.path.expanduser(self.workspace_dir),
            session_name.replace(" ", "-").lower(),
        )
        ws_config["path"] = project_dir

        from ..workspace.base import Workspace
        self._workspace = Workspace.from_config(ws_config)
        logger.info("Workspace ready: %s (%s)", project_dir, ws_type)

    def _poll_cycle(self):
        """One iteration of the poll loop."""
        prefix = f"/agent/sessions/{self.session_id}"

        # 1. Heartbeat
        try:
            self._client._post(f"{prefix}/heartbeat", {
                "status": self._status,
                "current_task_id": self._current_task or "",
            })
        except Exception as e:
            logger.debug("Heartbeat failed: %s", e)

        # 2. Check for assigned tasks
        try:
            mine = self._client._get(f"{prefix}/tasks/mine", params={"status": "all"})
            tasks = mine.get("tasks", [])
        except Exception as e:
            logger.warning("Failed to poll tasks: %s", e)
            return

        # 3. If nothing assigned, try to claim
        if not tasks and self.auto_claim:
            try:
                available = self._client._get(f"{prefix}/tasks/available")
                avail_tasks = available.get("tasks", [])
                for t in avail_tasks[:self.max_tasks]:
                    try:
                        self._client._post(f"{prefix}/tasks/{t['id']}/claim")
                        tasks.append(t)
                        logger.info("Claimed task: %s", t.get("title", t["id"]))
                    except Exception:
                        pass
            except Exception as e:
                logger.debug("Failed to poll available tasks: %s", e)

        # 4. Execute tasks
        for task_data in tasks[:self.max_tasks]:
            task = Task.from_dict(task_data)
            self._execute_task(task)

    def _execute_task(self, task: Task):
        """Execute a single task."""
        if not self._handler:
            logger.warning("No task handler registered — skipping %s", task.title)
            return

        self._current_task = task.id
        self._status = "working"
        logger.info("Executing: [%s] %s", task.priority.upper(), task.title)

        # Setup workspace + branch
        self._setup_workspace()
        branch = f"agent/{self.name}/{task.id[:8]}"
        try:
            self._workspace.create_branch(branch)
        except Exception:
            pass

        try:
            result = self._handler(task, self._workspace, self.graph) or {}
            summary = result.get("summary", f"Completed: {task.title}")
            files_changed = result.get("files_changed", [])

            # Commit + push
            try:
                self._workspace.commit(f"{self.name}: {task.title}")
                self._workspace.push(branch)
                self.graph.notify_push(
                    branch=branch, task_id=task.id, files_changed=files_changed,
                )
            except Exception as e:
                logger.debug("Git push failed: %s", e)

            # Report completion
            prefix = f"/agent/sessions/{self.session_id}"
            self._client._post(f"{prefix}/tasks/{task.id}/complete", {
                "summary": summary,
                "files_changed": files_changed,
            })
            logger.info("Completed: %s", task.title)

        except Exception as e:
            logger.error("Task %s failed: %s", task.title, e)
            try:
                self._client._post(f"/agent/sessions/{self.session_id}/heartbeat", {
                    "status": "error", "message": str(e), "current_task_id": task.id,
                })
            except Exception:
                pass

        self._current_task = None
        self._status = "idle"

    def run(self):
        """Start the blocking poll loop. Press Ctrl+C to stop."""
        if not self.session_id:
            raise ValueError("session_id is required to run the worker")
        if not self._handler:
            raise ValueError("No task handler registered. Use @worker.on_task")

        self._running = True
        self._setup_workspace()

        def _stop(sig, frame):
            logger.info("Shutting down worker...")
            self._running = False

        signal.signal(signal.SIGINT, _stop)

        logger.info("Worker '%s' started — polling every %ds", self.name, self.poll_interval)
        logger.info("Session: %s | Server: %s", self.session_id, self.server)

        while self._running:
            try:
                self._poll_cycle()
            except Exception as e:
                logger.error("Poll cycle error: %s", e)
            time.sleep(self.poll_interval)

        logger.info("Worker stopped.")

    def run_once(self):
        """Execute a single poll cycle (useful for cron/CI)."""
        if not self.session_id:
            raise ValueError("session_id is required")
        self._setup_workspace()
        self._poll_cycle()
