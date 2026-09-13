"""DuckDB content store -- structured storage for any content type.

Schema-flexible: stores content in a unified `content` table with
type discrimination. No hardcoded assumptions about passage/fact/price
structure -- the schema adapts to what you ingest.

Two tables:
  content    -- all content items (passages, facts, articles, tables, anything)
  timeseries -- time-indexed numeric data (prices, metrics, scores)
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from contextsynapse.storage.router.interface import ContentStoreInterface

logger = logging.getLogger(__name__)

_global_store: Optional["DuckDBStore"] = None
_store_lock = threading.Lock()


class DuckDBStore(ContentStoreInterface):
    """Flexible content store backed by DuckDB.

    Instead of separate tables per content type, uses two generic tables:

    content:
        id, type, namespace, text, metadata (JSONB)
        - type can be: passage, fact, document, article, table_row, anything
        - text holds the main content (passage text, fact statement, etc.)
        - metadata holds everything else as JSON (doc_id, position, confidence, etc.)

    timeseries:
        key, timestamp, namespace, value, metadata (JSONB)
        - key is the entity (ticker, sensor_id, metric_name)
        - value is the primary numeric value
        - metadata holds extra fields (open, high, low, volume, etc.)
    """

    def __init__(self, db_path: str = "contextcore_data/content.duckdb"):
        import duckdb

        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = duckdb.connect(str(self._path))
        self._lock = threading.Lock()
        self._init_schema()
        logger.info("DuckDB content store ready: %s", self._path)

    def _init_schema(self):
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS content (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                namespace TEXT DEFAULT 'default',
                text TEXT,
                parent_id TEXT,
                position INTEGER DEFAULT 0,
                metadata JSON,
                created_at TIMESTAMP DEFAULT current_timestamp
            );

            CREATE TABLE IF NOT EXISTS timeseries (
                key TEXT NOT NULL,
                ts TEXT NOT NULL,
                namespace TEXT DEFAULT 'default',
                value REAL,
                metadata JSON,
                PRIMARY KEY (key, ts)
            );
        """)
        for stmt in [
            "CREATE INDEX IF NOT EXISTS idx_content_type ON content(type)",
            "CREATE INDEX IF NOT EXISTS idx_content_ns ON content(namespace)",
            "CREATE INDEX IF NOT EXISTS idx_content_parent ON content(parent_id)",
            "CREATE INDEX IF NOT EXISTS idx_ts_key ON timeseries(key)",
        ]:
            try:
                self._db.execute(stmt)
            except Exception:
                pass

    # ── Generic content operations ──

    def store_items(self, items: List[Dict[str, Any]], content_type: str, namespace: str = "default") -> int:
        """Bulk store content items of any type.

        Each item should have at minimum: id, text.
        Everything else goes into metadata.
        """
        if not items:
            return 0
        import pyarrow as pa

        ids = []
        types = []
        texts = []
        parent_ids = []
        positions = []
        metadatas = []

        # Fields that go into dedicated columns (fast queries)
        _DEDICATED = {"id", "type", "text", "content", "statement",
                       "parent_id", "doc_id", "passage_id", "position", "namespace"}

        for item in items:
            ids.append(item.get("id", ""))
            types.append(content_type)
            texts.append(item.get("text", item.get("content", item.get("statement", ""))))
            parent_ids.append(item.get("parent_id", item.get("doc_id", item.get("passage_id", ""))))
            positions.append(item.get("position", 0))
            meta = {k: v for k, v in item.items() if k not in _DEDICATED}
            metadatas.append(json.dumps(meta, default=str))

        tbl = pa.table({
            "id": ids, "type": types, "namespace": [namespace] * len(items),
            "text": texts, "parent_id": parent_ids,
            "position": pa.array(positions, type=pa.int32()),
            "metadata": metadatas,
        })

        with self._lock:
            self._db.register("_bulk_content", tbl)
            self._db.execute("DELETE FROM content WHERE id IN (SELECT id FROM _bulk_content)")
            self._db.execute("INSERT INTO content (id, type, namespace, text, parent_id, position, metadata) SELECT * FROM _bulk_content")
            self._db.unregister("_bulk_content")

        return len(items)

    def store_timeseries(self, rows: List[Dict[str, Any]], namespace: str = "default") -> int:
        """Bulk store time-series data.

        Each row needs: key (ticker/sensor), ts (timestamp/date), value (primary number).
        Everything else goes into metadata.
        """
        if not rows:
            return 0
        import pyarrow as pa

        keys = []
        timestamps = []
        values = []
        metadatas = []

        _DEDICATED = {"key", "ticker", "ts", "date", "timestamp",
                       "value", "close", "price", "namespace"}

        for r in rows:
            keys.append(r.get("key", r.get("ticker", "")))
            timestamps.append(r.get("ts", r.get("date", r.get("timestamp", ""))))
            values.append(float(r.get("value", r.get("close", r.get("price", 0)))))
            meta = {k: v for k, v in r.items() if k not in _DEDICATED}
            metadatas.append(json.dumps(meta, default=str))

        tbl = pa.table({
            "key": keys, "ts": timestamps,
            "namespace": [namespace] * len(rows),
            "value": pa.array(values, type=pa.float64()),
            "metadata": metadatas,
        })

        # Deduplicate within batch before insert
        seen = set()
        deduped_keys = []
        deduped_ts = []
        deduped_ns = []
        deduped_vals = []
        deduped_meta = []
        for i in range(len(keys) - 1, -1, -1):  # reverse to keep last
            k = (keys[i], timestamps[i])
            if k not in seen:
                seen.add(k)
                deduped_keys.append(keys[i])
                deduped_ts.append(timestamps[i])
                deduped_ns.append(namespace)
                deduped_vals.append(values[i])
                deduped_meta.append(metadatas[i])

        tbl = pa.table({
            "key": deduped_keys, "ts": deduped_ts,
            "namespace": deduped_ns,
            "value": pa.array(deduped_vals, type=pa.float64()),
            "metadata": deduped_meta,
        })

        with self._lock:
            self._db.register("_bulk_ts", tbl)
            self._db.execute("DELETE FROM timeseries WHERE (key, ts) IN (SELECT key, ts FROM _bulk_ts)")
            self._db.execute("INSERT INTO timeseries SELECT * FROM _bulk_ts")
            self._db.unregister("_bulk_ts")

        return len(rows)

    # ── Read operations ──

    def get_item(self, item_id: str) -> Optional[Dict[str, Any]]:
        """Get any content item by ID."""
        row = self._db.execute("SELECT * FROM content WHERE id = ?", [item_id]).fetchone()
        if not row:
            return None
        return self._row_to_dict(row)

    def get_text(self, item_id: str) -> str:
        """Fast text-only lookup."""
        row = self._db.execute("SELECT text FROM content WHERE id = ?", [item_id]).fetchone()
        return row[0] if row else ""

    def get_texts_batch(self, item_ids: List[str]) -> Dict[str, str]:
        """Batch text lookup -- single query."""
        if not item_ids:
            return {}
        placeholders = ",".join(["?"] * len(item_ids))
        rows = self._db.execute(
            f"SELECT id, text FROM content WHERE id IN ({placeholders})", item_ids
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def get_items_by_parent(self, parent_id: str) -> List[Dict[str, Any]]:
        """Get all children of a parent (e.g., passages of a document)."""
        rows = self._db.execute(
            "SELECT * FROM content WHERE parent_id = ? ORDER BY position", [parent_id]
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_items_by_type(self, content_type: str, namespace: str = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Get items by type, optionally filtered by namespace."""
        if namespace:
            rows = self._db.execute(
                "SELECT * FROM content WHERE type = ? AND namespace = ? LIMIT ?",
                [content_type, namespace, limit]
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM content WHERE type = ? LIMIT ?", [content_type, limit]
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_timeseries(self, key: str, limit: int = 30) -> List[Dict[str, Any]]:
        """Get latest N time-series rows for a key."""
        rows = self._db.execute(
            "SELECT * FROM timeseries WHERE key = ? ORDER BY ts DESC LIMIT ?", [key, limit]
        ).fetchall()
        return [self._ts_row_to_dict(r) for r in rows]

    def get_timeseries_range(self, key: str, start: str, end: str) -> List[Dict[str, Any]]:
        """Get time-series in a date range."""
        rows = self._db.execute(
            "SELECT * FROM timeseries WHERE key = ? AND ts BETWEEN ? AND ? ORDER BY ts",
            [key, start, end]
        ).fetchall()
        return [self._ts_row_to_dict(r) for r in rows]

    def get_timeseries_latest(self, key: str) -> Optional[Dict[str, Any]]:
        """Get most recent value for a key."""
        row = self._db.execute(
            "SELECT * FROM timeseries WHERE key = ? ORDER BY ts DESC LIMIT 1", [key]
        ).fetchone()
        return self._ts_row_to_dict(row) if row else None

    # ── ContentStoreInterface compat (maps to generic operations) ──

    def store_document(self, doc: Dict[str, Any], namespace: str = "default") -> str:
        self.store_items([doc], "document", namespace)
        return doc.get("id", "")

    def store_documents(self, docs: List[Dict[str, Any]], namespace: str = "default") -> None:
        self.store_items(docs, "document", namespace)

    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        return self.get_item(doc_id)

    def get_documents_by_namespace(self, namespace: str) -> List[Dict[str, Any]]:
        return self.get_items_by_type("document", namespace)

    def store_passages(self, passages: List[Dict[str, Any]], namespace: str = "default") -> None:
        self.store_items(passages, "passage", namespace)

    def get_passage(self, passage_id: str) -> Optional[Dict[str, Any]]:
        return self.get_item(passage_id)

    def get_passage_text(self, passage_id: str) -> str:
        return self.get_text(passage_id)

    def get_passage_texts_batch(self, ids: List[str]) -> Dict[str, str]:
        return self.get_texts_batch(ids)

    def get_passages_by_doc(self, doc_id: str) -> List[Dict[str, Any]]:
        return self.get_items_by_parent(doc_id)

    def store_facts(self, facts: List[Dict[str, Any]], namespace: str = "default") -> None:
        self.store_items(facts, "fact", namespace)

    def get_fact(self, fact_id: str) -> Optional[Dict[str, Any]]:
        return self.get_item(fact_id)

    def get_facts_by_entity(self, entity_id: str) -> List[Dict[str, Any]]:
        rows = self._db.execute(
            "SELECT * FROM content WHERE type = 'fact' AND json_extract_string(metadata, '$.entity_id') = ?",
            [entity_id]
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_facts_by_passage(self, passage_id: str) -> List[Dict[str, Any]]:
        return self.get_items_by_parent(passage_id)

    def store_prices(self, prices: List[Dict[str, Any]], namespace: str = "default") -> None:
        self.store_timeseries(prices, namespace)

    def get_prices(self, ticker: str, days: int = 30) -> List[Dict[str, Any]]:
        return self.get_timeseries(ticker, days)

    def get_prices_range(self, ticker: str, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        return self.get_timeseries_range(ticker, start_date, end_date)

    def get_price_latest(self, ticker: str) -> Optional[Dict[str, Any]]:
        return self.get_timeseries_latest(ticker)

    def store_entity_details(self, entity: Dict[str, Any], namespace: str = "default") -> None:
        self.store_items([entity], "entity_detail", namespace)

    def store_entity_details_batch(self, entities: List[Dict[str, Any]], namespace: str = "default") -> None:
        self.store_items(entities, "entity_detail", namespace)

    def get_entity_details(self, entity_id: str) -> Optional[Dict[str, Any]]:
        return self.get_item(entity_id)

    # ── Query ──

    def query(self, sql: str, params: list = None) -> List[Dict[str, Any]]:
        rows = self._db.execute(sql, params or []).fetchall()
        cols = [d[0] for d in self._db.description]
        return [dict(zip(cols, r)) for r in rows]

    def stats(self) -> Dict[str, int]:
        result = {}
        try:
            # Content by type
            rows = self._db.execute("SELECT type, COUNT(*) FROM content GROUP BY type").fetchall()
            for r in rows:
                result[r[0]] = r[1]
            result["_total_content"] = sum(v for v in result.values())
        except Exception:
            pass
        try:
            ts_count = self._db.execute("SELECT COUNT(*) FROM timeseries").fetchone()[0]
            result["timeseries"] = ts_count
        except Exception:
            result["timeseries"] = 0
        return result

    def close(self):
        try:
            self._db.close()
        except Exception:
            pass

    # ── Helpers ──

    def _row_to_dict(self, row) -> Dict[str, Any]:
        cols = [d[0] for d in self._db.description]
        result = dict(zip(cols, row))
        # Unpack metadata into the result
        if "metadata" in result and result["metadata"]:
            try:
                meta = json.loads(result["metadata"]) if isinstance(result["metadata"], str) else result["metadata"]
                result.update(meta)
            except (json.JSONDecodeError, TypeError):
                pass
        return result

    def _ts_row_to_dict(self, row) -> Dict[str, Any]:
        cols = [d[0] for d in self._db.description]
        result = dict(zip(cols, row))
        if "metadata" in result and result["metadata"]:
            try:
                meta = json.loads(result["metadata"]) if isinstance(result["metadata"], str) else result["metadata"]
                result.update(meta)
            except (json.JSONDecodeError, TypeError):
                pass
        return result


def get_duckdb_store(db_path: str = None) -> DuckDBStore:
    global _global_store
    if _global_store is None:
        with _store_lock:
            if _global_store is None:
                path = db_path or os.environ.get(
                    "CONTEXTCORE_DUCKDB_PATH", "contextcore_data/content.duckdb"
                )
                _global_store = DuckDBStore(path)
    return _global_store
