"""
A2A Protocol Integration Tests
================================
Tests Agent Card discovery, task lifecycle, streaming, client loopback.
"""

import os
import pytest
from httpx import AsyncClient, ASGITransport

os.environ.setdefault("AICONTEXTDB_ADMIN_KEY", "test-a2a-key")
os.environ.setdefault("AICONTEXTDB_ENV", "development")

from contextcore.api.api import app


def _transport():
    return ASGITransport(app=app)


# ── Agent Card Discovery ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_card_discovery():
    """GET /.well-known/agent.json returns a valid Agent Card."""
    async with AsyncClient(transport=_transport(), base_url="http://test") as c:
        resp = await c.get("/.well-known/agent.json")
        assert resp.status_code == 200
        card = resp.json()
        assert card.get("name") == "AIContextDB"
        assert "capabilities" in card
        assert card["capabilities"]["streaming"] is True
        assert "skills" in card
        assert isinstance(card["skills"], list)
        assert "url" in card
        assert "provider" in card


@pytest.mark.asyncio
async def test_agent_card_has_valid_structure():
    """Agent Card should have all required A2A fields."""
    async with AsyncClient(transport=_transport(), base_url="http://test") as c:
        resp = await c.get("/.well-known/agent.json")
        card = resp.json()
        # Required A2A fields
        assert "name" in card
        assert "url" in card
        assert "version" in card
        assert "capabilities" in card
        assert "authentication" in card
        assert "defaultInputModes" in card
        assert "defaultOutputModes" in card
        assert "skills" in card
        assert isinstance(card["skills"], list)


# ── JSON-RPC Basics ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_invalid_jsonrpc():
    """Non-2.0 jsonrpc should return error."""
    async with AsyncClient(transport=_transport(), base_url="http://test") as c:
        resp = await c.post("/a2a", json={"jsonrpc": "1.0", "method": "tasks/get", "id": "1"})
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data


@pytest.mark.asyncio
async def test_unknown_method():
    """Unknown method should return -32601."""
    async with AsyncClient(transport=_transport(), base_url="http://test") as c:
        resp = await c.post("/a2a", json={
            "jsonrpc": "2.0", "method": "unknown/method", "params": {}, "id": "1"
        })
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == -32601


# ── Task Lifecycle ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_task_send_and_get():
    """tasks/send creates a task, tasks/get retrieves it."""
    async with AsyncClient(transport=_transport(), base_url="http://test") as c:
        # Send
        resp = await c.post("/a2a", json={
            "jsonrpc": "2.0", "id": "1",
            "method": "tasks/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "What is the project status?"}],
                },
            },
        })
        assert resp.status_code == 200
        result = resp.json()["result"]
        task_id = result["id"]
        assert task_id
        assert result["status"]["state"] in ("completed", "failed")
        assert len(result["history"]) >= 1

        # Get
        resp2 = await c.post("/a2a", json={
            "jsonrpc": "2.0", "id": "2",
            "method": "tasks/get",
            "params": {"id": task_id},
        })
        assert resp2.status_code == 200
        task = resp2.json()["result"]
        assert task["id"] == task_id


@pytest.mark.asyncio
async def test_task_cancel():
    """tasks/cancel should cancel a submitted task."""
    from contextcore.a2a.task_manager import A2ATaskManager
    from contextcore.a2a.models import Message

    mgr = A2ATaskManager()
    task = mgr.create_task(Message.text("test cancel"))
    assert task.status.state.value == "submitted"

    cancelled = mgr.cancel_task(task.id)
    assert cancelled.status.state.value == "canceled"

    # Can't cancel again
    with pytest.raises(ValueError, match="Invalid transition"):
        mgr.cancel_task(task.id)


@pytest.mark.asyncio
async def test_task_not_found():
    """tasks/get with invalid ID returns error."""
    async with AsyncClient(transport=_transport(), base_url="http://test") as c:
        resp = await c.post("/a2a", json={
            "jsonrpc": "2.0", "id": "1",
            "method": "tasks/get",
            "params": {"id": "nonexistent"},
        })
        assert resp.status_code == 200
        assert resp.json()["error"]["code"] == -32001


# ── Task State Machine ────────────────────────────────────────────────

def test_valid_state_transitions():
    """Test that the state machine enforces valid transitions."""
    from contextcore.a2a.task_manager import A2ATaskManager
    from contextcore.a2a.models import Message, TaskState

    mgr = A2ATaskManager()
    task = mgr.create_task(Message.text("test transitions"))
    assert task.status.state == TaskState.SUBMITTED

    # SUBMITTED → WORKING
    task = mgr.transition(task.id, TaskState.WORKING)
    assert task.status.state == TaskState.WORKING

    # WORKING → INPUT_REQUIRED
    task = mgr.transition(task.id, TaskState.INPUT_REQUIRED, Message.text("Need more info", role="agent"))
    assert task.status.state == TaskState.INPUT_REQUIRED

    # INPUT_REQUIRED → WORKING
    task = mgr.transition(task.id, TaskState.WORKING, Message.text("Here is the info"))
    assert task.status.state == TaskState.WORKING

    # WORKING → COMPLETED
    task = mgr.transition(task.id, TaskState.COMPLETED, Message.text("Done", role="agent"))
    assert task.status.state == TaskState.COMPLETED

    # Can't transition from COMPLETED
    with pytest.raises(ValueError):
        mgr.transition(task.id, TaskState.WORKING)


def test_invalid_state_transition():
    """SUBMITTED → COMPLETED should fail."""
    from contextcore.a2a.task_manager import A2ATaskManager
    from contextcore.a2a.models import Message, TaskState

    mgr = A2ATaskManager()
    task = mgr.create_task(Message.text("test"))
    with pytest.raises(ValueError, match="Invalid transition"):
        mgr.transition(task.id, TaskState.COMPLETED)


# ── Data Models ───────────────────────────────────────────────────────

def test_message_serialization():
    """Message round-trips through to_dict/from_dict."""
    from contextcore.a2a.models import Message, TextPart, DataPart

    msg = Message(role="user", parts=[
        TextPart(text="Hello"),
        DataPart(data={"key": "value"}),
    ], metadata={"source": "test"})

    d = msg.to_dict()
    assert d["role"] == "user"
    assert len(d["parts"]) == 2
    assert d["parts"][0]["type"] == "text"
    assert d["parts"][1]["type"] == "data"

    restored = Message.from_dict(d)
    assert restored.role == "user"
    assert len(restored.parts) == 2
    assert restored.get_text() == "Hello"


def test_task_serialization():
    """Task round-trips through to_dict/from_dict."""
    from contextcore.a2a.models import Task, Message, Artifact, TextPart

    task = Task(
        id="test123",
        session_id="sess456",
        history=[Message.text("Do something")],
        artifacts=[Artifact(name="output", parts=[TextPart(text="result")])],
    )

    d = task.to_dict()
    assert d["id"] == "test123"
    assert d["sessionId"] == "sess456"
    assert len(d["history"]) == 1
    assert len(d["artifacts"]) == 1

    restored = Task.from_dict(d)
    assert restored.id == "test123"
    assert restored.session_id == "sess456"


# ── Task List ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_tasks_endpoint():
    """GET /a2a/tasks returns task list."""
    async with AsyncClient(transport=_transport(), base_url="http://test") as c:
        resp = await c.get("/a2a/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert "tasks" in data
        assert isinstance(data["tasks"], list)


# ── SSE Streaming ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_task_send_subscribe():
    """tasks/sendSubscribe returns SSE stream."""
    async with AsyncClient(transport=_transport(), base_url="http://test") as c:
        resp = await c.post("/a2a", json={
            "jsonrpc": "2.0", "id": "1",
            "method": "tasks/sendSubscribe",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "Stream test"}],
                },
            },
        })
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")


# ── Discovery Registry ────────────────────────────────────────────────

def test_discovery_registry():
    """A2ADiscoveryRegistry stores and retrieves remote agent cards."""
    import tempfile
    from contextcore.a2a.discovery import A2ADiscoveryRegistry

    db_path = tempfile.mktemp(suffix=".db")
    registry = A2ADiscoveryRegistry(db_path=db_path)

    # Empty initially
    assert registry.list_remotes() == []

    # Can't discover a non-existent agent
    assert registry.get_card("http://fake") is None
