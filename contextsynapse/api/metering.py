"""
Usage Metering
==============
Lightweight usage tracking for per-tenant metering and quota checks.

Records individual events and rolls them up into monthly summaries.
Designed to be called as a FastAPI background task (zero latency impact).

Usage::

    meter = UsageMeter()
    meter.record(tenant_id, "api_call")
    meter.record(tenant_id, "node_create", quantity=5)
    summary = meter.get_summary(tenant_id, "2026-03")
"""

from __future__ import annotations

import json
import logging
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class UsageMeter:
    """Persistent usage meter backed by SQLite."""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS usage_events (
        event_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        quantity INTEGER DEFAULT 1,
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_ue_tenant ON usage_events(tenant_id);
    CREATE INDEX IF NOT EXISTS idx_ue_type ON usage_events(event_type);
    CREATE INDEX IF NOT EXISTS idx_ue_created ON usage_events(created_at);

    CREATE TABLE IF NOT EXISTS usage_summaries (
        tenant_id TEXT NOT NULL,
        period TEXT NOT NULL,
        event_type TEXT NOT NULL,
        total_count INTEGER DEFAULT 0,
        PRIMARY KEY (tenant_id, period, event_type)
    );
    """

    EVENT_TYPES = {
        "api_call", "node_create", "edge_create", "query",
        "ingest", "context_build", "search",
    }

    def __init__(self, db_path: str = "contextcore_data/metering.db"):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(self._SCHEMA)

    # ------------------------------------------------------------------
    # Record
    # ------------------------------------------------------------------

    def record(self, tenant_id: str, event_type: str, quantity: int = 1) -> None:
        """Record a usage event and update the monthly summary."""
        now = datetime.now(timezone.utc)
        event_id = secrets.token_hex(8)
        period = now.strftime("%Y-%m")

        self._conn.execute(
            "INSERT INTO usage_events (event_id, tenant_id, event_type, quantity, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (event_id, tenant_id, event_type, quantity, now.isoformat()),
        )

        # Upsert summary
        self._conn.execute(
            "INSERT INTO usage_summaries (tenant_id, period, event_type, total_count) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(tenant_id, period, event_type) "
            "DO UPDATE SET total_count = total_count + ?",
            (tenant_id, period, event_type, quantity, quantity),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_summary(self, tenant_id: str, period: Optional[str] = None) -> Dict[str, int]:
        """Get usage summary for a tenant in a given period (default: current month).

        Returns dict like {"api_call": 150, "node_create": 42, ...}
        """
        if period is None:
            period = datetime.now(timezone.utc).strftime("%Y-%m")

        rows = self._conn.execute(
            "SELECT event_type, total_count FROM usage_summaries "
            "WHERE tenant_id = ? AND period = ?",
            (tenant_id, period),
        ).fetchall()

        return {r["event_type"]: r["total_count"] for r in rows}

    def get_daily_breakdown(self, tenant_id: str, period: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get daily usage breakdown for charts.

        Returns list of {"date": "2026-03-14", "api_call": 20, "query": 5, ...}
        """
        if period is None:
            period = datetime.now(timezone.utc).strftime("%Y-%m")

        rows = self._conn.execute(
            "SELECT substr(created_at, 1, 10) as date, event_type, SUM(quantity) as total "
            "FROM usage_events "
            "WHERE tenant_id = ? AND substr(created_at, 1, 7) = ? "
            "GROUP BY date, event_type "
            "ORDER BY date",
            (tenant_id, period),
        ).fetchall()

        # Pivot into per-day dicts
        days: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            d = r["date"]
            if d not in days:
                days[d] = {"date": d}
            days[d][r["event_type"]] = r["total"]

        return list(days.values())

    def get_recent_events(self, tenant_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent usage events for activity feed."""
        rows = self._conn.execute(
            "SELECT event_type, quantity, created_at FROM usage_events "
            "WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?",
            (tenant_id, limit),
        ).fetchall()

        return [
            {"event_type": r["event_type"], "quantity": r["quantity"], "created_at": r["created_at"]}
            for r in rows
        ]

    def close(self):
        if self._conn:
            self._conn.close()
