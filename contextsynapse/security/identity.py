"""UnifiedIdentity — cross-vertical user identity with PostgreSQL backing.

A single user can have different roles in different verticals:
  - Alice: fund_manager in PMS, compliance_officer in MF
  - Bob: operations in both PMS and MF
  - Charlie: client_viewer in PMS only

Identity resolution:
  JWT token -> user_id -> user_roles table -> vertical-specific permissions
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from contextsynapse.core.db import (
    IS_POSTGRES, PH, connect, dict_cursor, run_ddl, row_to_dict,
)
from contextsynapse.api.auth import create_jwt, verify_jwt

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Password hashing (PBKDF2-SHA256 — no external dependency)
# ---------------------------------------------------------------------------

_PBKDF2_ITERATIONS = 260_000


def _hash_password(password: str) -> str:
    """Hash a password with PBKDF2-SHA256 and a random salt."""
    salt = secrets.token_bytes(32)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2:{salt.hex()}:{dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    """Verify a password against a stored PBKDF2 hash."""
    try:
        _, salt_hex, dk_hex = stored.split(":", 2)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(dk_hex)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
        return secrets.compare_digest(actual, expected)
    except (ValueError, AttributeError):
        return False


# ---------------------------------------------------------------------------
# DDL — tables are created by contextcore/db/schema.sql in production;
# this DDL is a fallback for SQLite / dev environments.
# ---------------------------------------------------------------------------

_ENSURE_TABLES = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    tenant_id TEXT,
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    password_hash TEXT,
    phone TEXT,
    pan TEXT,
    avatar_url TEXT,
    auth_provider TEXT DEFAULT 'local',
    auth_provider_id TEXT,
    status TEXT DEFAULT 'active',
    last_login_at TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS user_roles (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    vertical TEXT NOT NULL,
    role TEXT NOT NULL,
    scoped_ids TEXT DEFAULT '[]',
    granted_by TEXT,
    granted_at TEXT,
    UNIQUE(user_id, tenant_id, vertical, role)
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    ip_address TEXT,
    user_agent TEXT,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    created_at TEXT
);
"""


class IdentityService:
    """Unified identity management across all verticals.

    Handles user CRUD, role assignment with vertical scoping,
    JWT token issuance/verification, and session tracking.
    """

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or "contextsynapse.db"
        if not IS_POSTGRES:
            conn = connect(self._db_path)
            run_ddl(conn, _ENSURE_TABLES)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _conn(self):
        return connect(self._db_path)

    def _exec(self, sql: str, params: tuple = ()):
        conn = self._conn()
        cur = dict_cursor(conn)
        cur.execute(sql, params)
        conn.commit()
        return cur

    def _fetchone(self, sql: str, params: tuple = ()) -> Optional[dict]:
        conn = self._conn()
        cur = dict_cursor(conn)
        cur.execute(sql, params)
        return row_to_dict(cur.fetchone())

    def _fetchall(self, sql: str, params: tuple = ()) -> List[dict]:
        conn = self._conn()
        cur = dict_cursor(conn)
        cur.execute(sql, params)
        return [row_to_dict(r) for r in cur.fetchall() if r is not None]

    # ------------------------------------------------------------------
    # User CRUD
    # ------------------------------------------------------------------

    def create_user(
        self,
        tenant_id: str,
        email: str,
        name: str,
        password: Optional[str] = None,
        phone: Optional[str] = None,
        pan: Optional[str] = None,
        auth_provider: str = "local",
    ) -> dict:
        """Create a new user and return the user dict with id."""
        user_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        pw_hash = _hash_password(password) if password else None

        self._exec(
            f"INSERT INTO users (id, tenant_id, email, name, password_hash, "
            f"phone, pan, auth_provider, status, created_at, updated_at) "
            f"VALUES ({PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH}, 'active', {PH}, {PH})",
            (user_id, tenant_id, email.lower().strip(), name, pw_hash,
             phone, pan, auth_provider, now, now),
        )
        logger.info("Created user %s (%s) in tenant %s", user_id, email, tenant_id)
        return self.get_user(user_id)  # type: ignore[return-value]

    def authenticate(self, email: str, password: str) -> Optional[dict]:
        """Validate credentials. Returns user dict on success, None on failure."""
        row = self._fetchone(
            f"SELECT * FROM users WHERE email = {PH} AND status = 'active'",
            (email.lower().strip(),),
        )
        if row is None:
            return None
        stored_hash = row.get("password_hash")
        if not stored_hash:
            return None
        if not _verify_password(password, stored_hash):
            return None

        # Update last_login_at
        now = datetime.now(timezone.utc).isoformat()
        try:
            self._exec(
                f"UPDATE users SET last_login_at = {PH}, updated_at = {PH} WHERE id = {PH}",
                (now, now, row["id"]),
            )
        except Exception:
            pass  # non-critical

        return self._user_with_roles(row)

    def get_user(self, user_id: str) -> Optional[dict]:
        """Get user by ID, including all assigned roles."""
        row = self._fetchone(
            f"SELECT * FROM users WHERE id = {PH}", (user_id,)
        )
        if row is None:
            return None
        return self._user_with_roles(row)

    def get_user_by_email(self, email: str) -> Optional[dict]:
        """Get user by email, including all assigned roles."""
        row = self._fetchone(
            f"SELECT * FROM users WHERE email = {PH}", (email.lower().strip(),)
        )
        if row is None:
            return None
        return self._user_with_roles(row)

    def list_users(self, tenant_id: str) -> List[dict]:
        """List all users belonging to a tenant."""
        rows = self._fetchall(
            f"SELECT * FROM users WHERE tenant_id = {PH} ORDER BY created_at DESC",
            (tenant_id,),
        )
        return [self._user_with_roles(r) for r in rows]

    def _user_with_roles(self, row: dict) -> dict:
        """Attach roles to a user dict and strip sensitive fields."""
        user = {
            "id": row["id"],
            "tenant_id": row.get("tenant_id"),
            "email": row["email"],
            "name": row["name"],
            "phone": row.get("phone"),
            "pan": row.get("pan"),
            "auth_provider": row.get("auth_provider", "local"),
            "status": row.get("status", "active"),
            "last_login_at": row.get("last_login_at"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }
        user["roles"] = self.get_user_roles(row["id"])
        return user

    # ------------------------------------------------------------------
    # Role management
    # ------------------------------------------------------------------

    def assign_role(
        self,
        user_id: str,
        tenant_id: str,
        vertical: str,
        role: str,
        scoped_ids: Optional[List[str]] = None,
        granted_by: Optional[str] = None,
    ) -> dict:
        """Assign a vertical-specific role to a user. Returns the role entry."""
        role_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        scoped = self._encode_scoped_ids(scoped_ids)

        self._exec(
            f"INSERT INTO user_roles (id, user_id, tenant_id, vertical, role, "
            f"scoped_ids, granted_by, granted_at) "
            f"VALUES ({PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH})",
            (role_id, user_id, tenant_id, vertical, role, scoped, granted_by, now),
        )
        logger.info(
            "Assigned role %s/%s to user %s in tenant %s (granted_by=%s)",
            vertical, role, user_id, tenant_id, granted_by,
        )
        return {
            "id": role_id,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "vertical": vertical,
            "role": role,
            "scoped_ids": scoped_ids or [],
            "granted_by": granted_by,
            "granted_at": now,
        }

    def revoke_role(self, user_id: str, tenant_id: str, vertical: str, role: str) -> bool:
        """Remove a role assignment. Returns True if a row was deleted."""
        cur = self._exec(
            f"DELETE FROM user_roles "
            f"WHERE user_id = {PH} AND tenant_id = {PH} AND vertical = {PH} AND role = {PH}",
            (user_id, tenant_id, vertical, role),
        )
        deleted = cur.rowcount > 0
        if deleted:
            logger.info("Revoked role %s/%s from user %s", vertical, role, user_id)
        return deleted

    def get_user_roles(
        self,
        user_id: str,
        tenant_id: Optional[str] = None,
        vertical: Optional[str] = None,
    ) -> List[dict]:
        """Get roles for a user, optionally filtered by tenant and/or vertical."""
        sql = f"SELECT * FROM user_roles WHERE user_id = {PH}"
        params: list = [user_id]

        if tenant_id is not None:
            sql += f" AND tenant_id = {PH}"
            params.append(tenant_id)
        if vertical is not None:
            sql += f" AND vertical = {PH}"
            params.append(vertical)

        rows = self._fetchall(sql, tuple(params))
        return [
            {
                "vertical": r["vertical"],
                "role": r["role"],
                "scoped_ids": self._decode_scoped_ids(r.get("scoped_ids")),
                "tenant_id": r.get("tenant_id"),
                "granted_by": r.get("granted_by"),
                "granted_at": r.get("granted_at"),
            }
            for r in rows
        ]

    def get_scoped_ids(self, user_id: str, tenant_id: str, vertical: str) -> List[str]:
        """Return the combined scoped IDs (portfolio_ids, scheme_ids) for a user in a vertical.

        If a user has multiple roles in the same vertical, the scoped_ids are merged.
        """
        roles = self.get_user_roles(user_id, tenant_id=tenant_id, vertical=vertical)
        all_ids: set = set()
        for r in roles:
            all_ids.update(r.get("scoped_ids") or [])
        return sorted(all_ids)

    # ------------------------------------------------------------------
    # JWT tokens
    # ------------------------------------------------------------------

    def issue_token(
        self,
        user_id: str,
        tenant_id: str,
        vertical: str,
        expires_in: int = 86400,
    ) -> str:
        """Issue a JWT for a user scoped to a specific vertical.

        The token carries:
          - sub (user_id), tenant_id, vertical
          - role (primary role in that vertical)
          - scoped_ids (portfolio/scheme IDs the user can access)
          - type: "identity"
        """
        roles = self.get_user_roles(user_id, tenant_id=tenant_id, vertical=vertical)
        primary_role = roles[0]["role"] if roles else "viewer"
        scoped_ids = self.get_scoped_ids(user_id, tenant_id, vertical)

        payload = {
            "sub": user_id,
            "tenant_id": tenant_id,
            "vertical": vertical,
            "role": primary_role,
            "scoped_ids": scoped_ids,
            "type": "identity",
        }
        return create_jwt(payload, expires_in=expires_in)

    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Validate a JWT and return the decoded payload, or None if invalid/expired."""
        return verify_jwt(token)

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def record_session(
        self,
        user_id: str,
        token: str,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> str:
        """Record an active session. Returns the session ID."""
        session_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

        # Default expiry: 24h from now
        exp_ts = int(time.time()) + 86400
        expires_at = datetime.fromtimestamp(exp_ts, tz=timezone.utc).isoformat()

        self._exec(
            f"INSERT INTO sessions (id, user_id, token_hash, ip_address, user_agent, "
            f"expires_at, created_at) "
            f"VALUES ({PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH})",
            (session_id, user_id, token_hash, ip, user_agent, expires_at, now),
        )
        return session_id

    def revoke_session(self, session_id: str) -> bool:
        """Mark a session as revoked. Returns True if found."""
        now = datetime.now(timezone.utc).isoformat()
        cur = self._exec(
            f"UPDATE sessions SET revoked_at = {PH} WHERE id = {PH} AND revoked_at IS NULL",
            (now, session_id),
        )
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Scoped-IDs encoding (PostgreSQL TEXT[] vs SQLite JSON string)
    # ------------------------------------------------------------------

    @staticmethod
    def _encode_scoped_ids(ids: Optional[List[str]]) -> Any:
        """Encode scoped_ids for storage.

        PostgreSQL: native TEXT[] via list.
        SQLite: JSON string.
        """
        if ids is None:
            ids = []
        if IS_POSTGRES:
            return ids
        import json
        return json.dumps(ids)

    @staticmethod
    def _decode_scoped_ids(raw: Any) -> List[str]:
        """Decode scoped_ids from storage."""
        if raw is None:
            return []
        if isinstance(raw, list):
            return raw
        if isinstance(raw, str):
            try:
                import json
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return parsed
            except (ValueError, TypeError):
                pass
            # PostgreSQL TEXT[] string representation: {a,b,c}
            if raw.startswith("{") and raw.endswith("}"):
                inner = raw[1:-1]
                if not inner:
                    return []
                return [s.strip('"') for s in inner.split(",")]
        return []
