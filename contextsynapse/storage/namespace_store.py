"""
Namespace Store
================
Unified namespace-scoped storage that gives each namespace its own
directory with all polyglot stores properly isolated:

    contextcore_data/namespaces/{namespace}/
    ├── graph.h5           # Graph (CSR/HDF5)
    ├── namespace.db       # Per-namespace SQLite (metadata, blobs, custom tables)
    ├── vectors/           # Vector embeddings
    ├── documents/         # Document store (TinyDB)
    ├── blobs/             # Binary objects (images, PDFs, audio, video)
    ├── parquet/           # Columnar data (PyArrow/Parquet)
    ├── wal/               # Write-ahead log
    └── checkpoints/       # Graph snapshots

Usage:
    from contextsynapse.storage.namespace_store import NamespaceStore

    ns = NamespaceStore("my-project")
    ns.store_blob(data, "report.pdf", "application/pdf")
    ns.store_vector("node-1", [0.1, 0.2, ...])
    ns.store_columnar("metrics", [{"name": "revenue", "value": 4.2}])
    ns.sql("INSERT INTO custom_table VALUES (?, ?)", (1, "hello"))
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_BASE = Path("contextcore_data/namespaces")


def _safe_name(name: str) -> str:
    """Sanitize namespace name for filesystem."""
    return re.sub(r'[<>:"/\\|?*]', '_', name)


class NamespaceStore:
    """Namespace-scoped polyglot storage.

    Each namespace gets its own directory tree with isolated stores
    for graph, vector, document, blob, columnar, and SQL data.
    """

    def __init__(self, namespace: str, base_path: Optional[str] = None):
        self.namespace = namespace
        safe = _safe_name(namespace)
        self.root = Path(base_path or _BASE) / safe
        self.root.mkdir(parents=True, exist_ok=True)

        # Sub-directories
        self.graph_dir = self.root
        self.vector_dir = self.root / "vectors"
        self.document_dir = self.root / "documents"
        self.blob_dir = self.root / "blobs"
        self.parquet_dir = self.root / "parquet"
        self.wal_dir = self.root / "wal"
        self.checkpoint_dir = self.root / "checkpoints"

        # Create all dirs
        for d in [self.vector_dir, self.document_dir, self.blob_dir,
                  self.parquet_dir, self.wal_dir, self.checkpoint_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # Per-namespace SQLite DB
        self._db_path = self.root / "namespace.db"
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

        logger.debug("NamespaceStore ready: %s (%s)", namespace, self.root)

    def _init_schema(self):
        """Create base tables in the namespace SQLite DB."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS blobs (
                blob_id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                content_hash TEXT NOT NULL,
                storage_path TEXT NOT NULL,
                agent_id TEXT,
                created_at TEXT NOT NULL,
                metadata TEXT DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_blobs_hash ON blobs(content_hash);

            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS facts (
                fact_id TEXT PRIMARY KEY,
                fact_type TEXT NOT NULL,
                statement TEXT NOT NULL,
                confidence REAL DEFAULT 0.5,
                source_text TEXT,
                properties TEXT DEFAULT '{}',
                node_id TEXT,
                agent_id TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_facts_type ON facts(fact_type);
        """)
        self._conn.commit()

    # ==================================================================
    # Blob Storage (images, PDFs, audio, video)
    # ==================================================================

    def store_blob(
        self,
        data: bytes,
        filename: str,
        mime_type: str,
        agent_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Store a binary blob in the namespace's blob directory.

        Returns blob metadata dict.
        """
        content_hash = hashlib.sha256(data).hexdigest()
        blob_id = secrets.token_hex(12)
        now = datetime.now(timezone.utc).isoformat()

        # Shard by hash prefix
        shard_dir = self.blob_dir / content_hash[:2]
        shard_dir.mkdir(exist_ok=True)
        blob_path = shard_dir / content_hash
        blob_path.write_bytes(data)

        rel_path = str(blob_path.relative_to(self.root))

        self._conn.execute(
            "INSERT OR REPLACE INTO blobs (blob_id, filename, mime_type, size_bytes, "
            "content_hash, storage_path, agent_id, created_at, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (blob_id, filename, mime_type, len(data), content_hash,
             rel_path, agent_id, now, json.dumps(metadata or {})),
        )
        self._conn.commit()

        return {
            "blob_id": blob_id,
            "filename": filename,
            "mime_type": mime_type,
            "size_bytes": len(data),
            "content_hash": content_hash,
            "storage_path": rel_path,
        }

    def get_blob(self, blob_id: str) -> Optional[bytes]:
        """Retrieve blob data by ID."""
        row = self._conn.execute(
            "SELECT storage_path FROM blobs WHERE blob_id = ?", (blob_id,)
        ).fetchone()
        if not row:
            return None
        blob_path = self.root / row["storage_path"]
        return blob_path.read_bytes() if blob_path.exists() else None

    def list_blobs(self) -> List[Dict[str, Any]]:
        """List all blobs in this namespace."""
        rows = self._conn.execute("SELECT * FROM blobs ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    # ==================================================================
    # Vector Storage
    # ==================================================================

    def get_vector_store(self, dimension: int = 1536):
        """Get or create a vector store for this namespace (backend-aware)."""
        from ..vector.vector_db_manager import get_vector_db_manager
        mgr = get_vector_db_manager()
        collection_name = f"{self.namespace}_passages"
        store = mgr.create_store(
            dimension=dimension,
            metric="cosine",
            collection_name=collection_name,
            path=str(self.vector_dir),
        )
        # For non-Qdrant backends, try to load persisted index
        if mgr.backend_name != "qdrant":
            index_path = str(self.vector_dir / "index")
            try:
                store.load(index_path)
            except Exception:
                pass
        return store

    def save_vector_store(self, store):
        """Persist the vector store (no-op for Qdrant — it auto-persists)."""
        from ..vector.vector_db_manager import get_vector_db_manager
        if get_vector_db_manager().backend_name == "qdrant":
            return  # Qdrant persists automatically
        index_path = str(self.vector_dir / "index")
        store.save(index_path)

    # ==================================================================
    # Columnar Storage (Parquet)
    # ==================================================================

    def get_columnar_store(self):
        """Get a columnar store scoped to this namespace."""
        from .columnar_store_v2 import ColumnarStoreV2
        return ColumnarStoreV2(storage_path=str(self.parquet_dir))

    # ==================================================================
    # Document Storage (TinyDB)
    # ==================================================================

    def get_document_store(self, collection: str = "default"):
        """Get a document store scoped to this namespace."""
        from .document_store import TinyDBDocumentStore
        return TinyDBDocumentStore(
            namespace=self.namespace,
            collection=collection,
            config={"storage_path": str(self.document_dir / collection)},
        )

    # ==================================================================
    # SQL (per-namespace SQLite)
    # ==================================================================

    def sql(self, query: str, params: tuple = ()) -> List[Dict]:
        """Execute SQL on the namespace's SQLite database.

        For custom tables, metadata, or any structured data that
        doesn't fit the graph model.
        """
        cursor = self._conn.execute(query, params)
        if query.strip().upper().startswith("SELECT"):
            return [dict(row) for row in cursor.fetchall()]
        self._conn.commit()
        return []

    def set_metadata(self, key: str, value: Any):
        """Store namespace metadata."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value, updated_at) VALUES (?, ?, ?)",
            (key, json.dumps(value) if not isinstance(value, str) else value, now),
        )
        self._conn.commit()

    def get_metadata(self, key: str) -> Optional[Any]:
        """Get namespace metadata."""
        row = self._conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return row["value"]

    # ==================================================================
    # Facts (stored in namespace SQLite)
    # ==================================================================

    def store_fact(
        self,
        fact_type: str,
        statement: str,
        confidence: float = 0.5,
        source_text: str = "",
        properties: Optional[Dict] = None,
        node_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> str:
        """Store a fact in the namespace's SQLite DB."""
        fact_id = secrets.token_hex(12)
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO facts (fact_id, fact_type, statement, confidence, "
            "source_text, properties, node_id, agent_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (fact_id, fact_type, statement, confidence, source_text,
             json.dumps(properties or {}), node_id, agent_id, now),
        )
        self._conn.commit()
        return fact_id

    def get_facts(self, fact_type: Optional[str] = None, min_confidence: float = 0.0) -> List[Dict]:
        """Query facts from this namespace."""
        if fact_type:
            rows = self._conn.execute(
                "SELECT * FROM facts WHERE fact_type = ? AND confidence >= ? ORDER BY created_at DESC",
                (fact_type, min_confidence),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM facts WHERE confidence >= ? ORDER BY created_at DESC",
                (min_confidence,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ==================================================================
    # Info
    # ==================================================================

    def info(self) -> Dict[str, Any]:
        """Get namespace storage info."""
        blob_count = self._conn.execute("SELECT COUNT(*) as c FROM blobs").fetchone()["c"]
        fact_count = self._conn.execute("SELECT COUNT(*) as c FROM facts").fetchone()["c"]

        # Check what exists on disk
        vector_exists = any(self.vector_dir.iterdir()) if self.vector_dir.exists() else False
        parquet_files = list(self.parquet_dir.glob("*.parquet")) if self.parquet_dir.exists() else []

        return {
            "namespace": self.namespace,
            "root": str(self.root),
            "stores": {
                "graph": (self.root / "graph.h5").exists() or (self.root / "graph.json").exists(),
                "vectors": vector_exists,
                "documents": any(self.document_dir.iterdir()) if self.document_dir.exists() else False,
                "blobs": blob_count,
                "parquet_tables": len(parquet_files),
                "facts": fact_count,
                "wal": (self.wal_dir / "wal.log").exists(),
                "sql_db": self._db_path.exists(),
            },
        }

    def close(self):
        if self._conn:
            self._conn.close()

    def __repr__(self):
        return f"NamespaceStore({self.namespace!r}, root={self.root})"
