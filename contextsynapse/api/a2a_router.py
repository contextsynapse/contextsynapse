"""
A2A Protocol Router
====================
FastAPI router implementing the Google A2A protocol endpoints.

Endpoints:
  GET  /.well-known/agent.json  — Agent Card discovery
  POST /a2a                     — JSON-RPC 2.0 dispatch (tasks/send, tasks/get, tasks/cancel)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger(__name__)


def create_a2a_router(
    graph_registry=None,
    agent_registry=None,
    session_manager=None,
    tool_registry=None,
    tool_gateway=None,
    pubsub=None,
):
    """Create the A2A protocol router with injected dependencies."""

    from ..a2a.task_manager import A2ATaskManager
    from ..a2a import handlers

    router = APIRouter(tags=["a2a"])

    # Task manager — shared across requests
    task_manager = A2ATaskManager(
        session_manager=session_manager,
        pubsub=pubsub,
    )

    # ── Agent Card Discovery ──────────────────────────────────────────

    @router.get("/.well-known/agent.json")
    async def agent_card(request: Request):
        """Return the A2A Agent Card for this server."""
        from ..context.agent_card import AgentCard, AgentSkill

        base_url = str(request.base_url).rstrip("/")

        # Build skills from ToolRegistry
        skills = []
        if tool_registry:
            for tool_def in tool_registry.all():
                skills.append(AgentSkill(
                    id=tool_def.name,
                    name=tool_def.name,
                    description=tool_def.description,
                    tags=[tool_def.category] if hasattr(tool_def, "category") else [],
                ))

        card = AgentCard(
            name="AIContextDB",
            description="Shared context database for AI agent collaboration. "
                        "Supports knowledge graphs, entity extraction, multi-agent sessions, "
                        "and workspace management.",
            url=f"{base_url}/a2a",
            skills=skills,
        )

        # Build A2A-compliant response
        card_dict = card.to_dict()
        card_dict["provider"] = {
            "organization": "QGraph",
            "url": base_url,
        }
        card_dict["version"] = "1.0.0"
        card_dict["capabilities"] = {
            "streaming": True,
            "pushNotifications": bool(pubsub),
            "stateTransitionHistory": True,
        }
        card_dict["authentication"] = {
            "schemes": ["bearer"],
        }
        card_dict["defaultInputModes"] = ["text", "data"]
        card_dict["defaultOutputModes"] = ["text", "data"]

        return JSONResponse(content=card_dict)

    # ── JSON-RPC 2.0 Dispatch ─────────────────────────────────────────

    @router.post("/a2a")
    async def a2a_rpc(request: Request):
        """A2A JSON-RPC 2.0 endpoint."""
        try:
            body = await request.json()
        except Exception:
            return _jsonrpc_error(None, -32700, "Parse error")

        req_id = body.get("id")
        method = body.get("method", "")
        params = body.get("params", {})

        if body.get("jsonrpc") != "2.0":
            return _jsonrpc_error(req_id, -32600, "Invalid Request: jsonrpc must be '2.0'")

        # Extract caller agent from auth header (optional)
        caller_agent_id = ""
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer ") and ":" in auth_header[7:]:
            caller_agent_id = auth_header[7:].split(":")[0]

        # Dispatch
        try:
            if method == "tasks/send":
                task = handlers.handle_task_send(
                    task_manager=task_manager,
                    params=params,
                    caller_agent_id=caller_agent_id,
                    tool_registry=tool_registry,
                    tool_gateway=tool_gateway,
                    graph_registry=graph_registry,
                )
                return _jsonrpc_result(req_id, task.to_dict())

            elif method == "tasks/get":
                task_id = params.get("id", "")
                task = handlers.handle_task_get(task_manager, task_id)
                if not task:
                    return _jsonrpc_error(req_id, -32001, f"Task {task_id} not found")
                return _jsonrpc_result(req_id, task.to_dict())

            elif method == "tasks/cancel":
                task_id = params.get("id", "")
                task = handlers.handle_task_cancel(task_manager, task_id)
                return _jsonrpc_result(req_id, task.to_dict())

            elif method == "tasks/sendSubscribe":
                # SSE streaming variant
                task = handlers.handle_task_send(
                    task_manager=task_manager,
                    params=params,
                    caller_agent_id=caller_agent_id,
                    tool_registry=tool_registry,
                    tool_gateway=tool_gateway,
                    graph_registry=graph_registry,
                )
                # Return as SSE stream with task updates
                async def event_stream():
                    yield f"event: TaskStatusUpdateEvent\ndata: {json.dumps(task.to_dict())}\n\n"
                    for artifact in task.artifacts:
                        yield f"event: TaskArtifactUpdateEvent\ndata: {json.dumps(artifact.to_dict())}\n\n"

                return StreamingResponse(
                    event_stream(),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )

            elif method == "tasks/pushNotification/set":
                # Store webhook URL for push notifications
                task_id = params.get("id", "")
                webhook_url = params.get("pushNotificationConfig", {}).get("url", "")
                task = task_manager.get_task(task_id)
                if not task:
                    return _jsonrpc_error(req_id, -32001, f"Task {task_id} not found")
                task.metadata["push_notification_url"] = webhook_url
                # Persist (simple — store in metadata)
                return _jsonrpc_result(req_id, {
                    "id": task_id,
                    "pushNotificationConfig": {"url": webhook_url},
                })

            elif method == "tasks/pushNotification/get":
                task_id = params.get("id", "")
                task = task_manager.get_task(task_id)
                if not task:
                    return _jsonrpc_error(req_id, -32001, f"Task {task_id} not found")
                return _jsonrpc_result(req_id, {
                    "id": task_id,
                    "pushNotificationConfig": {
                        "url": task.metadata.get("push_notification_url", ""),
                    },
                })

            else:
                return _jsonrpc_error(req_id, -32601, f"Method not found: {method}")

        except ValueError as e:
            return _jsonrpc_error(req_id, -32001, str(e))
        except Exception as e:
            logger.exception("A2A RPC error: %s", e)
            return _jsonrpc_error(req_id, -32000, f"Server error: {e}")

    # ── Task list (convenience, not in A2A spec) ──────────────────────

    @router.get("/a2a/tasks")
    async def list_tasks(
        state: Optional[str] = None,
        target: Optional[str] = None,
        limit: int = 50,
    ):
        """List A2A tasks (convenience endpoint)."""
        tasks = task_manager.list_tasks(state=state, target_agent_id=target, limit=limit)
        return {"tasks": [t.to_dict() for t in tasks], "total": len(tasks)}

    return router


# ── JSON-RPC Helpers ──────────────────────────────────────────────────

def _jsonrpc_result(req_id, result: Any) -> JSONResponse:
    return JSONResponse(content={
        "jsonrpc": "2.0",
        "id": req_id,
        "result": result,
    })


def _jsonrpc_error(req_id, code: int, message: str) -> JSONResponse:
    status = 400 if code in (-32600, -32601, -32602) else 200
    return JSONResponse(
        status_code=status,
        content={
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": code, "message": message},
        },
    )
