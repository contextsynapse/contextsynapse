"""
Tool Policy Engine
===================
Per-boundary allowlist/blocklist for tool access control.

Admins define what tools each agent (or all agents) can use within a boundary.
This prevents agents from calling unauthorized external tools.

Policy evaluation order:
    1. Check agent-specific deny → DENY
    2. Check agent-specific allow → ALLOW
    3. Check boundary-level deny → DENY
    4. Check boundary-level allow → ALLOW
    5. Default policy (allow_by_default or deny_by_default)

Usage:
    policy = ToolPolicy()
    policy.set_boundary_policy("session-123", allow=["search_nodes", "add_knowledge"], deny=["ws_run_command"])
    decision = policy.evaluate("session-123", "codex", "ws_run_command")
    # → PolicyDecision(allowed=False, reason="Tool 'ws_run_command' is denied by boundary policy")
"""

from __future__ import annotations

import logging
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class PolicyDecision:
    """Result of a policy evaluation."""
    allowed: bool
    reason: str = ""
    policy_source: str = ""  # "agent", "boundary", "default"


@dataclass
class BoundaryPolicy:
    """Policy for a specific boundary."""
    namespace: str
    allow_tools: Set[str] = field(default_factory=set)  # empty = allow all
    deny_tools: Set[str] = field(default_factory=set)
    allow_categories: Set[str] = field(default_factory=set)  # e.g., {"graph", "context"}
    deny_categories: Set[str] = field(default_factory=set)
    allow_external: bool = True  # allow tools outside our registry
    max_tool_calls_per_minute: int = 60
    max_cost_per_session: float = 0.0  # 0 = unlimited
    default_allow: bool = True


class ToolPolicy:
    """Evaluates tool access policies per boundary and agent."""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS tool_policies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        namespace TEXT NOT NULL,
        agent_id TEXT DEFAULT '',
        policy_type TEXT NOT NULL,
        tool_name TEXT DEFAULT '',
        category TEXT DEFAULT '',
        value TEXT DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(namespace, agent_id, policy_type, tool_name, category)
    );
    CREATE INDEX IF NOT EXISTS idx_tp_ns ON tool_policies(namespace);
    """

    def __init__(self, db_path: str = "contextcore_data/policies.db"):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(self._SCHEMA)
        # In-memory cache for fast evaluation
        self._cache: Dict[str, BoundaryPolicy] = {}

    def set_boundary_policy(
        self,
        namespace: str,
        allow: Optional[List[str]] = None,
        deny: Optional[List[str]] = None,
        allow_categories: Optional[List[str]] = None,
        deny_categories: Optional[List[str]] = None,
        allow_external: bool = True,
        max_tool_calls_per_minute: int = 60,
        max_cost_per_session: float = 0.0,
        default_allow: bool = True,
    ):
        """Set policy for a boundary (all agents)."""
        # Clear existing boundary-level policies
        self._conn.execute(
            "DELETE FROM tool_policies WHERE namespace = ? AND agent_id = ''",
            (namespace,),
        )
        # Insert new policies
        now = "datetime('now')"
        if allow:
            for t in allow:
                self._conn.execute(
                    "INSERT OR REPLACE INTO tool_policies (namespace, agent_id, policy_type, tool_name) VALUES (?, '', 'allow_tool', ?)",
                    (namespace, t),
                )
        if deny:
            for t in deny:
                self._conn.execute(
                    "INSERT OR REPLACE INTO tool_policies (namespace, agent_id, policy_type, tool_name) VALUES (?, '', 'deny_tool', ?)",
                    (namespace, t),
                )
        if allow_categories:
            for c in allow_categories:
                self._conn.execute(
                    "INSERT OR REPLACE INTO tool_policies (namespace, agent_id, policy_type, category) VALUES (?, '', 'allow_category', ?)",
                    (namespace, c),
                )
        if deny_categories:
            for c in deny_categories:
                self._conn.execute(
                    "INSERT OR REPLACE INTO tool_policies (namespace, agent_id, policy_type, category) VALUES (?, '', 'deny_category', ?)",
                    (namespace, c),
                )
        # Store config
        config = json.dumps({
            "allow_external": allow_external,
            "max_tool_calls_per_minute": max_tool_calls_per_minute,
            "max_cost_per_session": max_cost_per_session,
            "default_allow": default_allow,
        })
        self._conn.execute(
            "INSERT OR REPLACE INTO tool_policies (namespace, agent_id, policy_type, value) VALUES (?, '', 'config', ?)",
            (namespace, config),
        )
        self._conn.commit()
        # Invalidate cache
        self._cache.pop(namespace, None)
        logger.info("Updated boundary policy for %s", namespace)

    def set_agent_policy(
        self,
        namespace: str,
        agent_id: str,
        allow: Optional[List[str]] = None,
        deny: Optional[List[str]] = None,
    ):
        """Set tool policy for a specific agent within a boundary."""
        self._conn.execute(
            "DELETE FROM tool_policies WHERE namespace = ? AND agent_id = ?",
            (namespace, agent_id),
        )
        if allow:
            for t in allow:
                self._conn.execute(
                    "INSERT OR REPLACE INTO tool_policies (namespace, agent_id, policy_type, tool_name) VALUES (?, ?, 'allow_tool', ?)",
                    (namespace, agent_id, t),
                )
        if deny:
            for t in deny:
                self._conn.execute(
                    "INSERT OR REPLACE INTO tool_policies (namespace, agent_id, policy_type, tool_name) VALUES (?, ?, 'deny_tool', ?)",
                    (namespace, agent_id, t),
                )
        self._conn.commit()
        self._cache.pop(namespace, None)

    def evaluate(
        self,
        namespace: str,
        agent_id: str,
        tool_name: str,
        tool_category: str = "",
    ) -> PolicyDecision:
        """Evaluate whether an agent can use a tool in a boundary.

        Returns PolicyDecision with allowed/denied and reason.
        """
        policy = self._load_policy(namespace)

        # 1. Agent-specific deny
        agent_deny = self._get_agent_tools(namespace, agent_id, "deny_tool")
        if tool_name in agent_deny:
            return PolicyDecision(
                allowed=False,
                reason=f"Tool '{tool_name}' denied for agent '{agent_id}'",
                policy_source="agent",
            )

        # 2. Agent-specific allow
        agent_allow = self._get_agent_tools(namespace, agent_id, "allow_tool")
        if tool_name in agent_allow:
            return PolicyDecision(allowed=True, reason="Agent-level allow", policy_source="agent")

        # 3. Boundary deny (tool name)
        if tool_name in policy.deny_tools:
            return PolicyDecision(
                allowed=False,
                reason=f"Tool '{tool_name}' denied by boundary policy",
                policy_source="boundary",
            )

        # 4. Boundary deny (category)
        if tool_category and tool_category in policy.deny_categories:
            return PolicyDecision(
                allowed=False,
                reason=f"Category '{tool_category}' denied by boundary policy",
                policy_source="boundary",
            )

        # 5. Boundary allow (tool name)
        if policy.allow_tools and tool_name in policy.allow_tools:
            return PolicyDecision(allowed=True, reason="Boundary allow list", policy_source="boundary")

        # 6. Boundary allow (category)
        if policy.allow_categories and tool_category in policy.allow_categories:
            return PolicyDecision(allowed=True, reason="Category allowed", policy_source="boundary")

        # 7. If allow lists exist but tool not in them → deny
        if policy.allow_tools or policy.allow_categories:
            return PolicyDecision(
                allowed=False,
                reason=f"Tool '{tool_name}' not in boundary allow list",
                policy_source="boundary",
            )

        # 8. Default
        return PolicyDecision(
            allowed=policy.default_allow,
            reason="Default policy",
            policy_source="default",
        )

    def get_boundary_policy(self, namespace: str) -> Dict[str, Any]:
        """Get current policy for a boundary (for display)."""
        policy = self._load_policy(namespace)
        return {
            "namespace": namespace,
            "allow_tools": sorted(policy.allow_tools),
            "deny_tools": sorted(policy.deny_tools),
            "allow_categories": sorted(policy.allow_categories),
            "deny_categories": sorted(policy.deny_categories),
            "allow_external": policy.allow_external,
            "max_tool_calls_per_minute": policy.max_tool_calls_per_minute,
            "max_cost_per_session": policy.max_cost_per_session,
            "default_allow": policy.default_allow,
        }

    def _load_policy(self, namespace: str) -> BoundaryPolicy:
        """Load boundary policy from DB (cached)."""
        if namespace in self._cache:
            return self._cache[namespace]

        policy = BoundaryPolicy(namespace=namespace)
        rows = self._conn.execute(
            "SELECT policy_type, tool_name, category, value FROM tool_policies WHERE namespace = ? AND agent_id = ''",
            (namespace,),
        ).fetchall()

        for r in rows:
            pt = r["policy_type"]
            if pt == "allow_tool" and r["tool_name"]:
                policy.allow_tools.add(r["tool_name"])
            elif pt == "deny_tool" and r["tool_name"]:
                policy.deny_tools.add(r["tool_name"])
            elif pt == "allow_category" and r["category"]:
                policy.allow_categories.add(r["category"])
            elif pt == "deny_category" and r["category"]:
                policy.deny_categories.add(r["category"])
            elif pt == "config" and r["value"]:
                try:
                    cfg = json.loads(r["value"])
                    policy.allow_external = cfg.get("allow_external", True)
                    policy.max_tool_calls_per_minute = cfg.get("max_tool_calls_per_minute", 60)
                    policy.max_cost_per_session = cfg.get("max_cost_per_session", 0.0)
                    policy.default_allow = cfg.get("default_allow", True)
                except json.JSONDecodeError:
                    pass

        self._cache[namespace] = policy
        return policy

    def _get_agent_tools(self, namespace: str, agent_id: str, policy_type: str) -> Set[str]:
        """Get agent-specific tool list."""
        rows = self._conn.execute(
            "SELECT tool_name FROM tool_policies WHERE namespace = ? AND agent_id = ? AND policy_type = ?",
            (namespace, agent_id, policy_type),
        ).fetchall()
        return {r["tool_name"] for r in rows if r["tool_name"]}


# Global singleton
_policy = None


def get_policy() -> ToolPolicy:
    global _policy
    if _policy is None:
        _policy = ToolPolicy()
    return _policy
