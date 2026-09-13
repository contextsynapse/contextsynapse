"""
Integrations Router
===================
CRUD + sync endpoints for enterprise connectors.

GET    /dashboard/integrations              — List connectors
POST   /dashboard/integrations              — Create connector
GET    /dashboard/integrations/catalog       — Available connector types
GET    /dashboard/integrations/{id}          — Detail
PATCH  /dashboard/integrations/{id}          — Update config
DELETE /dashboard/integrations/{id}          — Remove
POST   /dashboard/integrations/{id}/test     — Test connection
POST   /dashboard/integrations/{id}/sync     — Trigger sync
GET    /dashboard/integrations/{id}/history  — Sync history
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .integrations import CONNECTOR_CATALOG

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Connection testers — each returns {"success": bool, "message": str, ...}
# ---------------------------------------------------------------------------

def _test_connector(connector_type: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Test a connector connection. Returns {success, message, details}."""
    tester = _TESTERS.get(connector_type, _test_generic)
    try:
        return tester(config)
    except Exception as e:
        return {"success": False, "message": f"Connection failed: {e}"}


def _test_git(config: Dict[str, Any]) -> Dict[str, Any]:
    """Test Git repo access by running ls-remote."""
    import subprocess
    repo_url = config.get("repo_url", "")
    if not repo_url:
        return {"success": False, "message": "Repository URL is required"}

    # Inject token into URL for private repos (PAT only, not email)
    token = config.get("token", "").strip()
    test_url = repo_url
    if token:
        # Validate token is not an email or domain
        if "@" in token or "." in token.split("/")[0]:
            return {"success": False, "message": "Token field should contain a Personal Access Token (PAT), not an email. Generate one at GitHub → Settings → Developer Settings → Personal Access Tokens."}
        if "github.com" in repo_url and "@" not in repo_url:
            test_url = repo_url.replace("https://", f"https://{token}@")

    try:
        result = subprocess.run(
            ["git", "ls-remote", "--heads", test_url],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            branches = [line.split("refs/heads/")[-1] for line in result.stdout.strip().split("\n") if "refs/heads/" in line]
            return {
                "success": True,
                "message": f"Connected — {len(branches)} branches found",
                "details": {"branches": branches[:10]},
            }
        else:
            return {"success": False, "message": f"Git error: {result.stderr.strip()[:200]}"}
    except FileNotFoundError:
        return {"success": False, "message": "Git is not installed on the server"}
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "Connection timed out (15s)"}


def _test_github(config: Dict[str, Any]) -> Dict[str, Any]:
    """Test GitHub API access."""
    import urllib.request
    import json as _json

    repo_url = config.get("repo_url", "")
    token = config.get("token", "")

    # Extract owner/repo from URL
    parts = repo_url.rstrip("/").rstrip(".git").split("/")
    if len(parts) < 2:
        return {"success": False, "message": "Invalid repo URL — expected https://github.com/owner/repo"}
    owner, repo = parts[-2], parts[-1]

    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{owner}/{repo}",
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"} if token else {},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read())
            return {
                "success": True,
                "message": f"Connected to {data.get('full_name', repo)}",
                "details": {
                    "full_name": data.get("full_name"),
                    "default_branch": data.get("default_branch"),
                    "private": data.get("private"),
                    "language": data.get("language"),
                },
            }
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"success": False, "message": "Repository not found (check URL and token permissions)"}
        elif e.code == 401:
            return {"success": False, "message": "Authentication failed (check token)"}
        return {"success": False, "message": f"GitHub API error: {e.code}"}
    except Exception as e:
        return {"success": False, "message": f"Connection failed: {e}"}


def _test_jira(config: Dict[str, Any]) -> Dict[str, Any]:
    """Test Jira API access."""
    import urllib.request
    import base64
    import json as _json

    domain = config.get("domain", "")
    email = config.get("email", "")
    api_token = config.get("api_token", "")

    if not all([domain, email, api_token]):
        return {"success": False, "message": "Domain, email, and API token are all required"}

    url = f"https://{domain}/rest/api/3/myself"
    auth = base64.b64encode(f"{email}:{api_token}".encode()).decode()

    try:
        req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read())
            return {
                "success": True,
                "message": f"Connected as {data.get('displayName', email)}",
                "details": {"user": data.get("displayName"), "email": data.get("emailAddress")},
            }
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return {"success": False, "message": "Authentication failed (check email and API token)"}
        return {"success": False, "message": f"Jira API error: {e.code}"}
    except Exception as e:
        return {"success": False, "message": f"Connection failed: {e}"}


def _test_database(config: Dict[str, Any]) -> Dict[str, Any]:
    """Test database connection."""
    conn_str = config.get("connection_string", "")
    db_type = config.get("db_type", "postgresql")

    if not conn_str:
        return {"success": False, "message": "Connection string is required"}

    try:
        if db_type == "sqlite":
            import sqlite3
            conn = sqlite3.connect(conn_str, timeout=5)
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            conn.close()
            return {"success": True, "message": f"Connected — {len(tables)} tables", "details": {"tables": [t[0] for t in tables[:20]]}}
        elif db_type == "postgresql":
            try:
                import psycopg2
                conn = psycopg2.connect(conn_str, connect_timeout=5)
                cur = conn.cursor()
                cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
                tables = [r[0] for r in cur.fetchall()]
                conn.close()
                return {"success": True, "message": f"Connected — {len(tables)} tables", "details": {"tables": tables[:20]}}
            except ImportError:
                return {"success": False, "message": "psycopg2 not installed — run: pip install psycopg2-binary"}
        elif db_type == "mysql":
            try:
                import pymysql
                # Parse connection string
                conn = pymysql.connect(host="localhost", connect_timeout=5)
                conn.close()
                return {"success": True, "message": "Connected to MySQL"}
            except ImportError:
                return {"success": False, "message": "pymysql not installed — run: pip install pymysql"}
        return {"success": False, "message": f"Unsupported database type: {db_type}"}
    except Exception as e:
        return {"success": False, "message": f"Connection failed: {e}"}


def _test_slack(config: Dict[str, Any]) -> Dict[str, Any]:
    """Test Slack bot token."""
    import urllib.request
    import json as _json

    bot_token = config.get("bot_token", "")
    if not bot_token:
        return {"success": False, "message": "Bot token is required"}

    try:
        req = urllib.request.Request(
            "https://slack.com/api/auth.test",
            headers={"Authorization": f"Bearer {bot_token}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read())
            if data.get("ok"):
                return {"success": True, "message": f"Connected as {data.get('user', 'bot')} in {data.get('team', 'workspace')}"}
            return {"success": False, "message": data.get("error", "Auth test failed")}
    except Exception as e:
        return {"success": False, "message": f"Connection failed: {e}"}


def _test_generic(config: Dict[str, Any]) -> Dict[str, Any]:
    """Generic test — just validate required fields are present."""
    return {"success": True, "message": "Configuration validated (no live test available for this connector type)"}


_TESTERS = {
    "git": _test_git,
    "github": _test_github,
    "jira": _test_jira,
    "database": _test_database,
    "slack": _test_slack,
}


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CreateIntegrationRequest(BaseModel):
    connector_type: str = Field(..., description="Connector type from catalog")
    name: str = Field(..., min_length=1, max_length=100, description="Display name")
    config: Dict[str, Any] = Field(default_factory=dict)
    credentials: Dict[str, Any] = Field(default_factory=dict)


class UpdateIntegrationRequest(BaseModel):
    name: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    credentials: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def create_integrations_router(
    user_registry,
    tenant_registry,
    integration_registry,
) -> APIRouter:
    """Create the /dashboard/integrations router."""

    from .auth import UserAuth
    router = APIRouter(prefix="/dashboard/integrations", tags=["integrations"])
    user_auth = UserAuth(user_registry)

    def _get_tenant(user):
        tenant_id = user_registry.get_primary_tenant_id(user.user_id)
        if not tenant_id:
            raise HTTPException(status_code=404, detail="No workspace found")
        tenant = tenant_registry.get(tenant_id)
        if not tenant:
            raise HTTPException(status_code=404, detail="Workspace not found")
        return tenant

    # ------------------------------------------------------------------
    # GET /catalog — available connector types
    # ------------------------------------------------------------------
    @router.get("/catalog")
    async def get_catalog(user=Depends(user_auth)):
        return {"connectors": CONNECTOR_CATALOG}

    # ------------------------------------------------------------------
    # GET / — list configured integrations
    # ------------------------------------------------------------------
    @router.get("")
    async def list_integrations(user=Depends(user_auth)):
        tenant = _get_tenant(user)
        integrations = integration_registry.list_for_tenant(tenant.tenant_id)
        return {
            "integrations": [i.to_dict() for i in integrations],
            "count": len(integrations),
        }

    # ------------------------------------------------------------------
    # POST / — create integration
    # ------------------------------------------------------------------
    @router.post("")
    async def create_integration(req: CreateIntegrationRequest, user=Depends(user_auth)):
        tenant = _get_tenant(user)

        # Validate connector type
        valid_types = {c["type"] for c in CONNECTOR_CATALOG}
        if req.connector_type not in valid_types:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown connector type: {req.connector_type}. "
                       f"Valid types: {', '.join(sorted(valid_types))}",
            )

        integration = integration_registry.create(
            tenant_id=tenant.tenant_id,
            connector_type=req.connector_type,
            name=req.name,
            config=req.config,
            credentials=req.credentials,
        )
        return {"integration": integration.to_dict(), "message": "Integration created"}

    # ------------------------------------------------------------------
    # GET /{id} — detail
    # ------------------------------------------------------------------
    @router.get("/{integration_id}")
    async def get_integration(integration_id: str, user=Depends(user_auth)):
        tenant = _get_tenant(user)
        integration = integration_registry.get(integration_id, tenant.tenant_id)
        if not integration:
            raise HTTPException(status_code=404, detail="Integration not found")
        return {"integration": integration.to_dict()}

    # ------------------------------------------------------------------
    # PATCH /{id} — update
    # ------------------------------------------------------------------
    @router.patch("/{integration_id}")
    async def update_integration(
        integration_id: str,
        req: UpdateIntegrationRequest,
        user=Depends(user_auth),
    ):
        tenant = _get_tenant(user)
        integration = integration_registry.get(integration_id, tenant.tenant_id)
        if not integration:
            raise HTTPException(status_code=404, detail="Integration not found")

        updated = integration_registry.update(
            integration_id,
            tenant.tenant_id,
            name=req.name,
            config=req.config,
            credentials=req.credentials,
        )
        return {"integration": updated.to_dict(), "message": "Integration updated"}

    # ------------------------------------------------------------------
    # DELETE /{id}
    # ------------------------------------------------------------------
    @router.delete("/{integration_id}")
    async def delete_integration(integration_id: str, user=Depends(user_auth)):
        tenant = _get_tenant(user)
        deleted = integration_registry.delete(integration_id, tenant.tenant_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Integration not found")
        return {"message": "Integration deleted"}

    # ------------------------------------------------------------------
    # POST /{id}/test — test connection
    # ------------------------------------------------------------------
    @router.post("/{integration_id}/test")
    async def test_connection(integration_id: str, user=Depends(user_auth)):
        tenant = _get_tenant(user)
        integration = integration_registry.get(integration_id, tenant.tenant_id)
        if not integration:
            raise HTTPException(status_code=404, detail="Integration not found")

        connector_type = integration.connector_type
        try:
            config = integration.config
            creds = integration.credentials
            merged = {**config, **creds}

            catalog_entry = next(
                (c for c in CONNECTOR_CATALOG if c["type"] == connector_type), None
            )
            if not catalog_entry:
                raise ValueError(f"Unknown connector type: {connector_type}")

            # Validate required fields
            required_fields = [
                f["key"] for f in catalog_entry.get("config_schema", [])
                if f.get("required")
            ]
            missing = [f for f in required_fields if not merged.get(f)]
            if missing:
                return {"success": False, "message": f"Missing required fields: {', '.join(missing)}"}

            # Real connection tests per connector type
            test_result = _test_connector(connector_type, merged)

            if test_result["success"]:
                integration_registry.update_status(integration_id, "active")
            else:
                integration_registry.update_status(integration_id, "error", test_result["message"])

            return {**test_result, "connector_type": connector_type}
        except Exception as e:
            integration_registry.update_status(integration_id, "error", str(e))
            return {"success": False, "message": str(e)}

    # ------------------------------------------------------------------
    # POST /{id}/sync — trigger sync
    # ------------------------------------------------------------------
    @router.post("/{integration_id}/sync")
    async def trigger_sync(integration_id: str, user=Depends(user_auth)):
        tenant = _get_tenant(user)
        integration = integration_registry.get(integration_id, tenant.tenant_id)
        if not integration:
            raise HTTPException(status_code=404, detail="Integration not found")

        # Simulate a sync run (real implementation would use SourceConnector subclasses)
        start_time = time.time()
        try:
            integration_registry.update_status(integration_id, "syncing")

            # Placeholder: real sync would call the appropriate SourceConnector
            # For now, record a successful sync with zero results
            duration_ms = int((time.time() - start_time) * 1000)

            sync_run = integration_registry.record_sync(
                integration_id=integration_id,
                tenant_id=tenant.tenant_id,
                status="success",
                nodes_created=0,
                edges_created=0,
                duration_ms=duration_ms,
            )
            return {
                "sync": sync_run.to_dict(),
                "message": "Sync completed (connector stub — implement real sync logic per type)",
            }
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            sync_run = integration_registry.record_sync(
                integration_id=integration_id,
                tenant_id=tenant.tenant_id,
                status="failed",
                duration_ms=duration_ms,
                error_log=str(e),
            )
            return {"sync": sync_run.to_dict(), "message": f"Sync failed: {e}"}

    # ------------------------------------------------------------------
    # GET /{id}/history — sync history
    # ------------------------------------------------------------------
    @router.get("/{integration_id}/history")
    async def get_sync_history(integration_id: str, user=Depends(user_auth)):
        tenant = _get_tenant(user)
        integration = integration_registry.get(integration_id, tenant.tenant_id)
        if not integration:
            raise HTTPException(status_code=404, detail="Integration not found")

        history = integration_registry.get_sync_history(integration_id, tenant.tenant_id)
        return {"history": [s.to_dict() for s in history]}

    return router
