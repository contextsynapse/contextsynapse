"""Pipeline Health Monitor — detect and alert on repeated failures.

Tracks pipeline run outcomes and fires alerts when a pipeline
has consecutive failures exceeding a threshold.

Usage:
    from contextsynapse.intelligence.pipeline_health import PipelineHealthMonitor

    monitor = PipelineHealthMonitor(alert_dispatcher=dispatcher)
    monitor.record_run("TCS", "news", success=True)
    monitor.record_run("TCS", "news", success=False, error="timeout")
    monitor.record_run("TCS", "news", success=False, error="timeout")
    monitor.record_run("TCS", "news", success=False, error="timeout")
    # → alert fired: "TCS/news has failed 3 consecutive times"
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PipelineHealth:
    """Health status for a single pipeline."""
    context_name: str
    pipeline_name: str
    consecutive_failures: int = 0
    total_runs: int = 0
    total_failures: int = 0
    last_run: str = ""
    last_error: str = ""
    status: str = "healthy"  # healthy | degraded | failing

    def to_dict(self) -> Dict[str, Any]:
        return {
            "context": self.context_name,
            "pipeline": self.pipeline_name,
            "status": self.status,
            "consecutive_failures": self.consecutive_failures,
            "total_runs": self.total_runs,
            "total_failures": self.total_failures,
            "failure_rate": round(self.total_failures / max(1, self.total_runs), 2),
            "last_run": self.last_run,
            "last_error": self.last_error,
        }


class PipelineHealthMonitor:
    """Monitors pipeline health and alerts on repeated failures."""

    def __init__(self, alert_dispatcher=None, failure_threshold: int = 3):
        self._health: Dict[str, PipelineHealth] = {}  # "ctx:pipe" -> health
        self._alert_dispatcher = alert_dispatcher
        self._failure_threshold = failure_threshold

    def _key(self, context: str, pipeline: str) -> str:
        return f"{context}:{pipeline}"

    def record_run(self, context: str, pipeline: str,
                   success: bool, error: str = "") -> PipelineHealth:
        """Record a pipeline run outcome."""
        key = self._key(context, pipeline)
        health = self._health.get(key)
        if not health:
            health = PipelineHealth(context_name=context, pipeline_name=pipeline)
            self._health[key] = health

        health.total_runs += 1
        health.last_run = datetime.now(timezone.utc).isoformat()

        if success:
            health.consecutive_failures = 0
            health.status = "healthy"
        else:
            health.consecutive_failures += 1
            health.total_failures += 1
            health.last_error = error

            if health.consecutive_failures >= self._failure_threshold:
                health.status = "failing"
                self._fire_alert(health)
            elif health.consecutive_failures >= 2:
                health.status = "degraded"

        return health

    def _fire_alert(self, health: PipelineHealth):
        """Fire an alert for a failing pipeline."""
        if self._alert_dispatcher:
            try:
                self._alert_dispatcher.dispatch(
                    signal_type="pipeline_failure",
                    entity_name=f"{health.context_name}/{health.pipeline_name}",
                    context_name=health.context_name,
                    severity="alert",
                    details={
                        "consecutive_failures": health.consecutive_failures,
                        "last_error": health.last_error,
                        "total_failure_rate": round(health.total_failures / max(1, health.total_runs), 2),
                    },
                )
            except Exception as exc:
                logger.warning("[HEALTH] Alert dispatch failed: %s", exc)

        logger.warning("[HEALTH] Pipeline failing: %s/%s (%d consecutive failures: %s)",
                        health.context_name, health.pipeline_name,
                        health.consecutive_failures, health.last_error)

    def get_health(self, context: str = "", pipeline: str = "") -> List[Dict]:
        """Get health status for all or specific pipelines."""
        results = []
        for key, health in self._health.items():
            if context and health.context_name != context:
                continue
            if pipeline and health.pipeline_name != pipeline:
                continue
            results.append(health.to_dict())
        return results

    def get_failing(self) -> List[Dict]:
        """Get all failing pipelines."""
        return [h.to_dict() for h in self._health.values() if h.status == "failing"]

    def get_degraded(self) -> List[Dict]:
        """Get all degraded + failing pipelines."""
        return [h.to_dict() for h in self._health.values() if h.status in ("degraded", "failing")]
