"""
Collaboration — Comments & Reviews
====================================
Lightweight comment system for graph nodes. Any node (task, decision,
knowledge) can have comments attached. Comments are stored in SQLite
and linked by node_id.

Usage:
    store = CommentStore()
    store.add("node-123", "alice", "This needs more detail")
    comments = store.list("node-123")
"""

from __future__ import annotations

import json
import logging
import secrets
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Comment:
    comment_id: str
    node_id: str
    author: str  # user_id or agent_name
    author_name: str
    content: str
    created_at: str
    updated_at: Optional[str] = None
    parent_id: Optional[str] = None  # for threaded replies
    reaction: Optional[str] = None  # 👍 👎 ✅ ❌ 🔥
    resolved: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CommentStore:
    """SQLite-backed comment storage."""

    def __init__(self, db_path: str = "contextcore_data/collaboration.db"):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS comments (
                comment_id TEXT PRIMARY KEY,
                node_id TEXT NOT NULL,
                author TEXT NOT NULL,
                author_name TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                parent_id TEXT,
                reaction TEXT,
                resolved INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_comments_node ON comments(node_id);
            CREATE INDEX IF NOT EXISTS idx_comments_author ON comments(author);
        """)

    def add(
        self,
        node_id: str,
        author: str,
        author_name: str,
        content: str,
        parent_id: str = None,
    ) -> Comment:
        """Add a comment to a node."""
        comment = Comment(
            comment_id=secrets.token_hex(8),
            node_id=node_id,
            author=author,
            author_name=author_name,
            content=content,
            created_at=datetime.now(timezone.utc).isoformat(),
            parent_id=parent_id,
        )
        self._conn.execute(
            "INSERT INTO comments (comment_id, node_id, author, author_name, content, "
            "created_at, parent_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (comment.comment_id, node_id, author, author_name, content,
             comment.created_at, parent_id),
        )
        self._conn.commit()
        return comment

    def list(self, node_id: str) -> List[Comment]:
        """Get all comments for a node, ordered by creation time."""
        rows = self._conn.execute(
            "SELECT * FROM comments WHERE node_id = ? ORDER BY created_at ASC",
            (node_id,),
        ).fetchall()
        return [self._row_to_comment(r) for r in rows]

    def list_recent(self, limit: int = 20) -> List[Comment]:
        """Get most recent comments across all nodes."""
        rows = self._conn.execute(
            "SELECT * FROM comments ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_comment(r) for r in rows]

    def update(self, comment_id: str, content: str = None, resolved: bool = None,
               reaction: str = None) -> Optional[Comment]:
        """Update a comment."""
        updates = []
        params = []
        if content is not None:
            updates.append("content = ?")
            params.append(content)
            updates.append("updated_at = ?")
            params.append(datetime.now(timezone.utc).isoformat())
        if resolved is not None:
            updates.append("resolved = ?")
            params.append(1 if resolved else 0)
        if reaction is not None:
            updates.append("reaction = ?")
            params.append(reaction)
        if not updates:
            return None
        params.append(comment_id)
        self._conn.execute(
            f"UPDATE comments SET {', '.join(updates)} WHERE comment_id = ?",
            params,
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM comments WHERE comment_id = ?", (comment_id,)
        ).fetchone()
        return self._row_to_comment(row) if row else None

    def delete(self, comment_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM comments WHERE comment_id = ?", (comment_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def count(self, node_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) as c FROM comments WHERE node_id = ?", (node_id,)
        ).fetchone()
        return row["c"] if row else 0

    def _row_to_comment(self, row) -> Comment:
        return Comment(
            comment_id=row["comment_id"],
            node_id=row["node_id"],
            author=row["author"],
            author_name=row["author_name"],
            content=row["content"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            parent_id=row["parent_id"],
            reaction=row["reaction"],
            resolved=bool(row["resolved"]),
        )
