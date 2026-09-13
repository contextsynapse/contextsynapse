"""Breaking News Watchdog — ultra-fast polling for high-impact events.

A lightweight sentinel context that polls breaking news sources every
1-2 minutes looking ONLY for high-impact events. When detected, it
fires signals through the signal hierarchy, triggering immediate
collection runs on all affected contexts.

This solves the "war starts at 2pm, pipeline runs at 6pm" problem.
The watchdog runs every minute. Everything else runs on normal schedule
until the watchdog wakes them up.

Usage:
    from contextsynapse.intelligence.watchdog import BreakingNewsWatchdog

    watchdog = BreakingNewsWatchdog(
        hierarchy=signal_hierarchy,
        collector=context_collector,
        reactive=reactive_controller,
    )

    # Add sources to watch
    watchdog.add_source("https://feeds.reuters.com/reuters/topNews", name="Reuters")
    watchdog.add_source("https://feeds.bbci.co.uk/news/rss.xml", name="BBC")

    # Run once (call this every minute from scheduler)
    result = watchdog.check()
    # → {"checked": 2, "alerts": 1, "contexts_triggered": 5,
    #    "alert_details": [{"headline": "...", "impact": "high", ...}]}

    # Or start as background loop
    watchdog.start(interval_seconds=60)
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# High-impact keywords — if ANY of these appear in a headline,
# it's a potential breaking event worth investigating
_HIGH_IMPACT_PATTERNS = re.compile(
    r'\b(?:'
    # Geopolitical
    r'war|invasion|invade[ds]?|missile|bomb(?:ing|ed)?|airstrike|ceasefire|'
    r'coup|martial\s+law|state\s+of\s+emergency|'
    # Economic crisis
    r'crash(?:ed|es|ing)?|collapse[ds]?|default(?:ed|s)?|bankruptcy|'
    r'recession|depression|bail\s*out|'
    # Sanctions/policy
    r'sanction[sed]*|embargo|ban(?:ned|s)?|tariff|trade\s+war|'
    r'rate\s+(?:hike|cut)|emergency\s+(?:rate|meeting|session)|'
    # Market events
    r'circuit\s+breaker|trading\s+halt|flash\s+crash|black\s+(?:monday|swan)|'
    r'record\s+(?:high|low|drop|surge)|'
    # Natural/health
    r'earthquake|tsunami|hurricane|pandemic|outbreak|'
    # Corporate crisis
    r'fraud|arrest(?:ed)?|resign(?:ed|ation)?|fired|scandal|'
    r'data\s+breach|hack(?:ed)?'
    r')\b', re.IGNORECASE
)

# Medium-impact patterns — worth accelerating but not emergency
_MEDIUM_IMPACT_PATTERNS = re.compile(
    r'\b(?:'
    r'downgrade[ds]?|upgrade[ds]?|warning|alert|'
    r'surge[ds]?|plunge[ds]?|spike[ds]?|tank(?:ed|ing)?|'
    r'acquisition|merger|IPO|buyback|dividend\s+cut|'
    r'layoff[s]?|restructur|shutdown|'
    r'election|vote|referendum|protest'
    r')\b', re.IGNORECASE
)


@dataclass
class WatchdogAlert:
    """A detected breaking event."""
    headline: str
    source_name: str
    source_url: str
    impact_level: str           # critical | high | medium
    matched_keywords: List[str]
    detected_at: str = ""

    def __post_init__(self):
        if not self.detected_at:
            self.detected_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "headline": self.headline,
            "source_name": self.source_name,
            "source_url": self.source_url,
            "impact_level": self.impact_level,
            "matched_keywords": self.matched_keywords,
            "detected_at": self.detected_at,
        }


@dataclass
class WatchdogSource:
    """A source to monitor for breaking events."""
    url: str
    name: str = ""
    source_type: str = "rss"    # rss | api | webhook


@dataclass
class WatchdogResult:
    """Result of a single watchdog check cycle."""
    sources_checked: int = 0
    alerts: List[WatchdogAlert] = field(default_factory=list)
    contexts_triggered: int = 0
    errors: List[str] = field(default_factory=list)
    checked_at: str = ""

    def __post_init__(self):
        if not self.checked_at:
            self.checked_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sources_checked": self.sources_checked,
            "alerts": [a.to_dict() for a in self.alerts],
            "contexts_triggered": self.contexts_triggered,
            "errors": self.errors,
            "checked_at": self.checked_at,
        }


class BreakingNewsWatchdog:
    """Ultra-fast sentinel for breaking events.

    Polls sources every minute. Checks headlines against high-impact
    patterns. When a breaking event is detected:
    1. Creates a Signal
    2. Propagates through SignalHierarchy
    3. ReactiveController accelerates affected contexts
    4. ImpactTracker records the prediction
    """

    def __init__(
        self,
        db=None,
        watchdog_context: str = "Global Macro",
        hierarchy=None,
        collector=None,
        reactive=None,
        impact_tracker=None,
    ):
        self._db = db
        self._watchdog_context = watchdog_context  # context to ingest breaking news into
        self._sources: List[WatchdogSource] = []
        self._hierarchy = hierarchy
        self._collector = collector
        self._reactive = reactive
        self._impact_tracker = impact_tracker
        self._seen_headlines: Set[str] = set()  # dedup within session
        self._alert_log: List[WatchdogAlert] = []
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def add_source(self, url: str, name: str = "", source_type: str = "rss"):
        """Add a source to monitor."""
        self._sources.append(WatchdogSource(url=url, name=name or url, source_type=source_type))

    def check(self) -> WatchdogResult:
        """Run one check cycle across all sources.

        Call this every minute from the scheduler, or use start() for
        automatic background polling.
        """
        result = WatchdogResult()

        for source in self._sources:
            try:
                headlines = self._fetch_headlines(source)
                result.sources_checked += 1

                for headline, link in headlines:
                    # Dedup
                    h_key = hashlib.md5(headline.lower().encode()).hexdigest()
                    if h_key in self._seen_headlines:
                        continue
                    self._seen_headlines.add(h_key)

                    # Check impact
                    alert = self._classify_headline(headline, link, source)
                    if alert:
                        result.alerts.append(alert)
                        self._alert_log.append(alert)

                        # Step 1: INGEST the article into watchdog context
                        self._ingest_alert(alert, link)

                        # Step 2: Fire signals through hierarchy + trigger reactions
                        triggered = self._trigger_reactions(alert)
                        result.contexts_triggered += triggered

            except Exception as exc:
                result.errors.append(f"{source.name}: {exc}")

        if result.alerts:
            logger.warning("[WATCHDOG] %d breaking alerts detected, %d contexts triggered",
                            len(result.alerts), result.contexts_triggered)

        return result

    def _ingest_alert(self, alert: WatchdogAlert, article_url: str):
        """Ingest the breaking news article into the watchdog context.

        The article becomes a Document node with entities, facts, sentiment,
        geography — all the standard enrichment. It lives in the watchdog
        context graph (e.g., "Global Macro") and is queryable/time-travelable.
        """
        if not self._db or not article_url:
            return

        try:
            from ..ingestion.smart_ingest import ingest_url
            result = ingest_url(
                article_url,
                self._db,
                context_purpose=f"Breaking news: {self._watchdog_context}",
                strategy="news_article",
            )
            if result.errors:
                if "Duplicate" not in str(result.errors):
                    logger.warning("[WATCHDOG] Ingest failed for '%s': %s",
                                    alert.headline[:50], result.errors)
            else:
                logger.info("[WATCHDOG] Ingested breaking article: %s (%d entities, %d facts)",
                             alert.headline[:50], len(result.entity_ids), len(result.fact_ids))
        except Exception as exc:
            logger.warning("[WATCHDOG] Ingest error for '%s': %s", alert.headline[:50], exc)

    def _fetch_headlines(self, source: WatchdogSource) -> List[tuple]:
        """Fetch headlines from a source. Returns [(headline, link), ...]."""
        if source.source_type == "rss":
            return self._fetch_rss_headlines(source.url)
        return []

    def _fetch_rss_headlines(self, url: str) -> List[tuple]:
        """Parse RSS feed and extract (title, link) tuples."""
        import xml.etree.ElementTree as ET
        import urllib.request

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AIContextDB-Watchdog/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                xml_data = resp.read().decode("utf-8", errors="replace")
        except Exception:
            return []

        try:
            root = ET.fromstring(xml_data)
        except ET.ParseError:
            return []

        results = []

        # RSS 2.0
        for item in root.iter("item"):
            title = item.findtext("title", "").strip()
            link = item.findtext("link", "").strip()
            if title:
                results.append((title, link))

        # Atom
        if not results:
            ns = "{http://www.w3.org/2005/Atom}"
            for entry in root.iter(f"{ns}entry"):
                title = entry.findtext(f"{ns}title", "").strip()
                link_el = entry.find(f"{ns}link")
                link = link_el.get("href", "") if link_el is not None else ""
                if title:
                    results.append((title, link))

        return results[:20]  # cap at 20 headlines per source

    def _classify_headline(
        self, headline: str, link: str, source: WatchdogSource,
    ) -> Optional[WatchdogAlert]:
        """Check if a headline indicates a breaking event."""
        high_matches = _HIGH_IMPACT_PATTERNS.findall(headline)
        medium_matches = _MEDIUM_IMPACT_PATTERNS.findall(headline)

        if high_matches:
            return WatchdogAlert(
                headline=headline,
                source_name=source.name,
                source_url=link or source.url,
                impact_level="critical" if len(high_matches) >= 2 else "high",
                matched_keywords=high_matches[:5],
            )

        if medium_matches and len(medium_matches) >= 2:
            return WatchdogAlert(
                headline=headline,
                source_name=source.name,
                source_url=link or source.url,
                impact_level="medium",
                matched_keywords=medium_matches[:5],
            )

        return None

    def _trigger_reactions(self, alert: WatchdogAlert) -> int:
        """Fire signals and trigger reactions for a breaking alert."""
        triggered = 0

        if not self._hierarchy:
            return triggered

        # Create a signal from the alert
        from .signals import Signal
        severity = "alert" if alert.impact_level in ("critical", "high") else "warning"
        signal = Signal(
            type="threshold_breach",
            entity_name=alert.headline[:60],
            severity=severity,
            details={
                "source": alert.source_name,
                "impact_level": alert.impact_level,
                "keywords": alert.matched_keywords,
                "url": alert.source_url,
            },
        )

        # Propagate through hierarchy — find the right source context
        # The watchdog sits at "world" level, so propagate from there
        source_ctx = "Global Macro"  # default — watchdog is global

        propagated = self._hierarchy.propagate_signal(signal, source_ctx)

        # Trigger reactive controller for each affected context
        if self._reactive:
            for prop in propagated:
                reactions = self._reactive.react(signal, prop.target_context, self._collector)
                if reactions:
                    triggered += 1

        # Record prediction for impact tracking
        if self._impact_tracker:
            for prop in propagated:
                self._impact_tracker.record_prediction(
                    signal_id=f"watchdog_{hashlib.md5(alert.headline.encode()).hexdigest()[:8]}_{prop.target_context}",
                    source_context=source_ctx,
                    target_context=prop.target_context,
                    predicted_weight=prop.weight,
                    signal_type="threshold_breach",
                    relationship=prop.relationship,
                )

        return triggered

    def start(self, interval_seconds: int = 60):
        """Start background watchdog polling."""
        if self._running:
            return

        self._running = True

        def _loop():
            while self._running:
                try:
                    self.check()
                except Exception as exc:
                    logger.error("[WATCHDOG] Check failed: %s", exc)
                time.sleep(interval_seconds)

        self._thread = threading.Thread(target=_loop, daemon=True, name="watchdog")
        self._thread.start()
        logger.info("[WATCHDOG] Started (interval=%ds, sources=%d)",
                     interval_seconds, len(self._sources))

    def stop(self):
        """Stop background polling."""
        self._running = False
        logger.info("[WATCHDOG] Stopped")

    def get_alert_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent alerts."""
        return [a.to_dict() for a in self._alert_log[-limit:]]
