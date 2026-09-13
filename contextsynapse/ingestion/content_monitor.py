"""Content Monitor — auto-discovers and ingests relevant content.

Instead of FM manually pasting YouTube links, the system:
  1. Monitors YouTube channels per stock (company IR, analyst channels)
  2. Monitors RSS feeds for new analyst reports, earnings transcripts
  3. Auto-classifies content type (earnings_call, analyst_opinion, conference)
  4. Auto-queues for ingestion via AsyncIngestManager
  5. Runs on a schedule (every 6 hours by default)

Sources monitored per stock:
  - Company's official YouTube channel (IR presentations, AGMs)
  - Financial YouTube channels (ET Now, CNBC-TV18, Moneycontrol)
  - Earnings transcript sites (seekingalpha, tickertape)
  - Broker research portals

Usage:
    monitor = ContentMonitor(registry)
    monitor.run_scan()  # scans all stocks for new content
    # Or schedule:
    monitor.start_scheduler(interval_hours=6)
"""
import logging
import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

MONITOR_STATE_DIR = Path(__file__).parent.parent.parent / ".content_monitor"


# ── YouTube channel registry per stock ────────────────────────────────
# Company IR channels + major financial channels
STOCK_YOUTUBE_CHANNELS = {
    "tcs": {
        "company": ["TCS"],  # YouTube channel name search
        "keywords": ["TCS earnings", "TCS quarterly results", "TCS Q1", "TCS Q2", "TCS Q3", "TCS Q4",
                     "TCS management commentary", "TCS investor call"],
    },
    "reliance": {
        "company": ["Reliance Industries"],
        "keywords": ["Reliance AGM", "Reliance earnings", "Reliance quarterly",
                     "Mukesh Ambani speech", "Jio earnings"],
    },
    "hdfcbank": {
        "company": ["HDFC Bank"],
        "keywords": ["HDFC Bank earnings", "HDFC Bank results", "HDFC Bank quarterly"],
    },
    "icicibank": {
        "company": ["ICICI Bank"],
        "keywords": ["ICICI Bank earnings", "ICICI Bank results", "ICICI quarterly"],
    },
    "infosys": {
        "company": ["Infosys"],
        "keywords": ["Infosys earnings", "Infosys results", "Infosys guidance",
                     "Salil Parekh", "Infosys quarterly"],
    },
    "sunpharma": {
        "company": ["Sun Pharma"],
        "keywords": ["Sun Pharma earnings", "Sun Pharma results", "Dilip Shanghvi"],
    },
    "titan": {
        "company": ["Titan Company"],
        "keywords": ["Titan earnings", "Titan results", "Tanishq", "Titan quarterly"],
    },
    "bajfinance": {
        "company": ["Bajaj Finance"],
        "keywords": ["Bajaj Finance earnings", "Bajaj Finance results"],
    },
    "kotakbank": {
        "company": ["Kotak Bank", "Kotak Mahindra"],
        "keywords": ["Kotak Bank earnings", "Kotak results"],
    },
    "hindunilvr": {
        "company": ["Hindustan Unilever", "HUL"],
        "keywords": ["HUL earnings", "Hindustan Unilever results"],
    },
    "bhartiartl": {
        "company": ["Bharti Airtel", "Airtel"],
        "keywords": ["Airtel earnings", "Airtel results", "Airtel quarterly", "Sunil Mittal"],
    },
    "adanient": {
        "company": ["Adani"],
        "keywords": ["Adani earnings", "Adani results", "Gautam Adani"],
    },
    "asianpaint": {
        "company": ["Asian Paints"],
        "keywords": ["Asian Paints earnings", "Asian Paints results"],
    },
}

# Financial YouTube channels to monitor for ALL stocks
FINANCIAL_CHANNELS = [
    {"name": "ET Now", "keywords": ["earnings", "quarterly results", "management commentary"]},
    {"name": "CNBC-TV18", "keywords": ["earnings call", "results review", "stock analysis"]},
    {"name": "Moneycontrol", "keywords": ["quarterly results", "earnings", "management speak"]},
    {"name": "Zerodha Varsity", "keywords": ["stock analysis", "fundamental analysis"]},
    {"name": "SOIC", "keywords": ["stock analysis", "company analysis"]},
]

# Transcript/research RSS sources
TRANSCRIPT_FEEDS = {
    "seekingalpha": "https://seekingalpha.com/feed",
    "tickertape": "https://www.tickertape.in/blog/feed",
    "moneycontrol_research": "https://www.moneycontrol.com/rss/results.xml",
}


@dataclass
class DiscoveredContent:
    """A piece of content discovered by the monitor."""
    content_id: str = ""
    entity: str = ""             # which stock this relates to
    source: str = ""             # youtube, rss, web
    url: str = ""
    title: str = ""
    published_at: str = ""
    channel: str = ""
    job_type: str = "general"    # auto-classified: earnings_call, analyst_opinion, etc.
    confidence: float = 0.0      # how confident we are this is relevant
    ingested: bool = False
    job_id: str = ""             # if ingested, the async job ID
    discovered_at: str = ""

    def __post_init__(self):
        if not self.content_id:
            self.content_id = f"disc_{uuid.uuid4().hex[:8]}"
        if not self.discovered_at:
            self.discovered_at = datetime.now(timezone.utc).isoformat()


def _classify_content(title: str, description: str = "") -> dict:
    """Auto-classify content type from title/description.

    Returns: {job_type, confidence}
    """
    text = f"{title} {description}".lower()

    patterns = [
        (r"q[1-4]\s*(fy)?\d{2}.*?(result|earn|review)", "earnings_call", 0.9),
        (r"quarterly\s*(result|earn|review|update)", "earnings_call", 0.85),
        (r"(management|con\s*call|investor)\s*(call|commentary|meet)", "earnings_call", 0.85),
        (r"(agm|annual\s*general\s*meet)", "conference", 0.9),
        (r"(analyst|broker)\s*(meet|day|present)", "analyst_opinion", 0.8),
        (r"(upgrade|downgrade|target\s*price|initiating)", "analyst_opinion", 0.85),
        (r"(conference|summit|investor\s*day)", "conference", 0.8),
        (r"(interview|speaks|exclusive|in\s*conversation)", "management_interview", 0.75),
        (r"(ceo|cfo|md|chairman)\s*(speak|interview|comment)", "management_interview", 0.8),
        (r"(research|deep\s*dive|analysis|fundamental)", "research", 0.7),
        (r"(stock\s*pick|buy|sell|hold|outperform)", "analyst_opinion", 0.7),
    ]

    best_type = "general"
    best_conf = 0.0

    for pattern, job_type, confidence in patterns:
        if re.search(pattern, text):
            if confidence > best_conf:
                best_type = job_type
                best_conf = confidence

    return {"job_type": best_type, "confidence": best_conf}


class ContentMonitor:
    """Monitors YouTube channels and RSS feeds for new content to auto-ingest."""

    def __init__(self, registry):
        self._registry = registry
        self._scheduler_thread = None
        self._running = False

    def _get_seen_ids(self) -> set:
        """Load set of already-seen content IDs (to avoid re-ingesting)."""
        MONITOR_STATE_DIR.mkdir(parents=True, exist_ok=True)
        seen_path = MONITOR_STATE_DIR / "seen_ids.json"
        if seen_path.exists():
            try:
                return set(json.loads(seen_path.read_text()))
            except Exception:
                pass
        return set()

    def _save_seen_ids(self, seen: set):
        MONITOR_STATE_DIR.mkdir(parents=True, exist_ok=True)
        seen_path = MONITOR_STATE_DIR / "seen_ids.json"
        # Keep only last 5000 IDs
        ids = sorted(seen)[-5000:]
        seen_path.write_text(json.dumps(ids))

    def _save_discovery(self, item: DiscoveredContent):
        MONITOR_STATE_DIR.mkdir(parents=True, exist_ok=True)
        log_path = MONITOR_STATE_DIR / "discoveries.jsonl"
        with open(log_path, "a") as f:
            f.write(json.dumps(item.__dict__) + "\n")

    # ── YouTube Discovery ────────────────────────────────────────

    def scan_youtube(self, entity: str = None, max_results: int = 5,
                     days_back: int = 7) -> List[DiscoveredContent]:
        """Scan YouTube for new videos related to portfolio stocks.

        Uses yt-dlp to search YouTube (no API key needed).
        """
        try:
            import yt_dlp
        except ImportError:
            logger.warning("yt-dlp not installed — YouTube monitoring unavailable")
            return []

        seen = self._get_seen_ids()
        discoveries = []
        stocks_to_scan = {entity: STOCK_YOUTUBE_CHANNELS.get(entity, {})} if entity else STOCK_YOUTUBE_CHANNELS

        for stock, config in stocks_to_scan.items():
            keywords = config.get("keywords", [])
            if not keywords:
                continue

            for kw in keywords[:3]:  # limit searches per stock
                try:
                    ydl_opts = {
                        "quiet": True,
                        "no_warnings": True,
                        "extract_flat": True,
                        "playlistend": max_results,
                        "socket_timeout": 15,
                    }

                    search_url = f"ytsearch{max_results}:{kw}"

                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        result = ydl.extract_info(search_url, download=False)

                    entries = result.get("entries", []) if result else []

                    for entry in entries:
                        video_id = entry.get("id", "")
                        if not video_id or video_id in seen:
                            continue

                        title = entry.get("title", "")
                        upload_date = entry.get("upload_date", "")
                        channel = entry.get("channel", entry.get("uploader", ""))

                        # Check recency
                        if upload_date:
                            try:
                                vdate = datetime.strptime(upload_date, "%Y%m%d")
                                if (datetime.now() - vdate).days > days_back:
                                    continue
                            except Exception:
                                pass

                        # Check relevance — title must mention the stock or related terms
                        stock_name = stock.replace("_", " ")
                        title_lower = title.lower()
                        company_names = config.get("company", [stock_name])
                        relevant = any(cn.lower() in title_lower for cn in company_names)
                        if not relevant:
                            # Check if any keyword matches in title
                            relevant = any(k.lower() in title_lower for k in keywords)

                        if not relevant:
                            continue

                        # Classify
                        classification = _classify_content(title)

                        url = f"https://www.youtube.com/watch?v={video_id}"
                        disc = DiscoveredContent(
                            content_id=video_id,
                            entity=stock,
                            source="youtube",
                            url=url,
                            title=title,
                            published_at=upload_date,
                            channel=channel,
                            job_type=classification["job_type"],
                            confidence=classification["confidence"],
                        )
                        discoveries.append(disc)
                        seen.add(video_id)

                except Exception as e:
                    logger.debug("[MONITOR] YouTube search failed for '%s': %s", kw, e)

        self._save_seen_ids(seen)
        return discoveries

    # ── RSS Discovery ────────────────────────────────────────────

    def scan_rss_transcripts(self, entity: str = None) -> List[DiscoveredContent]:
        """Scan RSS feeds for earnings transcripts and research articles."""
        import urllib.request

        seen = self._get_seen_ids()
        discoveries = []

        for feed_name, feed_url in TRANSCRIPT_FEEDS.items():
            try:
                req = urllib.request.Request(feed_url, headers={"User-Agent": "ContextSynapse/1.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    content = resp.read().decode("utf-8", errors="ignore")

                # Simple RSS parsing (title + link extraction)
                titles = re.findall(r"<title[^>]*>(.*?)</title>", content, re.DOTALL)
                links = re.findall(r"<link[^>]*>(.*?)</link>", content, re.DOTALL)

                for title, link in zip(titles, links):
                    title = title.strip().replace("<![CDATA[", "").replace("]]>", "")
                    link = link.strip()
                    content_id = f"{feed_name}_{hash(link) % 100000}"

                    if content_id in seen:
                        continue

                    # Check if this article relates to any portfolio stock
                    matched_entity = self._match_article_to_entity(title, entity)
                    if not matched_entity:
                        continue

                    classification = _classify_content(title)

                    disc = DiscoveredContent(
                        content_id=content_id,
                        entity=matched_entity,
                        source="rss",
                        url=link,
                        title=title,
                        channel=feed_name,
                        job_type=classification["job_type"],
                        confidence=classification["confidence"],
                    )
                    discoveries.append(disc)
                    seen.add(content_id)

            except Exception as e:
                logger.debug("[MONITOR] RSS scan failed for %s: %s", feed_name, e)

        self._save_seen_ids(seen)
        return discoveries

    def _match_article_to_entity(self, title: str, filter_entity: str = None) -> Optional[str]:
        """Check if an article title matches any portfolio stock."""
        title_lower = title.lower()

        stocks_to_check = {filter_entity: STOCK_YOUTUBE_CHANNELS.get(filter_entity, {})} if filter_entity else STOCK_YOUTUBE_CHANNELS

        for stock, config in stocks_to_check.items():
            company_names = config.get("company", [stock.replace("_", " ")])
            if any(cn.lower() in title_lower for cn in company_names):
                return stock
            if stock.lower() in title_lower:
                return stock

        return None

    # ── Full Scan + Auto-Ingest ──────────────────────────────────

    def run_scan(self, auto_ingest: bool = True, min_confidence: float = 0.6) -> Dict[str, Any]:
        """Run a full content scan across all sources.

        If auto_ingest=True, automatically queue high-confidence discoveries for ingestion.
        """
        logger.info("[MONITOR] Starting content scan...")
        start = datetime.now(timezone.utc)

        # Scan all sources
        yt_discoveries = self.scan_youtube(days_back=3)
        rss_discoveries = self.scan_rss_transcripts()

        all_discoveries = yt_discoveries + rss_discoveries
        logger.info("[MONITOR] Found %d new items (%d YouTube, %d RSS)",
                    len(all_discoveries), len(yt_discoveries), len(rss_discoveries))

        # Auto-ingest high-confidence items
        ingested = []
        skipped = []

        if auto_ingest and all_discoveries:
            try:
                from contextsynapse.ingestion.async_ingest import AsyncIngestManager
                mgr = AsyncIngestManager(self._registry)

                for disc in all_discoveries:
                    if disc.confidence < min_confidence:
                        skipped.append(disc)
                        continue

                    try:
                        if disc.source == "youtube":
                            job = mgr.submit_youtube(
                                disc.url, entity=disc.entity, job_type=disc.job_type,
                            )
                        else:
                            # For RSS, we'd need to crawl the article first
                            # For now, skip RSS auto-ingest (needs web crawler)
                            skipped.append(disc)
                            continue

                        disc.ingested = True
                        disc.job_id = job.job_id
                        ingested.append(disc)
                        logger.info("[MONITOR] Auto-ingested: %s → %s (%s, conf=%.2f)",
                                    disc.title[:50], disc.entity, disc.job_type, disc.confidence)

                    except Exception as e:
                        logger.warning("[MONITOR] Auto-ingest failed for %s: %s", disc.title[:50], e)
                        skipped.append(disc)

            except Exception as e:
                logger.warning("[MONITOR] AsyncIngestManager unavailable: %s", e)

        # Save discoveries log
        for disc in all_discoveries:
            self._save_discovery(disc)

        elapsed = (datetime.now(timezone.utc) - start).total_seconds()

        result = {
            "scan_time_seconds": round(elapsed, 1),
            "total_discovered": len(all_discoveries),
            "youtube_discovered": len(yt_discoveries),
            "rss_discovered": len(rss_discoveries),
            "auto_ingested": len(ingested),
            "skipped_low_confidence": len(skipped),
            "ingested_items": [
                {"entity": d.entity, "title": d.title[:60], "type": d.job_type,
                 "confidence": d.confidence, "job_id": d.job_id}
                for d in ingested
            ],
            "discovered_items": [
                {"entity": d.entity, "title": d.title[:60], "type": d.job_type,
                 "source": d.source, "confidence": d.confidence, "url": d.url}
                for d in all_discoveries[:20]
            ],
        }

        logger.info("[MONITOR] Scan complete: %d found, %d ingested in %.1fs",
                    len(all_discoveries), len(ingested), elapsed)
        return result

    # ── Scheduler ────────────────────────────────────────────────

    def start_scheduler(self, interval_hours: int = 6):
        """Start background scheduler that scans for new content periodically."""
        if self._running:
            return

        self._running = True

        def _loop():
            while self._running:
                try:
                    self.run_scan(auto_ingest=True, min_confidence=0.7)
                except Exception as e:
                    logger.exception("[MONITOR] Scheduled scan failed: %s", e)
                # Sleep for interval
                for _ in range(interval_hours * 3600):
                    if not self._running:
                        break
                    time.sleep(1)

        self._scheduler_thread = threading.Thread(target=_loop, daemon=True)
        self._scheduler_thread.start()
        logger.info("[MONITOR] Content monitor scheduler started (every %dh)", interval_hours)

    def stop_scheduler(self):
        """Stop the background scheduler."""
        self._running = False
        logger.info("[MONITOR] Content monitor scheduler stopped")

    def get_discoveries(self, entity: str = None, limit: int = 50) -> List[Dict]:
        """Get recent discoveries from the log."""
        log_path = MONITOR_STATE_DIR / "discoveries.jsonl"
        if not log_path.exists():
            return []

        items = []
        try:
            with open(log_path) as f:
                for line in f:
                    try:
                        item = json.loads(line.strip())
                        if entity and item.get("entity") != entity:
                            continue
                        items.append(item)
                    except Exception:
                        continue
        except Exception:
            pass

        # Return most recent first
        items.reverse()
        return items[:limit]
