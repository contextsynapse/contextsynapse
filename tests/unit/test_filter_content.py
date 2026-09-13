"""Tests for filter_content operator."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from contextcore.ingestion.universal.ingest_content import Chunk
from contextcore.ingestion.universal.stage_executor import GraphContext
from contextcore.ingestion.universal.operators.filter_content import FilterContentOperator


def _make_graph_ctx() -> GraphContext:
    db = MagicMock()
    db.add_node = MagicMock()
    db.add_edge = MagicMock()
    db.get_node = MagicMock(return_value=None)
    return GraphContext(db=db, namespace="test")


def _make_chunk(content: str, index: int = 0) -> Chunk:
    return Chunk(content=content, index=index, metadata={})


class TestFilterContent:
    def test_keyword_include_keeps_matching(self):
        """3 chunks, 1 doesn't match include keywords -> 2 kept."""
        op = FilterContentOperator(keywords_include=["python", "fastapi"])
        ctx = _make_graph_ctx()
        chunks = [
            _make_chunk("Python is a great language", index=0),
            _make_chunk("FastAPI powers our backend", index=1),
            _make_chunk("The weather is nice today", index=2),
        ]

        result = op.process(chunks, ctx)

        assert len(result) == 2
        assert result[0].content == "Python is a great language"
        assert result[1].content == "FastAPI powers our backend"

    def test_keyword_exclude_removes_matching(self):
        """2 chunks, 1 has 'advertisement' -> 1 kept."""
        op = FilterContentOperator(keywords_exclude=["advertisement"])
        ctx = _make_graph_ctx()
        chunks = [
            _make_chunk("Important technical discussion about APIs", index=0),
            _make_chunk("This is an advertisement for a product", index=1),
        ]

        result = op.process(chunks, ctx)

        assert len(result) == 1
        assert result[0].content == "Important technical discussion about APIs"

    def test_min_length_filter(self):
        """'Short' (5 chars) vs long chunk with min_length=20 -> short removed."""
        op = FilterContentOperator(min_length=20)
        ctx = _make_graph_ctx()
        chunks = [
            _make_chunk("Short", index=0),
            _make_chunk("This is a sufficiently long chunk of content", index=1),
        ]

        result = op.process(chunks, ctx)

        assert len(result) == 1
        assert result[0].content == "This is a sufficiently long chunk of content"

    def test_no_filters_passes_all(self):
        """No filters configured -> all chunks pass through."""
        op = FilterContentOperator()
        ctx = _make_graph_ctx()
        chunks = [
            _make_chunk("First chunk", index=0),
            _make_chunk("Second chunk", index=1),
            _make_chunk("Third chunk", index=2),
        ]

        result = op.process(chunks, ctx)

        assert len(result) == 3
