"""
Audit Log
=========
Immutable audit trail for admin/security actions.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from contextsynapse.core.db import IS_POSTGRES, PH, connect, dict_cursor, run_ddl, row_to_dict

logger = logging.getLogger(__name__)


@dataclass
class AuditEntry:
    entry_id: str
    timestamp: float
    user_id: str
    action: str
    resource_type: str
    resource_id: str
    details: Dict[str, Any] = field(default_factory=dict)
    ip_address: str = ""

    def to_dict(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "timestamp": self.timestamp,
            "user_id": self.user_id,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "details": self.details,
            "ip_address": self.ip_address,
        }


_SCHEMA = """
    CREATE TABLE IF NOT EXISTS audit_log (
        entry_id TEXT PRIMARY KEY,
        timestamp REAL NOT NULL,
        user_id TEXT NOT NULL,
        action TEXT NOT NULL,
        resource_type TEXT NOT NULL DEFAULT '',
        resource_id TEXT NOT NULL DEFAULT '',
        details TEXT NOT NULL DEFAULT '{}',
        ip_address TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
    CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);
    CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id);
"""


class AuditLog:
    """Append-only audit log backed by SQLite or PostgreSQL."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = str(Path("contextcore_data") / "audit.db")
        if not IS_POSTGRES:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._conn = connect(db_path)
        run_ddl(self._conn, _SCHEMA)

    def _exec(self, sql: str, params: tuple = ()):
        cur = dict_cursor(self._conn)
        cur.execute(sql, params)
        return cur

    def log(
        self,
        user_id: str,
        action: str,
        resource_type: str = "",
        resource_id: str = "",
        details: Optional[Dict[str, Any]] = None,
        ip_address: str = "",
    ) -> AuditEntry:
        entry = AuditEntry(
            entry_id=str(uuid.uuid4()),
            timestamp=time.time(),
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
            ip_address=ip_address,
        )
        self._exec(
            f"INSERT INTO audit_log "
            f"(entry_id, timestamp, user_id, action, resource_type, resource_id, details, ip_address) "
            f"VALUES ({PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH})",
            (
                entry.entry_id,
                entry.timestamp,
                entry.user_id,
                entry.action,
                entry.resource_type,
                entry.resource_id,
                json.dumps(entry.details),
                entry.ip_address,
            ),
        )
        self._conn.commit()
        return entry

    def query(
        self,
        action: Optional[str] = None,
        user_id: Optional[str] = None,
        resource_type: Optional[str] = None,
        since: Optional[float] = None,
        until: Optional[float] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[AuditEntry]:
        clauses = []
        params: list = []

        if action:
            clauses.append(f"action = {PH}")
            params.append(action)
        if user_id:
            clauses.append(f"user_id = {PH}")
            params.append(user_id)
        if resource_type:
            clauses.append(f"resource_type = {PH}")
            params.append(resource_type)
        if since:
            clauses.append(f"timestamp >= {PH}")
            params.append(since)
        if until:
            clauses.append(f"timestamp <= {PH}")
            params.append(until)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM audit_log {where} ORDER BY timestamp DESC LIMIT {PH} OFFSET {PH}"
        params.extend([limit, offset])

        rows = self._exec(sql, tuple(params)).fetchall()

        return [
            AuditEntry(
                entry_id=r["entry_id"],
                timestamp=r["timestamp"],
                user_id=r["user_id"],
                action=r["action"],
                resource_type=r["resource_type"],
                resource_id=r["resource_id"],
                details=json.loads(r["details"]) if r["details"] else {},
                ip_address=r["ip_address"],
            )
            for r in [row_to_dict(r) for r in rows]
        ]

    def count(
        self,
        action: Optional[str] = None,
        user_id: Optional[str] = None,
        resource_type: Optional[str] = None,
    ) -> int:
        clauses = []
        params: list = []
        if action:
            clauses.append(f"action = {PH}")
            params.append(action)
        if user_id:
            clauses.append(f"user_id = {PH}")
            params.append(user_id)
        if resource_type:
            clauses.append(f"resource_type = {PH}")
            params.append(resource_type)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        row = row_to_dict(self._exec(
            f"SELECT COUNT(*) as cnt FROM audit_log {where}", tuple(params)
        ).fetchone())
        return row["cnt"] if row else 0

    def export_csv(self, **filters) -> str:
        """Export audit log as CSV string."""
        entries = self.query(limit=10000, **filters)
        lines = ["entry_id,timestamp,user_id,action,resource_type,resource_id,details,ip_address"]
        for e in entries:
            details_str = json.dumps(e.details).replace('"', '""')
            lines.append(
                f'{e.entry_id},{e.timestamp},{e.user_id},{e.action},'
                f'{e.resource_type},{e.resource_id},"{details_str}",{e.ip_address}'
            )
        return "\n".join(lines)

    def close(self):
        if self._conn:
            self._conn.close()
