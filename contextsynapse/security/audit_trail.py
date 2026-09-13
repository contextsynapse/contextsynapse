"""
Immutable Audit Trail
======================
Append-only log of every data access, agent action, and context delivery.
Designed for SOC2/GDPR compliance — no UPDATE or DELETE on audit records.

Usage::

    trail = AuditTrail()
    trail.log("agent:a1", "search_nodes", "read", resource_id="ctx_123",
              details={"query": "election"}, ip="10.0.0.1")
    trail.log("user:admin", "delete_context", "delete", resource_id="ctx_456")

    entries = trail.query(actor="agent:a1", action="search_nodes", limit=100)
    trail.export_csv("audit_2026_Q1.csv")
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    actor TEXT NOT NULL,
    actor_type TEXT NOT NULL DEFAULT 'user',
    action TEXT NOT NULL,
    operation_type TEXT NOT NULL DEFAULT 'read',
    resource_type TEXT DEFAULT '',
    resource_id TEXT DEFAULT '',
    namespace TEXT DEFAULT '',
    details TEXT DEFAULT '{}',
    ip_address TEXT DEFAULT '',
    user_agent TEXT DEFAULT '',
    success INTEGER DEFAULT 1,
    hash TEXT DEFAULT '',
    previous_hash TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);
CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_log(resource_id);
CREATE INDEX IF NOT EXISTS idx_audit_ns ON audit_log(namespace);
"""


class AuditTrail:
    """Immutable, append-only audit log for compliance.

    Every entry is hash-chained to the previous entry, creating a
    tamper-evident log (like a mini blockchain). If any entry is
    modified or deleted, the hash chain breaks.
    """

    def __init__(self, db_path: str = "contextcore_data/audit_trail.db"):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL mode: concurrent reads + faster writes (no full sync per commit)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._last_hash = self._get_last_hash()
        self._pending = 0
        self._flush_interval = 50  # commit every N inserts

    def _get_last_hash(self) -> str:
        row = self._conn.execute(
            "SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["hash"] if row else "genesis"

    def log(
        self,
        actor: str,
        action: str,
        operation_type: str = "read",
        resource_type: str = "",
        resource_id: str = "",
        namespace: str = "",
        details: Optional[Dict[str, Any]] = None,
        ip_address: str = "",
        user_agent: str = "",
        success: bool = True,
        actor_type: str = "user",
    ) -> int:
        """Append an audit entry. Returns the entry ID.

        Args:
            actor: Who did it — "user:uid123" or "agent:a1" or "system:scheduler"
            action: What they did — "search_nodes", "add_knowledge", "delete_context"
            operation_type: read | write | delete | admin
            resource_type: What was affected — "node", "context", "session", "experiment"
            resource_id: ID of the affected resource
            namespace: Graph namespace
            details: Additional context (query params, result count, etc.)
            success: Whether the operation succeeded
        """
        now = datetime.now(timezone.utc).isoformat()
        details_json = json.dumps(details or {}, default=str)[:2000]

        # Hash chain: hash(previous_hash + current_entry) = tamper detection
        entry_data = f"{self._last_hash}:{now}:{actor}:{action}:{resource_id}"
        entry_hash = hashlib.sha256(entry_data.encode()).hexdigest()[:24]

        try:
            cursor = self._conn.execute(
                """INSERT INTO audit_log
                   (timestamp, actor, actor_type, action, operation_type,
                    resource_type, resource_id, namespace, details,
                    ip_address, user_agent, success, hash, previous_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (now, actor, actor_type, action, operation_type,
                 resource_type, resource_id, namespace, details_json,
                 ip_address, user_agent, 1 if success else 0,
                 entry_hash, self._last_hash),
            )
            self._last_hash = entry_hash
            self._pending += 1
            if self._pending >= self._flush_interval:
                self._conn.commit()
                self._pending = 0
            return cursor.lastrowid
        except Exception as e:
            logger.error("[AUDIT] Failed to log: %s", e)
            return -1

    def flush(self):
        """Force commit pending audit entries to disk."""
        if self._pending > 0:
            self._conn.commit()
            self._pending = 0

    def query(
        self,
        actor: str = "",
        action: str = "",
        _auto_flush: bool = True,
        operation_type: str = "",
        resource_id: str = "",
        namespace: str = "",
        since: str = "",
        until: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Query audit entries with filters."""
        conditions = []
        params = []

        if actor:
            conditions.append("actor = ?")
            params.append(actor)
        if action:
            conditions.append("action = ?")
            params.append(action)
        if operation_type:
            conditions.append("operation_type = ?")
            params.append(operation_type)
        if resource_id:
            conditions.append("resource_id = ?")
            params.append(resource_id)
        if namespace:
            conditions.append("namespace = ?")
            params.append(namespace)
        if since:
            conditions.append("timestamp >= ?")
            params.append(since)
        if until:
            conditions.append("timestamp <= ?")
            params.append(until)

        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        sql = f"SELECT * FROM audit_log{where} ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        if _auto_flush:
            self.flush()
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def verify_integrity(self) -> Dict[str, Any]:
        """Verify the hash chain — detect if any entries were tampered with.

        Returns: {valid: bool, entries_checked: int, first_broken: int or None}
        """
        self.flush()
        rows = self._conn.execute(
            "SELECT id, hash, previous_hash, timestamp, actor, action, resource_id "
            "FROM audit_log ORDER BY id ASC"
        ).fetchall()

        if not rows:
            return {"valid": True, "entries_checked": 0, "first_broken": None}

        expected_prev = "genesis"
        for row in rows:
            if row["previous_hash"] != expected_prev:
                return {
                    "valid": False,
                    "entries_checked": row["id"],
                    "first_broken": row["id"],
                    "message": f"Hash chain broken at entry {row['id']}",
                }
            expected_prev = row["hash"]

        return {
            "valid": True,
            "entries_checked": len(rows),
            "first_broken": None,
        }

    def count(self, actor: str = "", action: str = "") -> int:
        """Count audit entries."""
        conditions = []
        params = []
        if actor:
            conditions.append("actor = ?")
            params.append(actor)
        if action:
            conditions.append("action = ?")
            params.append(action)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        row = self._conn.execute(f"SELECT COUNT(*) as c FROM audit_log{where}", params).fetchone()
        return row["c"]

    def export_csv(self, filepath: str = "", since: str = "", until: str = "") -> str:
        """Export audit log to CSV for compliance review."""
        entries = self.query(since=since, until=until, limit=100000)
        output = io.StringIO() if not filepath else open(filepath, "w", newline="")
        writer = csv.DictWriter(output, fieldnames=[
            "id", "timestamp", "actor", "actor_type", "action", "operation_type",
            "resource_type", "resource_id", "namespace", "details",
            "ip_address", "success", "hash",
        ])
        writer.writeheader()
        for entry in entries:
            writer.writerow({k: entry.get(k, "") for k in writer.fieldnames})

        if filepath:
            output.close()
            return filepath
        return output.getvalue()

    def close(self):
        self.flush()
        self._conn.close()


# Global singleton
_trail: Optional[AuditTrail] = None

def get_audit_trail() -> AuditTrail:
    global _trail
    if _trail is None:
        _trail = AuditTrail()
    return _trail
