"""
A2A Server Handlers
====================
Business logic for processing inbound A2A requests.
Separates HTTP concerns (router) from task execution logic.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    Artifact,
    DataPart,
    Message,
    Task,
    TaskState,
    TextPart,
)
from .task_manager import A2ATaskManager

logger = logging.getLogger(__name__)


def handle_task_send(
    task_manager: A2ATaskManager,
    params: Dict[str, Any],
    caller_agent_id: str = "",
    tool_registry=None,
    tool_gateway=None,
    graph_registry=None,
) -> Task:
    """Handle tasks/send — create a task and begin execution."""

    # Parse message from params
    msg_data = params.get("message", {})
    message = Message.from_dict(msg_data) if msg_data else Message.text(str(params.get("text", "")))

    task = task_manager.create_task(
        message=message,
        initiator_agent_id=caller_agent_id,
        target_agent_id=params.get("target_agent_id", ""),
        session_id=params.get("sessionId", ""),
        metadata=params.get("metadata", {}),
    )

    # Transition to WORKING
    task_manager.transition(task.id, TaskState.WORKING)

    # Try to execute: map message to tool calls
    try:
        text = message.get_text()
        results = _execute_message(
            text, task, tool_registry, tool_gateway, graph_registry
        )

        # Add results as artifacts
        if results:
            artifact = Artifact(
                name="result",
                description="Task execution result",
                parts=[TextPart(text=r) for r in results],
            )
            task_manager.add_artifact(task.id, artifact)

        # Complete the task
        result_text = "\n".join(results) if results else "Task completed"
        task = task_manager.complete_task(
            task.id, Message.text(result_text, role="agent")
        )

    except Exception as e:
        logger.warning("A2A task %s failed: %s", task.id, e)
        task = task_manager.fail_task(task.id, str(e))

    return task


def handle_task_get(task_manager: A2ATaskManager, task_id: str) -> Optional[Task]:
    """Handle tasks/get — return current task state."""
    return task_manager.get_task(task_id)


def handle_task_cancel(task_manager: A2ATaskManager, task_id: str) -> Task:
    """Handle tasks/cancel."""
    return task_manager.cancel_task(task_id)


def _execute_message(
    text: str,
    task: Task,
    tool_registry=None,
    tool_gateway=None,
    graph_registry=None,
) -> List[str]:
    """Map an A2A message to tool calls and execute them."""
    results = []

    if not tool_registry:
        results.append(f"Received: {text}")
        return results

    # Try to match message to a tool call
    tool_calls = map_message_to_tools(text, tool_registry)

    if not tool_calls:
        # No specific tool match — use the "ask" tool for general queries
        tool_calls = [("ask", {"question": text})]

    for tool_name, kwargs in tool_calls:
        try:
            if tool_gateway:
                from ..tools.registry import ToolContext
                # Build minimal ToolContext
                ctx = ToolContext(
                    conn=None,
                    agent_id=task.metadata.get("target_agent_id", ""),
                    agent_name="a2a",
                )
                result = tool_gateway.execute(tool_name, ctx, kwargs)
            else:
                result = tool_registry.dispatch(tool_name, None, kwargs)
            results.append(str(result))
        except Exception as e:
            results.append(f"Error executing {tool_name}: {e}")

    return results


def map_message_to_tools(text: str, tool_registry) -> List[Tuple[str, dict]]:
    """Map natural language message to tool calls.

    Uses simple keyword matching. For production, this would use an LLM
    to parse intent → tool mapping.
    """
    text_lower = text.lower().strip()
    calls = []

    # Direct tool invocation: "tool:search_nodes label=Person"
    if text_lower.startswith("tool:"):
        parts = text[5:].strip().split(" ", 1)
        tool_name = parts[0]
        kwargs = {}
        if len(parts) > 1:
            # Parse key=value pairs
            for pair in parts[1].split(" "):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    kwargs[k] = v
        calls.append((tool_name, kwargs))
        return calls

    # Keyword-based routing
    if any(w in text_lower for w in ["search", "find", "look for", "query"]):
        calls.append(("search_nodes", {"query": text}))
    elif any(w in text_lower for w in ["status", "progress", "overview"]):
        calls.append(("project_status", {}))
    elif any(w in text_lower for w in ["brief", "context", "summary"]):
        calls.append(("briefing", {}))
    elif any(w in text_lower for w in ["task", "todo", "work item"]):
        calls.append(("list_tasks", {}))

    return calls
