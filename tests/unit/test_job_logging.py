"""Tests for job logging in GraphContext."""
from __future__ import annotations
import pytest
from unittest.mock import MagicMock
from contextcore.ingestion.universal.stage_executor import GraphContext


class TestGraphContextLogging:
    def test_log_stores_entries(self):
        db = MagicMock()
        ctx = GraphContext(db=db, namespace="test")
        ctx.log("Processing chunk 1")
        ctx.log("Processing chunk 2")
        assert len(ctx.log_entries) == 2
        assert ctx.log_entries[0]["message"] == "Processing chunk 1"

    def test_log_debug_skipped_when_not_debug(self):
        db = MagicMock()
        ctx = GraphContext(db=db, namespace="test")
        ctx.log_debug("LLM prompt: hello")
        assert len(ctx.log_entries) == 0

    def test_log_debug_captured_when_debug_enabled(self):
        db = MagicMock()
        ctx = GraphContext(db=db, namespace="test", debug=True)
        ctx.log_debug("LLM prompt: hello")
        assert len(ctx.log_entries) == 1
        assert ctx.log_entries[0]["level"] == "debug"

    def test_log_includes_active_stage(self):
        db = MagicMock()
        ctx = GraphContext(db=db, namespace="test")
        ctx._active_stage = "extract_entities"
        ctx.log("Found 5 entities")
        assert ctx.log_entries[0]["stage"] == "extract_entities"

    def test_stage_complete_records_metrics(self):
        db = MagicMock()
        ctx = GraphContext(db=db, namespace="test")
        ctx._active_stage = "extract_entities"
        ctx.stage_complete(items=42, nodes_created=10, edges_created=5)
        assert ctx.stage_metrics["extract_entities"]["items"] == 42
        assert ctx.stage_metrics["extract_entities"]["nodes_created"] == 10
