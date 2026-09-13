"""
A2A Tools — Agent-facing tools for calling external A2A agents.

4 tools: a2a_discover, a2a_send_task, a2a_get_task, a2a_cancel_task
"""

from __future__ import annotations

import json
import logging

from .registry import ToolContext, ToolParam, tool

logger = logging.getLogger(__name__)


@tool("a2a_discover", "a2a",
      "Discover a remote A2A agent's capabilities by fetching its Agent Card.",
      params=[ToolParam("url", "string", "Base URL of the remote A2A agent (e.g. http://agent-b:8000)")])
def _a2a_discover(ctx: ToolContext, url: str) -> str:
    from ..a2a.client import A2AClient
    try:
        client = A2AClient(url)
        card = client.discover()
        name = card.get("name", "Unknown")
        skills = card.get("skills", [])
        skill_names = [s.get("name", s.get("id", "?")) for s in skills[:10]]
        caps = card.get("capabilities", {})
        return (
            f"Agent: {name}\n"
            f"URL: {url}\n"
            f"Skills ({len(skills)}): {', '.join(skill_names)}\n"
            f"Streaming: {caps.get('streaming', False)}\n"
            f"Push Notifications: {caps.get('pushNotifications', False)}"
        )
    except Exception as e:
        return f"Failed to discover agent at {url}: {e}"


@tool("a2a_send_task", "a2a",
      "Send a task to a remote A2A agent. Returns the task ID and status.",
      params=[
          ToolParam("url", "string", "Base URL of the remote A2A agent"),
          ToolParam("message", "string", "Task description or instruction for the remote agent"),
          ToolParam("session_id", "string", "Optional session ID to associate with", required=False, default=""),
      ])
def _a2a_send_task(ctx: ToolContext, url: str, message: str, session_id: str = "") -> str:
    from ..a2a.client import A2AClient
    from ..a2a.models import Message
    try:
        client = A2AClient(url)
        msg = Message.text(message, role="user")
        task = client.send_task(msg, session_id=session_id)
        artifacts_text = ""
        if task.artifacts:
            for a in task.artifacts:
                for p in a.parts:
                    if hasattr(p, "text"):
                        artifacts_text += f"\n  {a.name}: {p.text[:200]}"
        return (
            f"Task ID: {task.id}\n"
            f"State: {task.status.state.value}\n"
            f"Session: {task.session_id}"
            f"{artifacts_text}"
        )
    except Exception as e:
        return f"Failed to send task to {url}: {e}"


@tool("a2a_get_task", "a2a",
      "Check the status of a task on a remote A2A agent.",
      params=[
          ToolParam("url", "string", "Base URL of the remote A2A agent"),
          ToolParam("task_id", "string", "Task ID to check"),
      ])
def _a2a_get_task(ctx: ToolContext, url: str, task_id: str) -> str:
    from ..a2a.client import A2AClient
    try:
        client = A2AClient(url)
        task = client.get_task(task_id)
        artifacts = len(task.artifacts)
        messages = len(task.history)
        return (
            f"Task: {task.id}\n"
            f"State: {task.status.state.value}\n"
            f"Messages: {messages}\n"
            f"Artifacts: {artifacts}"
        )
    except Exception as e:
        return f"Failed to get task {task_id}: {e}"


@tool("a2a_cancel_task", "a2a",
      "Cancel a task on a remote A2A agent.",
      params=[
          ToolParam("url", "string", "Base URL of the remote A2A agent"),
          ToolParam("task_id", "string", "Task ID to cancel"),
      ])
def _a2a_cancel_task(ctx: ToolContext, url: str, task_id: str) -> str:
    from ..a2a.client import A2AClient
    try:
        client = A2AClient(url)
        task = client.cancel_task(task_id)
        return f"Task {task.id} canceled (state: {task.status.state.value})"
    except Exception as e:
        return f"Failed to cancel task {task_id}: {e}"
