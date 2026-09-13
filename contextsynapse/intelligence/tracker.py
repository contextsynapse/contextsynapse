"""Template-driven track() — one-call entity tracking.

Instead of manually configuring 8 pipelines per company, define a
tracking template once and call track("TCS") to set up everything.

Usage:
    from contextsynapse.intelligence.tracker import EntityTracker

    tracker = EntityTracker(collector, hierarchy, persistence)
    tracker.register_template("indian_listed_company", IndianListedCompanyTemplate)

    # One call does everything:
    tracker.track("TCS", template="indian_listed_company",
                  extra={"bse_code": "532540", "full_name": "Tata Consultancy Services"})

    # Creates: context + pipelines + hierarchy links + starts collection
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .collector import CollectionPlan, PipelineConfig, TopicFilter

logger = logging.getLogger(__name__)


@dataclass
class TrackingTemplate:
    """Defines how to auto-configure pipelines for an entity type."""
    name: str
    description: str = ""
    level: str = "company"                    # hierarchy level for tracked entities
    parent_context: str = ""                  # upstream context to link to
    peer_relationship: str = "competes_with"  # lateral link type between tracked entities
    correlation_template: str = ""            # which correlation template to use
    tags: List[str] = field(default_factory=list)

    def build_plan(self, entity_name: str, extra: Dict[str, Any] = None) -> CollectionPlan:
        """Override in subclasses to build a CollectionPlan for the entity."""
        extra = extra or {}
        return CollectionPlan(
            context_name=entity_name,
            context_description=f"Track {entity_name}",
            tags=[entity_name.lower()] + self.tags,
            template=self.correlation_template,
            pipelines=[
                PipelineConfig(
                    name="news", source_type="rss",
                    sources=extra.get("news_sources", []),
                    interval="30m", strategy="news_article",
                    filter=TopicFilter(
                        topics=extra.get("topics", [entity_name]),
                        exclude=extra.get("exclude", []),
                    ),
                ),
            ],
        )


class IndianListedCompanyTemplate(TrackingTemplate):
    """Auto-configure pipelines for an Indian listed company."""

    def __init__(self):
        super().__init__(
            name="indian_listed_company",
            description="Indian BSE/NSE listed company — news, filings, earnings",
            level="company",
            parent_context="IT Services Sector",
            peer_relationship="competes_with",
            correlation_template="market_analysis",
            tags=["india", "bse", "nse"],
        )

    def build_plan(self, entity_name: str, extra: Dict[str, Any] = None) -> CollectionPlan:
        extra = extra or {}
        full_name = extra.get("full_name", entity_name)
        bse_code = extra.get("bse_code", "")
        topics = extra.get("topics", [entity_name, full_name])

        pipelines = [
            PipelineConfig(
                name="company_news", source_type="rss",
                sources=[
                    "https://economictimes.indiatimes.com/rssfeedstopstories.cms",
                    "https://www.moneycontrol.com/rss/latestnews.xml",
                ],
                interval="30m", strategy="news_article",
                filter=TopicFilter(
                    topics=topics,
                    exclude=["cricket", "weather", "horoscope", "entertainment"],
                    semantic_query=f"{full_name} business performance, deals, earnings",
                ),
            ),
            PipelineConfig(
                name="earnings_transcripts", source_type="upload",
                interval="quarterly",
                filter=TopicFilter(
                    topics=["revenue", "margin", "guidance", "pipeline", "attrition"],
                ),
            ),
        ]

        if bse_code:
            pipelines.append(PipelineConfig(
                name="bse_filings", source_type="crawl",
                sources=[f"https://www.bseindia.com/stock-share-price/{bse_code}/announcements"],
                interval="daily", strategy="business_report",
                filter=TopicFilter(
                    topics=["quarterly results", "board meeting", "dividend", "disclosure"],
                ),
            ))

        return CollectionPlan(
            context_name=entity_name,
            context_description=f"Track {full_name} — news, filings, earnings, competitive position",
            tags=[entity_name.lower(), "it-services", "india"],
            template="market_analysis",
            pipelines=pipelines,
        )


class USListedCompanyTemplate(TrackingTemplate):
    """Auto-configure pipelines for a US listed company."""

    def __init__(self):
        super().__init__(
            name="us_listed_company",
            description="US NYSE/NASDAQ listed company",
            level="company",
            parent_context="US Economy",
            peer_relationship="competes_with",
            correlation_template="market_analysis",
            tags=["us", "nyse", "nasdaq"],
        )

    def build_plan(self, entity_name: str, extra: Dict[str, Any] = None) -> CollectionPlan:
        extra = extra or {}
        full_name = extra.get("full_name", entity_name)
        ticker = extra.get("ticker", entity_name)
        topics = extra.get("topics", [entity_name, full_name, ticker])

        return CollectionPlan(
            context_name=entity_name,
            context_description=f"Track {full_name} ({ticker})",
            tags=[entity_name.lower(), "us", "equity"],
            template="market_analysis",
            pipelines=[
                PipelineConfig(
                    name="company_news", source_type="rss",
                    sources=[
                        "https://feeds.reuters.com/reuters/businessNews",
                    ],
                    interval="30m", strategy="news_article",
                    filter=TopicFilter(
                        topics=topics,
                        exclude=["sports", "entertainment"],
                        semantic_query=f"{full_name} business, earnings, products, competition",
                    ),
                ),
                PipelineConfig(
                    name="sec_filings", source_type="crawl",
                    sources=[f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={ticker}&type=&dateb=&owner=include&count=10"],
                    interval="daily", strategy="business_report",
                ),
                PipelineConfig(
                    name="earnings", source_type="upload",
                    interval="quarterly",
                ),
            ],
        )


class GenericTopicTemplate(TrackingTemplate):
    """Generic template for tracking any topic (not company-specific)."""

    def __init__(self):
        super().__init__(
            name="generic_topic",
            description="Track any topic — news, analysis, developments",
            level="sector",
            correlation_template="competitive_intel",
            tags=[],
        )

    def build_plan(self, entity_name: str, extra: Dict[str, Any] = None) -> CollectionPlan:
        extra = extra or {}
        topics = extra.get("topics", [entity_name])
        sources = extra.get("sources", [
            "https://feeds.reuters.com/reuters/topNews",
        ])

        return CollectionPlan(
            context_name=entity_name,
            context_description=extra.get("description", f"Track {entity_name}"),
            tags=[entity_name.lower()] + extra.get("tags", []),
            template="competitive_intel",
            pipelines=[
                PipelineConfig(
                    name="news", source_type="rss",
                    sources=sources,
                    interval=extra.get("interval", "1h"),
                    strategy="news_article",
                    filter=TopicFilter(topics=topics, exclude=extra.get("exclude", [])),
                ),
            ],
        )


# Built-in templates registry
_BUILTIN_TEMPLATES = {
    "indian_listed_company": IndianListedCompanyTemplate,
    "us_listed_company": USListedCompanyTemplate,
    "generic_topic": GenericTopicTemplate,
}


class EntityTracker:
    """One-call entity tracking — template-driven setup."""

    def __init__(self, collector=None, hierarchy=None, persistence=None):
        self._collector = collector
        self._hierarchy = hierarchy
        self._persistence = persistence
        self._templates: Dict[str, TrackingTemplate] = {}
        self._tracked: Dict[str, str] = {}  # entity_name -> template_name

        # Register builtins
        for name, cls in _BUILTIN_TEMPLATES.items():
            self._templates[name] = cls()

    def register_template(self, name: str, template: TrackingTemplate):
        """Register a custom tracking template."""
        self._templates[name] = template

    def list_templates(self) -> List[str]:
        return list(self._templates.keys())

    def track(
        self,
        entity_name: str,
        template: str = "generic_topic",
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Start tracking an entity with one call.

        1. Looks up the template
        2. Builds a CollectionPlan
        3. Registers it with the collector
        4. Links it in the signal hierarchy
        5. Persists everything
        """
        tmpl = self._templates.get(template)
        if not tmpl:
            return {"error": f"Unknown template: {template}. Available: {list(self._templates.keys())}"}

        # Build plan from template
        plan = tmpl.build_plan(entity_name, extra or {})

        # Register with collector
        if self._collector:
            self._collector.register(plan)

        # Add to hierarchy
        if self._hierarchy:
            from .signal_hierarchy import ContextLink
            self._hierarchy.set_level(entity_name, tmpl.level)

            # Link to parent context
            if tmpl.parent_context:
                self._hierarchy.add_link(ContextLink(
                    upstream=tmpl.parent_context,
                    downstream=entity_name,
                    relationship=f"{tmpl.level}_in",
                    weight=0.8,
                ))

            # Link to existing peers (same template)
            for existing, existing_tmpl in self._tracked.items():
                if existing_tmpl == template and existing != entity_name:
                    self._hierarchy.add_lateral_link(ContextLink(
                        upstream=existing,
                        downstream=entity_name,
                        relationship=tmpl.peer_relationship,
                        weight=0.6,
                    ))

        # Persist
        if self._persistence:
            self._persistence.save_collection_plan(plan)
            if self._hierarchy:
                self._persistence.save_hierarchy(self._hierarchy)

        self._tracked[entity_name] = template

        result = {
            "status": "tracking",
            "entity": entity_name,
            "template": template,
            "pipelines": len(plan.pipelines),
            "pipeline_names": [p.name for p in plan.pipelines],
        }

        logger.info("[TRACK] Now tracking '%s' via template '%s' (%d pipelines)",
                     entity_name, template, len(plan.pipelines))
        return result

    def untrack(self, entity_name: str) -> Dict[str, Any]:
        """Stop tracking an entity."""
        if entity_name in self._tracked:
            del self._tracked[entity_name]
        if self._collector and entity_name in self._collector._plans:
            del self._collector._plans[entity_name]
        return {"status": "untracked", "entity": entity_name}

    def list_tracked(self) -> List[Dict[str, str]]:
        return [{"entity": k, "template": v} for k, v in self._tracked.items()]
