"""
User Registry
=============
Self-service user accounts for the AIContextDB SaaS layer.

Each user belongs to one or more tenants (organisations) via the
``user_tenants`` join table.  On signup a personal tenant is auto-created
and the user is set as its owner.

Usage::

    registry = UserRegistry()
    user, jwt = registry.signup("alice@example.com", "s3cret", "Alice")
    user = registry.authenticate("alice@example.com", "s3cret")
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from contextsynapse.core.db import (
    IS_POSTGRES, PH,
    connect, dict_cursor, run_ddl,
    integrity_error, column_exists, row_to_dict, serial_pk,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# bcrypt password hashing (direct, no passlib needed)
# ---------------------------------------------------------------------------
try:
    import bcrypt as _bcrypt
except ImportError:
    _bcrypt = None
    logger.warning("bcrypt not installed — falling back to SHA-256 password hashing")


def _hash_password(password: str) -> str:
    if _bcrypt:
        return _bcrypt.hashpw(password.encode("utf-8")[:72], _bcrypt.gensalt()).decode("utf-8")
    return hashlib.sha256(password.encode()).hexdigest()


def _verify_password(password: str, hashed: str) -> bool:
    if _bcrypt and hashed.startswith("$2"):
        return _bcrypt.checkpw(password.encode("utf-8")[:72], hashed.encode("utf-8"))
    return hashlib.sha256(password.encode()).hexdigest() == hashed


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class UserIdentity:
    """A registered user."""
    user_id: str
    email: str
    display_name: str
    status: str = "active"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_login_at: Optional[str] = None
    config: Dict[str, Any] = field(default_factory=dict)
    is_super_admin: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class UserTenantRole:
    """A user's membership in a tenant."""
    user_id: str
    tenant_id: str
    role: str  # owner, admin, contributor, reader
    joined_at: str


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")

# ---------------------------------------------------------------------------
# Password strength validation
# ---------------------------------------------------------------------------

_COMMON_PASSWORDS = {
    "password", "123456", "12345678", "qwerty", "abc123", "letmein",
    "admin", "welcome", "monkey", "dragon", "master", "login",
    "password1", "123456789", "1234567890",
}


def validate_password(password: str) -> Optional[str]:
    """Return error message if password is weak, None if acceptable.

    Rules (production):
      - Minimum 8 characters
      - At least 1 uppercase, 1 lowercase, 1 digit
      - Not in common password list

    For testing/dev (AICONTEXTDB_ENV=dev), relaxes to 6 chars min.
    """
    import os
    is_dev = os.environ.get("CONTEXTSYNAPSE_ENV") or os.environ.get("AICONTEXTDB_ENV", "").lower() in ("dev", "development", "test")
    min_len = 6 if is_dev else 8

    if len(password) < min_len:
        return f"Password must be at least {min_len} characters"
    if password.lower() in _COMMON_PASSWORDS:
        return "Password is too common — choose something more unique"
    if not is_dev:
        if not re.search(r"[A-Z]", password):
            return "Password must contain at least one uppercase letter"
        if not re.search(r"[a-z]", password):
            return "Password must contain at least one lowercase letter"
        if not re.search(r"[0-9]", password):
            return "Password must contain at least one digit"
    return None


class UserRegistry:
    """Persistent user registry backed by SQLite or PostgreSQL."""

    @staticmethod
    def _schema() -> str:
        return f"""
    CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        display_name TEXT NOT NULL,
        status TEXT DEFAULT 'active',
        created_at TEXT NOT NULL,
        last_login_at TEXT,
        config TEXT DEFAULT '{{}}',
        is_super_admin INTEGER DEFAULT 0
    );

    CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
    CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);

    CREATE TABLE IF NOT EXISTS user_tenants (
        user_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL,
        role TEXT DEFAULT 'owner',
        joined_at TEXT NOT NULL,
        PRIMARY KEY (user_id, tenant_id)
    );

    CREATE INDEX IF NOT EXISTS idx_ut_user ON user_tenants(user_id);
    CREATE INDEX IF NOT EXISTS idx_ut_tenant ON user_tenants(tenant_id);

    CREATE TABLE IF NOT EXISTS team_invitations (
        invite_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        email TEXT NOT NULL,
        role TEXT DEFAULT 'member',
        invited_by TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        created_at TEXT NOT NULL,
        UNIQUE(tenant_id, email)
    );

    CREATE INDEX IF NOT EXISTS idx_inv_tenant ON team_invitations(tenant_id);
    CREATE INDEX IF NOT EXISTS idx_inv_email ON team_invitations(email);

    CREATE TABLE IF NOT EXISTS login_attempts (
        id {serial_pk()},
        email TEXT NOT NULL,
        ip_address TEXT,
        success INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_la_email ON login_attempts(email);
    CREATE INDEX IF NOT EXISTS idx_la_created ON login_attempts(created_at);

    CREATE TABLE IF NOT EXISTS email_verifications (
        token TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        verified_at TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_ev_user ON email_verifications(user_id);
    """

    def __init__(self, db_path: str = "contextcore_data/users.db"):
        self._db_path = Path(db_path)
        if not IS_POSTGRES:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = connect(str(self._db_path))
        run_ddl(self._conn, self._schema())
        self._migrate()

    def _exec(self, sql: str, params: tuple = ()):
        """Execute SQL and return cursor (works for both SQLite and Postgres)."""
        cur = dict_cursor(self._conn)
        cur.execute(sql, params)
        return cur

    def _commit(self):
        self._conn.commit()

    def _migrate(self):
        """Run lightweight schema migrations for existing DBs."""
        if not column_exists(self._conn, "users", "is_super_admin"):
            self._exec(f"ALTER TABLE users ADD COLUMN is_super_admin INTEGER DEFAULT 0")
            self._commit()
        # Normalize legacy role names: viewer→reader, member→contributor
        self._exec("UPDATE user_tenants SET role = 'reader' WHERE role = 'viewer'")
        self._exec("UPDATE user_tenants SET role = 'contributor' WHERE role = 'member'")
        self._commit()

    def ensure_admin(
        self,
        email: str = "admin@contextsynapse.local",
        password: str = "Admin123!",
        display_name: str = "Admin",
    ) -> Optional[UserIdentity]:
        """Ensure a default admin user exists. Creates one if not found.

        Called on first startup so there's always a way to log in.
        Returns the admin user, or None if already exists.
        """
        import os
        email = os.environ.get("CONTEXTCORE_ADMIN_EMAIL", email)
        password = os.environ.get("CONTEXTCORE_ADMIN_PASSWORD", password)
        display_name = os.environ.get("CONTEXTCORE_ADMIN_NAME", display_name)

        cur = self._exec(f"SELECT user_id FROM users WHERE email = {PH}", (email,))
        row = cur.fetchone()
        if row:
            return None

        user_id = secrets.token_hex(12)
        password_hash = _hash_password(password)
        now = datetime.now(timezone.utc).isoformat()

        try:
            self._exec(
                f"INSERT INTO users (user_id, email, password_hash, display_name, status, created_at, is_super_admin) "
                f"VALUES ({PH}, {PH}, {PH}, {PH}, 'active', {PH}, 1)",
                (user_id, email, password_hash, display_name, now),
            )
            self._commit()
            logger.info("Default admin created: %s (change password after first login)", email)
            return UserIdentity(
                user_id=user_id, email=email, display_name=display_name,
                created_at=now, is_super_admin=True,
            )
        except Exception as e:
            logger.debug("Admin user creation skipped: %s", e)
            return None

    # ------------------------------------------------------------------
    # User CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        email: str,
        password: str,
        display_name: str,
    ) -> UserIdentity:
        """Create a new user.  Raises ValueError on invalid input or duplicate email."""
        email = email.strip().lower()
        if not _EMAIL_RE.match(email):
            raise ValueError("Invalid email address")
        pwd_err = validate_password(password)
        if pwd_err:
            raise ValueError(pwd_err)
        display_name = display_name.strip()
        if not display_name:
            raise ValueError("Display name is required")

        user_id = secrets.token_hex(12)
        password_hash = _hash_password(password)
        now = datetime.now(timezone.utc).isoformat()

        try:
            self._exec(
                f"INSERT INTO users (user_id, email, password_hash, display_name, status, created_at) "
                f"VALUES ({PH}, {PH}, {PH}, {PH}, 'active', {PH})",
                (user_id, email, password_hash, display_name, now),
            )
            self._commit()
        except integrity_error():
            raise ValueError("A user with this email already exists")

        return UserIdentity(
            user_id=user_id,
            email=email,
            display_name=display_name,
            created_at=now,
        )

    # Max failed login attempts before lockout (within window)
    MAX_FAILED_ATTEMPTS = 5
    LOCKOUT_WINDOW_MINUTES = 15

    def authenticate(self, email: str, password: str,
                     ip_address: str = None) -> Optional[UserIdentity]:
        """Validate email + password with brute-force protection.

        Returns user if valid, None if bad credentials.
        Raises ValueError if account is locked out.
        """
        email = email.strip().lower()
        now = datetime.now(timezone.utc).isoformat()

        # Check for lockout (too many failed attempts)
        if self._is_locked_out(email):
            raise ValueError(
                f"Account temporarily locked — too many failed attempts. "
                f"Try again in {self.LOCKOUT_WINDOW_MINUTES} minutes."
            )

        row = row_to_dict(self._exec(
            f"SELECT * FROM users WHERE email = {PH} AND status = 'active'",
            (email,),
        ).fetchone())

        if not row or not _verify_password(password, row["password_hash"]):
            # Record failed attempt
            self._record_login_attempt(email, ip_address, success=False)
            return None

        # Success — record and clear lockout
        self._record_login_attempt(email, ip_address, success=True)

        # Update last_login_at
        self._exec(
            f"UPDATE users SET last_login_at = {PH} WHERE user_id = {PH}",
            (now, row["user_id"]),
        )
        self._commit()

        user = self._row_to_user(row)
        user.last_login_at = now
        return user

    def _is_locked_out(self, email: str) -> bool:
        """Check if too many failed login attempts in the lockout window."""
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=self.LOCKOUT_WINDOW_MINUTES)).isoformat()
        row = row_to_dict(self._exec(
            f"SELECT COUNT(*) as cnt FROM login_attempts "
            f"WHERE email = {PH} AND success = 0 AND created_at > {PH}",
            (email, cutoff),
        ).fetchone())
        return (row["cnt"] >= self.MAX_FAILED_ATTEMPTS) if row else False

    def _record_login_attempt(self, email: str, ip_address: str = None,
                               success: bool = False):
        """Record a login attempt for rate limiting."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            self._exec(
                f"INSERT INTO login_attempts (email, ip_address, success, created_at) "
                f"VALUES ({PH}, {PH}, {PH}, {PH})",
                (email, ip_address, 1 if success else 0, now),
            )
            # Clean up old attempts (older than 24h)
            from datetime import timedelta
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            self._exec(f"DELETE FROM login_attempts WHERE created_at < {PH}", (cutoff,))
            self._commit()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Email verification
    # ------------------------------------------------------------------

    def create_verification_token(self, user_id: str, expires_hours: int = 24) -> str:
        """Create an email verification token. Returns the raw token."""
        from datetime import timedelta
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=expires_hours)
        self._exec(
            f"INSERT INTO email_verifications (token, user_id, created_at, expires_at) "
            f"VALUES ({PH}, {PH}, {PH}, {PH})",
            (token, user_id, now.isoformat(), expires.isoformat()),
        )
        self._commit()
        return token

    def verify_email(self, token: str) -> Optional[str]:
        """Verify an email token. Returns user_id if valid, None otherwise."""
        now = datetime.now(timezone.utc).isoformat()
        row = row_to_dict(self._exec(
            f"SELECT * FROM email_verifications "
            f"WHERE token = {PH} AND verified_at IS NULL AND expires_at > {PH}",
            (token, now),
        ).fetchone())
        if not row:
            return None
        user_id = row["user_id"]
        self._exec(
            f"UPDATE email_verifications SET verified_at = {PH} WHERE token = {PH}",
            (now, token),
        )
        # Mark user as email-verified in config
        user = self.get(user_id)
        if user:
            config = dict(user.config)
            config["email_verified"] = True
            config["email_verified_at"] = now
            self._exec(
                f"UPDATE users SET config = {PH} WHERE user_id = {PH}",
                (json.dumps(config), user_id),
            )
        self._commit()
        return user_id

    def is_email_verified(self, user_id: str) -> bool:
        """Check if a user's email is verified."""
        user = self.get(user_id)
        if not user:
            return False
        return user.config.get("email_verified", False)

    def get(self, user_id: str) -> Optional[UserIdentity]:
        row = row_to_dict(self._exec(
            f"SELECT * FROM users WHERE user_id = {PH} AND status != 'deleted'",
            (user_id,),
        ).fetchone())
        return self._row_to_user(row) if row else None

    def get_by_email(self, email: str) -> Optional[UserIdentity]:
        email = email.strip().lower()
        row = row_to_dict(self._exec(
            f"SELECT * FROM users WHERE email = {PH} AND status != 'deleted'",
            (email,),
        ).fetchone())
        return self._row_to_user(row) if row else None

    def update(self, user_id: str, display_name: Optional[str] = None,
               password: Optional[str] = None) -> Optional[UserIdentity]:
        """Update user profile fields."""
        user = self.get(user_id)
        if not user:
            return None

        if display_name:
            self._exec(
                f"UPDATE users SET display_name = {PH} WHERE user_id = {PH}",
                (display_name.strip(), user_id),
            )
        if password:
            if len(password) < 6:
                raise ValueError("Password must be at least 6 characters")
            self._exec(
                f"UPDATE users SET password_hash = {PH} WHERE user_id = {PH}",
                (_hash_password(password), user_id),
            )
        self._commit()
        return self.get(user_id)

    # ------------------------------------------------------------------
    # User ↔ Tenant membership
    # ------------------------------------------------------------------

    def link_tenant(self, user_id: str, tenant_id: str, role: str = "owner") -> None:
        """Associate a user with a tenant."""
        now = datetime.now(timezone.utc).isoformat()
        if IS_POSTGRES:
            self._exec(
                f"INSERT INTO user_tenants (user_id, tenant_id, role, joined_at) "
                f"VALUES ({PH}, {PH}, {PH}, {PH}) "
                f"ON CONFLICT (user_id, tenant_id) DO UPDATE SET role = EXCLUDED.role, joined_at = EXCLUDED.joined_at",
                (user_id, tenant_id, role, now),
            )
        else:
            self._exec(
                f"INSERT OR REPLACE INTO user_tenants (user_id, tenant_id, role, joined_at) "
                f"VALUES ({PH}, {PH}, {PH}, {PH})",
                (user_id, tenant_id, role, now),
            )
        self._commit()

    def get_user_tenants(self, user_id: str) -> List[UserTenantRole]:
        """Get all tenants a user belongs to."""
        rows = self._exec(
            f"SELECT * FROM user_tenants WHERE user_id = {PH}",
            (user_id,),
        ).fetchall()
        return [
            UserTenantRole(
                user_id=r["user_id"],
                tenant_id=r["tenant_id"],
                role=r["role"],
                joined_at=r["joined_at"],
            )
            for r in [row_to_dict(r) for r in rows]
        ]

    def get_tenant_members(self, tenant_id: str) -> List[UserTenantRole]:
        """Get all users in a tenant."""
        rows = self._exec(
            f"SELECT * FROM user_tenants WHERE tenant_id = {PH}",
            (tenant_id,),
        ).fetchall()
        return [
            UserTenantRole(
                user_id=r["user_id"],
                tenant_id=r["tenant_id"],
                role=r["role"],
                joined_at=r["joined_at"],
            )
            for r in [row_to_dict(r) for r in rows]
        ]

    def get_primary_tenant_id(self, user_id: str) -> Optional[str]:
        """Get the first (owner) tenant for a user."""
        row = row_to_dict(self._exec(
            f"SELECT tenant_id FROM user_tenants WHERE user_id = {PH} AND role = 'owner' LIMIT 1",
            (user_id,),
        ).fetchone())
        if row:
            return row["tenant_id"]
        # Fallback to any tenant
        row = row_to_dict(self._exec(
            f"SELECT tenant_id FROM user_tenants WHERE user_id = {PH} LIMIT 1",
            (user_id,),
        ).fetchone())
        return row["tenant_id"] if row else None

    # ------------------------------------------------------------------
    # Team invitations
    # ------------------------------------------------------------------

    def create_invitation(self, tenant_id: str, email: str, role: str, invited_by: str) -> Dict[str, Any]:
        """Create a team invitation. Returns the invitation dict."""
        email = email.strip().lower()
        invite_id = secrets.token_hex(12)
        now = datetime.now(timezone.utc).isoformat()

        try:
            self._exec(
                f"INSERT INTO team_invitations (invite_id, tenant_id, email, role, invited_by, status, created_at) "
                f"VALUES ({PH}, {PH}, {PH}, {PH}, {PH}, 'pending', {PH})",
                (invite_id, tenant_id, email, role, invited_by, now),
            )
            self._commit()
        except integrity_error():
            raise ValueError("An invitation for this email already exists")

        return {"invite_id": invite_id, "tenant_id": tenant_id, "email": email, "role": role, "status": "pending", "created_at": now}

    def list_invitations(self, tenant_id: str) -> List[Dict[str, Any]]:
        """List all pending invitations for a tenant."""
        rows = self._exec(
            f"SELECT * FROM team_invitations WHERE tenant_id = {PH} AND status = 'pending' ORDER BY created_at DESC",
            (tenant_id,),
        ).fetchall()
        return [row_to_dict(r) for r in rows]

    def accept_invitation(self, invite_id: str, user_id: str) -> bool:
        """Accept an invitation — link user to tenant."""
        row = row_to_dict(self._exec(
            f"SELECT * FROM team_invitations WHERE invite_id = {PH} AND status = 'pending'",
            (invite_id,),
        ).fetchone())
        if not row:
            return False

        self.link_tenant(user_id, row["tenant_id"], role=row["role"])
        self._exec(
            f"UPDATE team_invitations SET status = 'accepted' WHERE invite_id = {PH}",
            (invite_id,),
        )
        self._commit()
        return True

    def revoke_invitation(self, invite_id: str) -> bool:
        """Revoke a pending invitation."""
        cur = self._exec(
            f"UPDATE team_invitations SET status = 'revoked' WHERE invite_id = {PH} AND status = 'pending'",
            (invite_id,),
        )
        self._commit()
        return cur.rowcount > 0

    def remove_member(self, tenant_id: str, user_id: str) -> bool:
        """Remove a user from a tenant."""
        cur = self._exec(
            f"DELETE FROM user_tenants WHERE tenant_id = {PH} AND user_id = {PH} AND role != 'owner'",
            (tenant_id, user_id),
        )
        self._commit()
        return cur.rowcount > 0

    def update_member_role(self, tenant_id: str, user_id: str, role: str) -> bool:
        """Update a member's role (cannot change owner)."""
        cur = self._exec(
            f"UPDATE user_tenants SET role = {PH} WHERE tenant_id = {PH} AND user_id = {PH} AND role != 'owner'",
            (role, tenant_id, user_id),
        )
        self._commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _row_to_user(self, row) -> UserIdentity:
        d = row_to_dict(row) if not isinstance(row, dict) else row
        return UserIdentity(
            user_id=d["user_id"],
            email=d["email"],
            display_name=d["display_name"],
            status=d["status"],
            created_at=d["created_at"],
            last_login_at=d.get("last_login_at"),
            config=json.loads(d["config"]) if d.get("config") else {},
            is_super_admin=bool(d.get("is_super_admin", 0)),
        )

    def set_super_admin(self, user_id: str, is_super_admin: bool = True) -> bool:
        """Promote or demote a user to/from super_admin (platform-wide)."""
        cur = self._exec(
            f"UPDATE users SET is_super_admin = {PH} WHERE user_id = {PH}",
            (1 if is_super_admin else 0, user_id),
        )
        self._commit()
        return cur.rowcount > 0

    def close(self):
        if self._conn:
            self._conn.close()
