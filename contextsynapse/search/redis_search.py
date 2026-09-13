"""
Redis Search integration — shared full-text index across all workers.

Replaces the per-worker in-process keyword cache and single-process Whoosh
index with RediSearch (FT module). Falls back to the in-process inverted
index if RediSearch is not available.

Each graph gets its own search index: idx:{graph_name}
Documents are indexed with: name, content, statement, label fields.
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class RedisSearchIndex:
    """Full-text search index backed by RediSearch (FT module)."""

    def __init__(self, redis_url: str = None):
        import redis
        self._url = redis_url or os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL", "redis://localhost:6379/0")
        self._r = redis.from_url(self._url, decode_responses=True)
        self._available = self._check_ft_module()

    def _check_ft_module(self) -> bool:
        """Check if RediSearch module is loaded."""
        try:
            modules = self._r.module_list()
            return any(m.get("name", b"").lower() in (b"search", "search", b"ft", "ft") for m in modules)
        except Exception:
            return False

    @property
    def available(self) -> bool:
        return self._available

    def _index_name(self, graph: str) -> str:
        safe = hashlib.sha256(graph.encode()).hexdigest()[:12]
        return f"idx:{safe}"

    def ensure_index(self, graph_name: str):
        """Create the search index for a graph if it doesn't exist."""
        if not self._available:
            return
        idx = self._index_name(graph_name)
        try:
            self._r.execute_command("FT.INFO", idx)
            return  # already exists
        except Exception:
            pass
        try:
            self._r.execute_command(
                "FT.CREATE", idx,
                "ON", "HASH",
                "PREFIX", "1", f"doc:{graph_name}:",
                "SCHEMA",
                "name", "TEXT", "WEIGHT", "2.0",
                "content", "TEXT", "WEIGHT", "1.0",
                "statement", "TEXT", "WEIGHT", "1.5",
                "label", "TAG",
                "node_id", "TAG",
            )
            logger.info("[REDIS-FT] Created search index for '%s'", graph_name)
        except Exception as e:
            logger.debug("[REDIS-FT] Index creation failed: %s", e)

    def index_node(self, graph_name: str, node_id: str, label: str, props: Dict[str, Any]):
        """Index a single node's searchable fields."""
        if not self._available:
            return
        key = f"doc:{graph_name}:{node_id}"
        # Resolve content (handles vector-ref pointer-only nodes)
        content = props.get("content") or props.get("description") or ""
        if not content and props.get("vector_ref"):
            try:
                from .text_resolver import resolve_text
                content = resolve_text(node_id, props, graph_name)
            except Exception:
                pass
        doc = {
            "node_id": node_id,
            "label": label,
            "name": str(props.get("name", "") or ""),
            "content": str(content or "")[:2000],
            "statement": str(props.get("statement", "") or "")[:1000],
        }
        try:
            self._r.hset(key, mapping=doc)
        except Exception:
            pass

    def index_graph(self, graph_name: str, db):
        """Bulk-index all nodes from a graph."""
        if not self._available:
            return 0
        self.ensure_index(graph_name)
        count = 0
        try:
            pipe = self._r.pipeline()
            for node in db.get_all_nodes():
                if isinstance(node, dict):
                    props = node.get("properties", {})
                    nid = str(node.get("id", ""))
                    nlabel = node.get("label", "")
                else:
                    props = getattr(node, "properties", {}) or {}
                    nid = str(getattr(node, "id", ""))
                    nlabel = getattr(node, "label", "")

                # Resolve content for vector-ref nodes
                content = props.get("content") or props.get("description") or ""
                if not content and props.get("vector_ref"):
                    try:
                        from .text_resolver import resolve_text
                        content = resolve_text(nid, props, graph_name)
                    except Exception:
                        pass

                key = f"doc:{graph_name}:{nid}"
                pipe.hset(key, mapping={
                    "node_id": nid,
                    "label": nlabel,
                    "name": str(props.get("name", "") or "")[:500],
                    "content": str(content or "")[:2000],
                    "statement": str(props.get("statement", "") or "")[:1000],
                })
                count += 1
                if count % 200 == 0:
                    pipe.execute()
                    pipe = self._r.pipeline()
            pipe.execute()
            logger.info("[REDIS-FT] Indexed %d nodes for '%s'", count, graph_name)
        except Exception as e:
            logger.error("[REDIS-FT] Bulk index failed: %s", e)
        return count

    def search(self, graph_name: str, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Full-text search across a graph's indexed nodes."""
        if not self._available:
            return []
        idx = self._index_name(graph_name)
        try:
            # Escape special RediSearch characters
            safe_q = query.replace("@", " ").replace("{", " ").replace("}", " ")
            result = self._r.execute_command(
                "FT.SEARCH", idx, safe_q,
                "LIMIT", "0", str(limit),
                "RETURN", "4", "node_id", "label", "name", "statement",
            )
            # Parse FT.SEARCH result: [total, key1, [field, val, ...], key2, ...]
            total = result[0]
            hits = []
            i = 1
            while i < len(result):
                key = result[i]
                fields = result[i + 1] if i + 1 < len(result) else []
                doc = {}
                for j in range(0, len(fields), 2):
                    doc[fields[j]] = fields[j + 1]
                hits.append(doc)
                i += 2
            return hits
        except Exception as e:
            logger.debug("[REDIS-FT] Search failed: %s", e)
            return []

    def drop_index(self, graph_name: str):
        """Drop a graph's search index."""
        if not self._available:
            return
        idx = self._index_name(graph_name)
        try:
            self._r.execute_command("FT.DROPINDEX", idx, "DD")
        except Exception:
            pass


# Singleton
_index = None


def get_search_index() -> Optional[RedisSearchIndex]:
    """Get or create the Redis Search index (None if unavailable)."""
    global _index
    if _index is not None:
        return _index if _index.available else None

    url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
    if url:
        try:
            idx = RedisSearchIndex(url)
            if idx.available:
                logger.info("Redis Search (FT): available")
                _index = idx
                return _index
            else:
                logger.info("Redis Search (FT): not available (module not loaded)")
                _index = idx  # cache the negative result
        except Exception:
            pass
    return None
