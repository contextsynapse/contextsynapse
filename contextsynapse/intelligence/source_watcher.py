"""Source Watcher — track external sources and detect changes."""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default refresh intervals by source type (seconds)
DEFAULT_REFRESH_INTERVALS = {
    "url": 3600,
    "file": 3600,
    "git": 1800,
    "api": 3600,
    "manual": 0,      # never auto-refresh
    "unknown": 0,
}


def compute_content_hash(content: str) -> str:
    """SHA-256 hash of content for change detection."""
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def build_source_metadata(
    source_type: str,
    uri: Optional[str],
    content_hash: Optional[str],
    refresh_interval: Optional[int] = None,
) -> Dict[str, Any]:
    """Build _source metadata dict for a node."""
    return {
        "type": source_type,
        "uri": uri or "",
        "last_fetched": time.time(),
        "content_hash": content_hash or "",
        "refresh_interval": refresh_interval if refresh_interval is not None
                            else DEFAULT_REFRESH_INTERVALS.get(source_type, 0),
        "human_override": False,
    }


def should_refresh(source: Dict[str, Any]) -> bool:
    """Check if a source needs refreshing based on interval and override status."""
    if source.get("human_override", False):
        return False
    interval = source.get("refresh_interval", 0)
    if interval <= 0:
        return False
    last = source.get("last_fetched", 0)
    return (time.time() - last) > interval


def prune_snapshot_chain(snapshot_ids: List[str], max_versions: int = 10) -> List[str]:
    """Given oldest-first snapshot IDs, return IDs to archive (excess beyond max_versions)."""
    if len(snapshot_ids) <= max_versions:
        return []
    return snapshot_ids[:len(snapshot_ids) - max_versions]


async def check_url_freshness(url: str, stored_hash: str) -> Dict[str, Any]:
    """Fetch a URL and compare content hash. Returns status dict."""
    try:
        from ..ingestion.web_crawler import fetch_single_page
        page = fetch_single_page(url)
        if page is None:
            return {"status": "unreachable", "url": url}
        new_hash = compute_content_hash(page["content"])
        if new_hash == stored_hash:
            return {"status": "unchanged", "url": url, "hash": new_hash}
        return {
            "status": "modified", "url": url,
            "old_hash": stored_hash, "new_hash": new_hash,
            "new_content": page["content"], "new_title": page.get("title", ""),
        }
    except Exception as e:
        logger.debug("[SOURCE_WATCHER] URL check failed for %s: %s", url[:60], e)
        return {"status": "unreachable", "url": url, "error": str(e)}


class SourceWatcher:
    """Background service that monitors sources for changes."""

    def __init__(self, config, event_bus, graph_registry=None):
        self._config = config
        self._bus = event_bus
        self._registry = graph_registry
        self._running = False

    async def check_node(self, node_id: str, source_meta: Dict[str, Any]) -> Optional[Dict]:
        """Check a single node's source for changes. Returns change dict or None."""
        if not should_refresh(source_meta):
            return None

        source_type = source_meta.get("type", "unknown")
        if source_type == "url":
            result = await check_url_freshness(
                source_meta["uri"], source_meta.get("content_hash", "")
            )
            if result["status"] == "modified":
                await self._bus.publish("source_changed", {
                    "node_id": node_id,
                    "source_uri": source_meta["uri"],
                    "change_type": "modified",
                    "old_hash": result.get("old_hash", ""),
                    "new_hash": result.get("new_hash", ""),
                    "timestamp": time.time(),
                })
                return result
        return None

    def get_stats(self) -> Dict[str, Any]:
        """Return watcher stats for dashboard."""
        sources_watched = 0
        try:
            if self._registry:
                # Count contexts that have URL sources
                from ..context.context_manager import ContextManager
                import sqlite3
                from pathlib import Path
                db_path = Path(__file__).resolve().parents[2] / "contextcore_data" / "contexts.db"
                if db_path.exists():
                    conn = sqlite3.connect(str(db_path))
                    conn.row_factory = sqlite3.Row
                    rows = conn.execute("SELECT source FROM contexts WHERE source LIKE 'text:http%' OR source LIKE 'http%'").fetchall()
                    sources_watched = len(rows)
                    conn.close()
        except Exception:
            pass
        return {"running": sources_watched > 0, "sources_watched": sources_watched}
