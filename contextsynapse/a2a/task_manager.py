"""
A2A Task Manager
=================
State machine + persistence for A2A tasks.
Each task maps to a ContextSession for graph/vector/blob access.
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from contextsynapse.core.db import IS_POSTGRES, PH, connect, dict_cursor, run_ddl, row_to_dict

from .models import (
    Artifact,
    Message,
    Task,
    TaskState,
    TaskStatus,
    VALID_TRANSITIONS,
)

logger = logging.getLogger(__name__)


class A2ATaskManager:
    """Manages A2A task lifecycle with SQLite/PostgreSQL persistence."""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS a2a_tasks (
        task_id TEXT PRIMARY KEY,
        session_id TEXT,
        state TEXT NOT NULL DEFAULT 'submitted',
        history TEXT DEFAULT '[]',
        artifacts TEXT DEFAULT '[]',
        metadata TEXT DEFAULT '{}',
        initiator_agent_id TEXT,
        target_agent_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_a2a_state ON a2a_tasks(state);
    CREATE INDEX IF NOT EXISTS idx_a2a_session ON a2a_tasks(session_id);
    CREATE INDEX IF NOT EXISTS idx_a2a_target ON a2a_tasks(target_agent_id);
    """

    def __init__(self, db_path: str = "contextcore_data/context.db",
                 session_manager=None, pubsub=None):
        self._db_path = Path(db_path)
        if not IS_POSTGRES:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = connect(str(self._db_path))
        run_ddl(self._conn, self._SCHEMA)
        self._session_manager = session_manager
        self._pubsub = pubsub

    def _exec(self, sql: str, params: tuple = ()):
        cur = dict_cursor(self._conn)
        cur.execute(sql, params)
        return cur

    def _commit(self):
        self._conn.commit()

    # ── CRUD ──────────────────────────────────────────────────────────

    def create_task(
        self,
        message: Message,
        initiator_agent_id: str = "",
        target_agent_id: str = "",
        session_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Task:
        """Create a new task in SUBMITTED state."""
        task_id = secrets.token_hex(12)
        now = datetime.now(timezone.utc).isoformat()

        # Create a ContextSession if none provided
        if not session_id and self._session_manager:
            try:
                session = self._session_manager.create_session(
                    name=f"a2a_{task_id[:8]}",
                    owner_agent_id=target_agent_id or initiator_agent_id,
                    config={"a2a_task_id": task_id, "source": "a2a"},
                )
                session_id = session.session_id
            except Exception as e:
                logger.warning("Failed to create session for A2A task: %s", e)

        task = Task(
            id=task_id,
            session_id=session_id,
            status=TaskStatus(state=TaskState.SUBMITTED),
            history=[message],
            metadata=metadata or {},
        )

        self._exec(
            f"INSERT INTO a2a_tasks "
            f"(task_id, session_id, state, history, artifacts, metadata, "
            f"initiator_agent_id, target_agent_id, created_at, updated_at) "
            f"VALUES ({PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH})",
            (
                task_id, session_id, TaskState.SUBMITTED.value,
                json.dumps([m.to_dict() for m in task.history]),
                "[]",
                json.dumps(task.metadata),
                initiator_agent_id, target_agent_id, now, now,
            ),
        )
        self._commit()
        self._emit_event(task, "task_submitted")
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        row = row_to_dict(self._exec(
            f"SELECT * FROM a2a_tasks WHERE task_id = {PH}", (task_id,)
        ).fetchone())
        return self._row_to_task(row) if row else None

    def list_tasks(
        self,
        state: Optional[str] = None,
        target_agent_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Task]:
        """List tasks with optional filters."""
        query = "SELECT * FROM a2a_tasks WHERE 1=1"
        params: list = []
        if state:
            query += f" AND state = {PH}"
            params.append(state)
        if target_agent_id:
            query += f" AND target_agent_id = {PH}"
            params.append(target_agent_id)
        query += f" ORDER BY updated_at DESC LIMIT {PH}"
        params.append(limit)
        rows = self._exec(query, tuple(params)).fetchall()
        return [self._row_to_task(row_to_dict(r)) for r in rows]

    # ── State Transitions ─────────────────────────────────────────────

    def transition(
        self,
        task_id: str,
        new_state: TaskState,
        message: Optional[Message] = None,
    ) -> Task:
        """Transition a task to a new state (validates against state machine)."""
        task = self.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        current = task.status.state
        if new_state not in VALID_TRANSITIONS.get(current, set()):
            raise ValueError(
                f"Invalid transition: {current.value} → {new_state.value}. "
                f"Valid: {[s.value for s in VALID_TRANSITIONS.get(current, set())]}"
            )

        now = datetime.now(timezone.utc).isoformat()
        task.status = TaskStatus(state=new_state, message=message, timestamp=now)
        if message:
            task.history.append(message)

        self._exec(
            f"UPDATE a2a_tasks SET state = {PH}, history = {PH}, updated_at = {PH} WHERE task_id = {PH}",
            (
                new_state.value,
                json.dumps([m.to_dict() for m in task.history]),
                now, task_id,
            ),
        )
        self._commit()
        self._emit_event(task, f"task_{new_state.value}")
        return task

    def cancel_task(self, task_id: str) -> Task:
        """Cancel a task."""
        return self.transition(task_id, TaskState.CANCELED)

    def complete_task(self, task_id: str, message: Optional[Message] = None) -> Task:
        """Mark a task as completed."""
        return self.transition(task_id, TaskState.COMPLETED, message)

    def fail_task(self, task_id: str, error: str) -> Task:
        """Mark a task as failed."""
        msg = Message.text(f"Error: {error}", role="agent")
        return self.transition(task_id, TaskState.FAILED, msg)

    # ── Artifacts ─────────────────────────────────────────────────────

    def add_artifact(self, task_id: str, artifact: Artifact) -> Task:
        """Add an artifact to a task."""
        task = self.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        task.artifacts.append(artifact)
        now = datetime.now(timezone.utc).isoformat()
        self._exec(
            f"UPDATE a2a_tasks SET artifacts = {PH}, updated_at = {PH} WHERE task_id = {PH}",
            (json.dumps([a.to_dict() for a in task.artifacts]), now, task_id),
        )
        self._commit()
        self._emit_event(task, "task_artifact_added")
        return task

    def add_message(self, task_id: str, message: Message) -> Task:
        """Append a message to a task's history."""
        task = self.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        task.history.append(message)
        now = datetime.now(timezone.utc).isoformat()
        self._exec(
            f"UPDATE a2a_tasks SET history = {PH}, updated_at = {PH} WHERE task_id = {PH}",
            (json.dumps([m.to_dict() for m in task.history]), now, task_id),
        )
        self._commit()
        return task

    # ── Internal ──────────────────────────────────────────────────────

    def _row_to_task(self, row: dict) -> Task:
        history = [Message.from_dict(m) for m in json.loads(row["history"] or "[]")]
        artifacts = [Artifact.from_dict(a) for a in json.loads(row["artifacts"] or "[]")]
        meta = json.loads(row["metadata"] or "{}")
        meta["initiator_agent_id"] = row["initiator_agent_id"]
        meta["target_agent_id"] = row["target_agent_id"]
        return Task(
            id=row["task_id"],
            session_id=row["session_id"] or "",
            status=TaskStatus(state=TaskState(row["state"])),
            history=history,
            artifacts=artifacts,
            metadata=meta,
        )

    def _emit_event(self, task: Task, event_type: str):
        """Emit a PubSub event for task state changes."""
        if not self._pubsub:
            return
        try:
            from ..context.pubsub import ContextEvent
            event = ContextEvent(
                event_type=event_type,
                session_id=task.session_id,
                agent_id=task.metadata.get("target_agent_id", ""),
                data={
                    "task_id": task.id,
                    "state": task.status.state.value,
                    "timestamp": task.status.timestamp,
                },
            )
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._pubsub.publish(event))
            except RuntimeError:
                pass  # No event loop — skip async emit
        except Exception as e:
            logger.debug("Failed to emit A2A event: %s", e)

    def close(self):
        if self._conn:
            self._conn.close()
