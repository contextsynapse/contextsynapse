"""ConnectorScheduler — runs connectors on their configured intervals.

Usage:
    scheduler = ConnectorScheduler(registry, manager)
    results = scheduler.run_due()   # runs only due connectors
    results = scheduler.run_all()   # runs all connectors
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List

from .base import ConnectorManager
from .registry import ConnectorRegistry

logger = logging.getLogger(__name__)


class ConnectorScheduler:
    """Runs connectors based on their poll_interval_minutes."""

    def __init__(self, registry: ConnectorRegistry, manager: ConnectorManager):
        self._registry = registry
        self._manager = manager
        self._last_run: Dict[str, float] = {}  # connector_id → timestamp
        self._intervals: Dict[str, float] = {}  # connector_id → seconds

        # Initialize intervals from registry configs
        for config in registry.configs:
            cid = config.connector_id or config.name
            self._intervals[cid] = config.poll_interval_minutes * 60

        # Also pull intervals directly from manager's registered connectors
        # (when connectors are registered manually without going through registry YAML load)
        for cid, connector in manager._connectors.items():
            if cid not in self._intervals:
                self._intervals[cid] = connector.config.poll_interval_minutes * 60

        # Seed last_run=now for connectors with interval > 0 so they don't
        # all fire immediately on first run_due() call. Connectors with
        # interval==0 are left unseeded so they always fire immediately.
        now = time.time()
        for cid, interval in self._intervals.items():
            if interval > 0 and cid not in self._last_run:
                self._last_run[cid] = now

    def run_due(self) -> Dict[str, int]:
        """Run connectors whose interval has elapsed. Returns {id: doc_count}."""
        now = time.time()
        results = {}

        for cid, connector in self._manager._connectors.items():
            interval = self._intervals.get(cid, 300)  # default 5 min
            last = self._last_run.get(cid, 0)

            if (now - last) >= interval:
                docs = self._manager.poll_one(cid)
                results[cid] = len(docs)
                self._last_run[cid] = now
                if docs:
                    logger.info("[SCHEDULER] %s: %d new docs", cid, len(docs))

        return results

    def run_all(self) -> Dict[str, int]:
        """Run all connectors regardless of schedule."""
        results = self._manager.poll_all()
        now = time.time()
        for cid in results:
            self._last_run[cid] = now
        return results

    def get_status(self) -> List[Dict]:
        """Get status of all connectors."""
        statuses = self._manager.list_status()
        result = []
        for s in statuses:
            d = s.to_dict()
            # Enrich with connector name from config
            connector = self._manager._connectors.get(s.connector_id)
            if connector:
                d["name"] = connector.config.name
            d["last_scheduled_run"] = self._last_run.get(s.connector_id)
            d["interval_minutes"] = self._intervals.get(s.connector_id, 300) / 60
            result.append(d)
        return result
