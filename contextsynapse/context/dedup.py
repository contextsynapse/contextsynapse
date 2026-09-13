"""
Context Deduplication
=====================
Prevents context pollution by detecting duplicate or near-duplicate content
before it enters a session.

Two layers of protection:

1. **Content hash** (exact) — SHA-256 of normalised text.
   Catches exact duplicates and trivial reformattings (whitespace, case).

2. **Semantic similarity** (fuzzy) — cosine similarity between embeddings.
   Catches paraphrases, rewordings, and near-duplicates.

Usage::

    dedup = ContextDedup(vector_store=my_vector_store)
    result = dedup.check(session_id, "Alice is the CEO of Acme Corp")
    if result.is_duplicate:
        print(f"Duplicate of {result.matched_id} (score={result.score})")
    else:
        # safe to ingest
        dedup.register(session_id, node_id, text)
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Semantic similarity threshold — items above this score are considered duplicates
DEFAULT_SEMANTIC_THRESHOLD = 0.92


@dataclass
class DedupResult:
    """Result of a deduplication check."""
    is_duplicate: bool
    match_type: Optional[str] = None   # "exact" | "semantic" | None
    matched_id: Optional[str] = None   # node_id of the existing item
    score: float = 0.0                 # similarity score (1.0 = exact)
    matched_hash: Optional[str] = None


class ContextDedup:
    """
    Dual-layer deduplication for context sessions.

    Maintains a per-session hash index (SQLite) for exact matches and uses
    the SessionVectorStore for semantic similarity detection.
    """

    def __init__(
        self,
        vector_store=None,
        db_path: str = "contextcore_data/context.db",
        semantic_threshold: float = DEFAULT_SEMANTIC_THRESHOLD,
    ):
        self._vector_store = vector_store
        self._threshold = semantic_threshold

        # Hash index — stored in the shared context.db
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS content_hashes (
                session_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                normalised_hash TEXT NOT NULL,
                char_count INTEGER,
                created_at TEXT NOT NULL,
                PRIMARY KEY (session_id, content_hash)
            );
            CREATE INDEX IF NOT EXISTS idx_hashes_session ON content_hashes(session_id);
            CREATE INDEX IF NOT EXISTS idx_hashes_normalised ON content_hashes(session_id, normalised_hash);
        """)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self, session_id: str, text: str) -> DedupResult:
        """
        Check whether ``text`` is a duplicate of something already in the session.

        Returns a DedupResult. If ``is_duplicate`` is True, the caller should
        skip ingestion.
        """
        # Layer 1: exact content hash
        content_hash = self._hash_text(text)
        normalised_hash = self._hash_normalised(text)

        row = self._conn.execute(
            "SELECT node_id FROM content_hashes WHERE session_id = ? AND content_hash = ?",
            (session_id, content_hash),
        ).fetchone()
        if row:
            return DedupResult(
                is_duplicate=True,
                match_type="exact",
                matched_id=row["node_id"],
                score=1.0,
                matched_hash=content_hash,
            )

        # Also check normalised hash (catches whitespace/case diffs)
        row = self._conn.execute(
            "SELECT node_id FROM content_hashes WHERE session_id = ? AND normalised_hash = ?",
            (session_id, normalised_hash),
        ).fetchone()
        if row:
            return DedupResult(
                is_duplicate=True,
                match_type="exact",
                matched_id=row["node_id"],
                score=0.99,
                matched_hash=normalised_hash,
            )

        # Layer 2: semantic similarity via vector store
        if self._vector_store and self._vector_store.available:
            results = self._vector_store.search(
                session_id=session_id,
                query=text,
                k=1,  # just need the top match
            )
            if results:
                top = results[0]
                score = top.get("score", 0)
                if score >= self._threshold:
                    return DedupResult(
                        is_duplicate=True,
                        match_type="semantic",
                        matched_id=top.get("node_id"),
                        score=score,
                    )

        return DedupResult(is_duplicate=False)

    def register(self, session_id: str, node_id: str, text: str) -> str:
        """
        Register a piece of content as ingested.

        Call this AFTER successfully ingesting (and optionally embedding) the
        content so future checks will catch duplicates.

        Returns the content hash.
        """
        content_hash = self._hash_text(text)
        normalised_hash = self._hash_normalised(text)
        now = datetime.now(timezone.utc).isoformat()

        self._conn.execute(
            "INSERT OR IGNORE INTO content_hashes "
            "(session_id, node_id, content_hash, normalised_hash, char_count, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, node_id, content_hash, normalised_hash, len(text), now),
        )
        self._conn.commit()
        return content_hash

    def get_hash_count(self, session_id: str) -> int:
        """How many unique content hashes are in a session."""
        row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM content_hashes WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return row["cnt"] if row else 0

    def clear_session(self, session_id: str) -> int:
        """Remove all hashes for a session. Returns count deleted."""
        cur = self._conn.execute(
            "DELETE FROM content_hashes WHERE session_id = ?", (session_id,),
        )
        self._conn.commit()
        return cur.rowcount

    # ------------------------------------------------------------------
    # Hashing internals
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_text(text: str) -> str:
        """SHA-256 of raw text."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _hash_normalised(text: str) -> str:
        """SHA-256 of normalised text (lowercase, collapsed whitespace, stripped)."""
        normalised = re.sub(r"\s+", " ", text.lower().strip())
        return hashlib.sha256(normalised.encode("utf-8")).hexdigest()

    def close(self):
        if self._conn:
            self._conn.close()
