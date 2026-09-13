"""
Integration Registry
====================
Manages enterprise connector configurations and sync history.

Each integration connects a tenant to an external system (Salesforce, SAP,
HubSpot, etc.) and tracks sync runs.

Usage::

    registry = IntegrationRegistry()
    integration = registry.create("tenant-1", "salesforce", "My SF", {...}, {...})
    registry.update_status(integration.integration_id, "active")
    registry.record_sync(integration.integration_id, "tenant-1", "success", 42, 15, 1200)
"""

from __future__ import annotations

import json
import logging
import secrets
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DB_DIR = Path(__file__).resolve().parents[2] / "contextcore_data"
_DB_PATH = _DB_DIR / "integrations.db"


# ---------------------------------------------------------------------------
# Connector catalog — defines available connector types
# ---------------------------------------------------------------------------

CONNECTOR_CATALOG = [
    {
        "type": "salesforce",
        "name": "Salesforce",
        "category": "CRM",
        "description": "Sync contacts, accounts, opportunities, and custom objects from Salesforce.",
        "icon": "cloud",
        "config_schema": [
            {"key": "instance_url", "label": "Instance URL", "type": "text", "required": True, "placeholder": "https://yourorg.salesforce.com"},
            {"key": "client_id", "label": "Client ID", "type": "text", "required": True},
            {"key": "client_secret", "label": "Client Secret", "type": "password", "required": True},
            {"key": "objects", "label": "Objects to sync", "type": "multiselect", "options": ["Contact", "Account", "Opportunity", "Lead", "Case", "Custom"], "default": ["Contact", "Account"]},
            {"key": "sync_schedule", "label": "Sync schedule", "type": "select", "options": ["manual", "hourly", "daily", "weekly"], "default": "daily"},
        ],
    },
    {
        "type": "hubspot",
        "name": "HubSpot",
        "category": "CRM",
        "description": "Import contacts, deals, companies, and tickets from HubSpot.",
        "icon": "users",
        "config_schema": [
            {"key": "api_key", "label": "Private App Token", "type": "password", "required": True},
            {"key": "objects", "label": "Objects to sync", "type": "multiselect", "options": ["contacts", "deals", "companies", "tickets"], "default": ["contacts", "companies"]},
            {"key": "sync_schedule", "label": "Sync schedule", "type": "select", "options": ["manual", "hourly", "daily", "weekly"], "default": "daily"},
        ],
    },
    {
        "type": "sap",
        "name": "SAP",
        "category": "ERP",
        "description": "Connect to SAP systems via RFC to sync master data and transactions.",
        "icon": "server",
        "config_schema": [
            {"key": "host", "label": "SAP Host", "type": "text", "required": True},
            {"key": "system_number", "label": "System Number", "type": "text", "required": True, "placeholder": "00"},
            {"key": "client", "label": "Client", "type": "text", "required": True, "placeholder": "100"},
            {"key": "user", "label": "Username", "type": "text", "required": True},
            {"key": "password", "label": "Password", "type": "password", "required": True},
            {"key": "rfc_modules", "label": "RFC Modules", "type": "multiselect", "options": ["MM", "SD", "FI", "HR", "PP", "Custom"], "default": ["MM"]},
            {"key": "sync_schedule", "label": "Sync schedule", "type": "select", "options": ["manual", "hourly", "daily", "weekly"], "default": "daily"},
        ],
    },
    {
        "type": "servicenow",
        "name": "ServiceNow",
        "category": "ITSM",
        "description": "Sync incidents, changes, assets, and CMDB items from ServiceNow.",
        "icon": "headphones",
        "config_schema": [
            {"key": "instance", "label": "Instance", "type": "text", "required": True, "placeholder": "yourorg.service-now.com"},
            {"key": "username", "label": "Username", "type": "text", "required": True},
            {"key": "password", "label": "Password", "type": "password", "required": True},
            {"key": "tables", "label": "Tables to sync", "type": "multiselect", "options": ["incident", "change_request", "cmdb_ci", "sc_request", "problem"], "default": ["incident"]},
            {"key": "sync_schedule", "label": "Sync schedule", "type": "select", "options": ["manual", "hourly", "daily", "weekly"], "default": "daily"},
        ],
    },
    {
        "type": "jira",
        "name": "Jira",
        "category": "Project Management",
        "description": "Import issues, projects, sprints, and epics from Jira.",
        "icon": "clipboard-list",
        "config_schema": [
            {"key": "domain", "label": "Jira Domain", "type": "text", "required": True, "placeholder": "yourorg.atlassian.net"},
            {"key": "email", "label": "Email", "type": "text", "required": True},
            {"key": "api_token", "label": "API Token", "type": "password", "required": True},
            {"key": "projects", "label": "Project keys", "type": "text", "required": True, "placeholder": "PROJ1, PROJ2"},
            {"key": "sync_schedule", "label": "Sync schedule", "type": "select", "options": ["manual", "hourly", "daily", "weekly"], "default": "daily"},
        ],
    },
    {
        "type": "slack",
        "name": "Slack",
        "category": "Communication",
        "description": "Ingest messages and threads from Slack channels into the knowledge graph.",
        "icon": "message-square",
        "config_schema": [
            {"key": "bot_token", "label": "Bot Token", "type": "password", "required": True, "placeholder": "xoxb-..."},
            {"key": "channels", "label": "Channels", "type": "text", "required": True, "placeholder": "#general, #engineering"},
            {"key": "sync_schedule", "label": "Sync schedule", "type": "select", "options": ["manual", "hourly", "daily", "weekly"], "default": "hourly"},
        ],
    },
    {
        "type": "rest_api",
        "name": "REST API",
        "category": "Custom",
        "description": "Connect to any REST API to pull data into the graph.",
        "icon": "globe",
        "config_schema": [
            {"key": "base_url", "label": "Base URL", "type": "text", "required": True, "placeholder": "https://api.example.com"},
            {"key": "auth_type", "label": "Auth type", "type": "select", "options": ["none", "bearer", "basic", "api_key"], "default": "bearer"},
            {"key": "auth_value", "label": "Auth value", "type": "password", "required": False, "placeholder": "Bearer token or API key"},
            {"key": "endpoints", "label": "Endpoints (comma-separated)", "type": "text", "required": True, "placeholder": "/users, /orders"},
            {"key": "sync_schedule", "label": "Sync schedule", "type": "select", "options": ["manual", "hourly", "daily", "weekly"], "default": "manual"},
        ],
    },
    {
        "type": "webhook",
        "name": "Webhook",
        "category": "Custom",
        "description": "Receive inbound data via webhook URL. Events are ingested into the graph automatically.",
        "icon": "webhook",
        "config_schema": [
            {"key": "event_types", "label": "Event types", "type": "text", "required": False, "placeholder": "order.created, user.updated"},
            {"key": "secret", "label": "Webhook secret (for signature verification)", "type": "password", "required": False},
        ],
    },
    {
        "type": "database",
        "name": "Database",
        "category": "Data",
        "description": "Pull data from PostgreSQL, MySQL, or SQLite databases.",
        "icon": "database",
        "config_schema": [
            {"key": "db_type", "label": "Database type", "type": "select", "options": ["postgresql", "mysql", "sqlite"], "default": "postgresql"},
            {"key": "connection_string", "label": "Connection string", "type": "password", "required": True, "placeholder": "postgresql://user:pass@host:5432/db"},
            {"key": "query", "label": "SQL Query", "type": "textarea", "required": True, "placeholder": "SELECT * FROM customers"},
            {"key": "sync_schedule", "label": "Sync schedule", "type": "select", "options": ["manual", "hourly", "daily", "weekly"], "default": "daily"},
        ],
    },
    {
        "type": "file_watcher",
        "name": "File Watcher",
        "category": "Local",
        "description": "Watch a local directory for new files and ingest them automatically.",
        "icon": "folder-open",
        "config_schema": [
            {"key": "path", "label": "Directory path", "type": "text", "required": True, "placeholder": "/data/incoming"},
            {"key": "patterns", "label": "File patterns", "type": "text", "required": False, "placeholder": "*.pdf, *.csv, *.json"},
            {"key": "recursive", "label": "Watch subdirectories", "type": "select", "options": ["true", "false"], "default": "true"},
        ],
    },
    {
        "type": "s3",
        "name": "Amazon S3",
        "category": "Cloud Storage",
        "description": "Sync files from an S3 bucket into the graph via the multimodal pipeline.",
        "icon": "cloud",
        "config_schema": [
            {"key": "bucket", "label": "Bucket name", "type": "text", "required": True},
            {"key": "prefix", "label": "Key prefix", "type": "text", "required": False, "placeholder": "data/"},
            {"key": "access_key", "label": "Access Key ID", "type": "text", "required": True},
            {"key": "secret_key", "label": "Secret Access Key", "type": "password", "required": True},
            {"key": "region", "label": "Region", "type": "text", "required": False, "placeholder": "us-east-1"},
            {"key": "sync_schedule", "label": "Sync schedule", "type": "select", "options": ["manual", "hourly", "daily", "weekly"], "default": "daily"},
        ],
    },
    {
        "type": "mcp",
        "name": "MCP Agent",
        "category": "AI Agent",
        "description": "Connect an MCP-compatible AI agent to read/write the shared graph brain.",
        "icon": "bot",
        "config_schema": [
            {"key": "endpoint_url", "label": "MCP Endpoint", "type": "text", "required": True, "placeholder": "http://localhost:3001"},
            {"key": "capabilities", "label": "Capabilities", "type": "multiselect", "options": ["read", "write", "query", "context"], "default": ["read", "write", "query"]},
        ],
    },
    {
        "type": "git",
        "name": "Git Repository",
        "category": "Source Control",
        "description": "Clone a Git repo for agent workspace — agents commit, branch, and push code.",
        "icon": "git-branch",
        "config_schema": [
            {"key": "repo_url", "label": "Repository URL", "type": "text", "required": True, "placeholder": "https://github.com/user/repo.git"},
            {"key": "branch", "label": "Default Branch", "type": "text", "required": False, "placeholder": "main"},
            {"key": "token", "label": "Access Token", "type": "password", "required": False, "placeholder": "ghp_... (for private repos)"},
            {"key": "auto_branch", "label": "Auto-create feature branches", "type": "select", "options": ["true", "false"], "default": "true"},
        ],
    },
    {
        "type": "github",
        "name": "GitHub",
        "category": "Source Control",
        "description": "Connect to GitHub for repo access, PRs, issues, and code review via GitHub API.",
        "icon": "github",
        "config_schema": [
            {"key": "repo_url", "label": "Repository", "type": "text", "required": True, "placeholder": "https://github.com/user/repo"},
            {"key": "token", "label": "Personal Access Token", "type": "password", "required": True, "placeholder": "ghp_..."},
            {"key": "branch", "label": "Base Branch", "type": "text", "required": False, "placeholder": "main"},
            {"key": "auto_pr", "label": "Auto-create PRs", "type": "select", "options": ["true", "false"], "default": "true"},
        ],
    },
    {
        "type": "confluence",
        "name": "Confluence",
        "category": "Knowledge Management",
        "description": "Import pages and spaces from Atlassian Confluence into the knowledge graph.",
        "icon": "book-open",
        "config_schema": [
            {"key": "domain", "label": "Confluence Domain", "type": "text", "required": True, "placeholder": "yourorg.atlassian.net"},
            {"key": "email", "label": "Email", "type": "text", "required": True},
            {"key": "api_token", "label": "API Token", "type": "password", "required": True},
            {"key": "spaces", "label": "Space keys", "type": "text", "required": True, "placeholder": "ENG, PRODUCT"},
            {"key": "sync_schedule", "label": "Sync schedule", "type": "select", "options": ["manual", "hourly", "daily", "weekly"], "default": "daily"},
        ],
    },
    {
        "type": "notion",
        "name": "Notion",
        "category": "Knowledge Management",
        "description": "Sync pages and databases from Notion into the knowledge graph.",
        "icon": "file-text",
        "config_schema": [
            {"key": "api_key", "label": "Integration Token", "type": "password", "required": True, "placeholder": "ntn_..."},
            {"key": "database_ids", "label": "Database IDs (comma-separated)", "type": "text", "required": False},
            {"key": "sync_schedule", "label": "Sync schedule", "type": "select", "options": ["manual", "hourly", "daily", "weekly"], "default": "daily"},
        ],
    },
    {
        "type": "chatgpt",
        "name": "ChatGPT",
        "category": "AI Chat History",
        "description": "Import conversations and memories from ChatGPT. Use the data export (Settings → Data controls → Export data) or OpenAI API key.",
        "icon": "message-circle",
        "config_schema": [
            {"key": "export_file", "label": "Export file path", "type": "text", "required": False, "placeholder": "path/to/conversations.json"},
            {"key": "api_key", "label": "OpenAI API Key (optional)", "type": "password", "required": False},
            {"key": "max_conversations", "label": "Max conversations", "type": "text", "required": False, "placeholder": "50"},
        ],
    },
    {
        "type": "claude",
        "name": "Claude",
        "category": "AI Chat History",
        "description": "Import conversations from Claude export, or Claude Code project context (CLAUDE.md + memory files).",
        "icon": "bot",
        "config_schema": [
            {"key": "export_file", "label": "Export file path", "type": "text", "required": False, "placeholder": "path/to/claude_export.json"},
            {"key": "project_dir", "label": "Claude Code project dir", "type": "text", "required": False, "placeholder": "path/to/project"},
            {"key": "api_key", "label": "Anthropic API Key (optional)", "type": "password", "required": False},
            {"key": "max_conversations", "label": "Max conversations", "type": "text", "required": False, "placeholder": "50"},
        ],
    },
    {
        "type": "gemini",
        "name": "Gemini",
        "category": "AI Chat History",
        "description": "Import conversations from Google Gemini export.",
        "icon": "sparkles",
        "config_schema": [
            {"key": "export_file", "label": "Export file path", "type": "text", "required": False, "placeholder": "path/to/gemini_export.json"},
            {"key": "api_key", "label": "Google API Key (optional)", "type": "password", "required": False},
            {"key": "max_conversations", "label": "Max conversations", "type": "text", "required": False, "placeholder": "50"},
        ],
    },
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Integration:
    integration_id: str
    tenant_id: str
    connector_type: str
    name: str
    config: Dict[str, Any] = field(default_factory=dict)
    credentials: Dict[str, Any] = field(default_factory=dict)
    status: str = "inactive"
    last_sync_at: Optional[str] = None
    sync_count: int = 0
    error_message: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Never expose raw credentials
        d["credentials"] = {k: "••••••" for k in self.credentials}
        return d


@dataclass
class SyncRun:
    sync_id: str
    integration_id: str
    tenant_id: str
    status: str
    nodes_created: int = 0
    edges_created: int = 0
    duration_ms: int = 0
    error_log: Optional[str] = None
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class IntegrationRegistry:
    """SQLite-backed registry for integration configs and sync history."""

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS integrations (
                    integration_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    connector_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    config TEXT DEFAULT '{}',
                    credentials TEXT DEFAULT '{}',
                    status TEXT DEFAULT 'inactive',
                    last_sync_at TEXT,
                    sync_count INTEGER DEFAULT 0,
                    error_message TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_integrations_tenant
                    ON integrations(tenant_id);

                CREATE TABLE IF NOT EXISTS sync_history (
                    sync_id TEXT PRIMARY KEY,
                    integration_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    nodes_created INTEGER DEFAULT 0,
                    edges_created INTEGER DEFAULT 0,
                    duration_ms INTEGER DEFAULT 0,
                    error_log TEXT,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY (integration_id) REFERENCES integrations(integration_id)
                );

                CREATE INDEX IF NOT EXISTS idx_sync_history_integration
                    ON sync_history(integration_id);
            """)

    # -- CRUD ---------------------------------------------------------------

    def create(
        self,
        tenant_id: str,
        connector_type: str,
        name: str,
        config: Dict[str, Any],
        credentials: Dict[str, Any],
    ) -> Integration:
        integration = Integration(
            integration_id=secrets.token_hex(12),
            tenant_id=tenant_id,
            connector_type=connector_type,
            name=name,
            config=config,
            credentials=credentials,
        )
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO integrations
                   (integration_id, tenant_id, connector_type, name, config, credentials, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    integration.integration_id,
                    tenant_id,
                    connector_type,
                    name,
                    json.dumps(config),
                    json.dumps(credentials),
                    integration.status,
                    integration.created_at,
                ),
            )
        logger.info("Integration created: %s (%s) for tenant %s", name, connector_type, tenant_id)
        return integration

    def get(self, integration_id: str, tenant_id: str) -> Optional[Integration]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM integrations WHERE integration_id = ? AND tenant_id = ?",
                (integration_id, tenant_id),
            ).fetchone()
        if not row:
            return None
        return self._row_to_integration(row)

    def list_for_tenant(self, tenant_id: str) -> List[Integration]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM integrations WHERE tenant_id = ? ORDER BY created_at DESC",
                (tenant_id,),
            ).fetchall()
        return [self._row_to_integration(r) for r in rows]

    def update(self, integration_id: str, tenant_id: str, **kwargs) -> Optional[Integration]:
        allowed = {"name", "config", "credentials", "status", "error_message"}
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            return self.get(integration_id, tenant_id)

        set_parts = []
        params = []
        for k, v in updates.items():
            set_parts.append(f"{k} = ?")
            if k in ("config", "credentials"):
                params.append(json.dumps(v))
            else:
                params.append(v)
        params.extend([integration_id, tenant_id])

        with self._conn() as conn:
            conn.execute(
                f"UPDATE integrations SET {', '.join(set_parts)} WHERE integration_id = ? AND tenant_id = ?",
                params,
            )
        return self.get(integration_id, tenant_id)

    def delete(self, integration_id: str, tenant_id: str) -> bool:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM sync_history WHERE integration_id = ? AND tenant_id = ?",
                (integration_id, tenant_id),
            )
            cur = conn.execute(
                "DELETE FROM integrations WHERE integration_id = ? AND tenant_id = ?",
                (integration_id, tenant_id),
            )
        return cur.rowcount > 0

    def update_status(self, integration_id: str, status: str, error_message: str = None):
        with self._conn() as conn:
            conn.execute(
                "UPDATE integrations SET status = ?, error_message = ? WHERE integration_id = ?",
                (status, error_message, integration_id),
            )

    # -- Sync history -------------------------------------------------------

    def record_sync(
        self,
        integration_id: str,
        tenant_id: str,
        status: str,
        nodes_created: int = 0,
        edges_created: int = 0,
        duration_ms: int = 0,
        error_log: str = None,
    ) -> SyncRun:
        now = datetime.now(timezone.utc).isoformat()
        sync = SyncRun(
            sync_id=secrets.token_hex(12),
            integration_id=integration_id,
            tenant_id=tenant_id,
            status=status,
            nodes_created=nodes_created,
            edges_created=edges_created,
            duration_ms=duration_ms,
            error_log=error_log,
            started_at=now,
            completed_at=now,
        )
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO sync_history
                   (sync_id, integration_id, tenant_id, status, nodes_created, edges_created,
                    duration_ms, error_log, started_at, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (sync.sync_id, integration_id, tenant_id, status,
                 nodes_created, edges_created, duration_ms, error_log, now, now),
            )
            conn.execute(
                """UPDATE integrations
                   SET last_sync_at = ?, sync_count = sync_count + 1, status = ?, error_message = ?
                   WHERE integration_id = ?""",
                (now, "active" if status == "success" else "error", error_log, integration_id),
            )
        return sync

    def get_sync_history(self, integration_id: str, tenant_id: str, limit: int = 20) -> List[SyncRun]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM sync_history
                   WHERE integration_id = ? AND tenant_id = ?
                   ORDER BY started_at DESC LIMIT ?""",
                (integration_id, tenant_id, limit),
            ).fetchall()
        return [self._row_to_sync(r) for r in rows]

    # -- Helpers ------------------------------------------------------------

    @staticmethod
    def _row_to_integration(row) -> Integration:
        return Integration(
            integration_id=row["integration_id"],
            tenant_id=row["tenant_id"],
            connector_type=row["connector_type"],
            name=row["name"],
            config=json.loads(row["config"] or "{}"),
            credentials=json.loads(row["credentials"] or "{}"),
            status=row["status"],
            last_sync_at=row["last_sync_at"],
            sync_count=row["sync_count"],
            error_message=row["error_message"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_sync(row) -> SyncRun:
        return SyncRun(
            sync_id=row["sync_id"],
            integration_id=row["integration_id"],
            tenant_id=row["tenant_id"],
            status=row["status"],
            nodes_created=row["nodes_created"],
            edges_created=row["edges_created"],
            duration_ms=row["duration_ms"],
            error_log=row["error_log"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )
