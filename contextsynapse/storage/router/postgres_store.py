"""PostgreSQL content store -- production-grade structured storage.

Uses the connection pool from core/db.py. All tables live in one
PostgreSQL database under the 'content' schema.

Set: CONTEXTCORE_CONTENT_STORE=postgres
     DATABASE_URL=postgresql://user:pass@localhost:5432/contextcore
"""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional

from contextsynapse.storage.router.interface import ContentStoreInterface

logger = logging.getLogger(__name__)

_global_store: Optional["PostgresContentStore"] = None
_store_lock = threading.Lock()

# SQL for creating the content schema and tables
_INIT_SQL = """
CREATE SCHEMA IF NOT EXISTS content;

CREATE TABLE IF NOT EXISTS content.documents (
    id TEXT PRIMARY KEY,
    title TEXT,
    url TEXT,
    author TEXT,
    date TEXT,
    source TEXT,
    content_hash TEXT,
    namespace TEXT DEFAULT 'default',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS content.passages (
    id TEXT PRIMARY KEY,
    doc_id TEXT,
    text TEXT NOT NULL,
    section TEXT,
    position INTEGER DEFAULT 0,
    token_count INTEGER DEFAULT 0,
    namespace TEXT DEFAULT 'default',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS content.facts (
    id TEXT PRIMARY KEY,
    passage_id TEXT,
    statement TEXT NOT NULL,
    fact_type TEXT DEFAULT 'general',
    confidence REAL DEFAULT 0.5,
    entity_id TEXT,
    namespace TEXT DEFAULT 'default',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS content.prices (
    ticker TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume BIGINT,
    change_pct REAL,
    namespace TEXT DEFAULT 'default',
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS content.entity_details (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    description TEXT,
    aliases JSONB DEFAULT '[]',
    metadata JSONB DEFAULT '{}',
    namespace TEXT DEFAULT 'default',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_c_passages_doc ON content.passages(doc_id);
CREATE INDEX IF NOT EXISTS idx_c_passages_ns ON content.passages(namespace);
CREATE INDEX IF NOT EXISTS idx_c_facts_passage ON content.facts(passage_id);
CREATE INDEX IF NOT EXISTS idx_c_facts_entity ON content.facts(entity_id);
CREATE INDEX IF NOT EXISTS idx_c_facts_type ON content.facts(fact_type);
CREATE INDEX IF NOT EXISTS idx_c_prices_ticker ON content.prices(ticker);
CREATE INDEX IF NOT EXISTS idx_c_documents_ns ON content.documents(namespace);
CREATE INDEX IF NOT EXISTS idx_c_entity_type ON content.entity_details(type);
"""


class PostgresContentStore(ContentStoreInterface):
    """Production content store backed by PostgreSQL.

    Best for: production, multi-worker, concurrent writes, horizontal scale.
    Uses connection pooling from core/db.py.
    """

    def __init__(self, database_url: str = None):
        self._url = database_url or os.environ.get("DATABASE_URL", "")
        if not self._url:
            raise ValueError(
                "DATABASE_URL is required for PostgresContentStore. "
                "Set it in your environment or .env file."
            )
        self._init_schema()
        logger.info("PostgreSQL content store ready")

    def _conn(self):
        """Get a connection from the pool."""
        from contextsynapse.core.db import connect
        return connect()

    def _return(self, conn):
        """Return connection to pool."""
        from contextsynapse.core.db import _return_pg_conn
        _return_pg_conn(conn)

    def _init_schema(self):
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(_INIT_SQL)
            conn.commit()
        finally:
            self._return(conn)

    def _fetchone_dict(self, sql: str, params: list = None) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        try:
            import psycopg2.extras
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql, params or [])
            row = cur.fetchone()
            return dict(row) if row else None
        finally:
            self._return(conn)

    def _fetchall_dict(self, sql: str, params: list = None) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            import psycopg2.extras
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql, params or [])
            return [dict(r) for r in cur.fetchall()]
        finally:
            self._return(conn)

    def _execute(self, sql: str, params: list = None):
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(sql, params or [])
            conn.commit()
        finally:
            self._return(conn)

    def _executemany(self, sql: str, params_list: List[tuple]):
        conn = self._conn()
        try:
            cur = conn.cursor()
            import psycopg2.extras
            psycopg2.extras.execute_batch(cur, sql, params_list, page_size=500)
            conn.commit()
        finally:
            self._return(conn)

    # ── Documents ──

    def store_document(self, doc: Dict[str, Any], namespace: str = "default") -> str:
        self._execute(
            """INSERT INTO content.documents (id, title, url, author, date, source, content_hash, namespace, metadata)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO UPDATE SET title=EXCLUDED.title, url=EXCLUDED.url,
               author=EXCLUDED.author, date=EXCLUDED.date, source=EXCLUDED.source,
               content_hash=EXCLUDED.content_hash, metadata=EXCLUDED.metadata""",
            [doc.get("id"), doc.get("title"), doc.get("url"), doc.get("author"),
             doc.get("date"), doc.get("source"), doc.get("content_hash"),
             namespace, json.dumps(doc.get("metadata", {}))],
        )
        return doc["id"]

    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        return self._fetchone_dict("SELECT * FROM content.documents WHERE id = %s", [doc_id])

    def get_documents_by_namespace(self, namespace: str) -> List[Dict[str, Any]]:
        return self._fetchall_dict("SELECT * FROM content.documents WHERE namespace = %s", [namespace])

    # ── Passages ──

    def store_passages(self, passages: List[Dict[str, Any]], namespace: str = "default") -> None:
        if not passages:
            return
        self._executemany(
            """INSERT INTO content.passages (id, doc_id, text, section, position, token_count, namespace, metadata)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO UPDATE SET text=EXCLUDED.text, metadata=EXCLUDED.metadata""",
            [(p.get("id"), p.get("doc_id"), p["text"], p.get("section"),
              p.get("position", 0), p.get("token_count", 0),
              namespace, json.dumps(p.get("metadata", {})))
             for p in passages],
        )

    def get_passage(self, passage_id: str) -> Optional[Dict[str, Any]]:
        return self._fetchone_dict("SELECT * FROM content.passages WHERE id = %s", [passage_id])

    def get_passage_text(self, passage_id: str) -> str:
        row = self._fetchone_dict("SELECT text FROM content.passages WHERE id = %s", [passage_id])
        return row["text"] if row else ""

    def get_passage_texts_batch(self, passage_ids: List[str]) -> Dict[str, str]:
        if not passage_ids:
            return {}
        rows = self._fetchall_dict(
            "SELECT id, text FROM content.passages WHERE id = ANY(%s)", [passage_ids]
        )
        return {r["id"]: r["text"] for r in rows}

    def get_passages_by_doc(self, doc_id: str) -> List[Dict[str, Any]]:
        return self._fetchall_dict(
            "SELECT * FROM content.passages WHERE doc_id = %s ORDER BY position", [doc_id]
        )

    # ── Facts ──

    def store_facts(self, facts: List[Dict[str, Any]], namespace: str = "default") -> None:
        if not facts:
            return
        self._executemany(
            """INSERT INTO content.facts (id, passage_id, statement, fact_type, confidence, entity_id, namespace, metadata)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO UPDATE SET statement=EXCLUDED.statement, confidence=EXCLUDED.confidence""",
            [(f.get("id"), f.get("passage_id"), f["statement"], f.get("fact_type", "general"),
              f.get("confidence", 0.5), f.get("entity_id"),
              namespace, json.dumps(f.get("metadata", {})))
             for f in facts],
        )

    def get_fact(self, fact_id: str) -> Optional[Dict[str, Any]]:
        return self._fetchone_dict("SELECT * FROM content.facts WHERE id = %s", [fact_id])

    def get_facts_by_entity(self, entity_id: str) -> List[Dict[str, Any]]:
        return self._fetchall_dict(
            "SELECT * FROM content.facts WHERE entity_id = %s ORDER BY confidence DESC", [entity_id]
        )

    def get_facts_by_passage(self, passage_id: str) -> List[Dict[str, Any]]:
        return self._fetchall_dict(
            "SELECT * FROM content.facts WHERE passage_id = %s", [passage_id]
        )

    # ── Prices ──

    def store_prices(self, prices: List[Dict[str, Any]], namespace: str = "default") -> None:
        if not prices:
            return
        self._executemany(
            """INSERT INTO content.prices (ticker, date, open, high, low, close, volume, change_pct, namespace)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (ticker, date) DO UPDATE SET close=EXCLUDED.close, volume=EXCLUDED.volume""",
            [(p["ticker"], p["date"], p.get("open"), p.get("high"),
              p.get("low"), p.get("close"), p.get("volume"),
              p.get("change_pct"), namespace)
             for p in prices],
        )

    def get_prices(self, ticker: str, days: int = 30) -> List[Dict[str, Any]]:
        return self._fetchall_dict(
            "SELECT * FROM content.prices WHERE ticker = %s ORDER BY date DESC LIMIT %s",
            [ticker, days],
        )

    def get_prices_range(self, ticker: str, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        return self._fetchall_dict(
            "SELECT * FROM content.prices WHERE ticker = %s AND date BETWEEN %s AND %s ORDER BY date",
            [ticker, start_date, end_date],
        )

    def get_price_latest(self, ticker: str) -> Optional[Dict[str, Any]]:
        return self._fetchone_dict(
            "SELECT * FROM content.prices WHERE ticker = %s ORDER BY date DESC LIMIT 1", [ticker]
        )

    # ── Entity details ──

    def store_entity_details(self, entity: Dict[str, Any], namespace: str = "default") -> None:
        self._execute(
            """INSERT INTO content.entity_details (id, name, type, description, aliases, metadata, namespace)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO UPDATE SET description=EXCLUDED.description, aliases=EXCLUDED.aliases""",
            [entity["id"], entity["name"], entity["type"],
             entity.get("description"), json.dumps(entity.get("aliases", [])),
             json.dumps(entity.get("metadata", {})), namespace],
        )

    def get_entity_details(self, entity_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone_dict("SELECT * FROM content.entity_details WHERE id = %s", [entity_id])
        if row and isinstance(row.get("aliases"), str):
            row["aliases"] = json.loads(row["aliases"])
        return row

    # ── Query ──

    def query(self, sql: str, params: list = None) -> List[Dict[str, Any]]:
        return self._fetchall_dict(sql, params)

    # ── Stats ──

    def stats(self) -> Dict[str, int]:
        result = {}
        for table in ["documents", "passages", "facts", "prices", "entity_details"]:
            try:
                row = self._fetchone_dict(f"SELECT COUNT(*) as count FROM content.{table}")
                result[table] = row["count"] if row else 0
            except Exception:
                result[table] = 0
        return result

    def close(self):
        from contextsynapse.core.db import close_all
        close_all()


def get_postgres_store(database_url: str = None) -> PostgresContentStore:
    """Get or create the global PostgreSQL content store."""
    global _global_store
    if _global_store is None:
        with _store_lock:
            if _global_store is None:
                _global_store = PostgresContentStore(database_url)
    return _global_store
