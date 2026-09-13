"""Intelligence API Router — manage collection plans, hierarchy, watchdog.

Endpoints:
    GET    /intelligence/plans                — List all collection plans
    GET    /intelligence/plans/{name}         — Get a plan's details + status
    POST   /intelligence/plans                — Create/update a collection plan
    DELETE /intelligence/plans/{name}         — Delete a plan
    POST   /intelligence/plans/{name}/run     — Run all pipelines for a plan NOW
    POST   /intelligence/plans/{name}/run/{pipeline} — Run one pipeline

    GET    /intelligence/hierarchy            — Get signal hierarchy
    POST   /intelligence/hierarchy/link       — Add a link
    POST   /intelligence/hierarchy/lateral    — Add a lateral link

    GET    /intelligence/watchdog/status      — Watchdog status + recent alerts
    POST   /intelligence/watchdog/start       — Start watchdog
    POST   /intelligence/watchdog/stop        — Stop watchdog
    POST   /intelligence/watchdog/check       — Run one watchdog check cycle

    GET    /intelligence/signals              — Recent signals + propagation log
    GET    /intelligence/impact               — Impact tracker stats + training data
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException, Query

logger = logging.getLogger(__name__)


def create_intelligence_router(
    context_manager,
    graph_registry=None,
    user_auth=None,
):
    """Create the intelligence API router with injected dependencies."""

    router = APIRouter(prefix="/intelligence", tags=["intelligence"])

    # Lazy-init singletons
    _collector = None
    _hierarchy = None
    _reactive = None
    _watchdog = None
    _impact_tracker = None
    _persistence = None

    def _get_persistence():
        nonlocal _persistence
        if _persistence is None:
            from ..intelligence.persistence import IntelligencePersistence
            _persistence = IntelligencePersistence(context_manager)
        return _persistence

    _rate_limiter_instance = None
    _health_monitor_instance = None

    def _get_rate_limiter():
        nonlocal _rate_limiter_instance
        if _rate_limiter_instance is None:
            from ..intelligence.rate_limiter import PipelineRateLimiter
            _rate_limiter_instance = PipelineRateLimiter()
        return _rate_limiter_instance

    def _get_health_monitor():
        nonlocal _health_monitor_instance
        if _health_monitor_instance is None:
            from ..intelligence.pipeline_health import PipelineHealthMonitor
            _health_monitor_instance = PipelineHealthMonitor(
                alert_dispatcher=_get_alert_dispatcher(),
            )
        return _health_monitor_instance

    def _get_collector():
        nonlocal _collector
        if _collector is None:
            from ..intelligence.collector import ContextCollector
            _collector = ContextCollector(
                rate_limiter=_get_rate_limiter(),
                health_monitor=_get_health_monitor(),
            )
            # Load persisted plans
            store = _get_persistence()
            for plan in store.load_all_plans():
                _collector.register(plan)
            logger.info("[INTEL-API] Loaded %d collection plans", len(_collector.list_plans()))
        return _collector

    def _get_hierarchy():
        nonlocal _hierarchy
        if _hierarchy is None:
            store = _get_persistence()
            _hierarchy = store.load_hierarchy()
            if not _hierarchy:
                from ..intelligence.signal_hierarchy import SignalHierarchy
                _hierarchy = SignalHierarchy()
        return _hierarchy

    def _get_reactive():
        nonlocal _reactive
        if _reactive is None:
            from ..intelligence.reactive import ReactiveController
            _reactive = ReactiveController(alert_dispatcher=_get_alert_dispatcher())
            store = _get_persistence()
            if not store.load_reactive_rules(_reactive):
                _reactive.add_default_rules()
        return _reactive

    def _get_watchdog():
        nonlocal _watchdog
        if _watchdog is None:
            from ..intelligence.watchdog import BreakingNewsWatchdog
            from ..intelligence.impact_tracker import ImpactTracker
            _watchdog = BreakingNewsWatchdog(
                hierarchy=_get_hierarchy(),
                collector=_get_collector(),
                reactive=_get_reactive(),
                impact_tracker=_get_impact_tracker(),
            )
            store = _get_persistence()
            store.load_watchdog_config(_watchdog)
        return _watchdog

    def _get_impact_tracker():
        nonlocal _impact_tracker
        if _impact_tracker is None:
            from ..intelligence.impact_tracker import ImpactTracker
            _impact_tracker = ImpactTracker()
        return _impact_tracker

    # ═══════════════════════════════════════════════════════════════
    # COLLECTION PLANS
    # ═══════════════════════════════════════════════════════════════

    @router.get("/plans")
    async def list_plans():
        """List all registered collection plans."""
        collector = _get_collector()
        plans = []
        for name in collector.list_plans():
            plan = collector.get_plan(name)
            status = collector.status(name)
            plans.append({
                "name": name,
                "description": plan.context_description,
                "tags": plan.tags,
                "template": plan.template,
                "pipeline_count": len(plan.pipelines),
                "status": status,
            })
        return {"plans": plans, "count": len(plans)}

    @router.get("/plans/{name}")
    async def get_plan(name: str):
        """Get a collection plan's details and pipeline status."""
        collector = _get_collector()
        plan = collector.get_plan(name)
        if not plan:
            raise HTTPException(404, f"Plan '{name}' not found")
        return {
            "plan": plan.to_dict(),
            "status": collector.status(name),
        }

    @router.post("/plans")
    async def create_plan(body: dict = Body(...)):
        """Create or update a collection plan.

        Body:
        {
            "context_name": "TCS Intelligence",
            "context_description": "Track TCS...",
            "tags": ["tcs", "it-services"],
            "template": "market_analysis",
            "pipelines": [
                {
                    "name": "news",
                    "source_type": "rss",
                    "sources": ["https://..."],
                    "interval": "30m",
                    "strategy": "news_article",
                    "filter": {
                        "topics": ["TCS", "Tata Consultancy"],
                        "exclude": ["cricket"],
                        "semantic_query": "TCS business performance"
                    }
                }
            ]
        }
        """
        from ..intelligence.collector import CollectionPlan
        try:
            plan = CollectionPlan.from_dict(body)
        except Exception as exc:
            raise HTTPException(400, f"Invalid plan: {exc}")

        collector = _get_collector()
        collector.register(plan)

        # Persist
        store = _get_persistence()
        store.save_collection_plan(plan)

        return {
            "status": "created",
            "name": plan.context_name,
            "pipelines": len(plan.pipelines),
        }

    @router.delete("/plans/{name}")
    async def delete_plan(name: str):
        """Remove a collection plan."""
        collector = _get_collector()
        if name not in collector.list_plans():
            raise HTTPException(404, f"Plan '{name}' not found")
        del collector._plans[name]
        return {"status": "deleted", "name": name}

    @router.post("/plans/{name}/run")
    async def run_plan(name: str):
        """Run all pipelines for a plan immediately."""
        collector = _get_collector()
        plan = collector.get_plan(name)
        if not plan:
            raise HTTPException(404, f"Plan '{name}' not found")

        result = collector.run_all(name)
        return result.to_dict()

    @router.post("/plans/{name}/run/{pipeline}")
    async def run_pipeline(name: str, pipeline: str):
        """Run a single pipeline within a plan."""
        collector = _get_collector()
        plan = collector.get_plan(name)
        if not plan:
            raise HTTPException(404, f"Plan '{name}' not found")

        result = collector.run_pipeline(name, pipeline)
        return result

    # ═══════════════════════════════════════════════════════════════
    # SIGNAL HIERARCHY
    # ═══════════════════════════════════════════════════════════════

    @router.get("/hierarchy")
    async def get_hierarchy():
        """Get the signal hierarchy."""
        h = _get_hierarchy()
        return {
            "hierarchy": h.to_dict(),
            "context_count": len(h._context_levels),
            "link_count": len(h._links),
        }

    @router.post("/hierarchy/link")
    async def add_hierarchy_link(body: dict = Body(...)):
        """Add a hierarchical link (upstream → downstream).

        Body: {"upstream": "Global Macro", "downstream": "India Economy",
               "relationship": "macro_affects", "weight": 0.8}
        """
        from ..intelligence.signal_hierarchy import ContextLink
        h = _get_hierarchy()
        link = ContextLink.from_dict(body)
        h.add_link(link)

        store = _get_persistence()
        store.save_hierarchy(h)

        return {"status": "added", "link": link.to_dict()}

    @router.post("/hierarchy/lateral")
    async def add_lateral_link(body: dict = Body(...)):
        """Add a lateral link (peer ↔ peer).

        Body: {"upstream": "TCS", "downstream": "Infosys",
               "relationship": "competes_with", "weight": 0.7}
        """
        from ..intelligence.signal_hierarchy import ContextLink
        h = _get_hierarchy()
        link = ContextLink.from_dict(body)
        h.add_lateral_link(link)

        store = _get_persistence()
        store.save_hierarchy(h)

        return {"status": "added", "link": link.to_dict()}

    @router.post("/hierarchy/level")
    async def set_level(body: dict = Body(...)):
        """Set hierarchy level for a context.

        Body: {"context": "TCS", "level": "company"}
        """
        h = _get_hierarchy()
        h.set_level(body["context"], body["level"])

        store = _get_persistence()
        store.save_hierarchy(h)

        return {"status": "set", "context": body["context"], "level": body["level"]}

    @router.get("/hierarchy/chain/{context}")
    async def get_chain(context: str):
        """Get upstream + downstream + lateral connections for a context."""
        h = _get_hierarchy()
        return h.get_all_connected(context)

    # ═══════════════════════════════════════════════════════════════
    # WATCHDOG
    # ═══════════════════════════════════════════════════════════════

    @router.get("/watchdog/status")
    async def watchdog_status():
        """Get watchdog status and recent alerts."""
        wd = _get_watchdog()
        return {
            "running": wd._running,
            "sources": [{"url": s.url, "name": s.name} for s in wd._sources],
            "recent_alerts": wd.get_alert_log(limit=20),
        }

    @router.post("/watchdog/start")
    async def watchdog_start(interval: int = Query(60)):
        """Start the watchdog background loop."""
        wd = _get_watchdog()
        wd.start(interval_seconds=interval)
        return {"status": "started", "interval_seconds": interval}

    @router.post("/watchdog/stop")
    async def watchdog_stop():
        """Stop the watchdog."""
        wd = _get_watchdog()
        wd.stop()
        return {"status": "stopped"}

    @router.post("/watchdog/check")
    async def watchdog_check():
        """Run one watchdog check cycle immediately."""
        wd = _get_watchdog()
        result = wd.check()
        return result.to_dict()

    @router.post("/watchdog/source")
    async def add_watchdog_source(body: dict = Body(...)):
        """Add a source to the watchdog.

        Body: {"url": "https://feeds.reuters.com/...", "name": "Reuters"}
        """
        wd = _get_watchdog()
        wd.add_source(
            url=body["url"],
            name=body.get("name", ""),
            source_type=body.get("source_type", "rss"),
        )
        store = _get_persistence()
        store.save_watchdog_config(wd)
        return {"status": "added", "source": body["url"]}

    # ═══════════════════════════════════════════════════════════════
    # SIGNALS + IMPACT
    # ═══════════════════════════════════════════════════════════════

    @router.get("/signals")
    async def get_signals(limit: int = Query(50)):
        """Get recent propagated signals."""
        h = _get_hierarchy()
        reactive = _get_reactive()
        return {
            "propagation_log": h.get_propagation_log(limit),
            "reaction_log": reactive.get_reaction_log(limit),
            "active_escalations": reactive.get_escalations(),
        }

    @router.get("/impact")
    async def get_impact():
        """Get impact tracker stats and training data."""
        tracker = _get_impact_tracker()
        return {
            "link_stats": tracker.get_link_stats(),
            "training_data_count": len(tracker.get_training_data()),
            "training_sample": tracker.get_training_data()[:10],
        }

    @router.post("/impact/outcome")
    async def record_outcome(body: dict = Body(...)):
        """Record an actual outcome for a predicted signal.

        Body: {"signal_id": "...", "target_context": "TCS",
               "actual_impact": 0.45, "metric": "stock_price_change_pct",
               "observed_value": -2.3}
        """
        tracker = _get_impact_tracker()
        result = tracker.record_outcome(
            signal_id=body["signal_id"],
            target_context=body["target_context"],
            actual_impact=body.get("actual_impact", 0.0),
            metric=body.get("metric", ""),
            observed_value=body.get("observed_value", 0.0),
        )
        if result is None:
            raise HTTPException(404, "No matching prediction found")
        return result

    # ═══════════════════════════════════════════════════════════════
    # TRACK — one-call entity tracking
    # ═══════════════════════════════════════════════════════════════

    _tracker = None

    def _get_tracker():
        nonlocal _tracker
        if _tracker is None:
            from ..intelligence.tracker import EntityTracker
            _tracker = EntityTracker(
                collector=_get_collector(),
                hierarchy=_get_hierarchy(),
                persistence=_get_persistence(),
            )
        return _tracker

    @router.post("/track")
    async def track_entity(body: dict = Body(...)):
        """Start tracking an entity with one call.

        Body: {"entity": "TCS", "template": "indian_listed_company",
               "extra": {"bse_code": "532540", "full_name": "Tata Consultancy Services"}}
        """
        tracker = _get_tracker()
        result = tracker.track(
            entity_name=body["entity"],
            template=body.get("template", "generic_topic"),
            extra=body.get("extra"),
        )
        if "error" in result:
            raise HTTPException(400, result["error"])
        return result

    @router.delete("/track/{entity}")
    async def untrack_entity(entity: str):
        """Stop tracking an entity."""
        tracker = _get_tracker()
        return tracker.untrack(entity)

    @router.get("/track")
    async def list_tracked():
        """List all tracked entities."""
        tracker = _get_tracker()
        return {
            "tracked": tracker.list_tracked(),
            "templates": tracker.list_templates(),
        }

    # ═══════════════════════════════════════════════════════════════
    # WEBHOOKS — push-based ingestion
    # ═══════════════════════════════════════════════════════════════

    _webhook_receiver = None

    def _get_webhook_receiver():
        nonlocal _webhook_receiver
        if _webhook_receiver is None:
            from ..intelligence.webhook_receiver import WebhookReceiver
            _webhook_receiver = WebhookReceiver(db=None)
        return _webhook_receiver

    @router.post("/webhooks/register")
    async def register_webhook(body: dict = Body(...)):
        """Register a webhook endpoint.

        Body: {"webhook_id": "tcs_news", "context_name": "TCS", "strategy": "news_article"}
        Returns: {"webhook_id": "tcs_news", "endpoint": "/intelligence/webhooks/tcs_news", "secret": "..."}
        """
        receiver = _get_webhook_receiver()
        config = receiver.register(
            webhook_id=body["webhook_id"],
            context_name=body["context_name"],
            secret=body.get("secret", ""),
            strategy=body.get("strategy", "news_article"),
        )
        return {
            "webhook_id": config.webhook_id,
            "endpoint": f"/intelligence/webhooks/{config.webhook_id}",
            "secret": config.secret,
        }

    @router.get("/webhooks")
    async def list_webhooks():
        """List all registered webhooks."""
        receiver = _get_webhook_receiver()
        return {"webhooks": receiver.list_webhooks()}

    @router.post("/webhooks/{webhook_id}")
    async def receive_webhook(webhook_id: str, body: dict = Body(...)):
        """Receive a webhook payload and ingest it.

        Body: {"title": "...", "content": "...", "url": "...", "source": "..."}
        """
        receiver = _get_webhook_receiver()
        result = receiver.receive(webhook_id, body)
        if result.status == "error":
            raise HTTPException(400, result.error)
        return result.to_dict()

    # ═══════════════════════════════════════════════════════════════
    # ALERTS — notification channels
    # ═══════════════════════════════════════════════════════════════

    _alert_dispatcher = None

    def _get_alert_dispatcher():
        nonlocal _alert_dispatcher
        if _alert_dispatcher is None:
            from ..intelligence.alerts import AlertDispatcher, AlertChannel
            _alert_dispatcher = AlertDispatcher()
            # Default: log channel always active
            _alert_dispatcher.add_channel(AlertChannel(
                name="system_log", channel_type="log", min_severity="warning",
            ))
        return _alert_dispatcher

    @router.post("/alerts/channel")
    async def add_alert_channel(body: dict = Body(...)):
        """Add a notification channel.

        Body: {"name": "slack", "channel_type": "webhook",
               "config": {"url": "https://hooks.slack.com/..."}, "min_severity": "warning"}
        """
        from ..intelligence.alerts import AlertChannel
        dispatcher = _get_alert_dispatcher()
        channel = AlertChannel.from_dict(body)
        dispatcher.add_channel(channel)
        return {"status": "added", "channel": channel.to_dict()}

    @router.get("/alerts/channels")
    async def list_alert_channels():
        dispatcher = _get_alert_dispatcher()
        return {"channels": dispatcher.list_channels()}

    @router.get("/alerts/log")
    async def get_alert_log(limit: int = Query(50)):
        dispatcher = _get_alert_dispatcher()
        return {"deliveries": dispatcher.get_delivery_log(limit)}

    @router.post("/alerts/test")
    async def test_alert(body: dict = Body(...)):
        """Send a test alert to verify channels work.

        Body: {"severity": "warning", "message": "Test alert"}
        """
        dispatcher = _get_alert_dispatcher()
        results = dispatcher.dispatch(
            signal_type="test",
            entity_name="Test Entity",
            context_name="Test Context",
            severity=body.get("severity", "warning"),
            details={"message": body.get("message", "Test alert from intelligence API")},
        )
        return {"results": [r.to_dict() for r in results]}

    # ═══════════════════════════════════════════════════════════════
    # BULK IMPORT — batch URL ingestion
    # ═══════════════════════════════════════════════════════════════

    @router.post("/bulk/ingest")
    async def bulk_ingest(body: dict = Body(...)):
        """Ingest multiple URLs into a context in one call.

        Body: {
            "context_name": "TCS Intelligence",
            "urls": ["https://...", "https://...", ...],
            "strategy": "news_article",  # optional
        }
        """
        context_name = body.get("context_name", "")
        urls = body.get("urls", [])
        strategy = body.get("strategy")

        if not urls:
            raise HTTPException(400, "No URLs provided")

        from ..ingestion.smart_ingest import ingest_url
        from ..ingestion.amplifier import build_context_purpose

        # Get context purpose
        collector = _get_collector()
        plan = collector.get_plan(context_name)
        context_purpose = ""
        if plan:
            context_purpose = build_context_purpose(
                context_name=plan.context_name,
                context_description=plan.context_description,
                tags=plan.tags,
            )

        results = {
            "total": len(urls),
            "ingested": 0,
            "deduped": 0,
            "errors": 0,
            "details": [],
        }

        for url in urls:
            try:
                # Get a db for the context namespace
                if graph_registry:
                    ns = context_name.lower().replace(" ", "_") if context_name else "default"
                    db = graph_registry.get_graph(ns, load_if_missing=True)
                    if not db:
                        db = graph_registry.create_graph(ns)
                else:
                    from ..core.registry import graph_registry as _gr
                    db = _gr.get_graph("default", load_if_missing=True)

                r = ingest_url(
                    url, db,
                    strategy=strategy,
                    context_purpose=context_purpose,
                )

                if hasattr(r, 'errors') and r.errors:
                    if any("Duplicate" in str(e) for e in r.errors):
                        results["deduped"] += 1
                        results["details"].append({"url": url, "status": "deduped"})
                    else:
                        results["errors"] += 1
                        results["details"].append({"url": url, "status": "error", "error": str(r.errors)})
                else:
                    results["ingested"] += 1
                    results["details"].append({
                        "url": url, "status": "ingested",
                        "entities": len(getattr(r, 'entity_ids', {})),
                        "facts": len(getattr(r, 'fact_ids', [])),
                    })

            except Exception as exc:
                results["errors"] += 1
                results["details"].append({"url": url, "status": "error", "error": str(exc)})

        return results

    # ═══════════════════════════════════════════════════════════════
    # TIME-SERIES — convenience temporal queries
    # ═══════════════════════════════════════════════════════════════

    @router.get("/timeseries/sentiment/{entity}")
    async def sentiment_over_time(entity: str, days: int = Query(30), bucket: str = Query("day")):
        """Get sentiment distribution over time for an entity."""
        from ..intelligence.timeseries import TimeSeriesQuery
        db = graph_registry.get_graph("default", load_if_missing=True) if graph_registry else None
        ts = TimeSeriesQuery(db)
        return {"entity": entity, "days": days, "data": ts.sentiment_over_time(entity, days, bucket)}

    @router.get("/timeseries/compare")
    async def compare_sentiment(entities: str = Query(...), days: int = Query(7)):
        """Compare sentiment across entities. Comma-separated names."""
        from ..intelligence.timeseries import TimeSeriesQuery
        db = graph_registry.get_graph("default", load_if_missing=True) if graph_registry else None
        ts = TimeSeriesQuery(db)
        names = [e.strip() for e in entities.split(",")]
        return ts.compare_sentiment(names, days)

    @router.get("/timeseries/timeline/{entity}")
    async def entity_timeline(entity: str, days: int = Query(30), limit: int = Query(50)):
        """Get chronological timeline of events for an entity."""
        from ..intelligence.timeseries import TimeSeriesQuery
        db = graph_registry.get_graph("default", load_if_missing=True) if graph_registry else None
        ts = TimeSeriesQuery(db)
        return {"entity": entity, "events": ts.entity_timeline(entity, days, limit)}

    @router.get("/timeseries/indicator/{name}")
    async def indicator_over_time(name: str, days: int = Query(90)):
        """Get indicator values over time."""
        from ..intelligence.timeseries import TimeSeriesQuery
        db = graph_registry.get_graph("default", load_if_missing=True) if graph_registry else None
        ts = TimeSeriesQuery(db)
        return {"indicator": name, "data": ts.indicator_over_time(name, days)}

    # ═══════════════════════════════════════════════════════════════
    # HEALTH — pipeline health monitoring
    # ═══════════════════════════════════════════════════════════════

    @router.get("/health")
    async def pipeline_health(context: str = Query(""), pipeline: str = Query("")):
        """Get pipeline health status."""
        monitor = _get_health_monitor()
        return {"health": monitor.get_health(context, pipeline)}

    @router.get("/health/failing")
    async def failing_pipelines():
        """Get all failing pipelines."""
        monitor = _get_health_monitor()
        return {"failing": monitor.get_failing(), "degraded": monitor.get_degraded()}

    # ═══════════════════════════════════════════════════════════════
    # RATE LIMITING — per-source throttling
    # ═══════════════════════════════════════════════════════════════

    @router.get("/ratelimit/stats")
    async def rate_limit_stats():
        """Get rate limiting stats per domain."""
        from ..intelligence.rate_limiter import PipelineRateLimiter
        limiter = PipelineRateLimiter()
        return {"stats": limiter.get_stats()}

    # ═══════════════════════════════════════════════════════════════
    # RUNTIME CONTEXT — cross-context assembly
    # ═══════════════════════════════════════════════════════════════

    _assembler = None

    def _get_assembler():
        nonlocal _assembler
        if _assembler is None:
            from ..intelligence.runtime_context import RuntimeContextAssembler
            _assembler = RuntimeContextAssembler(
                graph_registry=graph_registry,
                context_manager=context_manager,
            )
        return _assembler

    @router.post("/runtime/assemble")
    async def assemble_runtime_context(body: dict = Body(...)):
        """Assemble a runtime view across multiple atomic contexts.

        Body: {
            "contexts": ["TCS", "India Economy", "Banking Sector"],
            "focus_entity": "TCS",
            "days": 30
        }
        """
        contexts = body.get("contexts", [])
        if not contexts:
            raise HTTPException(400, "No contexts specified")

        assembler = _get_assembler()
        view = assembler.assemble(
            contexts=contexts,
            focus_entity=body.get("focus_entity", ""),
            days=body.get("days", 30),
        )
        return view.to_dict()

    @router.get("/runtime/compare")
    async def compare_contexts(contexts: str = Query(...), days: int = Query(30)):
        """Quick comparison across contexts. Comma-separated names."""
        assembler = _get_assembler()
        names = [c.strip() for c in contexts.split(",")]
        view = assembler.assemble(contexts=names, days=days)
        return {
            "contexts": names,
            "sentiment_comparison": view.sentiment_comparison,
            "cross_entity_pairs": view.cross_entity_pairs,
            "stats": view.stats,
        }

    # ═══════════════════════════════════════════════════════════════
    # SESSIONS — attach/detach contexts, insight write-back
    # ═══════════════════════════════════════════════════════════════

    _session_mgr = None

    def _get_session_mgr():
        nonlocal _session_mgr
        if _session_mgr is None:
            from ..intelligence.session import SessionManager
            _session_mgr = SessionManager(
                graph_registry=graph_registry,
                context_manager=context_manager,
            )
        return _session_mgr

    @router.post("/sessions")
    async def create_session(body: dict = Body(...)):
        """Create a new session.
        Body: {"name": "IT Sector Analysis"}
        """
        mgr = _get_session_mgr()
        session = mgr.create(body.get("name", "Untitled"))

        # Auto-attach contexts if provided
        for ctx in body.get("contexts", []):
            session.attach(ctx)

        return session.to_dict()

    @router.get("/sessions")
    async def list_sessions():
        mgr = _get_session_mgr()
        return {"sessions": mgr.list_sessions()}

    @router.get("/sessions/{session_id}")
    async def get_session(session_id: str):
        mgr = _get_session_mgr()
        session = mgr.get(session_id)
        if not session:
            raise HTTPException(404, "Session not found")
        return session.to_dict()

    @router.post("/sessions/{session_id}/attach")
    async def attach_context(session_id: str, body: dict = Body(...)):
        """Attach a context to a session.
        Body: {"context": "tcs"}
        """
        mgr = _get_session_mgr()
        session = mgr.get(session_id)
        if not session:
            raise HTTPException(404, "Session not found")
        ctx = body.get("context", "")
        if not ctx:
            raise HTTPException(400, "No context specified")
        session.attach(ctx)
        return session.status()

    @router.post("/sessions/{session_id}/detach")
    async def detach_context(session_id: str, body: dict = Body(...)):
        """Detach a context from a session.
        Body: {"context": "tcs"}
        """
        mgr = _get_session_mgr()
        session = mgr.get(session_id)
        if not session:
            raise HTTPException(404, "Session not found")
        session.detach(body.get("context", ""))
        return session.status()

    @router.post("/sessions/{session_id}/assemble")
    async def assemble_session(session_id: str, body: dict = Body({})):
        """Assemble the runtime view for a session."""
        mgr = _get_session_mgr()
        session = mgr.get(session_id)
        if not session:
            raise HTTPException(404, "Session not found")
        view = session.assemble(
            focus_entity=body.get("focus_entity", ""),
            days=body.get("days", 30),
        )
        if not view:
            return {"error": "No contexts attached"}
        return view.to_dict()

    @router.post("/sessions/{session_id}/insight")
    async def add_insight(session_id: str, body: dict = Body(...)):
        """Write an insight back to a context (visible to all agents + dashboard).

        Body: {"insight": "TCS outperforms when USD weakens",
               "produced_by": "agent:claude", "confidence": 0.85,
               "context": "tcs",
               "evidence": ["TCS Q1 up 4.2%", "USD stable"]}

        If context is specified, the insight is written INTO that context's
        graph — visible in the dashboard, to other agents, and in runtime views.
        If context is omitted, stored in session-only graph.
        """
        mgr = _get_session_mgr()
        session = mgr.get(session_id)
        if not session:
            raise HTTPException(404, "Session not found")
        ins = session.add_insight(
            insight=body.get("insight", ""),
            produced_by=body.get("produced_by", ""),
            confidence=body.get("confidence", 0.0),
            evidence=body.get("evidence", []),
            tags=body.get("tags", []),
            context=body.get("context", ""),
        )
        return ins.to_dict()

    @router.get("/sessions/{session_id}/insights")
    async def get_insights(session_id: str, produced_by: str = Query("")):
        """Get insights from a session."""
        mgr = _get_session_mgr()
        session = mgr.get(session_id)
        if not session:
            raise HTTPException(404, "Session not found")
        insights = session.get_insights(produced_by=produced_by)
        return {"insights": [i.to_dict() for i in insights]}

    @router.delete("/sessions/{session_id}")
    async def delete_session(session_id: str):
        mgr = _get_session_mgr()
        if mgr.delete(session_id):
            return {"status": "deleted"}
        raise HTTPException(404, "Session not found")

    # ── Prediction Tracker ──

    def _get_predictor():
        try:
            from plugins.stock_analysis.predictor import PredictionTracker
            return PredictionTracker(graph_registry=graph_registry)
        except ImportError:
            # Add project root to path and retry
            import sys
            from pathlib import Path
            root = str(Path(__file__).parent.parent.parent)
            if root not in sys.path:
                sys.path.insert(0, root)
            try:
                from plugins.stock_analysis.predictor import PredictionTracker
                return PredictionTracker(graph_registry=graph_registry)
            except ImportError:
                raise HTTPException(501, "Stock analysis plugin not installed")

    @router.post("/predict/{entity}")
    async def predict_entity(entity: str, horizon: str = Query("1d")):
        """Make a directional prediction for an entity."""
        try:
            tracker = _get_predictor()
            pred = tracker.predict(entity, horizon=horizon)
            return pred.to_dict()
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("Prediction error for %s", entity)
            raise HTTPException(500, str(e))

    @router.post("/predict")
    async def predict_all(companies: list = Body(None)):
        """Make predictions for all tracked companies."""
        tracker = _get_predictor()
        preds = tracker.predict_all(companies)
        return {"predictions": [p.to_dict() for p in preds]}

    @router.post("/predictions/verify")
    async def verify_predictions():
        """Verify past predictions against actual outcomes."""
        tracker = _get_predictor()
        verified = tracker.verify_predictions()
        return {"verified": verified, "count": len(verified)}

    @router.get("/predictions/test")
    async def test_predictor():
        """Test if predictor can be instantiated."""
        try:
            tracker = _get_predictor()
            return {"status": "ok", "type": type(tracker).__name__}
        except Exception as e:
            return {"status": "error", "error": str(e), "type": type(e).__name__}

    @router.get("/predictions/accuracy")
    async def get_accuracy():
        """Get prediction accuracy stats."""
        try:
            tracker = _get_predictor()
            return tracker.get_accuracy()
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("Prediction accuracy error")
            raise HTTPException(500, str(e))

    @router.get("/predictions")
    async def list_predictions(entity: str = Query(""), verified_only: bool = Query(False)):
        """List all predictions, optionally filtered."""
        tracker = _get_predictor()
        stats = tracker.get_accuracy()
        preds = stats["predictions"]
        if entity:
            preds = [p for p in preds if p["entity"].lower() == entity.lower()]
        if verified_only:
            preds = [p for p in preds if p["verified"]]
        return {"predictions": preds, "total": len(preds)}

    # ── Signal Config ──

    def _get_signal_config():
        try:
            import sys
            from pathlib import Path
            root = str(Path(__file__).parent.parent.parent)
            if root not in sys.path:
                sys.path.insert(0, root)
            from plugins.stock_analysis.signal_config import SignalConfig
            return SignalConfig()
        except ImportError:
            raise HTTPException(501, "Stock analysis plugin not installed")

    @router.get("/signal-config/{entity}")
    async def get_signal_config(entity: str):
        """Get full signal config for an entity (weights, thresholds, decay, RL)."""
        cfg = _get_signal_config()
        return cfg.get_full_config(entity)

    @router.get("/signal-config")
    async def get_all_signal_configs():
        """Get configs for all tracked stocks + defaults."""
        cfg = _get_signal_config()
        return {"configs": cfg.get_all_configs(), "defaults": cfg.get_defaults()}

    @router.put("/signal-config/{entity}/weights")
    async def set_signal_weights(entity: str, weights: dict = Body(...)):
        """Set custom weights for a stock. Auto-normalized to sum=1."""
        cfg = _get_signal_config()
        cfg.set_weights(entity, weights)
        return {"status": "updated", "weights": cfg.get_weights(entity)}

    @router.put("/signal-config/{entity}/thresholds")
    async def set_signal_thresholds(entity: str, thresholds: dict = Body(...)):
        """Set custom thresholds for a stock."""
        cfg = _get_signal_config()
        cfg.set_thresholds(entity, thresholds)
        return {"status": "updated", "thresholds": cfg.get_thresholds(entity)}

    @router.put("/signal-config/rl")
    async def set_rl_config(rl: dict = Body(...)):
        """Set RL parameters (reward, penalty, enabled)."""
        cfg = _get_signal_config()
        cfg.set_rl(rl)
        return {"status": "updated", "rl": cfg.get_rl()}

    @router.delete("/signal-config/{entity}")
    async def reset_signal_config(entity: str):
        """Reset a stock's config to domain/global defaults."""
        cfg = _get_signal_config()
        cfg.reset(entity)
        return {"status": "reset", "config": cfg.get_full_config(entity)}

    @router.get("/learned-weights")
    async def get_learned_weights():
        """Get RL-learned weights for all stocks."""
        tracker = _get_predictor()
        return tracker.get_weights()

    return router
