"""Tests for StageOperator interface + StageExecutor engine."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, call

from contextcore.ingestion.universal.operators.base import StageOperator
from contextcore.ingestion.universal.stage_executor import GraphContext, StageExecutor
from contextcore.ingestion.universal.ingest_content import IngestContent, Chunk


# ── Helpers ─────────────────────────────────────────────────────────────────

class CounterOperator(StageOperator):
    """Stub operator that counts how many times it was called."""
    name = "counter"

    def __init__(self):
        self.call_count = 0

    def process(self, chunks, graph_ctx):
        self.call_count += 1
        return chunks


class UpperCaseOperator(StageOperator):
    """Transforms chunk content to uppercase."""
    name = "upper"

    def process(self, chunks, graph_ctx):
        for c in chunks:
            c.content = c.content.upper()
        return chunks


class AppendOperator(StageOperator):
    """Appends a new chunk to the list."""
    name = "append"

    def process(self, chunks, graph_ctx):
        chunks.append(Chunk(content="added", index=len(chunks)))
        return chunks


class FailOperator(StageOperator):
    """Always raises."""
    name = "fail"

    def process(self, chunks, graph_ctx):
        raise RuntimeError("boom")


def _mock_db():
    db = MagicMock()
    db.add_node = MagicMock()
    db.add_edge = MagicMock()
    db.get_node = MagicMock(return_value=None)
    return db


# ── TestStageOperator ──────────────────────────────────────────────────────

class TestStageOperator:
    def test_interface_enforced(self):
        """Cannot instantiate abstract StageOperator directly."""
        with pytest.raises(TypeError):
            StageOperator()

    def test_counter_operator(self):
        op = CounterOperator()
        chunks = [Chunk(content="a", index=0)]
        result = op.process(chunks, None)
        assert op.call_count == 1
        assert result is chunks

    def test_name_attribute(self):
        assert CounterOperator.name == "counter"
        assert UpperCaseOperator.name == "upper"


# ── TestGraphContext ───────────────────────────────────────────────────────

class TestGraphContext:
    def test_add_node_tracks_ids(self):
        db = _mock_db()
        ctx = GraphContext(db=db, namespace="test")
        nid = ctx.add_node("Person", {"name": "Alice"})
        assert nid in ctx.node_ids
        assert db.add_node.call_count == 1

    def test_add_node_registers_entity(self):
        db = _mock_db()
        ctx = GraphContext(db=db)
        nid = ctx.add_node("Person", {"name": "Bob"})
        assert ctx.find_entity("Person", "Bob") == nid
        assert ctx.find_entity("Person", "  BOB  ") == nid  # case-insensitive

    def test_add_node_custom_id(self):
        db = _mock_db()
        ctx = GraphContext(db=db)
        nid = ctx.add_node("Org", {"name": "ACME"}, node_id="org_custom")
        assert nid == "org_custom"

    def test_add_edge_increments_count(self):
        db = _mock_db()
        ctx = GraphContext(db=db)
        ctx.add_edge("n1", "n2", "KNOWS")
        ctx.add_edge("n2", "n3", "WORKS_AT")
        assert ctx.edge_count == 2
        assert db.add_edge.call_count == 2

    def test_find_entity_missing(self):
        ctx = GraphContext(db=_mock_db())
        assert ctx.find_entity("Person", "nobody") is None

    def test_get_node_delegates_to_db(self):
        db = _mock_db()
        sentinel = object()
        db.get_node.return_value = sentinel
        ctx = GraphContext(db=db)
        assert ctx.get_node("n1") is sentinel
        db.get_node.assert_called_once_with("n1")

    def test_build_result(self):
        db = _mock_db()
        ctx = GraphContext(db=db, namespace="ns")
        ctx.add_node("X", {"name": "a"})
        ctx.add_edge("x1", "x2", "R")
        result = ctx.build_result()
        assert result.edge_count == 1
        assert len(result.entity_ids) == 1
        assert result.errors == []

    def test_build_result_with_errors(self):
        ctx = GraphContext(db=_mock_db())
        ctx.errors.append("oops")
        result = ctx.build_result()
        assert result.errors == ["oops"]


# ── TestStageExecutor ──────────────────────────────────────────────────────

class TestStageExecutor:
    def test_empty_operators(self):
        db = _mock_db()
        ex = StageExecutor(operators=[], db=db)
        content = IngestContent(content_type="text", text="hello")
        chunks = [Chunk(content="hello", index=0)]
        result = ex.execute(content, chunks)
        assert result.errors == []
        assert result.edge_count == 0

    def test_operator_ordering(self):
        """Operators run in order: upper then append."""
        db = _mock_db()
        ex = StageExecutor(operators=[UpperCaseOperator(), AppendOperator()], db=db)
        content = IngestContent(content_type="text", text="x")
        chunks = [Chunk(content="hello", index=0)]
        result = ex.execute(content, chunks)
        # After upper: chunk[0].content == "HELLO"
        # After append: chunk[1].content == "added" (not uppercased)
        assert chunks[0].content == "HELLO"
        assert chunks[1].content == "added"
        assert result.errors == []

    def test_conversation_creates_session_node(self):
        db = _mock_db()
        ex = StageExecutor(operators=[CounterOperator()], db=db)
        content = IngestContent(content_type="conversation", title="My Chat", source="slack")
        chunks = [Chunk(content="msg1", index=0)]
        result = ex.execute(content, chunks)
        # Session node was created
        assert db.add_node.call_count == 1
        node_arg = db.add_node.call_args[0][0]
        assert node_arg.label == "Session"
        assert node_arg.properties["title"] == "My Chat"

    def test_non_conversation_no_session(self):
        db = _mock_db()
        ex = StageExecutor(operators=[], db=db)
        content = IngestContent(content_type="text", text="plain text")
        ex.execute(content, [])
        assert db.add_node.call_count == 0

    def test_operator_failure_captured(self):
        db = _mock_db()
        counter = CounterOperator()
        ex = StageExecutor(operators=[FailOperator(), counter], db=db)
        content = IngestContent(content_type="text")
        chunks = [Chunk(content="x", index=0)]
        result = ex.execute(content, chunks)
        # Error was captured
        assert len(result.errors) == 1
        assert "boom" in result.errors[0]
        # Second operator still ran
        assert counter.call_count == 1

    def test_multiple_operators_all_called(self):
        ops = [CounterOperator() for _ in range(5)]
        ex = StageExecutor(operators=ops, db=_mock_db())
        ex.execute(IngestContent(content_type="text"), [Chunk(content="x", index=0)])
        for op in ops:
            assert op.call_count == 1
