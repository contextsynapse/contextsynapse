"""
Conversation Store
==================
Key-value backed conversation management for context sessions.

Each conversation is an ordered sequence of messages tied to a session,
with an embedded KV store for fast state/metadata lookup.

Usage::

    from contextsynapse.context.conversation import ConversationStore

    store = ConversationStore()
    conv = store.create(session_id="abc123", title="Research Chat")
    store.append_message(conv.conversation_id, role="user", content="Hello")
    store.append_message(conv.conversation_id, role="assistant", content="Hi there!")

    history = store.get_history(conv.conversation_id)
    store.set_kv(conv.conversation_id, "intent", "research")
    intent = store.get_kv(conv.conversation_id, "intent")

    # Export to ContextHub for LLM consumption
    hub = store.to_hub(conv.conversation_id, system_prompt="You are helpful.")
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ConversationMessage:
    """A single message in a conversation."""
    message_id: str
    conversation_id: str
    turn_index: int
    role: str                         # user | assistant | system | tool
    content: str
    agent_id: Optional[str] = None
    created_at: str = field(default_factory=_utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "conversation_id": self.conversation_id,
            "turn_index": self.turn_index,
            "role": self.role,
            "content": self.content,
            "agent_id": self.agent_id,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


@dataclass
class Conversation:
    """A conversation with metadata and optional TTL."""
    conversation_id: str
    session_id: str
    title: Optional[str] = None
    created_at: str = field(default_factory=_utcnow)
    updated_at: str = field(default_factory=_utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)
    status: str = "active"            # active | archived | deleted
    ttl_seconds: Optional[int] = None
    messages: List[ConversationMessage] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "session_id": self.session_id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
            "status": self.status,
            "ttl_seconds": self.ttl_seconds,
            "message_count": len(self.messages),
        }


class ConversationStore:
    """
    SQLite-backed conversation store with KV support.

    Tables:
    - ``conversations`` — conversation metadata
    - ``conversation_messages`` — ordered messages
    - ``conversation_kv`` — arbitrary key-value pairs per conversation
    """

    def __init__(self, db_path: str = "contextcore_data/context.db"):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self):
        with self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    title TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}',
                    status TEXT DEFAULT 'active',
                    ttl_seconds INTEGER DEFAULT NULL
                );

                CREATE TABLE IF NOT EXISTS conversation_messages (
                    message_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    turn_index INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    agent_id TEXT,
                    created_at TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}',
                    UNIQUE(conversation_id, turn_index)
                );

                CREATE TABLE IF NOT EXISTS conversation_kv (
                    conversation_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (conversation_id, key)
                );

                CREATE INDEX IF NOT EXISTS idx_conv_session
                    ON conversations(session_id);
                CREATE INDEX IF NOT EXISTS idx_conv_status
                    ON conversations(status);
                CREATE INDEX IF NOT EXISTS idx_convmsg_conv
                    ON conversation_messages(conversation_id);
                CREATE INDEX IF NOT EXISTS idx_convkv_conv
                    ON conversation_kv(conversation_id);
            """)

    # ------------------------------------------------------------------
    # Conversation CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        session_id: str,
        title: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        ttl_seconds: Optional[int] = None,
    ) -> Conversation:
        """Create a new conversation."""
        conv_id = uuid.uuid4().hex[:12]
        now = _utcnow()
        meta_json = json.dumps(metadata or {})

        with self._conn:
            self._conn.execute(
                """INSERT INTO conversations
                   (conversation_id, session_id, title, created_at, updated_at,
                    metadata, status, ttl_seconds)
                   VALUES (?, ?, ?, ?, ?, ?, 'active', ?)""",
                (conv_id, session_id, title, now, now, meta_json, ttl_seconds),
            )

        return Conversation(
            conversation_id=conv_id,
            session_id=session_id,
            title=title,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
            status="active",
            ttl_seconds=ttl_seconds,
        )

    def get(self, conversation_id: str, include_messages: bool = True) -> Optional[Conversation]:
        """Get a conversation by ID. Returns None if not found or expired."""
        row = self._conn.execute(
            "SELECT * FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()

        if not row:
            return None

        conv = self._row_to_conversation(row)

        # Lazy TTL check
        if conv.ttl_seconds is not None and conv.status == "active":
            created = datetime.fromisoformat(conv.created_at)
            age = (datetime.now(timezone.utc) - created).total_seconds()
            if age > conv.ttl_seconds:
                self.archive(conversation_id)
                conv.status = "archived"

        if conv.status == "deleted":
            return None

        if include_messages:
            conv.messages = self.get_history(conversation_id)

        return conv

    def list_conversations(
        self,
        session_id: str,
        status: str = "active",
    ) -> List[Conversation]:
        """List conversations for a session."""
        rows = self._conn.execute(
            "SELECT * FROM conversations WHERE session_id = ? AND status = ? ORDER BY updated_at DESC",
            (session_id, status),
        ).fetchall()
        return [self._row_to_conversation(r) for r in rows]

    def archive(self, conversation_id: str) -> bool:
        """Archive a conversation."""
        with self._conn:
            cur = self._conn.execute(
                "UPDATE conversations SET status = 'archived', updated_at = ? WHERE conversation_id = ?",
                (_utcnow(), conversation_id),
            )
        return cur.rowcount > 0

    def delete(self, conversation_id: str) -> bool:
        """Soft-delete a conversation."""
        with self._conn:
            cur = self._conn.execute(
                "UPDATE conversations SET status = 'deleted', updated_at = ? WHERE conversation_id = ?",
                (_utcnow(), conversation_id),
            )
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Message operations
    # ------------------------------------------------------------------

    def append_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        agent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ConversationMessage:
        """Append a message to the conversation."""
        msg_id = uuid.uuid4().hex[:12]
        now = _utcnow()
        meta_json = json.dumps(metadata or {})

        with self._conn:
            # Get next turn_index
            row = self._conn.execute(
                "SELECT COALESCE(MAX(turn_index), -1) + 1 FROM conversation_messages WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            turn_index = row[0]

            self._conn.execute(
                """INSERT INTO conversation_messages
                   (message_id, conversation_id, turn_index, role, content,
                    agent_id, created_at, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (msg_id, conversation_id, turn_index, role, content, agent_id, now, meta_json),
            )

            # Touch conversation updated_at
            self._conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                (now, conversation_id),
            )

        return ConversationMessage(
            message_id=msg_id,
            conversation_id=conversation_id,
            turn_index=turn_index,
            role=role,
            content=content,
            agent_id=agent_id,
            created_at=now,
            metadata=metadata or {},
        )

    def get_history(
        self,
        conversation_id: str,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[ConversationMessage]:
        """Get message history for a conversation."""
        sql = "SELECT * FROM conversation_messages WHERE conversation_id = ? ORDER BY turn_index ASC"
        params: list = [conversation_id]

        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        elif offset > 0:
            sql += " LIMIT -1 OFFSET ?"
            params.append(offset)

        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_message(r) for r in rows]

    def get_last_n(self, conversation_id: str, n: int) -> List[ConversationMessage]:
        """Get the last N messages of a conversation."""
        rows = self._conn.execute(
            """SELECT * FROM (
                SELECT * FROM conversation_messages
                WHERE conversation_id = ?
                ORDER BY turn_index DESC
                LIMIT ?
            ) ORDER BY turn_index ASC""",
            (conversation_id, n),
        ).fetchall()
        return [self._row_to_message(r) for r in rows]

    def get_message(self, message_id: str) -> Optional[ConversationMessage]:
        """Get a single message by ID."""
        row = self._conn.execute(
            "SELECT * FROM conversation_messages WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        return self._row_to_message(row) if row else None

    def message_count(self, conversation_id: str) -> int:
        """Count messages in a conversation."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM conversation_messages WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        return row[0]

    # ------------------------------------------------------------------
    # KV store operations
    # ------------------------------------------------------------------

    def set_kv(self, conversation_id: str, key: str, value: Any) -> None:
        """Set a key-value pair for a conversation."""
        now = _utcnow()
        val_json = json.dumps(value)
        with self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO conversation_kv
                   (conversation_id, key, value, updated_at)
                   VALUES (?, ?, ?, ?)""",
                (conversation_id, key, val_json, now),
            )

    def get_kv(self, conversation_id: str, key: str) -> Optional[Any]:
        """Get a value by key. Returns None if not found."""
        row = self._conn.execute(
            "SELECT value FROM conversation_kv WHERE conversation_id = ? AND key = ?",
            (conversation_id, key),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def get_all_kv(self, conversation_id: str) -> Dict[str, Any]:
        """Get all KV pairs for a conversation."""
        rows = self._conn.execute(
            "SELECT key, value FROM conversation_kv WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchall()
        return {r[0]: json.loads(r[1]) for r in rows}

    def delete_kv(self, conversation_id: str, key: str) -> bool:
        """Delete a KV pair."""
        with self._conn:
            cur = self._conn.execute(
                "DELETE FROM conversation_kv WHERE conversation_id = ? AND key = ?",
                (conversation_id, key),
            )
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # TTL / expiry
    # ------------------------------------------------------------------

    def cleanup_expired(self) -> int:
        """Archive all conversations past their TTL. Returns count archived."""
        now = datetime.now(timezone.utc)
        rows = self._conn.execute(
            "SELECT conversation_id, created_at, ttl_seconds FROM conversations WHERE status = 'active' AND ttl_seconds IS NOT NULL",
        ).fetchall()

        archived = 0
        for row in rows:
            created = datetime.fromisoformat(row[1])
            age = (now - created).total_seconds()
            if age > row[2]:
                self.archive(row[0])
                archived += 1

        if archived:
            logger.info(f"Cleaned up {archived} expired conversations")
        return archived

    # ------------------------------------------------------------------
    # ContextHub integration
    # ------------------------------------------------------------------

    def to_hub(
        self,
        conversation_id: str,
        system_prompt: Optional[str] = None,
        last_n: Optional[int] = None,
    ):
        """
        Convert a conversation into a ContextHub for LLM consumption.

        Maps message roles: system → SYSTEM, user → USER,
        assistant → ASSISTANT, tool → USER.
        """
        from .hub import ContextHub, ContextRole

        hub = ContextHub(system_prompt=system_prompt)

        if last_n:
            messages = self.get_last_n(conversation_id, last_n)
        else:
            messages = self.get_history(conversation_id)

        role_map = {
            "system": ContextRole.SYSTEM,
            "user": ContextRole.USER,
            "assistant": ContextRole.ASSISTANT,
            "tool": ContextRole.TOOL,
        }

        for msg in messages:
            # Skip system prompt from conversation if we already added one
            if msg.role == "system" and system_prompt:
                continue
            hub.add_text(
                msg.content,
                role=role_map.get(msg.role, ContextRole.USER),
                label=f"Turn {msg.turn_index}",
                metadata={"message_id": msg.message_id, "agent_id": msg.agent_id},
            )

        return hub

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _row_to_conversation(self, row) -> Conversation:
        return Conversation(
            conversation_id=row["conversation_id"],
            session_id=row["session_id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=json.loads(row["metadata"] or "{}"),
            status=row["status"],
            ttl_seconds=row["ttl_seconds"],
        )

    def _row_to_message(self, row) -> ConversationMessage:
        return ConversationMessage(
            message_id=row["message_id"],
            conversation_id=row["conversation_id"],
            turn_index=row["turn_index"],
            role=row["role"],
            content=row["content"],
            agent_id=row["agent_id"],
            created_at=row["created_at"],
            metadata=json.loads(row["metadata"] or "{}"),
        )
