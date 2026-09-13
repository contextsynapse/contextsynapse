"""PostgreSQL connection pool and schema manager.

Uses psycopg2 with a thread-safe connection pool.
Set DATABASE_URL=postgresql://user:pass@localhost:5432/contextcore

Falls back to SQLite in dev mode if DATABASE_URL is not set.
"""

import os
import logging
import threading
from pathlib import Path
from contextlib import contextmanager

log = logging.getLogger(__name__)

_SCHEMA_FILE = Path(__file__).parent / "schema.sql"
_SQLITE_DIR = Path(__file__).resolve().parent.parent / "contextcore_data"
_SQLITE_PATH = _SQLITE_DIR / "platform.db"

# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL", "")
_USE_PG = DATABASE_URL.startswith("postgresql")

# ---------------------------------------------------------------------------
# Pool singleton (thread-safe init)
# ---------------------------------------------------------------------------

_pool = None
_pool_lock = threading.Lock()


def get_pool():
    """Return (or create) the connection pool / SQLite path."""
    global _pool
    if _pool is not None:
        return _pool

    with _pool_lock:
        if _pool is not None:
            return _pool

        if _USE_PG:
            import psycopg2.pool  # type: ignore

            log.info("Creating PostgreSQL connection pool: %s", DATABASE_URL[:40] + "...")
            _pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=2,
                maxconn=10,
                dsn=DATABASE_URL,
            )
        else:
            _SQLITE_DIR.mkdir(parents=True, exist_ok=True)
            log.info("Using SQLite fallback: %s", _SQLITE_PATH)
            _pool = str(_SQLITE_PATH)

        return _pool


def _close_pool():
    """Shutdown hook -- close all connections."""
    global _pool
    if _pool is not None and _USE_PG:
        try:
            _pool.closeall()
        except Exception:
            pass
        _pool = None


# ---------------------------------------------------------------------------
# Connection context manager
# ---------------------------------------------------------------------------

@contextmanager
def get_connection():
    """Yield a DB-API connection.

    * PostgreSQL: borrows from pool, commits on success, rolls back on error,
      returns to pool on exit.
    * SQLite: opens a connection (auto-creates file), same commit/rollback
      semantics.
    """
    pool = get_pool()

    if _USE_PG:
        conn = pool.getconn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            pool.putconn(conn)
    else:
        import sqlite3

        conn = sqlite3.connect(pool)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Schema migrations
# ---------------------------------------------------------------------------

def _adapt_schema_for_sqlite(sql: str) -> str:
    """Rough transform of PG DDL into SQLite-compatible DDL."""
    import re

    sql = sql.replace("gen_random_uuid()", "'no-uuid'")
    sql = sql.replace("UUID", "TEXT")
    sql = sql.replace("TIMESTAMPTZ", "TEXT")
    sql = sql.replace("BIGSERIAL", "INTEGER")
    sql = sql.replace("JSONB", "TEXT")
    sql = sql.replace("NUMERIC(15,2)", "REAL")
    sql = sql.replace("NUMERIC(15,4)", "REAL")
    sql = sql.replace("NUMERIC(12,4)", "REAL")
    sql = sql.replace("NUMERIC(8,4)", "REAL")
    sql = sql.replace("NUMERIC(8,2)", "REAL")
    sql = sql.replace("NUMERIC(6,4)", "REAL")
    sql = sql.replace("NUMERIC(5,1)", "REAL")
    sql = sql.replace("BIGINT", "INTEGER")
    sql = sql.replace("now()", "CURRENT_TIMESTAMP")
    # Remove TEXT[] default -- SQLite has no array type
    sql = sql.replace("TEXT[]", "TEXT")
    sql = re.sub(r"DEFAULT\s+'\{\}'", "DEFAULT ''", sql)
    return sql


def run_migrations():
    """Execute all CREATE TABLE / CREATE INDEX statements from schema.sql.

    Idempotent thanks to IF NOT EXISTS on every statement.
    """
    raw_sql = _SCHEMA_FILE.read_text(encoding="utf-8")

    if not _USE_PG:
        raw_sql = _adapt_schema_for_sqlite(raw_sql)

    with get_connection() as conn:
        cur = conn.cursor()
        # Split on semicolons and execute each statement
        for stmt in raw_sql.split(";"):
            # Strip comment-only lines, keep actual SQL
            lines = [
                ln for ln in stmt.splitlines()
                if ln.strip() and not ln.strip().startswith("--")
            ]
            cleaned = "\n".join(lines).strip()
            if not cleaned:
                continue
            try:
                cur.execute(cleaned)
            except Exception as exc:
                log.warning("Migration statement skipped: %s -- %s", cleaned[:80], exc)
        cur.close()

    log.info("Database migrations complete (%s)", "PostgreSQL" if _USE_PG else "SQLite")


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def _placeholder():
    """Return the parameter placeholder style for the active backend."""
    return "%s" if _USE_PG else "?"


def _rows_to_dicts(cursor) -> list[dict]:
    """Convert cursor results to list of dicts."""
    if cursor.description is None:
        return []
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def execute(sql: str, params=None) -> list[dict]:
    """Execute a query and return all rows as list of dicts."""
    if not _USE_PG and "%s" in sql:
        sql = _convert_placeholders(sql)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params or ())
        rows = _rows_to_dicts(cur)
        cur.close()
        return rows


def execute_one(sql: str, params=None) -> dict | None:
    """Execute a query and return the first row, or None."""
    rows = execute(sql, params)
    return rows[0] if rows else None


def insert(table: str, data: dict) -> dict:
    """Insert a row and return it (with generated id).

    PostgreSQL uses RETURNING *, SQLite uses last_insert_rowid().
    """
    cols = list(data.keys())
    ph = _placeholder()
    placeholders = ", ".join([ph] * len(cols))
    col_list = ", ".join(cols)
    values = [data[c] for c in cols]

    if _USE_PG:
        sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) RETURNING *"
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, values)
            row = _rows_to_dicts(cur)
            cur.close()
            return row[0] if row else data
    else:
        sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, values)
            rowid = cur.lastrowid
            cur.close()
            # Try to fetch the inserted row
            try:
                return execute_one(
                    f"SELECT * FROM {table} WHERE rowid = ?", (rowid,)
                ) or {**data, "rowid": rowid}
            except Exception:
                return {**data, "rowid": rowid}


def update(table: str, data: dict, where_clause: str, where_params: tuple = ()) -> int:
    """Update rows matching where_clause. Returns number of affected rows."""
    ph = _placeholder()
    set_parts = [f"{col} = {ph}" for col in data.keys()]
    set_clause = ", ".join(set_parts)
    values = list(data.values()) + list(where_params)

    sql = f"UPDATE {table} SET {set_clause} WHERE {where_clause}"
    if not _USE_PG and "%s" in sql:
        sql = _convert_placeholders(sql)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, values)
        affected = cur.rowcount
        cur.close()
        return affected


def _convert_placeholders(sql: str) -> str:
    """Convert %s placeholders to ? for SQLite."""
    return sql.replace("%s", "?")
