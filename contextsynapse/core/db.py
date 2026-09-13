"""
contextcore/core/db.py
──────────────────────
Thin database abstraction — SQLite (default) or PostgreSQL.

Set environment variable:
    DATABASE_URL=postgresql://user:password@localhost:5432/contextcore

If DATABASE_URL is not set, falls back to SQLite using the path
passed to each function. This keeps full backward compatibility.
"""
from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger(__name__)

_URL: str = os.environ.get("DATABASE_URL", "")

# True when a Postgres URL is configured
IS_POSTGRES: bool = _URL.startswith(("postgresql://", "postgres://"))

# SQL placeholder: %s for psycopg2, ? for sqlite3
PH: str = "%s" if IS_POSTGRES else "?"


# ── Connection pool ──────────────────────────────────────────────────────────

_pg_pool = None
_pg_pool_lock = threading.Lock()
_sqlite_connections: dict = {}
_sqlite_lock = threading.Lock()


def _get_pg_pool():
    """Get or create a PostgreSQL connection pool (thread-safe singleton)."""
    global _pg_pool
    if _pg_pool is not None:
        return _pg_pool
    with _pg_pool_lock:
        if _pg_pool is not None:
            return _pg_pool
        try:
            from psycopg2 import pool as pg_pool
            _pg_pool = pg_pool.ThreadedConnectionPool(
                minconn=2,
                maxconn=20,
                dsn=_URL,
            )
            logger.info("PostgreSQL connection pool created (2-20 connections)")
        except ImportError:
            logger.warning("psycopg2 not installed — falling back to direct connections")
            return None
        except Exception as e:
            logger.error("Failed to create PostgreSQL pool: %s", e)
            return None
    return _pg_pool


def _get_sqlite_conn(sqlite_path: str):
    """Get or create a SQLite connection for the given path (one per path, thread-safe).

    SQLite with WAL mode supports concurrent reads and serialized writes.
    Reusing connections avoids repeated open/close overhead.
    """
    path_key = str(sqlite_path)
    conn = _sqlite_connections.get(path_key)
    if conn is not None:
        try:
            conn.execute("SELECT 1")
            return conn
        except Exception:
            # Connection is broken, remove and recreate
            _sqlite_connections.pop(path_key, None)

    with _sqlite_lock:
        # Double-check after acquiring lock
        conn = _sqlite_connections.get(path_key)
        if conn is not None:
            try:
                conn.execute("SELECT 1")
                return conn
            except Exception:
                _sqlite_connections.pop(path_key, None)

        import sqlite3
        conn = sqlite3.connect(path_key, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _sqlite_connections[path_key] = conn
        return conn


# ── Connection helpers ────────────────────────────────────────────────────────

def connect(sqlite_path: Optional[str] = None):
    """
    Return a DB-API 2.0 connection.

    Postgres: from pool if available, else direct connect.
    SQLite  : cached per-path connection with WAL mode.
    """
    if IS_POSTGRES:
        pool = _get_pg_pool()
        if pool:
            return pool.getconn()
        import psycopg2
        return psycopg2.connect(_URL)

    return _get_sqlite_conn(sqlite_path or "contextsynapse.db")


def _return_pg_conn(conn):
    """Return a PostgreSQL connection to the pool."""
    pool = _get_pg_pool()
    if pool:
        try:
            pool.putconn(conn)
            return
        except Exception:
            pass
    try:
        conn.close()
    except Exception:
        pass


def dict_cursor(conn):
    """
    Return a cursor that yields dict-like rows.

    Postgres: RealDictCursor
    SQLite  : standard cursor (conn already has row_factory=sqlite3.Row)
    """
    if IS_POSTGRES:
        import psycopg2.extras
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    return conn.cursor()


@contextmanager
def transaction(sqlite_path: Optional[str] = None):
    """
    Context manager: yields (conn, cursor) inside a transaction.
    Commits on clean exit, rolls back on exception.

    Usage:
        with transaction(sqlite_path) as (conn, cur):
            cur.execute("INSERT ...")
    """
    conn = connect(sqlite_path)
    try:
        cur = dict_cursor(conn)
        yield conn, cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if IS_POSTGRES:
            _return_pg_conn(conn)
        # SQLite connections are cached, don't close them


def run_ddl(conn, sql: str) -> None:
    """Execute a DDL (Data Definition Language) statement.

    Postgres: execute directly via cursor.
    SQLite:   use executescript for multi-statement DDL.
    """
    if IS_POSTGRES:
        cur = conn.cursor()
        cur.execute(sql)
        conn.commit()
    else:
        conn.executescript(sql)


def close_all():
    """Close all pooled connections. Call on shutdown."""
    global _pg_pool
    if _pg_pool:
        try:
            _pg_pool.closeall()
        except Exception:
            pass
        _pg_pool = None

    for path, conn in list(_sqlite_connections.items()):
        try:
            conn.close()
        except Exception:
            pass
    _sqlite_connections.clear()


# ── SQL dialect helpers ───────────────────────────────────────────────────────

def insert_ignore(table: str, cols: list[str]) -> str:
    """
    Build an INSERT ... (ignore on conflict) statement.

    Postgres: INSERT INTO t (...) VALUES (...) ON CONFLICT DO NOTHING
    SQLite  : INSERT OR IGNORE INTO t (...) VALUES (...)
    """
    ph = ", ".join([PH] * len(cols))
    col_list = ", ".join(cols)
    if IS_POSTGRES:
        return f"INSERT INTO {table} ({col_list}) VALUES ({ph}) ON CONFLICT DO NOTHING"
    return f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({ph})"


def upsert(table: str, cols: list[str], conflict_cols: list[str],
           update_cols: Optional[list[str]] = None) -> str:
    """
    Build an INSERT ... ON CONFLICT DO UPDATE (upsert) statement.

    conflict_cols : columns in the UNIQUE / PK constraint
    update_cols   : columns to update on conflict (defaults to all non-conflict cols)

    Postgres: INSERT INTO t (...) VALUES (%s,...) ON CONFLICT (col) DO UPDATE SET col=%s,...
    SQLite  : INSERT OR REPLACE INTO t (...) VALUES (?,...)
    """
    ph = ", ".join([PH] * len(cols))
    col_list = ", ".join(cols)
    if IS_POSTGRES:
        update_cols = update_cols or [c for c in cols if c not in conflict_cols]
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
        conflict = ", ".join(conflict_cols)
        return (
            f"INSERT INTO {table} ({col_list}) VALUES ({ph}) "
            f"ON CONFLICT ({conflict}) DO UPDATE SET {updates}"
        )
    return f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({ph})"


# ── Error classes ─────────────────────────────────────────────────────────────

def integrity_error():
    """Return the IntegrityError exception class for the active backend."""
    if IS_POSTGRES:
        import psycopg2
        return psycopg2.IntegrityError
    import sqlite3
    return sqlite3.IntegrityError


# ── Schema inspection ─────────────────────────────────────────────────────────

def column_exists(conn, table: str, column: str) -> bool:
    """Check whether a column exists in a table (used for migrations)."""
    cur = conn.cursor()
    if IS_POSTGRES:
        cur.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name=%s AND column_name=%s",
            (table, column),
        )
    else:
        cur.execute(f"PRAGMA table_info({table})")
        rows = cur.fetchall()
        return any(r[1] == column for r in rows)
    return cur.fetchone() is not None


def row_to_dict(row) -> Optional[dict]:
    """Convert a DB row to a plain Python dict."""
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    # sqlite3.Row — supports dict() via keys()
    try:
        return dict(row)
    except Exception:
        return None


# ── DDL templates ─────────────────────────────────────────────────────────────
# Use these when DDL differs between backends.

def serial_pk() -> str:
    """Auto-increment integer primary key."""
    return "SERIAL PRIMARY KEY" if IS_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
