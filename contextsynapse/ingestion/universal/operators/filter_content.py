"""FilterContentOperator — keyword and length filtering for pipeline chunks."""
from __future__ import annotations

import logging
from typing import List, Optional, TYPE_CHECKING

from .base import StageOperator

if TYPE_CHECKING:
    from ..ingest_content import Chunk
    from ..stage_executor import GraphContext

log = logging.getLogger(__name__)


class FilterContentOperator(StageOperator):
    """Remove chunks based on keyword inclusion/exclusion and length rules.

    Runs BEFORE extraction operators to reduce noise early in the pipeline.
    """

    name = "filter_content"

    def __init__(
        self,
        keywords_include: Optional[List[str]] = None,
        keywords_exclude: Optional[List[str]] = None,
        min_length: int = 0,
        max_length: int = 0,
    ):
        self.keywords_include = [k.lower() for k in keywords_include] if keywords_include else None
        self.keywords_exclude = [k.lower() for k in keywords_exclude] if keywords_exclude else None
        self.min_length = min_length
        self.max_length = max_length

    def process(self, chunks: List["Chunk"], graph_ctx: "GraphContext") -> List["Chunk"]:
        original_count = len(chunks)
        filtered: List["Chunk"] = []

        for chunk in chunks:
            content = chunk.content

            # 1. Skip if content is shorter than min_length
            if self.min_length > 0 and len(content) < self.min_length:
                continue

            # 2. Truncate if max_length is set and content exceeds it
            if self.max_length > 0 and len(content) > self.max_length:
                chunk.content = content[: self.max_length]

            # 3. If keywords_include set: skip if none match (case-insensitive)
            if self.keywords_include:
                content_lower = chunk.content.lower()
                if not any(kw in content_lower for kw in self.keywords_include):
                    continue

            # 4. If keywords_exclude set: skip if any match
            if self.keywords_exclude:
                content_lower = chunk.content.lower()
                if any(kw in content_lower for kw in self.keywords_exclude):
                    continue

            filtered.append(chunk)

        removed = original_count - len(filtered)
        if removed:
            log.info("filter_content: removed %d/%d chunks", removed, original_count)

        return filtered
