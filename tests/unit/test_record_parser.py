"""Tests for the RecordParser."""
from __future__ import annotations

import json
import pytest

from contextcore.ingestion.universal.parsers.record_parser import RecordParser, RecordMeta
from contextcore.ingestion.universal.ingest_content import Chunk


class TestRecordParserJSON:
    def test_parses_json_array(self):
        data = json.dumps([
            {"name": "Alice", "role": "Engineer", "team": "Platform"},
            {"name": "Bob", "role": "Designer", "team": "Product"},
        ])
        parser = RecordParser()
        meta, chunks = parser.parse(data, record_type="Person")
        assert meta.record_count == 2
        assert meta.format == "json_array"
        assert len(chunks) == 2
        assert chunks[0].chunk_type == "record"
        assert chunks[0].metadata["record_type"] == "Person"
        assert "Alice" in chunks[0].content

    def test_parses_jsonl(self):
        data = '{"name": "Alice", "role": "Engineer"}\n{"name": "Bob", "role": "Designer"}\n'
        parser = RecordParser()
        meta, chunks = parser.parse(data, record_type="Employee")
        assert meta.record_count == 2
        assert meta.format == "jsonl"

    def test_parses_dict_list(self):
        records = [
            {"name": "Alice", "role": "Engineer"},
            {"name": "Bob", "role": "Designer"},
        ]
        parser = RecordParser()
        meta, chunks = parser.parse(records, record_type="Employee")
        assert meta.record_count == 2
        assert len(chunks) == 2


class TestRecordParserCSV:
    def test_parses_csv_string(self):
        data = "name,role,team\nAlice,Engineer,Platform\nBob,Designer,Product\n"
        parser = RecordParser()
        meta, chunks = parser.parse(data, record_type="Employee")
        assert meta.record_count == 2
        assert meta.format == "csv"
        assert meta.fields == ["name", "role", "team"]
        assert chunks[0].metadata["record_type"] == "Employee"

    def test_detects_tab_delimiter(self):
        data = "name\trole\nAlice\tEngineer\nBob\tDesigner\n"
        parser = RecordParser()
        meta, chunks = parser.parse(data, record_type="Employee")
        assert meta.record_count == 2
        assert meta.format == "csv"

    def test_auto_detects_record_type_from_fields(self):
        data = json.dumps([{"title": "Bug #1", "status": "open", "priority": "high"}])
        parser = RecordParser()
        meta, chunks = parser.parse(data)
        assert meta.record_type != ""


class TestRecordParserEdgeCases:
    def test_empty_input_returns_empty(self):
        parser = RecordParser()
        meta, chunks = parser.parse("[]")
        assert meta.record_count == 0
        assert len(chunks) == 0

    def test_single_record(self):
        parser = RecordParser()
        meta, chunks = parser.parse([{"name": "Alice"}], record_type="Person")
        assert meta.record_count == 1
        assert len(chunks) == 1

    def test_invalid_json_raises(self):
        parser = RecordParser()
        with pytest.raises(ValueError):
            parser.parse("{not valid json at all", record_type="X")

    def test_to_chunks_preserves_field_data(self):
        parser = RecordParser()
        meta, chunks = parser.parse(
            [{"name": "Alice", "age": 30, "role": "Engineer"}],
            record_type="Person",
        )
        assert chunks[0].metadata["fields"]["name"] == "Alice"
        assert chunks[0].metadata["fields"]["age"] == 30


# ---------------------------------------------------------------------------
# ParseRecordsOperator tests
# ---------------------------------------------------------------------------
from unittest.mock import MagicMock
from contextcore.ingestion.universal.operators.parse_records import ParseRecordsOperator
from contextcore.ingestion.universal.stage_executor import GraphContext


def _make_graph_ctx() -> GraphContext:
    db = MagicMock()
    db.add_node = MagicMock()
    db.add_edge = MagicMock()
    db.get_node = MagicMock(return_value=None)
    return GraphContext(db=db, namespace="test")


class TestParseRecordsOperator:
    def test_creates_nodes_for_records(self):
        op = ParseRecordsOperator()
        ctx = _make_graph_ctx()
        chunks = [
            Chunk(content='name: Alice; role: Engineer', index=0, chunk_type="record",
                  metadata={"record_type": "Person", "fields": {"name": "Alice", "role": "Engineer"}}),
            Chunk(content='name: Bob; role: Designer', index=1, chunk_type="record",
                  metadata={"record_type": "Person", "fields": {"name": "Bob", "role": "Designer"}}),
        ]
        result = op.process(chunks, ctx)
        assert len(result) == 2
        assert ctx.db.add_node.call_count == 2
        assert "record_node_id" in result[0].metadata
        assert "record_node_id" in result[1].metadata

    def test_skips_non_record_chunks(self):
        op = ParseRecordsOperator()
        ctx = _make_graph_ctx()
        chunks = [Chunk(content="Just a paragraph", index=0, chunk_type="paragraph")]
        result = op.process(chunks, ctx)
        assert len(result) == 1
        assert ctx.db.add_node.call_count == 0

    def test_uses_name_field_as_node_name(self):
        op = ParseRecordsOperator()
        ctx = _make_graph_ctx()
        chunks = [
            Chunk(content='name: Kubernetes; category: orchestration', index=0, chunk_type="record",
                  metadata={"record_type": "Technology", "fields": {"name": "Kubernetes", "category": "orchestration"}}),
        ]
        op.process(chunks, ctx)
        call_args = ctx.db.add_node.call_args
        node = call_args[0][0]  # GraphNode
        assert node.label == "Technology"
        assert node.properties["name"] == "Kubernetes"

    def test_fallback_name_from_title(self):
        op = ParseRecordsOperator()
        ctx = _make_graph_ctx()
        chunks = [
            Chunk(content='title: My Doc', index=0, chunk_type="record",
                  metadata={"record_type": "Document", "fields": {"title": "My Doc", "pages": 10}}),
        ]
        op.process(chunks, ctx)
        call_args = ctx.db.add_node.call_args
        node = call_args[0][0]
        assert node.properties["name"] == "My Doc"

    def test_fallback_name_from_first_field(self):
        op = ParseRecordsOperator()
        ctx = _make_graph_ctx()
        chunks = [
            Chunk(content='category: orchestration', index=0, chunk_type="record",
                  metadata={"record_type": "Thing", "fields": {"category": "orchestration", "level": 5}}),
        ]
        op.process(chunks, ctx)
        call_args = ctx.db.add_node.call_args
        node = call_args[0][0]
        assert node.properties["name"] == "orchestration"

    def test_truncates_long_fallback_name(self):
        op = ParseRecordsOperator()
        ctx = _make_graph_ctx()
        long_val = "x" * 200
        chunks = [
            Chunk(content='data: ...', index=0, chunk_type="record",
                  metadata={"record_type": "Thing", "fields": {"data": long_val}}),
        ]
        op.process(chunks, ctx)
        call_args = ctx.db.add_node.call_args
        node = call_args[0][0]
        assert len(node.properties["name"]) <= 80


class TestOperatorRegistry:
    def test_parse_records_in_registry(self):
        from contextcore.ingestion.universal._operator_registry import resolve_operators
        plan = MagicMock()
        plan.stages = ["parse_records"]
        plan.signals = None
        plan.significance_threshold = 0.2
        plan.topic_method = "keyword"
        ops = resolve_operators(plan)
        assert len(ops) == 1
        assert ops[0].name == "parse_records"
