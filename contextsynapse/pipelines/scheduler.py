"""Background scheduler for pipeline execution."""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import List

from .models import Pipeline, PipelineStore

logger = logging.getLogger(__name__)


class PipelineScheduler:
    """Daemon thread that checks for due pipelines every tick_seconds."""

    def __init__(self, store: PipelineStore = None,
                 redis_url: str = None,
                 tick_seconds: int = None, max_workers: int = None):
        if store is None:
            store = PipelineStore(redis_url=redis_url)
        self._store = store
        self._tick = tick_seconds or int(os.environ.get("CONTEXTSYNAPSE_PIPELINE_TICK") or os.environ.get("AICONTEXTDB_PIPELINE_TICK", "60"))
        self._max_workers = max_workers or int(os.environ.get("CONTEXTSYNAPSE_MAX_PIPELINE_WORKERS") or os.environ.get("AICONTEXTDB_MAX_PIPELINE_WORKERS", "2"))
        self._thread = None
        self._running = False
        self._executor = ThreadPoolExecutor(max_workers=self._max_workers,
                                            thread_name_prefix="pipeline-worker")

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="pipeline-scheduler")
        self._thread.start()
        logger.info("[PIPELINE-SCHED] Started (tick=%ds, workers=%d)", self._tick, self._max_workers)

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        self._executor.shutdown(wait=False)
        logger.info("[PIPELINE-SCHED] Stopped")

    def _run(self):
        while self._running:
            try:
                self._tick_once()
            except Exception as e:
                logger.error("[PIPELINE-SCHED] Tick error: %s", e)
            time.sleep(self._tick)

    def _tick_once(self):
        due = self._get_due_pipelines()
        for pipeline in due:
            logger.info("[PIPELINE-SCHED] Submitting pipeline: %s", pipeline.name)
            self._executor.submit(self._run_pipeline, pipeline.id)

    def _get_due_pipelines(self) -> List[Pipeline]:
        now = datetime.now(timezone.utc).isoformat()
        due = []
        for p in self._store.list_all():
            if p.trigger_type != "scheduled":
                continue
            if p.status != "active":
                continue
            if not p.next_run_at:
                due.append(p)
                continue
            if p.next_run_at <= now:
                due.append(p)
        return due

    def _run_pipeline(self, pipeline_id: str):
        try:
            from .executor import execute_pipeline
            execute_pipeline(pipeline_id, self._store)
        except Exception as e:
            logger.error("[PIPELINE-SCHED] Pipeline %s failed: %s", pipeline_id, e)

    @property
    def store(self) -> PipelineStore:
        return self._store


class ContextPipelineScheduler:
    """Daemon thread that scans all contexts for due scheduled pipelines.

    Replaces the store-backed ``PipelineScheduler`` for pipelines whose config
    lives on a Context (``context.config["pipeline"]``).
    """

    def __init__(self, context_manager, graph_registry=None,
                 tick_seconds: int = None, max_workers: int = None):
        self._context_manager = context_manager
        self._graph_registry = graph_registry
        self._tick = tick_seconds or int(os.environ.get("CONTEXTSYNAPSE_PIPELINE_TICK") or os.environ.get("AICONTEXTDB_PIPELINE_TICK", "60"))
        self._max_workers = max_workers or int(os.environ.get("CONTEXTSYNAPSE_MAX_PIPELINE_WORKERS") or os.environ.get("AICONTEXTDB_MAX_PIPELINE_WORKERS", "2"))
        self._thread = None
        self._running = False
        self._executor = ThreadPoolExecutor(max_workers=self._max_workers,
                                            thread_name_prefix="ctx-pipeline-worker")

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="ctx-pipeline-scheduler")
        self._thread.start()
        logger.info("[CTX-PIPELINE-SCHED] Started (tick=%ds, workers=%d)", self._tick, self._max_workers)

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        self._executor.shutdown(wait=False)
        logger.info("[CTX-PIPELINE-SCHED] Stopped")

    def _run(self):
        while self._running:
            try:
                self._tick_once()
            except Exception as e:
                logger.error("[CTX-PIPELINE-SCHED] Tick error: %s", e)
            time.sleep(self._tick)

    def _tick_once(self):
        due = self._get_due_contexts()
        for ctx in due:
            logger.info("[CTX-PIPELINE-SCHED] Submitting context pipeline: %s (%s)", ctx.name, ctx.context_id)
            self._executor.submit(self._run_context_pipeline, ctx.context_id)

        # Run correlation engine for contexts with correlation templates
        try:
            self._run_correlations()
        except Exception as e:
            logger.debug("[CTX-PIPELINE-SCHED] Correlation tick error: %s", e)

    def _run_correlations(self):
        """Run scheduled correlation computations for contexts with templates."""
        try:
            from ..intelligence.correlation_engine import CorrelationEngine
            from ..intelligence.correlation_template import load_template

            contexts = self._context_manager.list_contexts(status="active")
            for ctx in contexts:
                config = ctx.config or {}
                collection = config.get("collection", {})
                template_name = collection.get("template", "")
                if not template_name:
                    continue

                template = load_template(template_name)
                if not template or not template.correlations:
                    continue

                # Only run daily (check if already ran today)
                schedule = config.get("correlation_schedule", {})
                last_corr = schedule.get("last_run", "")
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if last_corr and last_corr.startswith(today):
                    continue  # already ran today

                # Get the graph for this context
                ns = ctx.graph_namespace or ctx.context_id
                db = None
                if self._graph_registry:
                    db = self._graph_registry.get_graph(ns, load_if_missing=True)

                if not db:
                    continue

                engine = CorrelationEngine()
                result = engine.run_scheduled(template, db)

                if result.correlations_created or result.correlations_expired:
                    logger.info("[CORR-SCHED] %s: %d created, %d expired",
                                 ctx.name, result.correlations_created, result.correlations_expired)

                # Update last run timestamp
                config.setdefault("correlation_schedule", {})["last_run"] = datetime.now(timezone.utc).isoformat()
                try:
                    self._context_manager.update_context(ctx.context_id, config=config)
                except Exception:
                    pass

        except ImportError:
            pass  # intelligence module not available

    def _get_due_contexts(self):
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        due = []
        try:
            contexts = self._context_manager.list_contexts(status="active")
        except Exception as e:
            logger.error("[CTX-PIPELINE-SCHED] list_contexts failed: %s", e)
            return due
        for ctx in contexts:
            config = ctx.config or {}
            pipeline = config.get("pipeline", {})
            schedule = config.get("schedule", {})
            # trigger_type can be in schedule or pipeline (check both)
            trigger = schedule.get("trigger_type") or pipeline.get("trigger_type", "")
            if trigger != "scheduled":
                continue
            if schedule.get("status") != "active":
                continue

            # Check if due: cron expression OR interval-based
            cron_expr = schedule.get("cron")
            if cron_expr:
                # Cron-based scheduling
                if self._cron_is_due(cron_expr, schedule.get("last_run_at", ""), now):
                    due.append(ctx)
            else:
                # Interval-based (legacy)
                next_run = schedule.get("next_run_at", "")
                if not next_run or next_run <= now_iso:
                    due.append(ctx)
        return due

    @staticmethod
    def _cron_is_due(cron_expr: str, last_run_iso: str, now: datetime) -> bool:
        """Delegate to core cron module. See contextsynapse.core.cron for full docs."""
        from contextsynapse.core.cron import is_due
        return is_due(cron_expr, last_run_iso, now)

    def _run_context_pipeline(self, context_id: str):
        try:
            from .executor import execute_context_pipeline
            result = execute_context_pipeline(
                context_id=context_id,
                context_manager=self._context_manager,
                graph_registry=self._graph_registry,
            )
            # Append pipeline result to entity's hash chain
            self._append_to_chain(context_id, result)
        except Exception as e:
            logger.error("[CTX-PIPELINE-SCHED] Context %s failed: %s", context_id, e)

    def _append_to_chain(self, context_id: str, pipeline_result=None):
        """Append pipeline execution summary to the entity's hash chain."""
        try:
            import os, redis as _redis_mod
            redis_url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL", "redis://localhost:6379/0")
            _r = _redis_mod.from_url(redis_url, decode_responses=True)
            from ..context.token import ContextChain

            # Get the context to find its graph namespace
            ctx = self._context_manager.get_context(context_id)
            if not ctx:
                return
            entity_path = f"market:{(ctx.graph_namespace or ctx.name).lower().replace(' ', '_')}"

            summary = {"pipeline_run": True, "context_id": context_id}
            if isinstance(pipeline_result, dict):
                summary["nodes_created"] = pipeline_result.get("nodes_created", 0)
                summary["pages_ingested"] = pipeline_result.get("pages_ingested", 0)

            chain = ContextChain(_r, entity_path)
            chain.append(summary, "pipeline_scheduler")
            logger.debug("[CTX-PIPELINE-SCHED] Chain appended for %s", entity_path)
        except Exception as e:
            logger.debug("[CTX-PIPELINE-SCHED] Chain append skipped: %s", e)
