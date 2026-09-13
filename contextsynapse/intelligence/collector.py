"""Context Collector — multi-pipeline orchestration for a single context.

Defines a collection plan: one context, multiple pipelines, each with its
own source type, schedule, and configuration. The collector manages all
pipelines as a unit.

Usage:
    from contextsynapse.intelligence.collector import ContextCollector, CollectionPlan, PipelineConfig

    plan = CollectionPlan(
        context_name="TCS Intelligence",
        context_description="Track TCS news, financials, macro indicators",
        tags=["tcs", "it-services", "india", "mid-cap"],
        template="market_analysis",
        pipelines=[
            PipelineConfig(name="news", source_type="rss", sources=["https://..."], interval="30m"),
            PipelineConfig(name="financials", source_type="api", sources=["bse://TCS"], interval="1h"),
            PipelineConfig(name="filings", source_type="crawl", sources=["https://bse..."], interval="daily"),
        ],
    )

    collector = ContextCollector(db)
    collector.register(plan)
    collector.run_all("TCS Intelligence")  # runs all pipelines once
    collector.status("TCS Intelligence")   # check pipeline status
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Interval name → seconds mapping (canonical)
_INTERVAL_SECONDS = {
    "1s": 1, "5s": 5, "10s": 10, "30s": 30,
    "1m": 60, "2m": 120, "5m": 300, "10m": 600, "15m": 900,
    "30m": 1800, "1h": 3600, "6h": 21600, "12h": 43200,
    "daily": 86400, "weekly": 604800,
}

# Backward-compat alias — fractional minutes for sub-minute intervals
_INTERVAL_MINUTES = {k: v / 60.0 for k, v in _INTERVAL_SECONDS.items()}


def _seconds_to_interval(secs: float) -> str:
    """Convert seconds back to the nearest named interval."""
    best = "daily"
    best_diff = abs(_INTERVAL_SECONDS["daily"] - secs)
    for name, val in _INTERVAL_SECONDS.items():
        diff = abs(val - secs)
        if diff < best_diff:
            best = name
            best_diff = diff
    return best


def _minutes_to_interval(mins: float) -> str:
    """Convert minutes back to the nearest named interval (kept for compat)."""
    return _seconds_to_interval(mins * 60)


@dataclass
class TopicFilter:
    """Topic/keyword/semantic filter for a pipeline."""
    topics: List[str] = field(default_factory=list)       # include keywords (word-boundary matched)
    exclude: List[str] = field(default_factory=list)       # exclude keywords
    semantic_query: str = ""                                # semantic filter description
    semantic_threshold: float = 0.6                         # similarity threshold

    def to_dict(self) -> Dict[str, Any]:
        d = {}
        if self.topics:
            d["topics"] = self.topics
        if self.exclude:
            d["exclude"] = self.exclude
        if self.semantic_query:
            d["semantic_query"] = self.semantic_query
            d["semantic_threshold"] = self.semantic_threshold
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TopicFilter":
        if not data:
            return cls()
        return cls(
            topics=data.get("topics", []),
            exclude=data.get("exclude", []),
            semantic_query=data.get("semantic_query", ""),
            semantic_threshold=data.get("semantic_threshold", 0.6),
        )

    def to_filter_config(self):
        """Convert to ingestion FilterConfig for the pipeline."""
        from ..ingestion.filters import FilterConfig, SemanticRule
        rules = []
        if self.semantic_query:
            rules.append(SemanticRule(
                query=self.semantic_query,
                direction="include",
                threshold=self.semantic_threshold,
            ))
        return FilterConfig(
            keywords_include=self.topics,
            keywords_exclude=self.exclude,
            semantic_rules=rules,
            semantic_query=self.semantic_query,
            semantic_auto_threshold=self.semantic_threshold,
        ) if (self.topics or self.exclude or self.semantic_query) else None


@dataclass
class PipelineConfig:
    """Configuration for a single collection pipeline within a context."""
    name: str                              # e.g., "news_sentiment", "financial_data"
    source_type: str                       # rss | api | crawl | upload | manual
    sources: List[str] = field(default_factory=list)  # URLs, API endpoints, file paths
    interval: str = "daily"                # 30m | 1h | 6h | daily | weekly | manual
    strategy: str = ""                     # news_article | business_report | technical_doc | auto
    enabled: bool = True
    filter: TopicFilter = field(default_factory=TopicFilter)
    max_pages: int = 10                    # for crawl sources
    config: Dict[str, Any] = field(default_factory=dict)  # source-specific config

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "name": self.name,
            "source_type": self.source_type,
            "sources": self.sources,
            "interval": self.interval,
            "strategy": self.strategy,
            "enabled": self.enabled,
            "max_pages": self.max_pages,
            "config": self.config,
        }
        filter_dict = self.filter.to_dict()
        if filter_dict:
            d["filter"] = filter_dict
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PipelineConfig":
        return cls(
            name=data.get("name", ""),
            source_type=data.get("source_type", "manual"),
            sources=data.get("sources", []),
            interval=data.get("interval", "daily"),
            strategy=data.get("strategy", ""),
            enabled=data.get("enabled", True),
            filter=TopicFilter.from_dict(data.get("filter", {})),
            max_pages=data.get("max_pages", 10),
            config=data.get("config", {}),
        )


@dataclass
class PipelineStatus:
    """Runtime status of a single pipeline."""
    name: str
    source_type: str
    enabled: bool = True
    last_run: str = ""
    last_result: str = ""       # success | error | skipped
    items_ingested: int = 0
    items_deduped: int = 0
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "source_type": self.source_type,
            "enabled": self.enabled,
            "last_run": self.last_run,
            "last_result": self.last_result,
            "items_ingested": self.items_ingested,
            "items_deduped": self.items_deduped,
            "errors": self.errors[-5:],
        }


@dataclass
class CollectionPlan:
    """A complete collection plan for a single context."""
    context_name: str
    context_description: str = ""
    tags: List[str] = field(default_factory=list)
    template: str = ""                     # correlation template name
    pipelines: List[PipelineConfig] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "context_name": self.context_name,
            "context_description": self.context_description,
            "tags": self.tags,
            "template": self.template,
            "pipelines": [p.to_dict() for p in self.pipelines],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CollectionPlan":
        return cls(
            context_name=data.get("context_name", ""),
            context_description=data.get("context_description", ""),
            tags=data.get("tags", []),
            template=data.get("template", ""),
            pipelines=[PipelineConfig.from_dict(p) for p in data.get("pipelines", [])],
        )


@dataclass
class CollectionResult:
    """Result of running all pipelines for a context."""
    context_name: str
    pipelines_run: int = 0
    pipelines_skipped: int = 0
    total_ingested: int = 0
    total_deduped: int = 0
    total_errors: int = 0
    pipeline_results: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "context_name": self.context_name,
            "pipelines_run": self.pipelines_run,
            "pipelines_skipped": self.pipelines_skipped,
            "total_ingested": self.total_ingested,
            "total_deduped": self.total_deduped,
            "total_errors": self.total_errors,
            "pipeline_results": self.pipeline_results,
        }


class ContextCollector:
    """Multi-pipeline orchestrator for context collection.

    Manages multiple collection pipelines feeding a single context.
    Each pipeline has its own source type, schedule, and configuration.
    """

    def __init__(self, db=None, rate_limiter=None, health_monitor=None):
        self._db = db
        self._plans: Dict[str, CollectionPlan] = {}
        self._status: Dict[str, Dict[str, PipelineStatus]] = {}  # context -> {pipeline_name -> status}
        self._rate_limiter = rate_limiter
        self._health_monitor = health_monitor
        self._seen_urls: set = set()  # URL-level dedup across RSS polls

    def register(self, plan: CollectionPlan):
        """Register a collection plan for a context."""
        self._plans[plan.context_name] = plan
        self._status[plan.context_name] = {
            p.name: PipelineStatus(name=p.name, source_type=p.source_type, enabled=p.enabled)
            for p in plan.pipelines
        }
        logger.info("[COLLECTOR] Registered plan '%s' with %d pipelines",
                     plan.context_name, len(plan.pipelines))

    def get_plan(self, context_name: str) -> Optional[CollectionPlan]:
        """Get a registered collection plan."""
        return self._plans.get(context_name)

    def list_plans(self) -> List[str]:
        """List all registered context collection plans."""
        return list(self._plans.keys())

    def status(self, context_name: str) -> Dict[str, Any]:
        """Get status of all pipelines for a context."""
        plan = self._plans.get(context_name)
        if not plan:
            return {"error": f"No plan registered for '{context_name}'"}

        statuses = self._status.get(context_name, {})
        return {
            "context_name": context_name,
            "template": plan.template,
            "pipeline_count": len(plan.pipelines),
            "pipelines": {name: s.to_dict() for name, s in statuses.items()},
        }

    def run_all(
        self,
        context_name: str,
        ingest_fn: Optional[Callable] = None,
    ) -> CollectionResult:
        """Run all enabled pipelines for a context.

        Args:
            context_name: Name of the registered context
            ingest_fn: Optional callable(text, title, source_url, strategy) for custom ingestion.
                       If None, uses the default smart_ingest pipeline.

        Returns CollectionResult with per-pipeline stats.
        """
        plan = self._plans.get(context_name)
        if not plan:
            return CollectionResult(context_name=context_name, total_errors=1,
                                     pipeline_results=[{"error": "No plan registered"}])

        result = CollectionResult(context_name=context_name)

        # Build context purpose from plan metadata
        from ..ingestion.amplifier import build_context_purpose
        context_purpose = build_context_purpose(
            context_name=plan.context_name,
            context_description=plan.context_description,
            tags=plan.tags,
        )

        for pipeline_config in plan.pipelines:
            if not pipeline_config.enabled:
                result.pipelines_skipped += 1
                continue

            pipe_result = self._run_pipeline(
                pipeline_config, plan, context_purpose, ingest_fn
            )

            # Apply adaptive scheduling — update pipeline interval for next run
            next_interval = pipe_result.get("next_interval", pipeline_config.interval)
            if next_interval != pipeline_config.interval:
                logger.info("[COLLECTOR] Adaptive schedule: '%s' %s -> %s (%s)",
                             pipeline_config.name, pipeline_config.interval, next_interval,
                             pipe_result.get("_schedule_reason", ""))
                pipeline_config.interval = next_interval

            result.pipeline_results.append(pipe_result)
            result.pipelines_run += 1
            result.total_ingested += pipe_result.get("ingested", 0)
            result.total_deduped += pipe_result.get("deduped", 0)
            if pipe_result.get("errors"):
                result.total_errors += len(pipe_result["errors"])

        logger.info("[COLLECTOR] '%s': %d pipelines run, %d items ingested, %d deduped",
                     context_name, result.pipelines_run, result.total_ingested, result.total_deduped)

        return result

    def run_pipeline(
        self,
        context_name: str,
        pipeline_name: str,
        ingest_fn: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """Run a single named pipeline for a context."""
        plan = self._plans.get(context_name)
        if not plan:
            return {"error": f"No plan registered for '{context_name}'"}

        pipeline_config = None
        for p in plan.pipelines:
            if p.name == pipeline_name:
                pipeline_config = p
                break

        if not pipeline_config:
            return {"error": f"Pipeline '{pipeline_name}' not found in plan '{context_name}'"}

        from ..ingestion.amplifier import build_context_purpose
        context_purpose = build_context_purpose(
            context_name=plan.context_name,
            context_description=plan.context_description,
            tags=plan.tags,
        )

        return self._run_pipeline(pipeline_config, plan, context_purpose, ingest_fn)

    def _run_pipeline(
        self,
        pipeline_config: PipelineConfig,
        plan: CollectionPlan,
        context_purpose: str,
        ingest_fn: Optional[Callable],
    ) -> Dict[str, Any]:
        """Execute a single pipeline's collection run.

        Source-type-aware ingestion:
        - rss:    Fetch RSS feed XML, extract entry URLs, ingest each article
        - crawl:  Follow links from seed URL, ingest pages (up to max_pages)
        - api:    Fetch JSON/text from endpoint, ingest as structured text
        - upload: Skip (triggered manually via file upload)
        - manual: Skip (triggered manually via text input)
        """
        now = datetime.now(timezone.utc).isoformat()
        status = self._status.get(plan.context_name, {}).get(pipeline_config.name)
        if status:
            status.last_run = now

        pipe_result = {
            "pipeline": pipeline_config.name,
            "source_type": pipeline_config.source_type,
            "ingested": 0,
            "deduped": 0,
            "filtered": 0,
            "errors": [],
        }

        try:
            if pipeline_config.source_type in ("manual", "upload"):
                pipe_result["skipped"] = f"{pipeline_config.source_type} pipeline — trigger manually"
                if status:
                    status.last_result = "skipped"
                return pipe_result

            # Build filter config from pipeline's TopicFilter
            filter_config = pipeline_config.filter.to_filter_config()

            for source in pipeline_config.sources:
                try:
                    if ingest_fn:
                        ingest_result = ingest_fn(
                            source=source,
                            strategy=pipeline_config.strategy,
                            context_purpose=context_purpose,
                        )
                        self._track_result(ingest_result, pipe_result)
                        continue

                    if pipeline_config.source_type == "rss":
                        # RSS: fetch feed, extract entry URLs, ingest each article
                        self._ingest_rss(source, pipeline_config, context_purpose, filter_config, pipe_result)

                    elif pipeline_config.source_type == "crawl":
                        # Crawl: follow links from seed URL
                        self._ingest_crawl(source, pipeline_config, context_purpose, filter_config, pipe_result)

                    elif pipeline_config.source_type == "api":
                        # API: fetch structured data, ingest as text
                        self._ingest_api(source, pipeline_config, context_purpose, filter_config, pipe_result)

                    else:
                        pipe_result["errors"].append(f"Unknown source_type: {pipeline_config.source_type}")

                except Exception as source_err:
                    pipe_result["errors"].append(f"{source}: {source_err}")

            if status:
                status.last_result = "success" if not pipe_result["errors"] else "error"
                status.items_ingested += pipe_result["ingested"]
                status.items_deduped += pipe_result["deduped"]
                status.errors.extend(pipe_result["errors"])

        except Exception as exc:
            pipe_result["errors"].append(str(exc))
            if status:
                status.last_result = "error"

        # Adaptive scheduling — current run decides next run interval
        pipe_result["next_interval"] = self._adaptive_interval(pipeline_config, pipe_result)

        # Track pipeline health
        if self._health_monitor:
            success = pipe_result["ingested"] > 0 or pipe_result["deduped"] > 0
            error_str = "; ".join(pipe_result.get("errors", [])[:2]) if pipe_result.get("errors") else ""
            self._health_monitor.record_run(
                plan.context_name, pipeline_config.name,
                success=success or not error_str,
                error=error_str,
            )

        return pipe_result

    def _adaptive_interval(self, config: PipelineConfig, result: Dict) -> str:
        """Decide next run interval based on current run results.

        Rules:
        - All content deduped (nothing new) → slow down (2x current interval, max daily)
        - Errors on all sources → retry sooner (half interval, min 5m)
        - New content found → keep current interval
        - High volume of new content (>5 items) → speed up (half interval, min 5m)
        """
        base = config.interval
        ingested = result.get("ingested", 0)
        deduped = result.get("deduped", 0)
        errors = result.get("errors", [])
        total_sources = len(config.sources) if config.sources else 1

        base_mins = _INTERVAL_MINUTES.get(base, 1440)

        # All deduped, nothing new → slow down
        if deduped > 0 and ingested == 0 and not errors:
            slower = min(base_mins * 2, 1440)  # max daily
            result["_schedule_reason"] = "all_deduped_slowing_down"
            return _minutes_to_interval(slower)

        # All sources errored → retry sooner
        if errors and ingested == 0 and deduped == 0:
            faster = max(base_mins // 2, 5)  # min 5 minutes
            result["_schedule_reason"] = "errors_retry_sooner"
            return _minutes_to_interval(faster)

        # High volume → speed up
        if ingested >= 5:
            faster = max(base_mins // 2, 5)
            result["_schedule_reason"] = "high_volume_speeding_up"
            return _minutes_to_interval(faster)

        # Normal — keep current
        result["_schedule_reason"] = "normal"
        return base

    def _track_result(self, ingest_result, pipe_result: Dict):
        """Track an ingestion result into pipeline stats."""
        if hasattr(ingest_result, 'errors') and ingest_result.errors:
            if any("Duplicate" in str(e) for e in ingest_result.errors):
                pipe_result["deduped"] += 1
            else:
                pipe_result["errors"].extend(ingest_result.errors)
        else:
            pipe_result["ingested"] += 1

    def _ingest_rss(self, feed_url: str, config: PipelineConfig, context_purpose: str,
                    filter_config, pipe_result: Dict):
        """Fetch RSS feed, filter headlines by topic, ingest relevant articles only."""
        try:
            import re
            import xml.etree.ElementTree as ET
            import urllib.request

            req = urllib.request.Request(feed_url, headers={"User-Agent": "AIContextDB/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                feed_xml = resp.read().decode("utf-8")

            root = ET.fromstring(feed_xml)

            # Extract (title, link) pairs from RSS 2.0 or Atom
            entries = []  # [(title, link, desc, pubdate), ...]
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                desc = (item.findtext("description") or "").strip()
                pubdate = (item.findtext("pubDate") or "").strip()
                if link:
                    entries.append((title, link, desc, pubdate))
            if not entries:
                ns = "{http://www.w3.org/2005/Atom}"
                for entry in root.iter(f"{ns}entry"):
                    title = (entry.findtext(f"{ns}title") or "").strip()
                    link_el = entry.find(f"{ns}link")
                    link = link_el.get("href", "") if link_el is not None else ""
                    desc = (entry.findtext(f"{ns}summary") or "").strip()
                    pubdate = (entry.findtext(f"{ns}published") or entry.findtext(f"{ns}updated") or "").strip()
                    if link:
                        entries.append((title, link.strip(), desc, pubdate))

            logger.info("[RSS] %s: found %d entries", feed_url, len(entries))

            # PRE-FILTER: check headlines against topic keywords BEFORE fetching
            # This prevents fetching irrelevant articles from broad feeds
            topics = config.filter.topics if config.filter.topics else []
            exclude = config.filter.exclude if config.filter.exclude else []

            relevant_entries = []
            for entry_tuple in entries:
                title, link, desc = entry_tuple[0], entry_tuple[1], entry_tuple[2]
                headline_text = f"{title} {desc}".lower()

                # Exclude check (word boundary)
                excluded = False
                for kw in exclude:
                    if re.search(r'\b' + re.escape(kw) + r'\b', headline_text, re.IGNORECASE):
                        excluded = True
                        break
                if excluded:
                    pipe_result.setdefault("headline_filtered", 0)
                    pipe_result["headline_filtered"] = pipe_result.get("headline_filtered", 0) + 1
                    continue

                # Topic include check (word boundary) — if topics configured, headline must match
                if topics:
                    matched = any(
                        re.search(r'\b' + re.escape(kw) + r'\b', headline_text, re.IGNORECASE)
                        for kw in topics
                    )
                    if not matched:
                        pipe_result.setdefault("headline_filtered", 0)
                        pipe_result["headline_filtered"] = pipe_result.get("headline_filtered", 0) + 1
                        continue

                relevant_entries.append((title, link))

            logger.info("[RSS] %s: %d/%d entries passed headline filter",
                         feed_url, len(relevant_entries), len(entries))

            # Ingest only relevant articles (rate-limited, URL-deduped)
            from ..ingestion.smart_ingest import ingest_url
            for title, article_url in relevant_entries[:config.max_pages]:
                # URL dedup — skip if we've already fetched this URL in any previous poll
                if article_url in self._seen_urls:
                    pipe_result.setdefault("url_deduped", 0)
                    pipe_result["url_deduped"] = pipe_result.get("url_deduped", 0) + 1
                    continue
                self._seen_urls.add(article_url)

                # Rate limit check
                if self._rate_limiter and not self._rate_limiter.allow(article_url):
                    pipe_result.setdefault("rate_limited", 0)
                    pipe_result["rate_limited"] += 1
                    continue
                try:
                    r = ingest_url(
                        article_url, self._db,
                        strategy=config.strategy or None,
                        context_purpose=context_purpose,
                        filter_config=filter_config,
                    )
                    self._track_result(r, pipe_result)
                except Exception as exc:
                    pipe_result["errors"].append(f"RSS article {article_url}: {exc}")

        except Exception as exc:
            pipe_result["errors"].append(f"RSS feed {feed_url}: {exc}")

    def _ingest_crawl(self, seed_url: str, config: PipelineConfig, context_purpose: str,
                      filter_config, pipe_result: Dict):
        """Crawl from seed URL, ingest discovered pages."""
        try:
            from ..ingestion.smart_ingest import ingest_url
            r = ingest_url(
                seed_url, self._db,
                strategy=config.strategy or None,
                context_purpose=context_purpose,
                filter_config=filter_config,
            )
            self._track_result(r, pipe_result)
        except Exception as exc:
            pipe_result["errors"].append(f"Crawl {seed_url}: {exc}")

    def _ingest_api(self, api_url: str, config: PipelineConfig, context_purpose: str,
                    filter_config, pipe_result: Dict):
        """Fetch API data and ingest as structured text."""
        try:
            import urllib.request

            headers = config.config.get("headers", {})
            req = urllib.request.Request(api_url, headers={
                "User-Agent": "AIContextDB/1.0",
                **headers,
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                api_data = resp.read().decode("utf-8")

            from ..ingestion.smart_ingest import ingest_text
            r = ingest_text(
                api_data, self._db,
                title=f"{config.name}: {api_url}",
                source_url=api_url,
                strategy=config.strategy or None,
                context_purpose=context_purpose,
                filter_config=filter_config,
            )
            self._track_result(r, pipe_result)
        except Exception as exc:
            pipe_result["errors"].append(f"API {api_url}: {exc}")
