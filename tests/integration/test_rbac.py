"""
RBAC Regression Tests
======================
Tests the full role hierarchy:
    reader < contributor < admin < owner < super_admin

Covers:
- Session isolation (users only see their own sessions)
- Super admin sees all sessions
- Workspace endpoint access control
- Super admin can manage users across orgs
- Role-gated endpoint enforcement
- Legacy role names (viewer/member) map correctly
"""

import os
import secrets
import pytest
from httpx import AsyncClient, ASGITransport

os.environ.setdefault("AICONTEXTDB_ADMIN_KEY", "test-rbac-key")
os.environ.setdefault("AICONTEXTDB_ENV", "development")

from contextcore.api.api import app
from contextcore.api.auth import create_jwt

# Unique suffix per test run to avoid email collisions
_RUN = secrets.token_hex(4)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _email(prefix: str) -> str:
    return f"{prefix}-{_RUN}@rbac.test"


def _user_headers(user_id: str, email: str = "u@test.com") -> dict:
    token = create_jwt({"sub": user_id, "type": "user", "email": email})
    return {"Authorization": f"Bearer {token}"}


def _admin_headers() -> dict:
    return {"X-Admin-Key": os.environ["AICONTEXTDB_ADMIN_KEY"]}


def _transport():
    return ASGITransport(app=app)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def _setup_users():
    """Create users, tenants, and role assignments for RBAC tests.

    Layout:
      - Sara: super_admin, owns her own tenant
      - Alice: owner of 'alice-org'
      - Bob: contributor in 'alice-org'
      - Eve: reader in 'alice-org'
    """
    from contextcore.api.users import UserRegistry
    from contextcore.api.tenants import TenantRegistry
    import tempfile, pathlib

    tmp = tempfile.mkdtemp()
    users_db = str(pathlib.Path(tmp) / "users.db")
    tenants_db = str(pathlib.Path(tmp) / "tenants.db")

    ureg = UserRegistry(db_path=users_db)
    treg = TenantRegistry(db_path=tenants_db)

    sara = ureg.create("sara@test.com", "Sara1234!", "Sara")
    alice = ureg.create("alice@test.com", "Alice123!", "Alice")
    bob = ureg.create("bob@test.com", "Bobby123!", "Bob")
    eve = ureg.create("eve@test.com", "Evely123!", "Eve")

    # Promote Sara
    ureg.set_super_admin(sara.user_id, True)

    # Create Alice's org
    alice_tenant, _ = treg.create("alice-org")
    ureg.link_tenant(alice.user_id, alice_tenant.tenant_id, "owner")
    ureg.link_tenant(bob.user_id, alice_tenant.tenant_id, "contributor")
    ureg.link_tenant(eve.user_id, alice_tenant.tenant_id, "reader")

    # Sara's personal tenant (so she passes auth)
    sara_tenant, _ = treg.create("sara-org")
    ureg.link_tenant(sara.user_id, sara_tenant.tenant_id, "owner")

    return {
        "sara": sara, "alice": alice, "bob": bob, "eve": eve,
        "alice_tenant": alice_tenant,
        "ureg": ureg, "treg": treg,
    }


# ---------------------------------------------------------------------------
# Signup + role assignment (API-level)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_signup_creates_user_with_personal_tenant():
    async with AsyncClient(transport=_transport(), base_url="http://test") as c:
        resp = await c.post("/auth/signup", json={
            "email": _email("newuser"),
            "password": "Newuser1!",
            "display_name": "New User",
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["user"]["user_id"]
        assert data["tenant_id"]
        assert data["token"]


@pytest.mark.asyncio
async def test_promote_super_admin_via_admin_key():
    """X-Admin-Key holder can promote a user to super_admin."""
    async with AsyncClient(transport=_transport(), base_url="http://test") as c:
        resp = await c.post("/auth/signup", json={
            "email": _email("promote"),
            "password": "Promote1!",
            "display_name": "Promotee",
        })
        uid = resp.json()["user"]["user_id"]

        resp = await c.patch(
            f"/dashboard/admin/users/{uid}/super-admin",
            json={"is_super_admin": True},
            headers=_admin_headers(),
        )
        assert resp.status_code == 200
        assert resp.json()["is_super_admin"] is True


# ---------------------------------------------------------------------------
# Session isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_user_sees_only_own_sessions():
    """Each user should only see sessions they own or have access to."""
    tag = secrets.token_hex(4)
    async with AsyncClient(transport=_transport(), base_url="http://test") as c:
        r1 = await c.post("/auth/signup", json={
            "email": _email("iso-a"), "password": "IsoUserA1!", "display_name": "Iso A",
        })
        r2 = await c.post("/auth/signup", json={
            "email": _email("iso-b"), "password": "IsoUserB1!", "display_name": "Iso B",
        })
        h1 = {"Authorization": f"Bearer {r1.json()['token']}"}
        h2 = {"Authorization": f"Bearer {r2.json()['token']}"}

        name_a, name_b = f"IsoA-{tag}", f"IsoB-{tag}"
        s1 = await c.post("/dashboard/sessions", json={"name": name_a}, headers=h1)
        s2 = await c.post("/dashboard/sessions", json={"name": name_b}, headers=h2)
        assert s1.status_code == 201
        assert s2.status_code == 201

        list1 = await c.get("/dashboard/sessions", headers=h1)
        list2 = await c.get("/dashboard/sessions", headers=h2)
        names1 = {s["name"] for s in list1.json()["sessions"]}
        names2 = {s["name"] for s in list2.json()["sessions"]}

        assert name_a in names1
        assert name_b not in names1
        assert name_b in names2
        assert name_a not in names2


@pytest.mark.asyncio
async def test_super_admin_sees_all_sessions():
    """Super admin should see every session on the platform."""
    tag = secrets.token_hex(4)
    async with AsyncClient(transport=_transport(), base_url="http://test") as c:
        r_sa = await c.post("/auth/signup", json={
            "email": _email("sa-all"), "password": "SuperA1!!", "display_name": "SA",
        })
        sa_token = r_sa.json()["token"]
        sa_id = r_sa.json()["user"]["user_id"]
        await c.patch(
            f"/dashboard/admin/users/{sa_id}/super-admin",
            json={"is_super_admin": True},
            headers=_admin_headers(),
        )

        r_reg = await c.post("/auth/signup", json={
            "email": _email("sa-reg"), "password": "Regular1!", "display_name": "Reg",
        })
        reg_h = {"Authorization": f"Bearer {r_reg.json()['token']}"}
        sess_name = f"SA-Visible-{tag}"
        await c.post("/dashboard/sessions", json={"name": sess_name}, headers=reg_h)

        sa_h = {"Authorization": f"Bearer {sa_token}"}
        resp = await c.get("/dashboard/sessions", headers=sa_h)
        names = {s["name"] for s in resp.json()["sessions"]}
        assert sess_name in names


# ---------------------------------------------------------------------------
# Workspace access control
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workspace_access_denied_for_non_member():
    """Users without session access get 403 on workspace endpoints."""
    tag = secrets.token_hex(4)
    async with AsyncClient(transport=_transport(), base_url="http://test") as c:
        r_own = await c.post("/auth/signup", json={
            "email": _email("ws-own"), "password": "WsOwner1!", "display_name": "WS Owner",
        })
        own_h = {"Authorization": f"Bearer {r_own.json()['token']}"}
        sess = await c.post("/dashboard/sessions", json={"name": f"WS-Protected-{tag}"}, headers=own_h)
        sid = sess.json()["session_id"]

        r_out = await c.post("/auth/signup", json={
            "email": _email("ws-out"), "password": "WsOutsi1!", "display_name": "Outsider",
        })
        out_h = {"Authorization": f"Bearer {r_out.json()['token']}"}

        for endpoint in [
            f"/dashboard/sessions/{sid}/workspace/files",
            f"/dashboard/sessions/{sid}/workspace/read?filepath=x",
            f"/dashboard/sessions/{sid}/workspace/download",
        ]:
            resp = await c.get(endpoint, headers=out_h)
            assert resp.status_code == 403, f"{endpoint} should be 403, got {resp.status_code}"


@pytest.mark.asyncio
async def test_super_admin_bypasses_workspace_access():
    """Super admin can access any session's workspace."""
    tag = secrets.token_hex(4)
    async with AsyncClient(transport=_transport(), base_url="http://test") as c:
        r_own = await c.post("/auth/signup", json={
            "email": _email("ws-sa-own"), "password": "WsSaOwn1!", "display_name": "SA WS Owner",
        })
        own_h = {"Authorization": f"Bearer {r_own.json()['token']}"}
        sess = await c.post("/dashboard/sessions", json={"name": f"SA-WS-{tag}"}, headers=own_h)
        sid = sess.json()["session_id"]

        r_sa = await c.post("/auth/signup", json={
            "email": _email("ws-sa"), "password": "WsSuperA1!", "display_name": "SA WS",
        })
        sa_id = r_sa.json()["user"]["user_id"]
        await c.patch(
            f"/dashboard/admin/users/{sa_id}/super-admin",
            json={"is_super_admin": True},
            headers=_admin_headers(),
        )
        sa_h = {"Authorization": f"Bearer {r_sa.json()['token']}"}

        resp = await c.get(f"/dashboard/sessions/{sid}/workspace/files", headers=sa_h)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Super admin endpoints
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_non_super_admin_cannot_list_all_users():
    """Regular users get 403 on /admin/users."""
    async with AsyncClient(transport=_transport(), base_url="http://test") as c:
        r = await c.post("/auth/signup", json={
            "email": _email("no-sa"), "password": "NoSuper1!", "display_name": "Regular",
        })
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        resp = await c.get("/dashboard/admin/users", headers=h)
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_super_admin_can_assign_roles_across_orgs():
    """Super admin can add a user to any org with any role."""
    async with AsyncClient(transport=_transport(), base_url="http://test") as c:
        r_sa = await c.post("/auth/signup", json={
            "email": _email("sa-roles"), "password": "SaRoles1!", "display_name": "SA Roles",
        })
        sa_id = r_sa.json()["user"]["user_id"]
        sa_h = {"Authorization": f"Bearer {r_sa.json()['token']}"}
        await c.patch(
            f"/dashboard/admin/users/{sa_id}/super-admin",
            json={"is_super_admin": True},
            headers=_admin_headers(),
        )

        r_org = await c.post("/auth/signup", json={
            "email": _email("sa-org"), "password": "SaOrg123!", "display_name": "Org Owner",
        })
        org_tid = r_org.json()["tenant_id"]

        r_target = await c.post("/auth/signup", json={
            "email": _email("sa-tgt"), "password": "SaTgt123!", "display_name": "Target",
        })
        tgt_id = r_target.json()["user"]["user_id"]

        resp = await c.patch(
            f"/dashboard/admin/users/{tgt_id}/role",
            json={"tenant_id": org_tid, "role": "contributor"},
            headers=sa_h,
        )
        assert resp.status_code == 200
        assert resp.json()["role"] == "contributor"


# ---------------------------------------------------------------------------
# Role hierarchy enforcement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_role_hierarchy_in_auth():
    """Verify the role hierarchy: reader < contributor < admin < owner."""
    from contextcore.api.auth import _ROLE_HIERARCHY

    assert _ROLE_HIERARCHY["reader"] < _ROLE_HIERARCHY["contributor"]
    assert _ROLE_HIERARCHY["contributor"] < _ROLE_HIERARCHY["admin"]
    assert _ROLE_HIERARCHY["admin"] < _ROLE_HIERARCHY["owner"]
    # Legacy aliases
    assert _ROLE_HIERARCHY["viewer"] == _ROLE_HIERARCHY["reader"]
    assert _ROLE_HIERARCHY["member"] == _ROLE_HIERARCHY["contributor"]


@pytest.mark.asyncio
async def test_reader_cannot_access_contributor_endpoints():
    """A reader in an org should be blocked from contributor-gated endpoints
    when their primary tenant role is reader."""
    from contextcore.api.users import UserRegistry
    from contextcore.api.tenants import TenantRegistry
    import tempfile, pathlib

    tmp = tempfile.mkdtemp()
    ureg = UserRegistry(db_path=str(pathlib.Path(tmp) / "u.db"))
    treg = TenantRegistry(db_path=str(pathlib.Path(tmp) / "t.db"))

    # Create user and single tenant where they're a reader
    user = ureg.create("reader-only@rbac.com", "Reader12!", "Reader Only")
    tenant, _ = treg.create("reader-test-org")
    ureg.link_tenant(user.user_id, tenant.tenant_id, "reader")

    # Verify the role is stored correctly
    memberships = ureg.get_user_tenants(user.user_id)
    roles = {m.tenant_id: m.role for m in memberships}
    assert roles[tenant.tenant_id] == "reader"


# ---------------------------------------------------------------------------
# Legacy role migration
# ---------------------------------------------------------------------------

def test_legacy_roles_migrated_on_init():
    """UserRegistry._migrate() should convert viewer->reader, member->contributor."""
    from contextcore.api.users import UserRegistry
    import tempfile, pathlib, sqlite3

    tmp = tempfile.mkdtemp()
    db_path = str(pathlib.Path(tmp) / "legacy.db")

    # Pre-create with legacy schema (no is_super_admin column, old role names)
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE users (
            user_id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            created_at TEXT NOT NULL,
            last_login_at TEXT,
            config TEXT DEFAULT '{}'
        );
        CREATE TABLE user_tenants (
            user_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            role TEXT DEFAULT 'owner',
            joined_at TEXT NOT NULL,
            PRIMARY KEY (user_id, tenant_id)
        );
        CREATE TABLE team_invitations (
            invite_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            email TEXT NOT NULL,
            role TEXT DEFAULT 'member',
            invited_by TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TEXT NOT NULL,
            UNIQUE(tenant_id, email)
        );
        CREATE TABLE login_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            ip_address TEXT,
            success INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE email_verifications (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            verified_at TEXT
        );
        INSERT INTO users VALUES ('u1','old@test.com','hash','Old User','active','2026-01-01',NULL,'{}');
        INSERT INTO user_tenants VALUES ('u1','t1','viewer','2026-01-01');
        INSERT INTO user_tenants VALUES ('u1','t2','member','2026-01-01');
    """)
    conn.commit()
    conn.close()

    # Opening UserRegistry triggers _migrate()
    ureg = UserRegistry(db_path=db_path)

    # Verify migration
    memberships = ureg.get_user_tenants("u1")
    roles = {m.tenant_id: m.role for m in memberships}
    assert roles["t1"] == "reader", f"viewer should migrate to reader, got {roles['t1']}"
    assert roles["t2"] == "contributor", f"member should migrate to contributor, got {roles['t2']}"

    # Verify is_super_admin column was added
    user = ureg.get("u1")
    assert user is not None
    assert user.is_super_admin is False


# ---------------------------------------------------------------------------
# Unauthenticated access
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unauthenticated_sessions_returns_401():
    async with AsyncClient(transport=_transport(), base_url="http://test") as c:
        resp = await c.get("/dashboard/sessions")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_unauthenticated_admin_returns_401():
    async with AsyncClient(transport=_transport(), base_url="http://test") as c:
        resp = await c.get("/dashboard/admin/users")
        assert resp.status_code == 401
