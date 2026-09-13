"""
Agent Identity & Registry
=========================
Track which agents (LLMs, tools, humans) read/write context in a session.

Every context write is tagged with an agent_id so that provenance is always
available downstream.
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


# Valid agent platforms — determines how the agent connects and what it can do.
AGENT_PLATFORMS = ("desktop", "mobile", "app", "browser", "embedded")


@dataclass
class AgentIdentity:
    """A registered agent that can participate in context sessions."""
    agent_id: str
    name: str
    role: str = "agent"
    platform: str = "app"             # desktop | mobile | app | browser | embedded
    capabilities: List[str] = field(default_factory=lambda: ["read", "write"])
    status: str = "active"            # active | inactive | suspended
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_seen: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    api_key_hash: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("api_key_hash", None)  # never expose hash
        return d


@dataclass
class ProvenanceRecord:
    """Who did what, when, and derived from what."""
    session_id: str
    agent_id: str
    operation: str          # ingest, write, update, delete
    target_type: str        # node, edge, blob, context_item
    target_id: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class AgentRegistry:
    """
    Persistent registry of agents backed by SQLite.

    The registry is intentionally lightweight — it does NOT own a MetadataDB
    instance.  Instead it opens its own small DB so the context layer stays
    self-contained and can be used without the heavier metadata subsystem.
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS agents (
        agent_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        role TEXT DEFAULT 'agent',
        platform TEXT DEFAULT 'app',
        capabilities TEXT DEFAULT '[]',
        status TEXT DEFAULT 'active',
        created_at TEXT NOT NULL,
        last_seen TEXT,
        metadata TEXT DEFAULT '{}',
        api_key_hash TEXT
    );

    CREATE TABLE IF NOT EXISTS provenance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        operation TEXT NOT NULL,
        target_type TEXT,
        target_id TEXT,
        source_ids TEXT DEFAULT '[]',
        timestamp TEXT NOT NULL,
        metadata TEXT DEFAULT '{}'
    );

    CREATE INDEX IF NOT EXISTS idx_provenance_session ON provenance(session_id);
    CREATE INDEX IF NOT EXISTS idx_provenance_agent ON provenance(agent_id);
    CREATE INDEX IF NOT EXISTS idx_provenance_timestamp ON provenance(timestamp);

    CREATE TABLE IF NOT EXISTS agent_trust (
        agent_id TEXT PRIMARY KEY,
        trust_level TEXT DEFAULT 'provisional',
        verified_claims INTEGER DEFAULT 0,
        false_claims INTEGER DEFAULT 0,
        total_actions INTEGER DEFAULT 0,
        trust_score REAL DEFAULT 0.5,
        last_evaluated TEXT,
        notes TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_trust_level ON agent_trust(trust_level);
    """

    def __init__(self, db_path: str = "contextcore_data/context.db"):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        # Enable WAL mode for concurrent access (MCP + API + adapters)
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=30000")
        except sqlite3.OperationalError:
            pass  # locked by another process — WAL may already be set
        self._conn.executescript(self._SCHEMA)
        self._migrate()

    def _migrate(self):
        """Add columns that may be missing in older databases."""
        cursor = self._conn.execute("PRAGMA table_info(agents)")
        existing = {row["name"] for row in cursor.fetchall()}
        migrations = [
            ("platform", "TEXT DEFAULT 'app'"),
            ("status", "TEXT DEFAULT 'active'"),
            ("last_seen", "TEXT"),
        ]
        for col, typedef in migrations:
            if col not in existing:
                self._conn.execute(f"ALTER TABLE agents ADD COLUMN {col} {typedef}")
                logger.info("Migrated agents table: added column %s", col)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Agent CRUD
    # ------------------------------------------------------------------

    def find_by_name(self, name: str) -> Optional[AgentIdentity]:
        """Find an existing agent by name."""
        row = self._conn.execute(
            "SELECT * FROM agents WHERE name = ? AND status = 'active' ORDER BY created_at DESC LIMIT 1",
            (name,),
        ).fetchone()
        if not row:
            return None
        return self._row_to_agent(row)

    def register(
        self,
        name: str,
        role: str = "agent",
        platform: str = "app",
        capabilities: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> tuple[AgentIdentity, str]:
        """Register an agent. If one with this name already exists, refresh its key and return it.

        Returns (identity, api_key).
        """
        if platform not in AGENT_PLATFORMS:
            raise ValueError(f"Invalid platform {platform!r}. Must be one of: {', '.join(AGENT_PLATFORMS)}")

        # Reuse existing agent with same name
        existing = self.find_by_name(name)
        if existing:
            new_key = self.refresh_key(existing.agent_id)
            if new_key:
                return existing, new_key

        agent_id = secrets.token_hex(12)
        api_key = secrets.token_urlsafe(32)
        api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()

        agent = AgentIdentity(
            agent_id=agent_id,
            name=name,
            role=role,
            platform=platform,
            capabilities=capabilities or ["read", "write"],
            metadata=metadata or {},
            api_key_hash=api_key_hash,
        )
        self._conn.execute(
            "INSERT INTO agents (agent_id, name, role, platform, capabilities, status, created_at, metadata, api_key_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                agent.agent_id,
                agent.name,
                agent.role,
                agent.platform,
                json.dumps(agent.capabilities),
                agent.status,
                agent.created_at,
                json.dumps(agent.metadata),
                agent.api_key_hash,
            ),
        )
        self._conn.commit()
        return agent, api_key

    def get(self, agent_id: str) -> Optional[AgentIdentity]:
        row = self._conn.execute(
            "SELECT * FROM agents WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_agent(row)

    def authenticate(self, agent_id: str, api_key: str) -> bool:
        row = self._conn.execute(
            "SELECT api_key_hash FROM agents WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if not row:
            return False
        expected = row["api_key_hash"]
        return hashlib.sha256(api_key.encode()).hexdigest() == expected

    def touch(self, agent_id: str) -> None:
        """Update last_seen timestamp for an agent."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE agents SET last_seen = ? WHERE agent_id = ?",
            (now, agent_id),
        )
        self._conn.commit()

    def refresh_key(self, agent_id: str) -> Optional[str]:
        """Generate a new API key for an existing agent. Returns new key or None."""
        row = self._conn.execute(
            "SELECT agent_id FROM agents WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if not row:
            return None
        new_key = secrets.token_urlsafe(32)
        new_hash = hashlib.sha256(new_key.encode()).hexdigest()
        self._conn.execute(
            "UPDATE agents SET api_key_hash = ? WHERE agent_id = ?",
            (new_hash, agent_id),
        )
        self._conn.commit()
        return new_key

    def list_agents(self, platform: Optional[str] = None) -> List[AgentIdentity]:
        if platform:
            rows = self._conn.execute(
                "SELECT * FROM agents WHERE platform = ? ORDER BY created_at DESC",
                (platform,),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM agents ORDER BY created_at DESC").fetchall()
        return [self._row_to_agent(r) for r in rows]

    def deregister(self, agent_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def update_metadata(self, agent_id: str, metadata: Dict[str, Any]) -> bool:
        """Update an agent's metadata (used for agent card updates and stats)."""
        cur = self._conn.execute(
            "UPDATE agents SET metadata = ? WHERE agent_id = ?",
            (json.dumps(metadata), agent_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def increment_stat(self, agent_id: str, stat_key: str, value: int = 1):
        """Increment a runtime stat counter (tasks_completed, tasks_failed, etc.)."""
        agent = self.get(agent_id)
        if not agent:
            return
        meta = agent.metadata or {}
        stats = meta.get("stats", {})
        stats[stat_key] = stats.get(stat_key, 0) + value
        meta["stats"] = stats
        self.update_metadata(agent_id, meta)

    def record_error(self, agent_id: str, error_message: str):
        """Record a task error for an agent."""
        agent = self.get(agent_id)
        if not agent:
            return
        meta = agent.metadata or {}
        stats = meta.get("stats", {})
        stats["tasks_failed"] = stats.get("tasks_failed", 0) + 1
        stats["last_error"] = error_message
        meta["stats"] = stats
        self.update_metadata(agent_id, meta)

    # ------------------------------------------------------------------
    # Provenance
    # ------------------------------------------------------------------

    def record_provenance(self, record: ProvenanceRecord) -> int:
        """Record a provenance event. Returns the row id."""
        cur = self._conn.execute(
            "INSERT INTO provenance (session_id, agent_id, operation, target_type, target_id, source_ids, timestamp, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.session_id,
                record.agent_id,
                record.operation,
                record.target_type,
                record.target_id,
                json.dumps(record.source_ids),
                record.timestamp,
                json.dumps(record.metadata),
            ),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_provenance(
        self,
        session_id: str,
        agent_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get provenance records for a session."""
        if agent_id:
            rows = self._conn.execute(
                "SELECT * FROM provenance WHERE session_id = ? AND agent_id = ? ORDER BY timestamp DESC LIMIT ?",
                (session_id, agent_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM provenance WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [self._row_to_provenance(r) for r in rows]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _row_to_agent(self, row: sqlite3.Row) -> AgentIdentity:
        return AgentIdentity(
            agent_id=row["agent_id"],
            name=row["name"],
            role=row["role"],
            platform=row["platform"] or "app",
            capabilities=json.loads(row["capabilities"]),
            status=row["status"] or "active",
            created_at=row["created_at"],
            last_seen=row["last_seen"],
            metadata=json.loads(row["metadata"]),
            api_key_hash=row["api_key_hash"],
        )

    def _row_to_provenance(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "agent_id": row["agent_id"],
            "operation": row["operation"],
            "target_type": row["target_type"],
            "target_id": row["target_id"],
            "source_ids": json.loads(row["source_ids"]),
            "timestamp": row["timestamp"],
            "metadata": json.loads(row["metadata"]),
        }

    # ------------------------------------------------------------------
    # Trust Scoring
    # ------------------------------------------------------------------

    TRUST_LEVELS = {
        "untrusted": 0.0,
        "provisional": 0.5,
        "verified": 0.8,
        "trusted": 1.0,
    }

    def get_trust(self, agent_id: str) -> Dict[str, Any]:
        """Get trust score for an agent. Creates default if not exists."""
        row = self._conn.execute(
            "SELECT * FROM agent_trust WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if row:
            return dict(row)
        # Create default provisional trust
        self._conn.execute(
            "INSERT OR IGNORE INTO agent_trust (agent_id) VALUES (?)", (agent_id,)
        )
        self._conn.commit()
        return {
            "agent_id": agent_id, "trust_level": "provisional",
            "verified_claims": 0, "false_claims": 0,
            "total_actions": 0, "trust_score": 0.5,
            "last_evaluated": None, "notes": "",
        }

    def record_claim(self, agent_id: str, verified: bool, note: str = ""):
        """Record a verified or false claim and update trust score."""
        trust = self.get_trust(agent_id)
        verified_claims = trust["verified_claims"] + (1 if verified else 0)
        false_claims = trust["false_claims"] + (0 if verified else 1)
        total = trust["total_actions"] + 1

        # Calculate trust score: verified / (verified + false * 5)
        # False claims penalized 5x — one lie costs 5 truths
        denominator = verified_claims + (false_claims * 5)
        score = verified_claims / denominator if denominator > 0 else 0.5

        # Determine trust level from score
        if false_claims >= 3 and score < 0.3:
            level = "untrusted"
        elif verified_claims >= 10 and score >= 0.9:
            level = "trusted"
        elif verified_claims >= 3 and score >= 0.7:
            level = "verified"
        else:
            level = "provisional"

        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO agent_trust "
            "(agent_id, trust_level, verified_claims, false_claims, total_actions, trust_score, last_evaluated, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (agent_id, level, verified_claims, false_claims, total, round(score, 3), now, note),
        )
        self._conn.commit()

        if not verified:
            logger.warning("[TRUST] Agent %s false claim recorded (score: %.2f, level: %s)", agent_id, score, level)
        return {"agent_id": agent_id, "trust_level": level, "trust_score": round(score, 3)}

    def record_action(self, agent_id: str):
        """Increment action count without changing claim stats."""
        self._conn.execute(
            "UPDATE agent_trust SET total_actions = total_actions + 1 WHERE agent_id = ?",
            (agent_id,),
        )
        self._conn.commit()

    def list_trust_scores(self, min_score: float = 0.0) -> List[Dict[str, Any]]:
        """List all agents with trust scores, optionally filtered by minimum score."""
        rows = self._conn.execute(
            "SELECT * FROM agent_trust WHERE trust_score >= ? ORDER BY trust_score DESC",
            (min_score,),
        ).fetchall()
        return [dict(r) for r in rows]

    def set_trust_level(self, agent_id: str, level: str, note: str = ""):
        """Manually override an agent's trust level (admin action)."""
        if level not in self.TRUST_LEVELS:
            raise ValueError(f"Invalid trust level: {level}. Must be one of: {list(self.TRUST_LEVELS)}")
        score = self.TRUST_LEVELS[level]
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO agent_trust "
            "(agent_id, trust_level, verified_claims, false_claims, total_actions, trust_score, last_evaluated, notes) "
            "VALUES (?, ?, COALESCE((SELECT verified_claims FROM agent_trust WHERE agent_id = ?), 0), "
            "COALESCE((SELECT false_claims FROM agent_trust WHERE agent_id = ?), 0), "
            "COALESCE((SELECT total_actions FROM agent_trust WHERE agent_id = ?), 0), ?, ?, ?)",
            (agent_id, level, agent_id, agent_id, agent_id, score, now, note),
        )
        self._conn.commit()
        logger.info("[TRUST] Agent %s manually set to %s by admin", agent_id, level)

    def close(self):
        if self._conn:
            self._conn.close()
