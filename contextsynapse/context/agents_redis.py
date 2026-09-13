"""
Redis-Backed Agent Registry
=============================
Drop-in replacement for the SQLite ``AgentRegistry`` that stores agent
identities and provenance in Redis.  Eliminates the ``database is locked``
errors caused by multiple processes (API server, MCP server, Codex adapter)
fighting over ``context.db``.

Same public API as ``AgentRegistry`` — swap transparently via ``store_factory``.

Requires:
    pip install redis          # synchronous redis-py
    AICONTEXTDB_REDIS_URL      # e.g. redis://localhost:6379/0
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import redis

from .agents import AgentIdentity, ProvenanceRecord, AGENT_PLATFORMS

logger = logging.getLogger(__name__)

# Redis key prefixes
_P = "contextsynapse"


class RedisAgentRegistry:
    """
    Agent registry backed by Redis.

    Key layout:
        {prefix}:agents:{agent_id}             → Hash  (AgentIdentity fields)
        {prefix}:agents:key:{sha256_hash}      → String(agent_id)  reverse auth lookup
        {prefix}:agents:index                  → Set   (all agent_ids)
        {prefix}:agents:platform:{platform}    → Set   (agent_ids filtered by platform)
        {prefix}:provenance:{session_id}       → List  (JSON ProvenanceRecords, newest first)
    """

    _MAX_PROVENANCE = 10_000  # cap per session

    def __init__(
        self,
        redis_url: Optional[str] = None,
        key_prefix: str = _P,
        redis_client: Optional[redis.Redis] = None,
    ):
        self._prefix = key_prefix
        if redis_client is not None:
            self._r = redis_client
        else:
            url = redis_url or os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL", "redis://localhost:6379/0")
            self._r = redis.Redis.from_url(url, decode_responses=True)

    # ------------------------------------------------------------------
    # Key helpers
    # ------------------------------------------------------------------

    def _k(self, *parts: str) -> str:
        return ":".join([self._prefix, *parts])

    # ------------------------------------------------------------------
    # Agent CRUD
    # ------------------------------------------------------------------

    def find_by_name(self, name: str) -> Optional[AgentIdentity]:
        """Find an existing agent by name. Returns the latest one if multiple exist."""
        for agent_id in self._r.smembers(self._k("agents:index")):
            data = self._r.hgetall(self._k("agents", agent_id))
            if data and data.get("name") == name and data.get("status", "active") == "active":
                return self._hash_to_agent(data)
        return None

    def register(
        self,
        name: str,
        role: str = "agent",
        platform: str = "app",
        capabilities: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> tuple:
        """Register an agent. If one with this name already exists, refresh its key and return it.

        Returns (AgentIdentity, api_key).
        """
        if platform not in AGENT_PLATFORMS:
            raise ValueError(f"Invalid platform {platform!r}. Must be one of: {', '.join(AGENT_PLATFORMS)}")

        # Reuse existing agent with same name — keep the same key (don't rotate)
        existing = self.find_by_name(name)
        if existing:
            # Generate a new key only if explicitly requested, not on re-register
            new_key = self.refresh_key(existing.agent_id)
            if new_key:
                logger.info("Agent '%s' already exists — key refreshed (old key invalidated)", name)
                return existing, new_key

        agent_id = secrets.token_hex(12)
        api_key = secrets.token_urlsafe(32)
        api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        now = datetime.now(timezone.utc).isoformat()

        agent = AgentIdentity(
            agent_id=agent_id,
            name=name,
            role=role,
            platform=platform,
            capabilities=capabilities or ["read", "write"],
            metadata=metadata or {},
            api_key_hash=api_key_hash,
            created_at=now,
        )

        # Atomic write via pipeline
        pipe = self._r.pipeline()
        pipe.hset(self._k("agents", agent_id), mapping={
            "agent_id": agent_id,
            "name": name,
            "role": role,
            "platform": platform,
            "capabilities": json.dumps(agent.capabilities),
            "status": "active",
            "created_at": now,
            "last_seen": "",
            "metadata": json.dumps(agent.metadata),
            "api_key_hash": api_key_hash,
        })
        pipe.sadd(self._k("agents:index"), agent_id)
        pipe.sadd(self._k("agents:platform", platform), agent_id)
        # Reverse lookup: hash → agent_id (for O(1) auth)
        pipe.set(self._k("agents:key", api_key_hash), agent_id)
        pipe.execute()

        return agent, api_key

    def get(self, agent_id: str) -> Optional[AgentIdentity]:
        data = self._r.hgetall(self._k("agents", agent_id))
        if not data:
            return None
        return self._hash_to_agent(data)

    def authenticate(self, agent_id: str, api_key: str) -> bool:
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        # O(1) reverse lookup
        stored_id = self._r.get(self._k("agents:key", key_hash))
        return stored_id == agent_id

    def touch(self, agent_id: str) -> None:
        """Update last_seen timestamp."""
        now = datetime.now(timezone.utc).isoformat()
        self._r.hset(self._k("agents", agent_id), "last_seen", now)

    def refresh_key(self, agent_id: str) -> Optional[str]:
        """Generate a new API key for an existing agent. Returns new key or None."""
        data = self._r.hgetall(self._k("agents", agent_id))
        if not data:
            return None

        # Remove old key hash reverse lookup
        old_hash = data.get("api_key_hash", "")
        if old_hash:
            self._r.delete(self._k("agents:key", old_hash))

        # Generate new key
        new_key = secrets.token_urlsafe(32)
        new_hash = hashlib.sha256(new_key.encode()).hexdigest()

        pipe = self._r.pipeline()
        pipe.hset(self._k("agents", agent_id), "api_key_hash", new_hash)
        pipe.set(self._k("agents:key", new_hash), agent_id)
        pipe.execute()

        return new_key

    def list_agents(self, platform: Optional[str] = None) -> List[AgentIdentity]:
        if platform:
            agent_ids = self._r.smembers(self._k("agents:platform", platform))
        else:
            agent_ids = self._r.smembers(self._k("agents:index"))

        agents = []
        pipe = self._r.pipeline()
        for aid in sorted(agent_ids):
            pipe.hgetall(self._k("agents", aid))
        results = pipe.execute()

        for data in results:
            if data:
                agents.append(self._hash_to_agent(data))

        # Sort by created_at descending (newest first)
        agents.sort(key=lambda a: a.created_at, reverse=True)
        return agents

    def query_by_capability(self, capability: str) -> List[Dict]:
        """Find agents that have a specific capability."""
        result = []
        try:
            agent_ids = self._r.smembers(self._k("agents:index"))
            for aid in agent_ids:
                aid_str = aid.decode() if isinstance(aid, bytes) else aid
                data = self._r.hgetall(self._k("agents", aid_str))
                if not data:
                    continue
                decoded = {(k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v) for k, v in data.items()}
                caps = decoded.get("capabilities", "[]")
                try:
                    cap_list = json.loads(caps) if isinstance(caps, str) else caps
                except Exception:
                    cap_list = []
                if capability.lower() in [c.lower() for c in cap_list]:
                    result.append(decoded)
        except Exception:
            pass
        return result

    def deregister(self, agent_id: str) -> bool:
        data = self._r.hgetall(self._k("agents", agent_id))
        if not data:
            return False

        pipe = self._r.pipeline()
        pipe.delete(self._k("agents", agent_id))
        pipe.srem(self._k("agents:index"), agent_id)
        platform = data.get("platform", "app")
        pipe.srem(self._k("agents:platform", platform), agent_id)
        # Remove reverse key lookup
        key_hash = data.get("api_key_hash", "")
        if key_hash:
            pipe.delete(self._k("agents:key", key_hash))
        pipe.execute()
        return True

    def update_metadata(self, agent_id: str, metadata: Dict[str, Any]) -> bool:
        """Update an agent's metadata."""
        key = self._k("agents", agent_id)
        if not self._r.exists(key):
            return False
        self._r.hset(key, "metadata", json.dumps(metadata))
        return True

    def increment_stat(self, agent_id: str, stat_key: str, value: int = 1):
        """Increment a runtime stat counter."""
        agent = self.get(agent_id)
        if not agent:
            return
        meta = agent.metadata or {}
        stats = meta.get("stats", {})
        stats[stat_key] = stats.get(stat_key, 0) + value
        meta["stats"] = stats
        self.update_metadata(agent_id, meta)

    def record_error(self, agent_id: str, error_message: str):
        """Record a task error."""
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
        """Record a provenance event. Returns approximate list length."""
        entry = json.dumps({
            "session_id": record.session_id,
            "agent_id": record.agent_id,
            "operation": record.operation,
            "target_type": record.target_type,
            "target_id": record.target_id,
            "source_ids": record.source_ids,
            "timestamp": record.timestamp,
            "metadata": record.metadata,
        })
        key = self._k("provenance", record.session_id)
        pipe = self._r.pipeline()
        pipe.lpush(key, entry)
        pipe.ltrim(key, 0, self._MAX_PROVENANCE - 1)
        results = pipe.execute()
        return results[0]  # length after push

    def get_provenance(
        self,
        session_id: str,
        agent_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get provenance records for a session (newest first)."""
        key = self._k("provenance", session_id)
        raw = self._r.lrange(key, 0, limit - 1)
        records = []
        for entry in raw:
            try:
                rec = json.loads(entry)
                if agent_id and rec.get("agent_id") != agent_id:
                    continue
                records.append(rec)
            except json.JSONDecodeError:
                continue
        return records

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_to_agent(data: Dict[str, str]) -> AgentIdentity:
        return AgentIdentity(
            agent_id=data.get("agent_id", ""),
            name=data.get("name", ""),
            role=data.get("role", "agent"),
            platform=data.get("platform", "app"),
            capabilities=json.loads(data.get("capabilities", "[]")),
            status=data.get("status", "active"),
            created_at=data.get("created_at", ""),
            last_seen=data.get("last_seen") or None,
            metadata=json.loads(data.get("metadata", "{}")),
            api_key_hash=data.get("api_key_hash"),
        )

    # ── Trust management ──────────────────────────────────────────────

    TRUST_LEVELS = {"untrusted": 0.1, "provisional": 0.5, "verified": 0.75, "trusted": 0.95}

    def get_trust(self, agent_id: str) -> Dict[str, Any]:
        key = f"{self._prefix}:trust:{agent_id}"
        raw = self._r.hgetall(key)
        if raw:
            def _dec(v):
                return v.decode() if isinstance(v, bytes) else v
            return {
                "agent_id": agent_id,
                "trust_level": _dec(raw.get(b"trust_level", raw.get("trust_level", b"provisional"))),
                "trust_score": float(_dec(raw.get(b"trust_score", raw.get("trust_score", b"0.5")))),
                "verified_claims": int(_dec(raw.get(b"verified_claims", raw.get("verified_claims", b"0")))),
                "false_claims": int(_dec(raw.get(b"false_claims", raw.get("false_claims", b"0")))),
                "total_actions": int(_dec(raw.get(b"total_actions", raw.get("total_actions", b"0")))),
                "last_evaluated": _dec(raw.get(b"last_evaluated", raw.get("last_evaluated", b""))),
                "notes": _dec(raw.get(b"notes", raw.get("notes", b""))),
            }
        default = {"trust_level": "provisional", "trust_score": "0.5",
                   "verified_claims": "0", "false_claims": "0",
                   "total_actions": "0", "last_evaluated": "", "notes": ""}
        self._r.hset(key, mapping=default)
        return {"agent_id": agent_id, "trust_level": "provisional", "trust_score": 0.5,
                "verified_claims": 0, "false_claims": 0, "total_actions": 0,
                "last_evaluated": None, "notes": ""}

    def record_action(self, agent_id: str):
        key = f"{self._prefix}:trust:{agent_id}"
        self._r.hincrby(key, "total_actions", 1)

    def list_trust_scores(self, min_score: float = 0.0) -> List[Dict[str, Any]]:
        """List all agents with trust scores."""
        # Get all registered agent IDs and check for trust data
        results = []
        agent_ids = self._r.smembers(f"{self._prefix}:agents:index") or set()
        for agent_id in agent_ids:
            trust = self.get_trust(agent_id)
            if trust.get("trust_score", 0) >= min_score:
                results.append(trust)
        results.sort(key=lambda x: x.get("trust_score", 0), reverse=True)
        return results

    def set_trust_level(self, agent_id: str, level: str, note: str = ""):
        if level not in self.TRUST_LEVELS:
            raise ValueError(f"Invalid trust level: {level}")
        score = self.TRUST_LEVELS[level]
        now = datetime.now(timezone.utc).isoformat()
        key = f"{self._prefix}:trust:{agent_id}"
        self._r.hset(key, mapping={
            "trust_level": level, "trust_score": str(score),
            "last_evaluated": now, "notes": note,
        })

    def close(self):
        self._r.close()
