"""
Tenant Registry
===============
Multi-tenant isolation layer.  Each tenant gets an API key and a set of
namespaces that are invisible to other tenants.

Usage::

    registry = TenantRegistry()
    tenant, api_key = registry.create("acme-corp")
    # Caller uses: Authorization: Bearer <api_key>
    # Internally, namespaces are stored as "{tenant_id}:{namespace}"
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class TenantIdentity:
    """A registered tenant (organisation / user)."""
    tenant_id: str
    name: str
    status: str = "active"  # active, suspended, deleted
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    config: Dict[str, Any] = field(default_factory=dict)
    # config may contain: max_graphs, max_sessions, rate_limit_rpm, etc.
    api_key_hash: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("api_key_hash", None)
        return d

    def ns_prefix(self) -> str:
        """Return the namespace prefix for this tenant."""
        return f"{self.tenant_id}:"

    def scoped_namespace(self, namespace: str) -> str:
        """Convert a user-facing namespace to the internal tenant-scoped form."""
        return f"{self.tenant_id}:{namespace}"

    def unscoped_namespace(self, internal_ns: str) -> Optional[str]:
        """Strip tenant prefix. Returns None if the namespace doesn't belong to this tenant."""
        prefix = self.ns_prefix()
        if internal_ns.startswith(prefix):
            return internal_ns[len(prefix):]
        return None


class TenantRegistry:
    """
    Persistent tenant registry backed by SQLite.
    Same pattern as AgentRegistry in context/agents.py.
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS tenants (
        tenant_id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        status TEXT DEFAULT 'active',
        created_at TEXT NOT NULL,
        config TEXT DEFAULT '{}',
        api_key_hash TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_tenants_name ON tenants(name);
    CREATE INDEX IF NOT EXISTS idx_tenants_status ON tenants(status);

    CREATE TABLE IF NOT EXISTS api_keys (
        key_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        name TEXT NOT NULL,
        key_hash TEXT NOT NULL,
        scope TEXT DEFAULT 'read_write',
        created_by TEXT,
        created_at TEXT NOT NULL,
        last_used_at TEXT,
        expires_at TEXT,
        status TEXT DEFAULT 'active'
    );

    CREATE INDEX IF NOT EXISTS idx_ak_tenant ON api_keys(tenant_id);
    CREATE INDEX IF NOT EXISTS idx_ak_hash ON api_keys(key_hash);
    """

    def __init__(self, db_path: str = "contextcore_data/tenants.db"):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(self._SCHEMA)

    # ------------------------------------------------------------------
    # Tenant CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        name: str,
        config: Optional[Dict[str, Any]] = None,
    ) -> tuple[TenantIdentity, str]:
        """Create a new tenant. Returns (tenant, raw_api_key)."""
        tenant_id = secrets.token_hex(12)
        api_key = secrets.token_urlsafe(32)
        api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()

        tenant = TenantIdentity(
            tenant_id=tenant_id,
            name=name,
            config=config or {},
            api_key_hash=api_key_hash,
        )

        self._conn.execute(
            "INSERT INTO tenants (tenant_id, name, status, created_at, config, api_key_hash) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                tenant.tenant_id,
                tenant.name,
                tenant.status,
                tenant.created_at,
                json.dumps(tenant.config),
                tenant.api_key_hash,
            ),
        )
        self._conn.commit()
        return tenant, api_key

    def get(self, tenant_id: str) -> Optional[TenantIdentity]:
        row = self._conn.execute(
            "SELECT * FROM tenants WHERE tenant_id = ? AND status != 'deleted'",
            (tenant_id,),
        ).fetchone()
        return self._row_to_tenant(row) if row else None

    def get_by_name(self, name: str) -> Optional[TenantIdentity]:
        row = self._conn.execute(
            "SELECT * FROM tenants WHERE name = ? AND status != 'deleted'",
            (name,),
        ).fetchone()
        return self._row_to_tenant(row) if row else None

    def authenticate(self, api_key: str) -> Optional[TenantIdentity]:
        """Validate an API key and return the tenant if valid.

        Scans all active tenants' key hashes. For large deployments, consider
        a key-prefix lookup table instead.
        """
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        row = self._conn.execute(
            "SELECT * FROM tenants WHERE api_key_hash = ? AND status = 'active'",
            (key_hash,),
        ).fetchone()
        return self._row_to_tenant(row) if row else None

    def list_tenants(self, include_inactive: bool = False) -> List[TenantIdentity]:
        if include_inactive:
            rows = self._conn.execute(
                "SELECT * FROM tenants ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM tenants WHERE status = 'active' ORDER BY created_at DESC"
            ).fetchall()
        return [self._row_to_tenant(r) for r in rows]

    def update_status(self, tenant_id: str, status: str) -> bool:
        """Set tenant status: active, suspended, deleted."""
        cur = self._conn.execute(
            "UPDATE tenants SET status = ? WHERE tenant_id = ?",
            (status, tenant_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def rotate_key(self, tenant_id: str) -> Optional[str]:
        """Generate a new API key for a tenant. Returns the new raw key."""
        tenant = self.get(tenant_id)
        if not tenant:
            return None
        new_key = secrets.token_urlsafe(32)
        new_hash = hashlib.sha256(new_key.encode()).hexdigest()
        self._conn.execute(
            "UPDATE tenants SET api_key_hash = ? WHERE tenant_id = ?",
            (new_hash, tenant_id),
        )
        self._conn.commit()
        return new_key

    def delete(self, tenant_id: str) -> bool:
        """Soft-delete a tenant."""
        return self.update_status(tenant_id, "deleted")

    # ------------------------------------------------------------------
    # Scoped API Keys
    # ------------------------------------------------------------------

    VALID_SCOPES = {"read", "read_write", "admin"}

    def create_api_key(self, tenant_id: str, name: str, scope: str = "read_write",
                       created_by: str = None, expires_hours: int = None) -> tuple:
        """Create a scoped API key for a tenant.

        Scopes:
          - read: GET only (queries, searches, context reads)
          - read_write: GET + POST/PUT (create nodes, ingest, run pipelines)
          - admin: full access (delete, manage team, billing)

        Returns (key_id, raw_key, scope).
        """
        if scope not in self.VALID_SCOPES:
            raise ValueError(f"Invalid scope: {scope}. Must be one of: {self.VALID_SCOPES}")

        key_id = secrets.token_hex(8)
        raw_key = secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        now = datetime.now(timezone.utc)

        expires_at = None
        if expires_hours:
            from datetime import timedelta
            expires_at = (now + timedelta(hours=expires_hours)).isoformat()

        self._conn.execute(
            "INSERT INTO api_keys (key_id, tenant_id, name, key_hash, scope, "
            "created_by, created_at, expires_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')",
            (key_id, tenant_id, name, key_hash, scope, created_by, now.isoformat(), expires_at),
        )
        self._conn.commit()
        return key_id, raw_key, scope

    def authenticate_api_key(self, raw_key: str):
        """Validate a scoped API key. Returns (TenantIdentity, scope) or (None, None)."""
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        row = self._conn.execute(
            "SELECT ak.*, t.* FROM api_keys ak "
            "JOIN tenants t ON ak.tenant_id = t.tenant_id "
            "WHERE ak.key_hash = ? AND ak.status = 'active' AND t.status = 'active'",
            (key_hash,),
        ).fetchone()
        if not row:
            return None, None

        # Check expiry
        if row["expires_at"]:
            now = datetime.now(timezone.utc).isoformat()
            if now > row["expires_at"]:
                return None, None

        # Update last_used_at
        self._conn.execute(
            "UPDATE api_keys SET last_used_at = ? WHERE key_id = ?",
            (datetime.now(timezone.utc).isoformat(), row["key_id"]),
        )
        self._conn.commit()

        tenant = self._row_to_tenant(row)
        return tenant, row["scope"]

    def list_api_keys(self, tenant_id: str) -> List[Dict[str, Any]]:
        """List all API keys for a tenant (without hashes)."""
        rows = self._conn.execute(
            "SELECT key_id, name, scope, created_by, created_at, last_used_at, "
            "expires_at, status FROM api_keys WHERE tenant_id = ? ORDER BY created_at DESC",
            (tenant_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def revoke_api_key(self, key_id: str) -> bool:
        """Revoke an API key."""
        cur = self._conn.execute(
            "UPDATE api_keys SET status = 'revoked' WHERE key_id = ?", (key_id,),
        )
        self._conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _row_to_tenant(self, row: sqlite3.Row) -> TenantIdentity:
        return TenantIdentity(
            tenant_id=row["tenant_id"],
            name=row["name"],
            status=row["status"],
            created_at=row["created_at"],
            config=json.loads(row["config"]) if row["config"] else {},
            api_key_hash=row["api_key_hash"],
        )

    def close(self):
        if self._conn:
            self._conn.close()
