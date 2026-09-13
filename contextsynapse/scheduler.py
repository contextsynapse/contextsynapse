"""
Pipeline Scheduler
==================
Lightweight scheduled pipeline execution using threading + Redis.

Supports: manual, hourly, daily, weekly schedules.
Each schedule is stored in Redis and checked by a background thread.

Usage:
    scheduler = PipelineScheduler(redis_url="redis://localhost:6379/0")
    scheduler.schedule(session_id, prompt, interval="daily", lead_agent_id="abc")
    scheduler.start()  # begins background thread
    scheduler.stop()
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = {
    "hourly": 3600,
    "daily": 86400,
    "weekly": 604800,
    "every_6h": 21600,
    "every_12h": 43200,
}


class PipelineScheduler:
    """Background scheduler for pipeline runs."""

    def __init__(self, redis_url: str = None):
        self._redis = None
        self._running = False
        self._thread = None
        self._run_callback = None  # set by api.py

        if redis_url:
            try:
                import redis
                self._redis = redis.Redis.from_url(redis_url, decode_responses=True)
                self._redis.ping()
                logger.info("Scheduler connected to Redis")
            except Exception as e:
                logger.warning("Scheduler Redis connection failed: %s — using in-memory", e)
                self._redis = None

        # In-memory fallback
        self._schedules: Dict[str, Dict] = {}
        self._load_from_redis()

    def _load_from_redis(self):
        """Load schedules from Redis on startup."""
        if not self._redis:
            return
        try:
            keys = self._redis.keys("schedule:*")
            for key in keys:
                data = self._redis.get(key)
                if data:
                    schedule = json.loads(data)
                    self._schedules[schedule["schedule_id"]] = schedule
            logger.info("Loaded %d schedules from Redis", len(self._schedules))
        except Exception:
            pass

    def _save_to_redis(self, schedule: Dict):
        """Persist a schedule to Redis."""
        if self._redis:
            try:
                self._redis.set(
                    f"schedule:{schedule['schedule_id']}",
                    json.dumps(schedule, default=str),
                )
            except Exception:
                pass

    def _delete_from_redis(self, schedule_id: str):
        if self._redis:
            try:
                self._redis.delete(f"schedule:{schedule_id}")
            except Exception:
                pass

    def schedule(
        self,
        session_id: str,
        prompt: str,
        interval: str = "daily",
        lead_agent_id: str = None,
        max_turns: int = 10,
    ) -> Dict[str, Any]:
        """Create or update a scheduled pipeline run."""
        import secrets

        if interval not in INTERVAL_SECONDS and interval != "manual":
            raise ValueError(f"Invalid interval: {interval}. Use: {list(INTERVAL_SECONDS.keys())}")

        # Check if schedule already exists for this session
        existing = next(
            (s for s in self._schedules.values() if s["session_id"] == session_id and s["status"] == "active"),
            None,
        )
        schedule_id = existing["schedule_id"] if existing else secrets.token_hex(8)

        schedule = {
            "schedule_id": schedule_id,
            "session_id": session_id,
            "prompt": prompt,
            "interval": interval,
            "interval_seconds": INTERVAL_SECONDS.get(interval, 0),
            "lead_agent_id": lead_agent_id,
            "max_turns": max_turns,
            "status": "active",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_run_at": None,
            "next_run_at": (datetime.now(timezone.utc) + timedelta(seconds=INTERVAL_SECONDS.get(interval, 86400))).isoformat(),
            "run_count": 0,
            "last_error": None,
        }

        self._schedules[schedule_id] = schedule
        self._save_to_redis(schedule)
        logger.info("Schedule %s created: %s every %s", schedule_id, session_id[:12], interval)
        return schedule

    def unschedule(self, schedule_id: str) -> bool:
        """Remove a schedule."""
        if schedule_id in self._schedules:
            self._schedules[schedule_id]["status"] = "disabled"
            self._save_to_redis(self._schedules[schedule_id])
            return True
        return False

    def list_schedules(self, session_id: str = None) -> List[Dict]:
        """List all schedules, optionally filtered by session."""
        schedules = list(self._schedules.values())
        if session_id:
            schedules = [s for s in schedules if s["session_id"] == session_id]
        return sorted(schedules, key=lambda s: s.get("created_at", ""), reverse=True)

    def get_schedule(self, schedule_id: str) -> Optional[Dict]:
        return self._schedules.get(schedule_id)

    def set_callback(self, callback):
        """Set the function to call when a pipeline should run.

        Signature: callback(session_id, prompt, lead_agent_id, max_turns)
        """
        self._run_callback = callback

    def start(self):
        """Start the background scheduler thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="pipeline-scheduler")
        self._thread.start()
        logger.info("Pipeline scheduler started")

    def stop(self):
        """Stop the scheduler."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Pipeline scheduler stopped")

    def _loop(self):
        """Main scheduler loop — checks every 60s for due schedules."""
        while self._running:
            try:
                now = datetime.now(timezone.utc)
                for sid, schedule in list(self._schedules.items()):
                    if schedule["status"] != "active":
                        continue
                    if schedule["interval"] == "manual":
                        continue

                    next_run = schedule.get("next_run_at")
                    if not next_run:
                        continue

                    if now.isoformat() >= next_run:
                        self._execute(schedule)
            except Exception as e:
                logger.error("Scheduler loop error: %s", e)

            # Sleep 60 seconds between checks
            for _ in range(60):
                if not self._running:
                    break
                time.sleep(1)

    def _execute(self, schedule: Dict):
        """Execute a scheduled pipeline run."""
        schedule_id = schedule["schedule_id"]
        logger.info("Executing scheduled pipeline: %s (session: %s)",
                     schedule_id, schedule["session_id"][:12])

        try:
            if self._run_callback:
                self._run_callback(
                    session_id=schedule["session_id"],
                    prompt=schedule["prompt"],
                    lead_agent_id=schedule.get("lead_agent_id"),
                    max_turns=schedule.get("max_turns", 10),
                )

            # Update schedule
            now = datetime.now(timezone.utc)
            schedule["last_run_at"] = now.isoformat()
            schedule["run_count"] = schedule.get("run_count", 0) + 1
            schedule["next_run_at"] = (
                now + timedelta(seconds=schedule.get("interval_seconds", 86400))
            ).isoformat()
            schedule["last_error"] = None

        except Exception as e:
            schedule["last_error"] = str(e)
            logger.error("Scheduled pipeline failed: %s", e)

        self._save_to_redis(schedule)
