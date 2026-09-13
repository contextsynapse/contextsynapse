"""
Cost Tracker
=============
Per-agent, per-boundary cost and usage tracking.

Tracks:
    - Tool calls (count, type, duration)
    - Token usage (input, output, total)
    - API costs (estimated from model + tokens)
    - External tool calls (web, file, API)

This is the "save money" feature — orgs see exactly where tokens go,
which agents are expensive, and what can be optimized.

Usage:
    tracker = CostTracker()
    tracker.record_tool_call("session-123", "codex", "search_nodes", duration_ms=150)
    tracker.record_tokens("session-123", "codex", input_tokens=500, output_tokens=200, model="gpt-4o")
    report = tracker.get_agent_report("session-123", "codex")
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Estimated cost per 1M tokens (USD) — used for cost estimation
MODEL_COSTS = {
    # OpenAI
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4.1": {"input": 2.00, "output": 8.00},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
    "o3": {"input": 2.00, "output": 8.00},
    "o4-mini": {"input": 1.10, "output": 4.40},
    "codex-mini": {"input": 1.50, "output": 6.00},
    # Anthropic
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "claude-opus-4": {"input": 15.00, "output": 75.00},
    "claude-haiku-3-5": {"input": 0.80, "output": 4.00},
    # Groq
    "gpt-oss-120b": {"input": 0.00, "output": 0.00},
    "gpt-oss-20b": {"input": 0.00, "output": 0.00},
    # Open source
    "llama-3.3-70b": {"input": 0.59, "output": 0.79},
    "deepseek-r1": {"input": 0.55, "output": 2.19},
}


@dataclass
class ToolCallRecord:
    """A single tool call record."""
    namespace: str
    agent_id: str
    agent_name: str
    tool_name: str
    tool_category: str
    duration_ms: int
    success: bool
    is_external: bool  # tool outside our registry
    timestamp: float


class CostTracker:
    """Persistent cost and usage tracking."""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS tool_calls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        namespace TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        agent_name TEXT DEFAULT '',
        tool_name TEXT NOT NULL,
        tool_category TEXT DEFAULT '',
        duration_ms INTEGER DEFAULT 0,
        success INTEGER DEFAULT 1,
        is_external INTEGER DEFAULT 0,
        args_summary TEXT DEFAULT '',
        result_length INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_tc_ns ON tool_calls(namespace);
    CREATE INDEX IF NOT EXISTS idx_tc_agent ON tool_calls(agent_id);
    CREATE INDEX IF NOT EXISTS idx_tc_tool ON tool_calls(tool_name);
    CREATE INDEX IF NOT EXISTS idx_tc_created ON tool_calls(created_at);

    CREATE TABLE IF NOT EXISTS token_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        namespace TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        agent_name TEXT DEFAULT '',
        model TEXT NOT NULL,
        input_tokens INTEGER DEFAULT 0,
        output_tokens INTEGER DEFAULT 0,
        total_tokens INTEGER DEFAULT 0,
        estimated_cost_usd REAL DEFAULT 0.0,
        operation TEXT DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_tu_ns ON token_usage(namespace);
    CREATE INDEX IF NOT EXISTS idx_tu_agent ON token_usage(agent_id);

    CREATE TABLE IF NOT EXISTS cost_summaries (
        namespace TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        period TEXT NOT NULL,
        total_tool_calls INTEGER DEFAULT 0,
        total_external_calls INTEGER DEFAULT 0,
        total_input_tokens INTEGER DEFAULT 0,
        total_output_tokens INTEGER DEFAULT 0,
        total_cost_usd REAL DEFAULT 0.0,
        avg_duration_ms REAL DEFAULT 0.0,
        PRIMARY KEY (namespace, agent_id, period)
    );

    CREATE TABLE IF NOT EXISTS shadow_counts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        namespace TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        actual_tokens INTEGER NOT NULL,
        baseline_tokens INTEGER NOT NULL,
        actual_chars INTEGER NOT NULL,
        baseline_chars INTEGER NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_shadow_ns ON shadow_counts(namespace);
    CREATE INDEX IF NOT EXISTS idx_shadow_created ON shadow_counts(created_at);
    """

    def __init__(self, db_path: str = "contextcore_data/cost_tracking.db"):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(self._SCHEMA)

    def record_tool_call(
        self,
        namespace: str,
        agent_id: str,
        tool_name: str,
        tool_category: str = "",
        duration_ms: int = 0,
        success: bool = True,
        is_external: bool = False,
        agent_name: str = "",
        args_summary: str = "",
        result_length: int = 0,
    ):
        """Record a tool call."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO tool_calls (namespace, agent_id, agent_name, tool_name, tool_category, "
            "duration_ms, success, is_external, args_summary, result_length, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (namespace, agent_id, agent_name, tool_name, tool_category,
             duration_ms, int(success), int(is_external), args_summary[:200], result_length, now),
        )
        # Update summary
        period = datetime.now(timezone.utc).strftime("%Y-%m")
        self._conn.execute(
            "INSERT INTO cost_summaries (namespace, agent_id, period, total_tool_calls, total_external_calls) "
            "VALUES (?, ?, ?, 1, ?) "
            "ON CONFLICT(namespace, agent_id, period) "
            "DO UPDATE SET total_tool_calls = total_tool_calls + 1, "
            "total_external_calls = total_external_calls + ?",
            (namespace, agent_id, period, int(is_external), int(is_external)),
        )
        self._conn.commit()

    def record_tokens(
        self,
        namespace: str,
        agent_id: str,
        input_tokens: int,
        output_tokens: int,
        model: str,
        operation: str = "",
        agent_name: str = "",
    ):
        """Record token usage and estimate cost."""
        total = input_tokens + output_tokens
        cost = self._estimate_cost(model, input_tokens, output_tokens)
        now = datetime.now(timezone.utc).isoformat()

        self._conn.execute(
            "INSERT INTO token_usage (namespace, agent_id, agent_name, model, "
            "input_tokens, output_tokens, total_tokens, estimated_cost_usd, operation, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (namespace, agent_id, agent_name, model, input_tokens, output_tokens, total, cost, operation, now),
        )
        period = datetime.now(timezone.utc).strftime("%Y-%m")
        self._conn.execute(
            "INSERT INTO cost_summaries (namespace, agent_id, period, total_input_tokens, total_output_tokens, total_cost_usd) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(namespace, agent_id, period) "
            "DO UPDATE SET total_input_tokens = total_input_tokens + ?, "
            "total_output_tokens = total_output_tokens + ?, "
            "total_cost_usd = total_cost_usd + ?",
            (namespace, agent_id, period, input_tokens, output_tokens, cost, input_tokens, output_tokens, cost),
        )
        self._conn.commit()
        return cost

    def get_agent_report(self, namespace: str, agent_id: str, period: Optional[str] = None) -> Dict[str, Any]:
        """Get cost/usage report for an agent in a boundary."""
        if period is None:
            period = datetime.now(timezone.utc).strftime("%Y-%m")

        summary = self._conn.execute(
            "SELECT * FROM cost_summaries WHERE namespace = ? AND agent_id = ? AND period = ?",
            (namespace, agent_id, period),
        ).fetchone()

        # Top tools by call count
        top_tools = self._conn.execute(
            "SELECT tool_name, COUNT(*) as calls, AVG(duration_ms) as avg_ms, "
            "SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failures "
            "FROM tool_calls WHERE namespace = ? AND agent_id = ? AND substr(created_at, 1, 7) = ? "
            "GROUP BY tool_name ORDER BY calls DESC LIMIT 10",
            (namespace, agent_id, period),
        ).fetchall()

        # Token usage by model
        by_model = self._conn.execute(
            "SELECT model, SUM(input_tokens) as input_t, SUM(output_tokens) as output_t, "
            "SUM(estimated_cost_usd) as cost "
            "FROM token_usage WHERE namespace = ? AND agent_id = ? AND substr(created_at, 1, 7) = ? "
            "GROUP BY model",
            (namespace, agent_id, period),
        ).fetchall()

        return {
            "agent_id": agent_id,
            "namespace": namespace,
            "period": period,
            "summary": {
                "total_tool_calls": summary["total_tool_calls"] if summary else 0,
                "total_external_calls": summary["total_external_calls"] if summary else 0,
                "total_input_tokens": summary["total_input_tokens"] if summary else 0,
                "total_output_tokens": summary["total_output_tokens"] if summary else 0,
                "total_cost_usd": round(summary["total_cost_usd"], 4) if summary else 0,
            },
            "top_tools": [
                {"tool": r["tool_name"], "calls": r["calls"],
                 "avg_ms": round(r["avg_ms"], 1), "failures": r["failures"]}
                for r in top_tools
            ],
            "by_model": [
                {"model": r["model"], "input_tokens": r["input_t"],
                 "output_tokens": r["output_t"], "cost_usd": round(r["cost"], 4)}
                for r in by_model
            ],
        }

    def get_boundary_report(self, namespace: str, period: Optional[str] = None) -> Dict[str, Any]:
        """Get aggregate cost report for an entire boundary."""
        if period is None:
            period = datetime.now(timezone.utc).strftime("%Y-%m")

        agents = self._conn.execute(
            "SELECT agent_id, total_tool_calls, total_external_calls, "
            "total_input_tokens, total_output_tokens, total_cost_usd "
            "FROM cost_summaries WHERE namespace = ? AND period = ? "
            "ORDER BY total_cost_usd DESC",
            (namespace, period),
        ).fetchall()

        total_cost = sum(r["total_cost_usd"] for r in agents)
        total_tokens = sum(r["total_input_tokens"] + r["total_output_tokens"] for r in agents)
        total_calls = sum(r["total_tool_calls"] for r in agents)

        # External tool breakdown
        external = self._conn.execute(
            "SELECT tool_name, COUNT(*) as calls, agent_id "
            "FROM tool_calls WHERE namespace = ? AND is_external = 1 AND substr(created_at, 1, 7) = ? "
            "GROUP BY tool_name, agent_id ORDER BY calls DESC LIMIT 20",
            (namespace, period),
        ).fetchall()

        return {
            "namespace": namespace,
            "period": period,
            "totals": {
                "cost_usd": round(total_cost, 4),
                "tokens": total_tokens,
                "tool_calls": total_calls,
                "agents": len(agents),
            },
            "agents": [
                {
                    "agent_id": r["agent_id"],
                    "tool_calls": r["total_tool_calls"],
                    "external_calls": r["total_external_calls"],
                    "tokens": r["total_input_tokens"] + r["total_output_tokens"],
                    "cost_usd": round(r["total_cost_usd"], 4),
                }
                for r in agents
            ],
            "external_tools": [
                {"tool": r["tool_name"], "calls": r["calls"], "agent_id": r["agent_id"]}
                for r in external
            ],
            "savings_tips": self._generate_savings_tips(namespace, period),
        }

    def get_external_tool_log(self, namespace: str, agent_id: str = "", limit: int = 50) -> List[Dict[str, Any]]:
        """Get log of external tool calls for observability."""
        query = (
            "SELECT agent_id, agent_name, tool_name, args_summary, duration_ms, success, created_at "
            "FROM tool_calls WHERE namespace = ? AND is_external = 1"
        )
        params: list = [namespace]
        if agent_id:
            query += " AND agent_id = ?"
            params.append(agent_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(query, params).fetchall()
        return [
            {
                "agent_id": r["agent_id"],
                "agent_name": r["agent_name"],
                "tool": r["tool_name"],
                "args": r["args_summary"],
                "duration_ms": r["duration_ms"],
                "success": bool(r["success"]),
                "timestamp": r["created_at"],
            }
            for r in rows
        ]

    def _estimate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Estimate cost in USD."""
        # Try exact match, then prefix match
        costs = MODEL_COSTS.get(model)
        if not costs:
            for key in MODEL_COSTS:
                if key in model or model in key:
                    costs = MODEL_COSTS[key]
                    break
        if not costs:
            costs = {"input": 1.0, "output": 3.0}  # conservative default

        input_cost = (input_tokens / 1_000_000) * costs["input"]
        output_cost = (output_tokens / 1_000_000) * costs["output"]
        return round(input_cost + output_cost, 6)

    def _generate_savings_tips(self, namespace: str, period: str) -> List[str]:
        """Generate actionable cost-saving recommendations."""
        tips = []

        # Check for expensive models on simple tasks
        expensive_simple = self._conn.execute(
            "SELECT model, operation, AVG(output_tokens) as avg_out "
            "FROM token_usage WHERE namespace = ? AND substr(created_at, 1, 7) = ? "
            "GROUP BY model, operation HAVING avg_out < 200 AND model LIKE '%opus%'",
            (namespace, period),
        ).fetchall()
        if expensive_simple:
            tips.append(
                "Some operations use expensive models (Opus/GPT-4o) for short outputs. "
                "Consider using Haiku/GPT-4o-mini for simple tasks to save 80%+ on those calls."
            )

        # Check for high failure rates
        failures = self._conn.execute(
            "SELECT tool_name, COUNT(*) as total, "
            "SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as fails "
            "FROM tool_calls WHERE namespace = ? AND substr(created_at, 1, 7) = ? "
            "GROUP BY tool_name HAVING fails > 0",
            (namespace, period),
        ).fetchall()
        high_fail = [r for r in failures if r["fails"] / r["total"] > 0.3]
        if high_fail:
            names = ", ".join(r["tool_name"] for r in high_fail[:3])
            tips.append(
                f"High failure rate on tools: {names}. "
                "Failed calls still cost tokens. Fix the root cause to reduce waste."
            )

        # Check for redundant searches
        search_count = self._conn.execute(
            "SELECT COUNT(*) as c FROM tool_calls "
            "WHERE namespace = ? AND tool_name = 'search_nodes' AND substr(created_at, 1, 7) = ?",
            (namespace, period),
        ).fetchone()
        if search_count and search_count["c"] > 50:
            tips.append(
                f"{search_count['c']} search_nodes calls this month. "
                "Consider using briefing/ask tools which pre-load context, reducing redundant searches."
            )

        # Check external call volume
        ext_count = self._conn.execute(
            "SELECT COUNT(*) as c FROM tool_calls "
            "WHERE namespace = ? AND is_external = 1 AND substr(created_at, 1, 7) = ?",
            (namespace, period),
        ).fetchone()
        if ext_count and ext_count["c"] > 20:
            tips.append(
                f"{ext_count['c']} external tool calls this month. "
                "Route external APIs through cached proxies where possible."
            )

        if not tips:
            tips.append("Usage looks efficient. No immediate savings opportunities detected.")

        return tips


    # ------------------------------------------------------------------
    # Shadow Token Counting — real baseline comparison
    # ------------------------------------------------------------------

    def record_shadow(
        self,
        namespace: str,
        agent_id: str,
        tool_name: str,
        actual_chars: int,
        baseline_chars: int,
    ):
        """Record actual vs baseline context size for a query.

        actual_chars: characters actually sent to agent (with AIContextDB)
        baseline_chars: characters that would be sent without shared context (full graph dump)
        """
        actual_tokens = actual_chars // 4  # ~4 chars per token
        baseline_tokens = baseline_chars // 4
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO shadow_counts (namespace, agent_id, tool_name, "
            "actual_tokens, baseline_tokens, actual_chars, baseline_chars, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (namespace, agent_id, tool_name, actual_tokens, baseline_tokens,
             actual_chars, baseline_chars, now),
        )
        self._conn.commit()

    def get_shadow_savings(self, namespace: Optional[str] = None, period: Optional[str] = None) -> Dict[str, Any]:
        """Get real measured savings from shadow counting."""
        if period is None:
            period = datetime.now(timezone.utc).strftime("%Y-%m")

        if namespace:
            row = self._conn.execute(
                "SELECT COUNT(*) as queries, "
                "SUM(actual_tokens) as actual_tokens, "
                "SUM(baseline_tokens) as baseline_tokens, "
                "SUM(actual_chars) as actual_chars, "
                "SUM(baseline_chars) as baseline_chars "
                "FROM shadow_counts WHERE namespace = ? AND substr(created_at, 1, 7) = ?",
                (namespace, period),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) as queries, "
                "SUM(actual_tokens) as actual_tokens, "
                "SUM(baseline_tokens) as baseline_tokens, "
                "SUM(actual_chars) as actual_chars, "
                "SUM(baseline_chars) as baseline_chars "
                "FROM shadow_counts WHERE substr(created_at, 1, 7) = ?",
                (period,),
            ).fetchone()

        if not row or not row["queries"]:
            return {"queries": 0, "has_data": False}

        actual = row["actual_tokens"] or 0
        baseline = row["baseline_tokens"] or 0
        saved = max(0, baseline - actual)
        pct = round(saved / baseline * 100, 1) if baseline > 0 else 0

        return {
            "has_data": True,
            "queries": row["queries"],
            "actual_tokens": actual,
            "baseline_tokens": baseline,
            "tokens_saved": saved,
            "savings_pct": pct,
            "actual_chars": row["actual_chars"] or 0,
            "baseline_chars": row["baseline_chars"] or 0,
            "avg_actual_tokens_per_query": round(actual / row["queries"]) if row["queries"] else 0,
            "avg_baseline_tokens_per_query": round(baseline / row["queries"]) if row["queries"] else 0,
            "period": period,
        }


# Global singleton
_tracker = None


def get_cost_tracker() -> CostTracker:
    global _tracker
    if _tracker is None:
        _tracker = CostTracker()
    return _tracker
