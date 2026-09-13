"""
Blob Storage
=============
File-based storage for binary objects (images, audio, video, PDFs, etc.)
with metadata tracked in SQLite and a reference node in the session graph.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class BlobStore:
    """
    Store binary files on disk under
    ``{base_path}/{session_id}/{content_hash[:2]}/{content_hash}``.

    Metadata (size, MIME type, hash, filename) is persisted to SQLite.
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS blobs (
        blob_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        filename TEXT NOT NULL,
        mime_type TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        content_hash TEXT NOT NULL,
        storage_path TEXT NOT NULL,
        agent_id TEXT,
        created_at TEXT NOT NULL,
        metadata TEXT DEFAULT '{}'
    );

    CREATE INDEX IF NOT EXISTS idx_blobs_session ON blobs(session_id);
    CREATE INDEX IF NOT EXISTS idx_blobs_hash ON blobs(content_hash);
    """

    def __init__(
        self,
        base_path: str = "contextcore_data/blobs",
        db_path: str = "contextcore_data/context.db",
    ):
        self._base = Path(base_path)
        self._base.mkdir(parents=True, exist_ok=True)
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(self._SCHEMA)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def store(
        self,
        session_id: str,
        data: bytes,
        filename: str,
        mime_type: str,
        agent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Store a blob and return its metadata.

        Returns::

            {
                "blob_id": str,
                "content_hash": str,
                "size_bytes": int,
                "storage_path": str,
            }
        """
        content_hash = hashlib.sha256(data).hexdigest()
        blob_id = content_hash[:24]  # short but collision-safe

        # Shard by first 2 hex chars
        shard = content_hash[:2]
        blob_dir = self._base / session_id / shard
        blob_dir.mkdir(parents=True, exist_ok=True)

        blob_path = blob_dir / content_hash
        blob_path.write_bytes(data)

        now = datetime.now(timezone.utc).isoformat()
        storage_path = str(blob_path)

        self._conn.execute(
            "INSERT OR REPLACE INTO blobs "
            "(blob_id, session_id, filename, mime_type, size_bytes, content_hash, "
            "storage_path, agent_id, created_at, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                blob_id, session_id, filename, mime_type, len(data),
                content_hash, storage_path, agent_id, now,
                json.dumps(metadata or {}),
            ),
        )
        self._conn.commit()

        return {
            "blob_id": blob_id,
            "content_hash": content_hash,
            "size_bytes": len(data),
            "storage_path": storage_path,
        }

    def get(self, blob_id: str) -> Optional[Tuple[bytes, Dict[str, Any]]]:
        """Return (raw_bytes, metadata_dict) or None."""
        row = self._conn.execute(
            "SELECT * FROM blobs WHERE blob_id = ?", (blob_id,)
        ).fetchone()
        if not row:
            return None

        blob_path = Path(row["storage_path"])
        if not blob_path.exists():
            logger.warning(f"Blob file missing on disk: {blob_path}")
            return None

        return blob_path.read_bytes(), self._row_to_meta(row)

    def get_metadata(self, blob_id: str) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM blobs WHERE blob_id = ?", (blob_id,)
        ).fetchone()
        return self._row_to_meta(row) if row else None

    def list_blobs(self, session_id: str) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM blobs WHERE session_id = ? ORDER BY created_at DESC",
            (session_id,),
        ).fetchall()
        return [self._row_to_meta(r) for r in rows]

    def delete(self, blob_id: str) -> bool:
        row = self._conn.execute(
            "SELECT storage_path FROM blobs WHERE blob_id = ?", (blob_id,)
        ).fetchone()
        if not row:
            return False

        # Remove file
        blob_path = Path(row["storage_path"])
        if blob_path.exists():
            blob_path.unlink()

        # Remove DB record
        self._conn.execute("DELETE FROM blobs WHERE blob_id = ?", (blob_id,))
        self._conn.commit()
        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _row_to_meta(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "blob_id": row["blob_id"],
            "session_id": row["session_id"],
            "filename": row["filename"],
            "mime_type": row["mime_type"],
            "size_bytes": row["size_bytes"],
            "content_hash": row["content_hash"],
            "storage_path": row["storage_path"],
            "agent_id": row["agent_id"],
            "created_at": row["created_at"],
            "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
        }

    def close(self):
        if self._conn:
            self._conn.close()
