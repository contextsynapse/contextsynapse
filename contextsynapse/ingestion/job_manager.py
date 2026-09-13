"""Ingestion Job Manager — background job queue for all ingestion work.

Every ingestion (URL, text, file, re-process, cleanse) creates a Job.
Jobs run in background threads. Review mode creates sub-jobs for per-item approval.

Job lifecycle:
  queued -> running -> completed | failed
  queued -> running -> review_pending (has sub-jobs) -> completed

Sub-job lifecycle (review mode):
  pending -> accepted | rejected
"""
from __future__ import annotations

import logging
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    REVIEW_PENDING = "review_pending"
    COMPLETED = "completed"
    FAILED = "failed"


class SubJobStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass
class SubJob:
    """A reviewable item within a parent job."""
    sub_job_id: str = ""
    parent_job_id: str = ""
    item_type: str = ""        # passage | entity | fact
    label: str = ""            # display label
    content_preview: str = ""  # text preview
    properties: Dict[str, Any] = field(default_factory=dict)
    status: SubJobStatus = SubJobStatus.PENDING
    _staging_id: str = ""      # links to staging item for commit/skip

    def to_dict(self) -> Dict:
        return {
            "sub_job_id": self.sub_job_id,
            "item_type": self.item_type,
            "label": self.label,
            "content_preview": self.content_preview,
            "properties": self.properties,
            "status": self.status.value,
        }


@dataclass
class LogEntry:
    """A single log entry in a job."""
    timestamp: str
    stage: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    level: str = "info"       # "info" | "debug"
    duration_ms: float = 0.0  # stage timing

    def to_dict(self) -> Dict:
        d = {"timestamp": self.timestamp, "stage": self.stage, "message": self.message, "details": self.details}
        if self.level != "info":
            d["level"] = self.level
        if self.duration_ms:
            d["duration_ms"] = self.duration_ms
        return d


@dataclass
class Job:
    """An ingestion job that runs in background."""
    job_id: str = ""
    job_type: str = ""         # ingest_url | ingest_text | reprocess | cleanse
    status: JobStatus = JobStatus.QUEUED
    pipeline: str = ""
    mode: str = "auto"         # auto | review
    debug: bool = False        # debug mode — detailed stage/filter/LLM logging
    context_id: str = ""       # target context/graph
    input_summary: str = ""    # URL or text preview
    created_at: str = ""
    started_at: str = ""
    completed_at: str = ""
    progress: str = ""         # current stage name
    result: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    log: List[LogEntry] = field(default_factory=list)
    sub_jobs: List[SubJob] = field(default_factory=list)
    _fn: Optional[Callable] = field(default=None, repr=False)
    _args: tuple = field(default_factory=tuple, repr=False)
    _kwargs: Dict = field(default_factory=dict, repr=False)

    def add_log(self, stage: str, message: str, details: Dict = None):
        self.log.append(LogEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            stage=stage,
            message=message,
            details=details or {},
        ))

    def add_debug_log(self, stage: str, message: str, details: Dict = None):
        """Add a debug-level log entry. No-op when debug=False (zero overhead)."""
        if not self.debug:
            return
        self.log.append(LogEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            stage=stage,
            message=message,
            details=details or {},
            level="debug",
        ))

    def to_dict(self) -> Dict:
        d = {
            "job_id": self.job_id,
            "job_type": self.job_type,
            "status": self.status.value,
            "pipeline": self.pipeline,
            "mode": self.mode,
            "context_id": self.context_id,
            "input_summary": self.input_summary,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
            "log": [entry.to_dict() for entry in self.log],
        }
        if self.sub_jobs:
            d["sub_jobs"] = [sj.to_dict() for sj in self.sub_jobs]
            d["sub_jobs_total"] = len(self.sub_jobs)
            d["sub_jobs_accepted"] = sum(1 for sj in self.sub_jobs if sj.status == SubJobStatus.ACCEPTED)
            d["sub_jobs_rejected"] = sum(1 for sj in self.sub_jobs if sj.status == SubJobStatus.REJECTED)
            d["sub_jobs_pending"] = sum(1 for sj in self.sub_jobs if sj.status == SubJobStatus.PENDING)
        return d


class JobManager:
    """Manages background ingestion jobs."""

    # Maximum time a job can run before being auto-killed (seconds)
    # Embedding 500+ nodes via Ollama + graph ops can take 30-60 minutes
    JOB_TIMEOUT_S = int(os.environ.get("CONTEXTSYNAPSE_JOB_TIMEOUT") or os.environ.get("AICONTEXTDB_JOB_TIMEOUT", "7200"))  # 2 hours default

    def __init__(self, max_workers: int = 4):
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self._max_workers = max_workers
        self._active_count = 0
        self._redis = None
        # Try Redis for persistence
        try:
            import os, redis
            url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
            if url:
                self._redis = redis.from_url(url, decode_responses=True)
                self._load_from_redis()
        except Exception:
            pass

        # Start watchdog thread to auto-kill stuck jobs
        self._watchdog = threading.Thread(target=self._watchdog_loop, daemon=True, name="job-watchdog")
        self._watchdog.start()

    def _watchdog_loop(self):
        """Periodically check for stuck jobs and auto-fail them."""
        import time as _time
        while True:
            try:
                _time.sleep(30)  # Check every 30 seconds
                self._cleanup_stuck_jobs()
            except Exception as e:
                logger.debug("[WATCHDOG] Error: %s", e)

    def _cleanup_stuck_jobs(self):
        """Find and fail jobs that have been running longer than JOB_TIMEOUT_S."""
        now = datetime.now(timezone.utc)
        with self._lock:
            for job in list(self._jobs.values()):
                if job.status != JobStatus.RUNNING:
                    continue
                if not job.started_at:
                    continue
                try:
                    started = datetime.fromisoformat(job.started_at)
                    if started.tzinfo is None:
                        started = started.replace(tzinfo=timezone.utc)
                    elapsed = (now - started).total_seconds()
                    if elapsed > self.JOB_TIMEOUT_S:
                        job.status = JobStatus.FAILED
                        job.error = f"Timed out after {int(elapsed)}s (limit: {self.JOB_TIMEOUT_S}s)"
                        job.completed_at = now.isoformat()
                        job.add_log("watchdog", f"Job auto-killed: running for {int(elapsed)}s")
                        self._active_count = max(0, self._active_count - 1)
                        self._save_to_redis(job)
                        logger.warning("[WATCHDOG] Auto-killed stuck job %s after %ds: %s",
                                       job.job_id[:12], int(elapsed), job.input_summary[:40])
                except Exception:
                    pass

        # Also clean stuck jobs in Redis that aren't in memory (from previous server runs)
        if self._redis:
            try:
                import json
                data = self._redis.hgetall("contextcore:jobs")
                for job_id, job_json in data.items():
                    if job_id in self._jobs:
                        continue  # Already handled above
                    d = json.loads(job_json)
                    if d.get("status") == "running":
                        started = d.get("started_at", "")
                        if started:
                            try:
                                started_dt = datetime.fromisoformat(started)
                                if started_dt.tzinfo is None:
                                    started_dt = started_dt.replace(tzinfo=timezone.utc)
                                elapsed = (now - started_dt).total_seconds()
                                if elapsed > self.JOB_TIMEOUT_S:
                                    d["status"] = "failed"
                                    d["error"] = f"Timed out after {int(elapsed)}s (stale from previous server run)"
                                    d["completed_at"] = now.isoformat()
                                    self._redis.hset("contextcore:jobs", job_id, json.dumps(d))
                                    logger.warning("[WATCHDOG] Cleared stale Redis job %s (%ds old)", job_id[:12], int(elapsed))
                            except Exception:
                                pass
            except Exception:
                pass

    def _save_to_redis(self, job: Job):
        """Persist job to Redis."""
        if not self._redis:
            return
        try:
            import json
            self._redis.hset("contextcore:jobs", job.job_id, json.dumps(job.to_dict()))
            self._redis.expire("contextcore:jobs", 86400 * 7)  # keep 7 days
        except Exception:
            pass

    def _load_from_redis(self):
        """Load persisted jobs from Redis on startup."""
        if not self._redis:
            return
        try:
            import json
            data = self._redis.hgetall("contextcore:jobs")
            for job_id, job_json in data.items():
                if job_id not in self._jobs:
                    d = json.loads(job_json)
                    job = Job(
                        job_id=d.get("job_id", job_id),
                        job_type=d.get("job_type", ""),
                        status=JobStatus(d.get("status", "completed")),
                        pipeline=d.get("pipeline", ""),
                        mode=d.get("mode", "auto"),
                        context_id=d.get("context_id", ""),
                        input_summary=d.get("input_summary", ""),
                        created_at=d.get("created_at", ""),
                        started_at=d.get("started_at", ""),
                        completed_at=d.get("completed_at", ""),
                        progress=d.get("progress", ""),
                        result=d.get("result", {}),
                        error=d.get("error", ""),
                    )
                    # Restore log entries
                    for entry in d.get("log", []):
                        job.log.append(LogEntry(
                            timestamp=entry.get("timestamp", ""),
                            stage=entry.get("stage", ""),
                            message=entry.get("message", ""),
                            details=entry.get("details", {}),
                        ))
                    self._jobs[job_id] = job
        except Exception:
            pass

    def submit(
        self,
        job_type: str,
        fn: Callable,
        *args,
        pipeline: str = "",
        mode: str = "auto",
        context_id: str = "",
        input_summary: str = "",
        debug: bool = False,
        **kwargs,
    ) -> Job:
        """Submit a new job. Returns immediately, job runs in background.

        Note: pipeline, mode, context_id, input_summary are JOB metadata.
        All other **kwargs are passed to the function. If the function also
        needs pipeline/mode, include them in **kwargs explicitly.
        """
        # Ensure pipeline and mode are also in kwargs for the function
        if 'pipeline' not in kwargs:
            kwargs['pipeline'] = pipeline
        if 'mode' not in kwargs:
            kwargs['mode'] = mode

        job = Job(
            job_id=str(uuid.uuid4()),
            job_type=job_type,
            pipeline=pipeline,
            mode=mode,
            debug=debug,
            context_id=context_id,
            input_summary=input_summary[:200],
            created_at=datetime.now(timezone.utc).isoformat(),
            _fn=fn,
            _args=args,
            _kwargs=kwargs,
        )

        with self._lock:
            self._jobs[job.job_id] = job

        # Start in background thread
        thread = threading.Thread(target=self._run_job, args=(job,), daemon=True)
        thread.start()

        logger.info("Job submitted: %s (%s) %s", job.job_id[:12], job_type, input_summary[:60])
        return job

    def _run_job(self, job: Job):
        """Execute job in background thread."""
        try:
            with self._lock:
                self._active_count += 1
            job.status = JobStatus.RUNNING
            job.started_at = datetime.now(timezone.utc).isoformat()

            job.add_log("system", "Job started", {"job_type": job.job_type, "mode": job.mode})

            # Progress callback — logs each stage + saves to Redis for real-time visibility
            import time as _time
            _stage_start = [_time.monotonic()]

            def on_stage(stage_name, details=None):
                now = _time.monotonic()
                duration = (now - _stage_start[0]) * 1000
                _stage_start[0] = now
                job.progress = stage_name
                entry = LogEntry(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    stage=stage_name,
                    message=f"Stage: {stage_name}",
                    details=details or {},
                    duration_ms=round(duration, 1),
                )
                job.log.append(entry)
                self._save_to_redis(job)  # persist each step for real-time log viewing

            # Build clean kwargs for the function (strip job metadata)
            fn_kwargs = {k: v for k, v in job._kwargs.items()
                         if k not in ('context_id', 'input_summary')}
            fn_kwargs["on_stage"] = on_stage
            fn_kwargs["debug"] = job.debug

            # Run the function
            result = job._fn(*job._args, **fn_kwargs)

            # Check if result is a StagingResult (review mode)
            from .smart_ingest import StagingResult
            if isinstance(result, StagingResult) and job.mode == "review":
                # Create sub-jobs from staging preview
                job.status = JobStatus.REVIEW_PENDING
                job.result = result.to_dict()

                for p in result.passages:
                    job.sub_jobs.append(SubJob(
                        sub_job_id=str(uuid.uuid4()),
                        parent_job_id=job.job_id,
                        item_type="passage",
                        label=f"Passage {p.get('chunk_index', '?')}",
                        content_preview=p.get("content", "")[:150],
                        properties=p,
                        _staging_id=p.get("_staging_id", ""),
                    ))
                for e in result.entities:
                    job.sub_jobs.append(SubJob(
                        sub_job_id=str(uuid.uuid4()),
                        parent_job_id=job.job_id,
                        item_type="entity",
                        label=f"[{e.get('label', '')}] {e.get('name', '')}",
                        content_preview=e.get("name", ""),
                        properties=e,
                        _staging_id=e.get("_staging_id", ""),
                    ))
                for f in result.facts:
                    job.sub_jobs.append(SubJob(
                        sub_job_id=str(uuid.uuid4()),
                        parent_job_id=job.job_id,
                        item_type="fact",
                        label="Fact",
                        content_preview=f.get("statement", "")[:150],
                        properties=f,
                        _staging_id=f.get("_staging_id", ""),
                    ))

                # Store staging reference for later commit
                job._kwargs["_staging"] = result
                job.add_log("review", f"Review pending: {len(job.sub_jobs)} items to review")
                self._save_to_redis(job)
                logger.info("Job %s review_pending: %d sub-jobs", job.job_id[:12], len(job.sub_jobs))

            else:
                # Auto mode or BuildResult — completed
                job.status = JobStatus.COMPLETED
                job.completed_at = datetime.now(timezone.utc).isoformat()
                if hasattr(result, 'document_id'):
                    suggestions = [s.to_dict() for s in getattr(result, 'schema_suggestions', [])]
                    job.result = {
                        "document_id": result.document_id,
                        "passages": len(getattr(result, 'passage_ids', [])),
                        "entities": len(getattr(result, 'entity_ids', {})),
                        "facts": len(getattr(result, 'fact_ids', [])),
                        "edges": getattr(result, 'edge_count', 0),
                    }
                    if suggestions:
                        job.result["schema_suggestions"] = suggestions
                        job.add_log("schema", f"Schema suggestions: {len(suggestions)} new types/edges discovered", {"suggestions": suggestions})
                elif isinstance(result, dict):
                    job.result = result
                else:
                    job.result = {"status": "done"}

                job.add_log("complete", "Job completed", job.result)
                self._save_to_redis(job)
                logger.info("Job %s completed", job.job_id[:12])

                # Persist graph to disk so it appears in graph listings
                if job._args:
                    try:
                        for arg in job._args:
                            if hasattr(arg, '_save_to_disk'):
                                arg._save_to_disk()
                                break
                    except Exception as e:
                        logger.warning("Post-ingest save_graph failed: %s", e)

        except Exception as e:
            job.status = JobStatus.FAILED
            job.error = str(e)
            job.completed_at = datetime.now(timezone.utc).isoformat()
            job.add_log("error", f"Job failed: {e}")
            self._save_to_redis(job)
            logger.error("Job %s failed: %s", job.job_id[:12], e)
        finally:
            with self._lock:
                self._active_count -= 1
            # Clear function references to free memory
            job._fn = None
            job._args = ()

    def get_job(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def list_jobs(self, context_id: str = "", limit: int = 50) -> List[Dict]:
        jobs = list(self._jobs.values())
        if context_id:
            # Match exact or partial (scoped context_id may have tenant prefix)
            jobs = [j for j in jobs if context_id in j.context_id or j.context_id in context_id or j.context_id == context_id]
        if not jobs and not context_id:
            # Return all jobs if no filter
            pass
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return [j.to_dict() for j in jobs[:limit]]

    def remove_job(self, job_id: str) -> bool:
        """Remove a job from tracking."""
        if job_id in self._jobs:
            del self._jobs[job_id]
            return True
        return False

    def clear_finished(self) -> int:
        """Remove all completed and failed jobs. Returns count removed."""
        finished = [jid for jid, j in self._jobs.items()
                    if j.status in (JobStatus.COMPLETED, JobStatus.FAILED)]
        for jid in finished:
            del self._jobs[jid]
        return len(finished)

    def accept_sub_job(self, job_id: str, sub_job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        for sj in job.sub_jobs:
            if sj.sub_job_id == sub_job_id:
                sj.status = SubJobStatus.ACCEPTED
                return True
        return False

    def reject_sub_job(self, job_id: str, sub_job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        for sj in job.sub_jobs:
            if sj.sub_job_id == sub_job_id:
                sj.status = SubJobStatus.REJECTED
                return True
        return False

    def accept_all(self, job_id: str) -> int:
        job = self._jobs.get(job_id)
        if not job:
            return 0
        count = 0
        for sj in job.sub_jobs:
            if sj.status == SubJobStatus.PENDING:
                sj.status = SubJobStatus.ACCEPTED
                count += 1
        return count

    def commit_reviewed(self, job_id: str, db) -> Dict:
        """Commit accepted sub-jobs to graph."""
        job = self._jobs.get(job_id)
        if not job or job.status != JobStatus.REVIEW_PENDING:
            return {"error": "Job not in review state"}

        staging = job._kwargs.get("_staging")
        if not staging:
            return {"error": "No staging data"}

        # Collect rejected staging IDs
        rejected_ids = [sj._staging_id for sj in job.sub_jobs if sj.status == SubJobStatus.REJECTED]

        try:
            result = staging.commit(db, exclude_ids=rejected_ids)
            job.status = JobStatus.COMPLETED
            job.completed_at = datetime.now(timezone.utc).isoformat()
            job.result = {
                "document_id": result.document_id,
                "passages": len(result.passage_ids),
                "entities": len(result.entity_ids),
                "facts": len(result.fact_ids),
                "edges": result.edge_count,
                "accepted": sum(1 for sj in job.sub_jobs if sj.status == SubJobStatus.ACCEPTED),
                "rejected": len(rejected_ids),
            }
            return job.result
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error = str(e)
            return {"error": str(e)}


# Singleton
_job_manager: Optional[JobManager] = None


def get_job_manager() -> JobManager:
    global _job_manager
    if _job_manager is None:
        _job_manager = JobManager()
    return _job_manager
