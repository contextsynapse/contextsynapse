"""DeduplicateOperator -- hash-based chunk deduplication."""
from __future__ import annotations

import hashlib
import logging
from typing import List, TYPE_CHECKING

from .base import StageOperator

if TYPE_CHECKING:
    from ..ingest_content import Chunk
    from ..stage_executor import GraphContext

logger = logging.getLogger(__name__)


def _content_hash(text: str) -> str:
    """Normalized content hash -- lowercase, collapsed whitespace."""
    normalized = " ".join(text.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


class DeduplicateOperator(StageOperator):
    """Remove chunks with identical content via normalized hashing.

    Two chunks are considered duplicates when their lowercased,
    whitespace-collapsed text produces the same SHA-256 prefix.
    The first occurrence is kept; later duplicates are dropped.
    """

    name = "deduplicate"

    def process(self, chunks: List["Chunk"], graph_ctx: "GraphContext") -> List["Chunk"]:
        seen: set[str] = set()
        unique: List["Chunk"] = []
        for chunk in chunks:
            h = _content_hash(chunk.content)
            if h in seen:
                continue
            seen.add(h)
            unique.append(chunk)
        removed = len(chunks) - len(unique)
        if removed:
            logger.info("deduplicate: removed %d duplicate chunks (kept %d)", removed, len(unique))
        return unique
