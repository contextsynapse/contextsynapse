"""Watchdog Manager — multiple watchdogs, one per hierarchy level.

Each watchdog monitors a specific scope (global, country, sector) with
its own sources and keyword patterns. When it detects an event, it
ingests into its linked atomic context and signals propagate down.

Usage:
    from contextsynapse.intelligence.watchdog_manager import WatchdogManager

    manager = WatchdogManager(hierarchy=h, reactive=rc)

    manager.add_watchdog(
        name="global",
        context="Global Macro",
        sources=[("https://feeds.reuters.com/reuters/topNews", "Reuters")],
        keywords=["war", "sanctions", "crash", "pandemic"],
    )

    manager.add_watchdog(
        name="india",
        context="India Economy",
        sources=[("https://economictimes.indiatimes.com/rssfeedstopstories.cms", "ET")],
        keywords=["RBI", "SEBI", "rupee crash", "policy change", "budget"],
    )

    manager.start_all()  # each polls every 60s independently
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .watchdog import BreakingNewsWatchdog

logger = logging.getLogger(__name__)


@dataclass
class WatchdogConfig:
    """Configuration for a single watchdog instance."""
    name: str
    context: str                    # which atomic context to ingest into + signal from
    sources: List[tuple] = field(default_factory=list)  # [(url, name), ...]
    keywords: List[str] = field(default_factory=list)    # level-specific high-impact keywords
    interval_seconds: int = 60
    enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "context": self.context,
            "sources": [{"url": u, "name": n} for u, n in self.sources],
            "keywords": self.keywords,
            "interval_seconds": self.interval_seconds,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WatchdogConfig":
        sources = [(s["url"], s.get("name", "")) for s in data.get("sources", [])]
        return cls(
            name=data.get("name", ""),
            context=data.get("context", ""),
            sources=sources,
            keywords=data.get("keywords", []),
            interval_seconds=data.get("interval_seconds", 60),
            enabled=data.get("enabled", True),
        )


# Default watchdog configurations per hierarchy level
DEFAULT_WATCHDOGS = [
    WatchdogConfig(
        name="global",
        context="Global Macro",
        sources=[
            ("https://feeds.bbci.co.uk/news/rss.xml", "BBC Breaking"),
            ("https://www.thehindubusinessline.com/feeder/default.rss", "Hindu BusinessLine"),
        ],
        keywords=[
            "war", "invasion", "missile", "sanctions", "embargo",
            "crash", "collapse", "default", "recession", "pandemic",
            "emergency", "crisis", "nuclear",
        ],
    ),
    WatchdogConfig(
        name="india",
        context="India Economy",
        sources=[
            ("https://economictimes.indiatimes.com/rssfeedstopstories.cms", "ET India"),
        ],
        keywords=[
            "RBI", "repo rate", "SEBI", "rupee crash", "fiscal deficit",
            "budget", "demonetization", "FII", "policy change",
            "inflation surge", "GDP",
        ],
    ),
    WatchdogConfig(
        name="us",
        context="US Economy",
        sources=[
            ("https://feeds.bbci.co.uk/news/business/rss.xml", "BBC Business"),
        ],
        keywords=[
            "Federal Reserve", "Fed rate", "debt ceiling", "government shutdown",
            "recession", "employment", "CPI", "treasury",
        ],
    ),
    WatchdogConfig(
        name="it_sector",
        context="IT Services Sector",
        sources=[
            ("https://economictimes.indiatimes.com/rssfeedstopstories.cms", "ET Tech"),
        ],
        keywords=[
            "TCS", "Infosys", "Wipro", "HCL", "mega deal", "layoffs",
            "NASSCOM", "outsourcing ban", "H1B", "visa",
        ],
    ),
]


class WatchdogManager:
    """Manages multiple watchdog instances, one per hierarchy level."""

    def __init__(self, hierarchy=None, reactive=None, impact_tracker=None, db=None):
        self._hierarchy = hierarchy
        self._reactive = reactive
        self._impact_tracker = impact_tracker
        self._db = db
        self._watchdogs: Dict[str, BreakingNewsWatchdog] = {}
        self._configs: Dict[str, WatchdogConfig] = {}

    def add_watchdog(self, config: WatchdogConfig):
        """Add a watchdog for a specific hierarchy level."""
        wd = BreakingNewsWatchdog(
            db=self._db,
            watchdog_context=config.context,
            hierarchy=self._hierarchy,
            reactive=self._reactive,
            impact_tracker=self._impact_tracker,
        )

        for url, name in config.sources:
            wd.add_source(url, name=name)

        # Add level-specific keywords to the watchdog's detection patterns
        if config.keywords:
            self._add_custom_keywords(wd, config.keywords)

        self._watchdogs[config.name] = wd
        self._configs[config.name] = config
        logger.info("[WATCHDOG-MGR] Added '%s' -> context '%s' (%d sources, %d keywords)",
                     config.name, config.context, len(config.sources), len(config.keywords))

    def _add_custom_keywords(self, wd, keywords: List[str]):
        """Inject custom keywords into the watchdog's detection patterns."""
        # The watchdog uses _HIGH_IMPACT_PATTERNS from watchdog.py
        # We can't modify the compiled regex, but we can add a custom check
        # by storing keywords on the watchdog instance
        wd._custom_keywords = keywords
        wd._custom_pattern = re.compile(
            r'\b(?:' + '|'.join(re.escape(k) for k in keywords) + r')\b',
            re.IGNORECASE,
        )

        # Monkey-patch the classify method to also check custom keywords
        original_classify = wd._classify_headline

        def enhanced_classify(headline, link, source):
            # First check built-in patterns
            result = original_classify(headline, link, source)
            if result:
                return result

            # Then check custom keywords
            matches = wd._custom_pattern.findall(headline)
            if matches:
                from .watchdog import WatchdogAlert
                return WatchdogAlert(
                    headline=headline,
                    source_name=source.name,
                    source_url=link or source.url,
                    impact_level="high" if len(matches) >= 2 else "medium",
                    matched_keywords=matches[:5],
                )
            return None

        wd._classify_headline = enhanced_classify

    def add_defaults(self):
        """Add all default watchdog configurations."""
        for config in DEFAULT_WATCHDOGS:
            self.add_watchdog(config)

    def start_all(self):
        """Start all enabled watchdogs."""
        started = 0
        for name, config in self._configs.items():
            if not config.enabled:
                continue
            wd = self._watchdogs[name]
            wd.start(interval_seconds=config.interval_seconds)
            started += 1

        logger.info("[WATCHDOG-MGR] Started %d/%d watchdogs", started, len(self._configs))

    def stop_all(self):
        """Stop all watchdogs."""
        for wd in self._watchdogs.values():
            wd.stop()
        logger.info("[WATCHDOG-MGR] All watchdogs stopped")

    def check_all(self) -> Dict[str, Any]:
        """Run one check cycle on all watchdogs. Returns results per watchdog."""
        results = {}
        for name, wd in self._watchdogs.items():
            if not self._configs[name].enabled:
                continue
            result = wd.check()
            results[name] = result.to_dict()
        return results

    def status(self) -> Dict[str, Any]:
        """Get status of all watchdogs."""
        statuses = {}
        for name, wd in self._watchdogs.items():
            config = self._configs[name]
            statuses[name] = {
                "context": config.context,
                "running": wd._running,
                "sources": len(wd._sources),
                "keywords": config.keywords[:5],
                "interval": config.interval_seconds,
                "recent_alerts": wd.get_alert_log(limit=3),
            }
        return {"watchdogs": statuses, "total": len(self._watchdogs)}

    def get_all_alerts(self, limit: int = 20) -> List[Dict]:
        """Get recent alerts across all watchdogs."""
        all_alerts = []
        for name, wd in self._watchdogs.items():
            for alert in wd.get_alert_log(limit=10):
                alert["watchdog"] = name
                alert["context"] = self._configs[name].context
                all_alerts.append(alert)

        all_alerts.sort(key=lambda a: a.get("detected_at", ""), reverse=True)
        return all_alerts[:limit]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "watchdogs": [c.to_dict() for c in self._configs.values()],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], **kwargs) -> "WatchdogManager":
        mgr = cls(**kwargs)
        for wd_data in data.get("watchdogs", []):
            mgr.add_watchdog(WatchdogConfig.from_dict(wd_data))
        return mgr
