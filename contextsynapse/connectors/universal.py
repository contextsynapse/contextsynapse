"""Universal Connectors -- plug-and-play data sources for ContextCore.

5 built-in connectors that any vertical can use:
  1. RSS/Atom feed connector
  2. REST API connector (any JSON API)
  3. Web scraper connector (URL -> passages)
  4. File watcher connector (local directory)
  5. Webhook receiver connector (push-based)

Each connector implements collect() -> List[dict] that returns
items ready for the StorageRouter.

Usage:
    from contextsynapse.connectors.universal import RSSConnector, APIConnector

    rss = RSSConnector(feeds=["https://news.ycombinator.com/rss"])
    items = await rss.collect()
    engine.ingest(items)
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ConnectorConfig:
    """Configuration for a connector."""
    name: str = ""
    interval_seconds: int = 300
    max_items: int = 100
    namespace: str = "default"
    metadata: Dict[str, Any] = field(default_factory=dict)


class UniversalConnector(ABC):
    """Base class for universal connectors.

    Subclasses implement collect() which returns items
    ready for engine.ingest().
    """

    def __init__(self, config: ConnectorConfig = None):
        self.config = config or ConnectorConfig()
        self._last_poll = 0
        self._seen_ids: set = set()

    @property
    @abstractmethod
    def connector_type(self) -> str:
        """e.g. 'rss', 'api', 'scraper', 'file', 'webhook'"""

    @abstractmethod
    async def collect(self) -> List[Dict[str, Any]]:
        """Collect data from the source. Returns items for engine.ingest()."""

    async def health_check(self) -> Dict[str, Any]:
        return {"healthy": True, "type": self.connector_type, "name": self.config.name}

    def _dedup_id(self, content: str) -> str:
        return hashlib.md5(content.encode()).hexdigest()[:12]


class RSSConnector(UniversalConnector):
    """RSS/Atom feed connector. Polls feeds and returns articles as Document + Passage items."""

    connector_type = "rss"

    def __init__(self, feeds: List[str], config: ConnectorConfig = None):
        super().__init__(config)
        self.feeds = feeds

    async def collect(self) -> List[Dict[str, Any]]:
        try:
            import feedparser
        except ImportError:
            logger.error("feedparser not installed: pip install feedparser")
            return []

        items = []
        for feed_url in self.feeds:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:self.config.max_items]:
                    entry_id = self._dedup_id(entry.get("link", entry.get("title", "")))
                    if entry_id in self._seen_ids:
                        continue
                    self._seen_ids.add(entry_id)

                    doc_id = f"rss_{entry_id}"
                    title = entry.get("title", "")
                    summary = entry.get("summary", entry.get("description", ""))
                    link = entry.get("link", "")
                    published = entry.get("published", "")

                    # Document node
                    items.append({
                        "id": doc_id, "type": "Document",
                        "properties": {
                            "title": title, "url": link,
                            "date": published, "source": "rss",
                            "feed_url": feed_url,
                        },
                    })

                    # Passage from summary
                    if summary and len(summary) > 20:
                        items.append({
                            "id": f"p_{entry_id}", "type": "Passage",
                            "properties": {
                                "text": f"{title}. {summary}",
                                "doc_id": doc_id, "position": 0,
                            },
                        })

            except Exception as e:
                logger.warning("RSS feed error %s: %s", feed_url, e)

        return items


class APIConnector(UniversalConnector):
    """REST API connector. Polls any JSON API and maps response to items."""

    connector_type = "api"

    def __init__(self, url: str, headers: Dict[str, str] = None,
                 item_path: str = "", item_type: str = "Document",
                 text_field: str = "content", id_field: str = "id",
                 config: ConnectorConfig = None):
        super().__init__(config)
        self.url = url
        self.headers = headers or {}
        self.item_path = item_path  # JSON path to items array (e.g. "data.articles")
        self.item_type = item_type
        self.text_field = text_field
        self.id_field = id_field

    async def collect(self) -> List[Dict[str, Any]]:
        try:
            import aiohttp
        except ImportError:
            # Fallback to urllib
            import urllib.request
            import json
            req = urllib.request.Request(self.url, headers=self.headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
            return self._parse_response(data)

        async with aiohttp.ClientSession() as session:
            async with session.get(self.url, headers=self.headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                data = await resp.json()
                return self._parse_response(data)

    def _parse_response(self, data: Any) -> List[Dict[str, Any]]:
        # Navigate to item_path
        items_raw = data
        if self.item_path:
            for key in self.item_path.split("."):
                if isinstance(items_raw, dict):
                    items_raw = items_raw.get(key, [])
                else:
                    break

        if not isinstance(items_raw, list):
            items_raw = [items_raw]

        items = []
        for raw in items_raw[:self.config.max_items]:
            if not isinstance(raw, dict):
                continue
            item_id = str(raw.get(self.id_field, self._dedup_id(str(raw))))
            text = str(raw.get(self.text_field, ""))

            items.append({
                "id": f"api_{item_id}", "type": self.item_type,
                "properties": {"text": text, "source": "api", "api_url": self.url, **raw},
            })
        return items


class WebScraperConnector(UniversalConnector):
    """Web scraper connector. Fetches a URL, extracts text, returns as passages."""

    connector_type = "scraper"

    def __init__(self, urls: List[str], config: ConnectorConfig = None):
        super().__init__(config)
        self.urls = urls

    async def collect(self) -> List[Dict[str, Any]]:
        items = []
        for url in self.urls:
            try:
                import urllib.request
                req = urllib.request.Request(url, headers={"User-Agent": "ContextCore/1.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")

                # Extract text (try trafilatura, fallback to regex)
                text = ""
                try:
                    import trafilatura
                    text = trafilatura.extract(html) or ""
                except ImportError:
                    import re
                    text = re.sub(r"<[^>]+>", " ", html)
                    text = re.sub(r"\s+", " ", text).strip()

                if not text or len(text) < 50:
                    continue

                url_id = self._dedup_id(url)
                doc_id = f"web_{url_id}"

                # Extract title
                title = ""
                import re
                title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
                if title_match:
                    title = title_match.group(1).strip()

                items.append({
                    "id": doc_id, "type": "Document",
                    "properties": {"title": title or url, "url": url, "source": "web"},
                })

                # Chunk into passages (~500 chars each)
                chunks = [text[i:i+500] for i in range(0, len(text), 450)]
                for idx, chunk in enumerate(chunks[:20]):  # max 20 passages per page
                    items.append({
                        "id": f"p_{url_id}_{idx}", "type": "Passage",
                        "properties": {"text": chunk.strip(), "doc_id": doc_id, "position": idx},
                    })

            except Exception as e:
                logger.warning("Scrape error %s: %s", url, e)

        return items


class FileWatcherConnector(UniversalConnector):
    """File watcher connector. Monitors a directory for new/changed files."""

    connector_type = "file"

    def __init__(self, directory: str, extensions: List[str] = None,
                 config: ConnectorConfig = None):
        super().__init__(config)
        self.directory = Path(directory)
        self.extensions = set(extensions or [".txt", ".md", ".csv", ".json"])
        self._file_mtimes: Dict[str, float] = {}

    async def collect(self) -> List[Dict[str, Any]]:
        if not self.directory.exists():
            return []

        items = []
        for fpath in self.directory.rglob("*"):
            if not fpath.is_file():
                continue
            if fpath.suffix.lower() not in self.extensions:
                continue

            mtime = fpath.stat().st_mtime
            str_path = str(fpath)
            if str_path in self._file_mtimes and self._file_mtimes[str_path] >= mtime:
                continue  # unchanged
            self._file_mtimes[str_path] = mtime

            try:
                content = fpath.read_text(encoding="utf-8", errors="ignore")
                if len(content) < 10:
                    continue

                file_id = self._dedup_id(str_path)
                doc_id = f"file_{file_id}"

                items.append({
                    "id": doc_id, "type": "Document",
                    "properties": {
                        "title": fpath.name, "url": str(fpath),
                        "source": "file", "file_path": str_path,
                        "file_size": fpath.stat().st_size,
                    },
                })

                # Chunk into passages
                chunks = [content[i:i+500] for i in range(0, len(content), 450)]
                for idx, chunk in enumerate(chunks[:50]):
                    items.append({
                        "id": f"p_{file_id}_{idx}", "type": "Passage",
                        "properties": {"text": chunk.strip(), "doc_id": doc_id, "position": idx},
                    })

            except Exception as e:
                logger.warning("File read error %s: %s", fpath, e)

        return items


class WebhookConnector(UniversalConnector):
    """Webhook receiver connector. Receives push data via HTTP POST.

    Call webhook.receive(data) when a webhook arrives.
    collect() returns buffered items.
    """

    connector_type = "webhook"

    def __init__(self, item_type: str = "Document", text_field: str = "content",
                 config: ConnectorConfig = None):
        super().__init__(config)
        self.item_type = item_type
        self.text_field = text_field
        self._buffer: List[Dict] = []

    def receive(self, data: Dict[str, Any]) -> str:
        """Buffer an incoming webhook payload. Returns item ID."""
        item_id = f"wh_{self._dedup_id(str(data))}"
        text = str(data.get(self.text_field, data.get("text", data.get("message", ""))))
        self._buffer.append({
            "id": item_id, "type": self.item_type,
            "properties": {"text": text, "source": "webhook", **data},
        })
        return item_id

    async def collect(self) -> List[Dict[str, Any]]:
        """Return and clear the buffer."""
        items = self._buffer[:self.config.max_items]
        self._buffer = self._buffer[self.config.max_items:]
        return items


# ── Connector Registry ──

BUILTIN_CONNECTORS = {
    "rss": RSSConnector,
    "api": APIConnector,
    "scraper": WebScraperConnector,
    "file": FileWatcherConnector,
    "webhook": WebhookConnector,
}

def create_connector(connector_type: str, **kwargs) -> UniversalConnector:
    """Factory to create a connector by type."""
    cls = BUILTIN_CONNECTORS.get(connector_type)
    if not cls:
        raise ValueError(f"Unknown connector type: {connector_type}. Available: {list(BUILTIN_CONNECTORS.keys())}")
    return cls(**kwargs)
