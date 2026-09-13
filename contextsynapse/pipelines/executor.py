"""Pipeline executor — runs a single Pipeline through its 9-step chain."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy top-level imports (avoids circular dependency on startup)
# ---------------------------------------------------------------------------
from ..ingestion.smart_ingest import ingest_crawl  # noqa: E402


def _get_graph(namespace: str):
    """Get or create a named graph."""
    from ..core.registry import graph_registry
    graph = graph_registry.get_graph(namespace, load_if_missing=True)
    if not graph:
        graph = graph_registry.create_graph(namespace)
    return graph


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def execute_pipeline(pipeline_id: str, store) -> Dict[str, Any]:
    """
    Execute a pipeline by id.

    Steps:
      1. Load Pipeline from store
      2. Get/create target graph
      3. Build filter_config from keyword/semantic settings
      4. Call ingest_crawl() using pipeline.steps, pipeline.schema_id/schema_mode
      5. Update pipeline stats (total_runs, last_run_at, total_documents)
      6. one_time  → set status="completed"
         scheduled → calculate next_run_at, keep status="active"
      7. Save run history
      8. Propagate "pipeline_completed" event
      9. Handle errors gracefully (set last_error, return error dict)

    Returns a result dict with keys: status, pages_ingested, nodes_created,
    pages_filtered, pages_failed, pipeline_id.
    """
    # Step 1 — load pipeline
    pipeline = store.get(pipeline_id)
    if pipeline is None:
        return {"status": "error", "error": f"Pipeline {pipeline_id} not found"}

    now = datetime.now(timezone.utc)

    try:
        # Step 2 — get/create graph
        graph = _get_graph(pipeline.target_context_id or pipeline_id)

        # Step 3 — build filter_config
        filter_config: Optional[Dict[str, Any]] = None
        if pipeline.include_keywords or pipeline.exclude_keywords or pipeline.semantic_filter:
            filter_config = {
                "include_keywords": pipeline.include_keywords,
                "exclude_keywords": pipeline.exclude_keywords,
                "semantic_filter": pipeline.semantic_filter,
                "semantic_threshold": pipeline.semantic_threshold,
            }

        # Step 4 — call ingest_crawl
        crawl_result = ingest_crawl(
            start_url=pipeline.source_url,
            db=graph,
            max_pages=pipeline.max_pages,
            schema=pipeline.schema_id or None,
            pipeline=pipeline.schema_mode or "smart_article",
            mode="auto",
            filter_config=filter_config,
            debug=False,
            owner=pipeline.target_context_id,
        )

        ingested = crawl_result.get("ingested", [])
        filtered = crawl_result.get("filtered", [])
        failed = crawl_result.get("failed", [])

        pages_ingested = len(ingested)
        nodes_created = sum(
            item.get("nodes", 0) or
            (item.get("passages", 0) + item.get("entities", 0) + item.get("facts", 0))
            for item in ingested
        )
        pages_filtered = len(filtered)
        pages_failed = len(failed)

        # Step 5 — update pipeline stats
        pipeline.total_runs += 1
        pipeline.last_run_at = now.isoformat()
        pipeline.total_documents += pages_ingested
        pipeline.last_error = ""

        # Step 6 — lifecycle state
        if pipeline.trigger_type == "one_time":
            pipeline.status = "completed"
        elif pipeline.trigger_type == "scheduled":
            interval = max(pipeline.interval_minutes, 1 / 60)  # min 1s
            pipeline.next_run_at = (now + timedelta(minutes=interval)).isoformat()
            pipeline.status = "active"

        # Step 7 — persist updated pipeline + run history
        store.save(pipeline)
        run_record = {
            "run_at": now.isoformat(),
            "pages_ingested": pages_ingested,
            "nodes_created": nodes_created,
            "pages_filtered": pages_filtered,
            "pages_failed": pages_failed,
        }
        store.save_run(pipeline_id, run_record)

        # Step 8 — propagate event (best-effort)
        try:
            from ..context.propagation import get_propagator
            propagator = get_propagator()
            propagator.propagate(
                source_agent="pipeline_executor",
                event_type="pipeline_completed",
                content=(
                    f"Pipeline '{pipeline.name}' finished: "
                    f"{pages_ingested} pages ingested, {nodes_created} nodes created."
                ),
                namespace=pipeline.target_context_id or "default",
            )
        except Exception as prop_err:
            logger.debug("Propagation skipped: %s", prop_err)

        result: Dict[str, Any] = {
            "status": "success",
            "pipeline_id": pipeline_id,
            "pages_ingested": pages_ingested,
            "nodes_created": nodes_created,
            "pages_filtered": pages_filtered,
            "pages_failed": pages_failed,
        }
        logger.info("Pipeline %s completed: %s", pipeline_id, result)
        return result

    except Exception as exc:
        # Step 9 — graceful error handling
        err_msg = str(exc)
        logger.exception("Pipeline %s failed: %s", pipeline_id, err_msg)

        pipeline.last_error = err_msg
        pipeline.last_run_at = now.isoformat()
        pipeline.total_runs += 1
        store.save(pipeline)

        return {
            "status": "error",
            "pipeline_id": pipeline_id,
            "error": err_msg,
        }


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

def rollback_run(context_id: str, run_id: str, context_manager, graph_registry=None) -> Dict[str, Any]:
    """Rollback a pipeline run — delete all nodes created by that run.

    Nodes are identified by _owner == run_id.
    """
    ctx = context_manager.get_context(context_id)
    if not ctx:
        return {"status": "error", "error": "Context not found"}

    namespace = ctx.graph_namespace or context_id
    if graph_registry:
        graph = graph_registry.get_graph(namespace, load_if_missing=True)
    else:
        graph = _get_graph(namespace)

    if not graph:
        return {"status": "error", "error": "Graph not found"}

    # Find all nodes with _owner == run_id
    deleted = 0
    try:
        all_nodes = list(graph.csr_adapter.get_all_nodes())
        for node in all_nodes:
            props = getattr(node, "properties", {}) or {}
            if props.get("_owner") == run_id:
                node_id = getattr(node, "id", None)
                if node_id:
                    graph.remove_node(node_id)
                    deleted += 1
    except Exception as e:
        return {"status": "error", "error": f"Rollback failed: {e}", "deleted": deleted}

    # Refresh stats
    try:
        context_manager.refresh_stats(context_id)
    except Exception:
        pass

    logger.info("[PIPELINE] Rollback %s: deleted %d nodes from %s", run_id, deleted, namespace)
    return {"status": "success", "run_id": run_id, "deleted": deleted}


# ---------------------------------------------------------------------------
# Context-based pipeline executor
# ---------------------------------------------------------------------------

def _execute_multi_pipeline(
    context_id: str,
    ctx,
    pipelines_list: list,
    context_manager,
    graph_registry=None,
) -> Dict[str, Any]:
    """Execute multiple pipelines feeding a single context.

    Each pipeline in config["pipelines"] is a dict with:
      - name: str (identifier)
      - source_url: str or sources: list[str]
      - interval: str (schedule)
      - strategy: str (content strategy)
      - enabled: bool (default True)
      - All other fields from the single-pipeline config

    All pipelines feed the SAME graph namespace (the context's graph).
    Dedup runs per-pipeline — same content from different pipelines is caught.
    """
    from ..ingestion.amplifier import build_context_purpose

    namespace = ctx.graph_namespace or context_id
    if graph_registry is not None:
        graph = graph_registry.get_graph(namespace, load_if_missing=True)
        if not graph:
            graph = graph_registry.create_graph(namespace)
    else:
        graph = _get_graph(namespace)

    context_purpose = build_context_purpose(
        context_name=ctx.name,
        context_description=ctx.description,
        tags=ctx.tags or [],
    )

    now = datetime.now(timezone.utc)
    combined_result = {
        "status": "success",
        "context_id": context_id,
        "pipelines_run": 0,
        "pipelines_skipped": 0,
        "total_pages_ingested": 0,
        "total_nodes_created": 0,
        "total_deduped": 0,
        "pipeline_results": [],
    }

    for pipe_config in pipelines_list:
        pipe_name = pipe_config.get("name", "unnamed")
        enabled = pipe_config.get("enabled", True)

        if not enabled:
            combined_result["pipelines_skipped"] += 1
            combined_result["pipeline_results"].append({
                "pipeline": pipe_name, "status": "skipped", "reason": "disabled",
            })
            continue

        # Check schedule — only run if due
        pipe_schedule = pipe_config.get("schedule", {})
        next_run = pipe_schedule.get("next_run_at", "")
        if next_run:
            try:
                next_dt = datetime.fromisoformat(next_run.replace("Z", "+00:00"))
                if next_dt > now:
                    combined_result["pipelines_skipped"] += 1
                    combined_result["pipeline_results"].append({
                        "pipeline": pipe_name, "status": "skipped", "reason": "not due",
                        "next_run_at": next_run,
                    })
                    continue
            except (ValueError, TypeError):
                pass

        # Get sources — support both "source_url" (single) and "sources" (list)
        sources = pipe_config.get("sources", [])
        source_url = pipe_config.get("source_url", "")
        if source_url and not sources:
            sources = [source_url]

        if not sources:
            combined_result["pipeline_results"].append({
                "pipeline": pipe_name, "status": "skipped", "reason": "no sources",
            })
            combined_result["pipelines_skipped"] += 1
            continue

        # Build filter config for this pipeline
        from ..ingestion.filters import FilterConfig, SemanticRule
        fc = None
        include_kw = pipe_config.get("include_keywords") or []
        exclude_kw = pipe_config.get("exclude_keywords") or []
        semantic_filter = pipe_config.get("semantic_filter", "")
        if include_kw or exclude_kw or semantic_filter:
            fc = FilterConfig(
                keywords_include=include_kw,
                keywords_exclude=exclude_kw,
                semantic_query=semantic_filter,
                semantic_auto_threshold=pipe_config.get("semantic_threshold", 0.6),
            )

        max_pages = pipe_config.get("max_pages", 5)
        strategy = pipe_config.get("strategy", "")

        pipe_result = {
            "pipeline": pipe_name,
            "source_type": pipe_config.get("source_type", "crawl"),
            "pages_ingested": 0,
            "nodes_created": 0,
            "deduped": 0,
            "errors": [],
        }

        for source in sources:
            try:
                crawl_kwargs = {}
                if fc:
                    crawl_kwargs["filter_config"] = fc

                src_type = pipe_config.get("source_type", "crawl")

                if src_type == "stock_price":
                    # Stock price sources: each "source" is a Yahoo Finance ticker
                    # (e.g., "TCS.NS"). Fetches OHLCV via PriceFeed and stores
                    # Indicator nodes in the graph namespace named
                    # "{company}_price" relative to the context name.
                    try:
                        from ..intelligence.price_feed import PriceFeed
                        feed = PriceFeed()
                        # Build price namespace: e.g. ctx "TCS" → "tcs_price"
                        price_ns_base = source.split(".")[0].lower()  # TCS.NS → tcs
                        price_ns = f"{price_ns_base}_price"
                        price_graph = graph_registry.get_graph(price_ns, load_if_missing=True) if graph_registry else None
                        if price_graph is None and graph_registry:
                            graph_registry.create_graph(price_ns)
                            price_graph = graph_registry.get_graph(price_ns, load_if_missing=True)
                        if price_graph is None:
                            price_graph = graph  # fallback to context graph
                        period = pipe_config.get("config", {}).get("period", "5d")
                        interval = pipe_config.get("config", {}).get("interval", "1h")
                        created = feed.ingest_into_graph(price_graph, source, period=period, interval=interval)
                        pipe_result["nodes_created"] += created
                        pipe_result["pages_ingested"] += 1 if created > 0 else 0
                    except Exception as price_err:
                        pipe_result["errors"].append(f"Stock price {source}: {price_err}")
                    continue  # sources loop — no further handling needed

                elif src_type == "rss":
                    # RSS feed — parse with feedparser, ingest each article as a node
                    from ..ingestion.smart_ingest import ingest_text
                    try:
                        import feedparser
                        parsed = feedparser.parse(source)
                        entries = parsed.entries or []
                        filter_topics = []
                        if fc:
                            filter_topics = [t.lower() for t in (getattr(fc, 'topics', None) or [])]
                        ingested_count = 0
                        for entry in entries[:20]:  # cap at 20 articles per feed per run
                            title = entry.get("title", "")
                            summary = entry.get("summary", entry.get("description", ""))
                            link = entry.get("link", source)
                            content = f"{title}\n\n{summary}".strip()
                            if not content:
                                continue
                            # Topic filter — skip if none of the filter topics appear
                            if filter_topics:
                                combined = (title + " " + summary).lower()
                                if not any(t in combined for t in filter_topics):
                                    continue
                            try:
                                node_r = ingest_text(
                                    content, graph,
                                    title=title,
                                    source_url=link,
                                    strategy=strategy or "news_article",
                                    context_purpose=context_purpose,
                                )
                                if node_r and (getattr(node_r, 'entity_ids', None) or getattr(node_r, 'fact_ids', None)):
                                    ingested_count += 1
                                    pipe_result["nodes_created"] += len(getattr(node_r, 'entity_ids', [])) + len(getattr(node_r, 'fact_ids', []))
                                elif isinstance(node_r, dict) and node_r.get('node_id'):
                                    ingested_count += 1
                                    pipe_result["nodes_created"] += 1
                            except Exception:
                                pass
                        pipe_result["pages_ingested"] += ingested_count
                        if not entries:
                            pipe_result["errors"].append(f"RSS {source}: no entries returned")
                    except ImportError:
                        pipe_result["errors"].append("feedparser not installed — run: pip install feedparser")
                    except Exception as rss_err:
                        pipe_result["errors"].append(f"RSS {source}: {rss_err}")
                    continue

                elif src_type == "api":
                    # API sources — fetch and ingest as text
                    from ..ingestion.smart_ingest import ingest_text
                    try:
                        import urllib.request
                        with urllib.request.urlopen(source, timeout=30) as resp:
                            api_data = resp.read().decode("utf-8")
                        r = ingest_text(
                            api_data, graph,
                            title=f"{pipe_name}: {source}",
                            source_url=source,
                            strategy=strategy or None,
                            context_purpose=context_purpose,
                        )
                    except Exception as fetch_err:
                        pipe_result["errors"].append(f"API fetch: {fetch_err}")
                        continue
                else:
                    # URL/crawl/rss sources
                    r = ingest_crawl(
                        source, graph, max_pages=max_pages,
                        pipeline=strategy or "smart_pipeline",
                        context_purpose=context_purpose,
                        **crawl_kwargs,
                    )

                if hasattr(r, 'errors') and r.errors:
                    if any("Duplicate" in str(e) for e in r.errors):
                        pipe_result["deduped"] += 1
                    else:
                        pipe_result["errors"].extend(r.errors)
                elif hasattr(r, 'ingested'):
                    pipe_result["pages_ingested"] += len(r.ingested)
                    pipe_result["nodes_created"] += sum(
                        item.get("nodes", 0) for item in (r.ingested or [])
                    )
                elif hasattr(r, 'entity_ids'):
                    pipe_result["pages_ingested"] += 1
                    pipe_result["nodes_created"] += len(r.entity_ids) + len(r.fact_ids)
            except Exception as exc:
                pipe_result["errors"].append(f"{source}: {exc}")

        # Update per-pipeline schedule
        interval_str = pipe_config.get("interval", "daily")
        try:
            from contextsynapse.intelligence.collector import _INTERVAL_SECONDS
        except Exception:
            _INTERVAL_SECONDS = {}
        _fallback = {
            "1s": 1, "5s": 5, "10s": 10, "30s": 30,
            "1m": 60, "2m": 120, "5m": 300, "10m": 600, "15m": 900,
            "30m": 1800, "1h": 3600, "6h": 21600, "12h": 43200,
            "daily": 86400, "weekly": 604800,
        }
        interval_secs = (_INTERVAL_SECONDS or _fallback).get(interval_str, 86400)
        pipe_config.setdefault("schedule", {})
        pipe_config["schedule"]["last_run_at"] = now.isoformat()
        pipe_config["schedule"]["next_run_at"] = (now + timedelta(seconds=interval_secs)).isoformat()
        pipe_config["schedule"]["last_nodes_created"] = pipe_result.get("nodes_created", 0)
        pipe_config["schedule"]["last_pages_ingested"] = pipe_result.get("pages_ingested", 0)
        pipe_config["schedule"]["last_deduped"] = pipe_result.get("deduped", 0)
        pipe_config["schedule"]["last_error_count"] = len(pipe_result.get("errors", []))
        pipe_config["schedule"]["last_status"] = "error" if pipe_result.get("errors") else "ok"

        combined_result["pipelines_run"] += 1
        combined_result["total_pages_ingested"] += pipe_result["pages_ingested"]
        combined_result["total_nodes_created"] += pipe_result["nodes_created"]
        combined_result["total_deduped"] += pipe_result["deduped"]
        combined_result["pipeline_results"].append(pipe_result)

        logger.info("[MULTI-PIPE] '%s' pipeline '%s': %d ingested, %d deduped",
                     ctx.name, pipe_name, pipe_result["pages_ingested"], pipe_result["deduped"])

    # Persist updated pipeline schedules
    try:
        updated_config = dict(ctx.config or {})
        updated_config["pipelines"] = pipelines_list
        context_manager.update_context(context_id, config=updated_config)
    except Exception:
        pass

    if any(p.get("errors") for p in combined_result["pipeline_results"]):
        combined_result["status"] = "partial"

    logger.info("[MULTI-PIPE] Context '%s': %d pipelines run, %d total ingested, %d deduped",
                 ctx.name, combined_result["pipelines_run"],
                 combined_result["total_pages_ingested"], combined_result["total_deduped"])

    return combined_result


def execute_context_pipeline(
    context_id: str,
    context_manager,
    graph_registry=None,
) -> Dict[str, Any]:
    """Execute a pipeline run for a context.

    Reads pipeline settings from ``context.config["pipeline"]`` and schedule
    state from ``context.config["schedule"]``.  After the run it writes updated
    schedule stats back to the context via ``context_manager.update_context``.

    Returns a result dict with keys: status, context_id, pages_ingested,
    nodes_created, pages_filtered, pages_failed (and optionally ``error``).
    """
    ctx = context_manager.get_context(context_id)
    if not ctx:
        return {"status": "error", "context_id": context_id, "error": "Context not found"}

    # Multi-pipeline support: if config["pipelines"] is a list, run each pipeline
    pipelines_list = (ctx.config or {}).get("pipelines", [])
    if isinstance(pipelines_list, list) and len(pipelines_list) > 0:
        return _execute_multi_pipeline(context_id, ctx, pipelines_list, context_manager, graph_registry)

    pipeline_config = ctx.config.get("pipeline", {}) if ctx.config else {}
    schedule = ctx.config.get("schedule", {}) if ctx.config else {}

    source_url = pipeline_config.get("source_url", "")
    if not source_url:
        return {"status": "error", "context_id": context_id, "error": "No source URL configured"}

    # Generate run_id for rollback tracking
    import uuid as _uuid
    run_id = f"run_{_uuid.uuid4().hex[:12]}"

    # Register as a Job so it shows in the Jobs page
    job = None
    jm = None
    try:
        from ..ingestion.job_manager import get_job_manager, Job, JobStatus
        jm = get_job_manager()
        job = Job(
            job_type="pipeline_crawl",
            pipeline=pipeline_config.get("template_id", "smart-article"),
            context_id=context_id,
            input_summary=source_url,
        )
        job.job_id = f"pipe_{context_id[:8]}_{datetime.now(timezone.utc).strftime('%H%M%S')}"
        job.status = JobStatus.RUNNING
        job.created_at = datetime.now(timezone.utc).isoformat()
        job.started_at = job.created_at
        job.add_log("start", f"Pipeline crawl: {source_url} (max {pipeline_config.get('max_pages', 5)} pages)")
        jm._jobs[job.job_id] = job
        jm._save_to_redis(job)
    except Exception as e:
        logger.debug("Job registration skipped: %s", e)
        job = None

    now = datetime.now(timezone.utc)

    try:
        # Get target graph
        namespace = ctx.graph_namespace or context_id
        if graph_registry is not None:
            graph = graph_registry.get_graph(namespace, load_if_missing=True)
            if not graph:
                graph = graph_registry.create_graph(namespace)
        else:
            graph = _get_graph(namespace)

        # Build filter_config as a proper FilterConfig dataclass
        from ..ingestion.filters import FilterConfig, SemanticRule

        filters = ctx.config.get("filters", {}) if ctx.config else {}
        crawl_kwargs: Dict[str, Any] = {}
        fc = None

        if filters:
            semantic_rules = []
            for rule in (filters.get("semantic_rules") or []):
                semantic_rules.append(SemanticRule(
                    query=rule.get("description", ""),
                    direction="include",
                    threshold=rule.get("threshold", 0.6),
                ))
            fc = FilterConfig(
                keywords_include=filters.get("keyword_include") or [],
                keywords_exclude=filters.get("keyword_exclude") or [],
                semantic_rules=semantic_rules,
                semantic_query=semantic_rules[0].query if semantic_rules else "",
                semantic_auto_threshold=semantic_rules[0].threshold if semantic_rules else 0.55,
            )
        else:
            include_kw = pipeline_config.get("include_keywords") or []
            exclude_kw = pipeline_config.get("exclude_keywords") or []
            semantic_filter_val = pipeline_config.get("semantic_filter", "")
            semantic_threshold = pipeline_config.get("semantic_threshold", 0.6)
            if include_kw or exclude_kw or semantic_filter_val:
                fc = FilterConfig(
                    keywords_include=include_kw,
                    keywords_exclude=exclude_kw,
                    semantic_query=semantic_filter_val,
                    semantic_auto_threshold=semantic_threshold,
                )

        if fc:
            crawl_kwargs["filter_config"] = fc

        max_pages = pipeline_config.get("max_pages", 5)
        schema_id = pipeline_config.get("schema_id") or None
        schema_mode = pipeline_config.get("schema_mode") or "schema_plus"
        template_id = pipeline_config.get("template_id", "")
        # Derive pipeline name from template_id (strip "builtin:" prefix, convert hyphens to underscores)
        pipeline_name = template_id.replace("builtin:", "").replace("-", "_") if template_id else "smart_article"

        # Load extraction schema as IngestionSchema (what ingest_crawl expects)
        schema_obj = None
        if schema_id:
            try:
                from ..ingestion.schema_extractor import IngestionSchema, _parse_yaml_schema
                from ..extraction.schema_loader import get_default_schema_yaml
                # Load the YAML string for this schema
                yaml_str = get_default_schema_yaml(schema_id)
                if yaml_str:
                    schema_obj = _parse_yaml_schema(yaml_str, name=schema_id)
                if not schema_obj:
                    # Try loading from schema registry as YAML
                    from ..extraction.schema_loader import SchemaRegistry
                    sr = SchemaRegistry(graph)
                    ext_schema = sr.get(schema_id)
                    if ext_schema and hasattr(ext_schema, 'node_types'):
                        # Convert ExtractionSchema to IngestionSchema
                        node_types = {}
                        for nt_name, nt_def in ext_schema.node_types.items():
                            fields = nt_def.fields if hasattr(nt_def, 'fields') else []
                            node_types[nt_name] = {"fields": fields}
                        edge_types = {}
                        for et_name, et_def in ext_schema.edge_types.items():
                            src = et_def.source if hasattr(et_def, 'source') else '*'
                            tgt = et_def.target if hasattr(et_def, 'target') else '*'
                            edge_types[et_name] = {"from": src, "to": tgt}
                        schema_obj = IngestionSchema(name=schema_id, node_types=node_types, edge_types=edge_types)
                if schema_obj:
                    logger.info("[PIPELINE] Loaded schema '%s': %d node types, %d edge types",
                                schema_id, len(schema_obj.node_types), len(schema_obj.edge_types))
                else:
                    logger.warning("[PIPELINE] Schema '%s' not found — using default", schema_id)
            except Exception as e:
                logger.warning("[PIPELINE] Failed to load schema '%s': %s", schema_id, e)

        # Log crawl stages to the Job
        def _on_stage(stage_name, details=None):
            if job:
                msg = (details or {}).get("message", "")
                job.add_log(stage_name, msg[:200] if msg else stage_name)
                job.progress = stage_name
                if jm:
                    jm._save_to_redis(job)

        crawl_result = ingest_crawl(
            start_url=source_url,
            db=graph,
            max_pages=max_pages,
            schema=schema_obj,
            pipeline=pipeline_name,
            mode="auto",
            debug=False,
            owner=run_id,
            on_stage=_on_stage,
            **crawl_kwargs,
        )

        ingested = crawl_result.get("ingested", [])
        filtered = crawl_result.get("filtered", [])
        failed = crawl_result.get("failed", [])

        pages_ingested = len(ingested)
        nodes_created = sum(
            item.get("nodes", 0) or
            (item.get("passages", 0) + item.get("entities", 0) + item.get("facts", 0))
            for item in ingested
        )
        edges_created = sum(item.get("edges", 0) for item in ingested)
        total_passages = sum(item.get("passages", 0) for item in ingested)
        total_entities = sum(item.get("entities", 0) for item in ingested)
        total_facts = sum(item.get("facts", 0) for item in ingested)
        pages_filtered = len(filtered)
        pages_failed = len(failed)

        # Update schedule state
        total_runs = int(schedule.get("total_runs", 0)) + 1
        total_documents = int(schedule.get("total_documents", 0)) + pages_ingested

        trigger_type = schedule.get("trigger_type") or pipeline_config.get("trigger_type", "one_time")
        if trigger_type == "one_time":
            next_run_at = ""
            new_status = "completed"
        else:
            interval = max(float(schedule.get("interval_minutes") or pipeline_config.get("interval_minutes", 60)), 1 / 60)
            next_run_at = (now + timedelta(minutes=interval)).isoformat()
            new_status = "active"

        updated_schedule = dict(schedule)
        updated_schedule["last_run_at"] = now.isoformat()
        updated_schedule["next_run_at"] = next_run_at
        updated_schedule["total_runs"] = total_runs
        updated_schedule["total_documents"] = total_documents
        updated_schedule["last_error"] = ""
        if trigger_type == "one_time":
            updated_schedule["status"] = new_status

        updated_config = dict(ctx.config or {})
        updated_config["schedule"] = updated_schedule
        context_manager.update_context(context_id, config=updated_config)

        # Save run record to Redis
        run_record = {
            "run_id": run_id,
            "run_at": now.isoformat(),
            "pages_ingested": pages_ingested,
            "nodes_created": nodes_created,
            "edges_created": edges_created,
            "passages": total_passages,
            "entities": total_entities,
            "facts": total_facts,
            "pages_filtered": pages_filtered,
            "pages_failed": pages_failed,
            "duration_ms": int((datetime.now(timezone.utc) - now).total_seconds() * 1000),
        }
        _save_context_run(context_id, run_record)

        # Refresh context stats (item_count, estimated_tokens)
        try:
            context_manager.refresh_stats(context_id)
        except Exception:
            pass

        # Rebuild ContextIntelligence node (auto-briefing data)
        try:
            from ..core.context_state import get_context_state
            state = get_context_state(graph, namespace)
            state._last_rebuilt = 0  # force rebuild
            state.rebuild()
        except Exception:
            pass

        # Propagate event (best-effort)
        try:
            from ..context.propagation import get_propagator
            propagator = get_propagator()
            propagator.propagate(
                source_agent="pipeline_executor",
                event_type="pipeline_completed",
                content=(
                    f"Context pipeline '{ctx.name}' finished: "
                    f"{pages_ingested} pages ingested, {nodes_created} nodes created."
                ),
                namespace=namespace,
            )
        except Exception as prop_err:
            logger.debug("Propagation skipped: %s", prop_err)

        result: Dict[str, Any] = {
            "status": "success",
            "context_id": context_id,
            "pages_ingested": pages_ingested,
            "nodes_created": nodes_created,
            "pages_filtered": pages_filtered,
            "pages_failed": pages_failed,
        }

        # Update Job status
        if job and jm:
            try:
                from ..ingestion.job_manager import JobStatus
                job.status = JobStatus.COMPLETED
                job.completed_at = datetime.now(timezone.utc).isoformat()
                job.result = result
                job.add_log("done", f"Completed: {pages_ingested} pages, {nodes_created} nodes")
                jm._save_to_redis(job)
            except Exception:
                pass

        logger.info("Context pipeline %s completed: %s", context_id, result)
        return result

    except Exception as exc:
        err_msg = str(exc)
        logger.exception("Context pipeline %s failed: %s", context_id, err_msg)

        # Update schedule with error info
        updated_schedule = dict(schedule)
        updated_schedule["last_run_at"] = now.isoformat()
        updated_schedule["last_error"] = err_msg
        updated_schedule["total_runs"] = int(schedule.get("total_runs", 0)) + 1
        updated_config = dict(ctx.config or {})
        updated_config["schedule"] = updated_schedule
        try:
            context_manager.update_context(context_id, config=updated_config)
        except Exception:
            pass

        # Update Job status on error
        if job and jm:
            try:
                from ..ingestion.job_manager import JobStatus
                job.status = JobStatus.FAILED
                job.completed_at = datetime.now(timezone.utc).isoformat()
                job.error = err_msg
                job.add_log("error", f"Failed: {err_msg}")
                jm._save_to_redis(job)
            except Exception:
                pass

        return {
            "status": "error",
            "context_id": context_id,
            "error": err_msg,
        }


def _save_context_run(context_id: str, run_data: Dict) -> None:
    """Persist a context pipeline run record to Redis (best-effort)."""
    import os
    redis_url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL", "")
    if not redis_url:
        return
    try:
        import redis as _redis
        r = _redis.from_url(redis_url)
        key = f"context_pipeline:{context_id}:runs"
        r.lpush(key, json.dumps(run_data))
        r.ltrim(key, 0, 99)
    except Exception as e:
        logger.debug("Could not save context run to Redis: %s", e)


def _get_context_runs(context_id: str, limit: int = 20) -> list:
    """Retrieve run history for a context pipeline from Redis."""
    import os
    redis_url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL", "")
    if not redis_url:
        return []
    try:
        import redis as _redis
        r = _redis.from_url(redis_url)
        raws = r.lrange(f"context_pipeline:{context_id}:runs", 0, limit - 1)
        return [json.loads(x) for x in raws]
    except Exception as e:
        logger.debug("Could not read context runs from Redis: %s", e)
        return []
