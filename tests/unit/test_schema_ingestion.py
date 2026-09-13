"""Tests for schema-based ingestion utilities."""

import pytest
from dataclasses import dataclass
from typing import Dict, Any, Optional

from contextcore.core.graph_structures import GraphNode
from contextcore.ingestion.dedup import dedup_check, dedup_check_batch, _keywords, _keyword_overlap


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

class FakeCsrAdapter:
    """Minimal stub that satisfies db.csr_adapter.get_all_nodes()."""

    def __init__(self, nodes):
        self._nodes = nodes

    def get_all_nodes(self):
        return self._nodes


class FakeDB:
    def __init__(self, nodes):
        self.csr_adapter = FakeCsrAdapter(nodes)


@pytest.fixture
def populated_db():
    """Graph with Person('Alice Smith') and Fact('Redis is fast')."""
    nodes = [
        GraphNode(id="n1", label="Person", properties={"name": "Alice Smith"}),
        GraphNode(id="n2", label="Fact", properties={"name": "Redis is fast"}),
    ]
    return FakeDB(nodes)


# ---------------------------------------------------------------------------
# TestDedupCheck
# ---------------------------------------------------------------------------

class TestDedupCheck:
    """Dedup detection: exact, fuzzy, and no-match cases."""

    def test_exact_match_returns_existing_id(self, populated_db):
        node = {"label": "Person", "properties": {"name": "alice smith"}}
        result = dedup_check(node, populated_db)
        assert result is not None
        assert result["match_type"] == "exact"
        assert result["existing_id"] == "n1"

    def test_no_match_returns_none(self, populated_db):
        node = {"label": "Person", "properties": {"name": "Bob Jones"}}
        result = dedup_check(node, populated_db)
        assert result is None

    def test_fuzzy_match_returns_candidate(self, populated_db):
        node = {"label": "Person", "properties": {"name": "Alice J. Smith"}}
        result = dedup_check(node, populated_db, fuzzy_threshold=0.5)
        assert result is not None
        assert result["match_type"] == "fuzzy"
        assert result["existing_id"] == "n1"

    def test_different_label_no_match(self, populated_db):
        node = {"label": "Fact", "properties": {"name": "Alice Smith"}}
        result = dedup_check(node, populated_db)
        # Same name but different label — not a match
        assert result is None

    def test_batch_adds_dedup_field(self, populated_db):
        nodes = [
            {"label": "Person", "properties": {"name": "alice smith"}},
            {"label": "Person", "properties": {"name": "Unknown Person"}},
        ]
        results = dedup_check_batch(nodes, populated_db)
        assert results[0]["_dedup_match"] is not None
        assert results[0]["_dedup_match"]["match_type"] == "exact"
        assert results[1]["_dedup_match"] is None

    def test_keywords_helper(self):
        assert _keywords("Alice J. Smith") == {"alice", "j", "smith"}
        assert _keywords("") == set()

    def test_keyword_overlap_identical(self):
        assert _keyword_overlap("hello world", "hello world") == 1.0

    def test_keyword_overlap_partial(self):
        overlap = _keyword_overlap("Alice Smith", "Alice J. Smith")
        # 2 shared out of union of 3 → 0.666…
        assert overlap > 0.6


# ---------------------------------------------------------------------------
# TestValidateAndFlag
# ---------------------------------------------------------------------------

from contextcore.ingestion.schema_validator import validate_and_flag


class _MockSchema:
    def __init__(self, node_types):
        self.node_types = node_types


class TestValidateAndFlag:
    """validate_and_flag: schema validation with confidence penalties."""

    def _make_schema(self):
        return _MockSchema({
            "Person": {"required": ["name"], "optional": ["role"], "description": "A person"},
            "Event": {"required": ["title", "date"], "optional": [], "description": "An event"},
        })

    def test_valid_node_gets_valid_status(self):
        schema = self._make_schema()
        node = {
            "label": "Person",
            "properties": {"name": "Alice", "confidence": 1.0},
        }
        result = validate_and_flag(node, schema)
        assert result["properties"]["validation_status"] == "valid"
        assert result["properties"]["validation_errors"] == []
        assert result["properties"]["confidence"] == 1.0

    def test_missing_required_field_flags_and_penalizes(self):
        schema = self._make_schema()
        node = {
            "label": "Person",
            "properties": {"role": "engineer", "confidence": 1.0},
        }
        result = validate_and_flag(node, schema)
        assert result["properties"]["validation_status"] == "missing_fields"
        assert "name" in result["properties"]["_missing_fields"]
        assert len(result["properties"]["validation_errors"]) > 0
        # confidence should drop by *0.7
        assert result["properties"]["confidence"] == pytest.approx(0.7)

    def test_unknown_type_flags_and_penalizes(self):
        schema = self._make_schema()
        node = {
            "label": "Spaceship",
            "properties": {"name": "Falcon", "confidence": 1.0},
        }
        result = validate_and_flag(node, schema)
        assert result["properties"]["validation_status"] == "unknown_type"
        assert result["properties"]["confidence"] <= 0.5


# ---------------------------------------------------------------------------
# TestIngestionSchema
# ---------------------------------------------------------------------------

from contextcore.core.registry import GraphRegistry
from contextcore.ingestion.schema_extractor import (
    DEFAULT_EDGE_TYPES,
    DEFAULT_NODE_TYPES,
    IngestionSchema,
    load_schema,
)


class TestIngestionSchema:
    """Tests for IngestionSchema dataclass and load_schema helper."""

    def _make_db(self, name: str = "test_schema"):
        registry = GraphRegistry()
        return registry.create_graph(name)

    def test_load_schema_returns_default_when_no_schema(self):
        """Graph without schema config gets permissive default."""
        db = self._make_db("no_schema_graph")
        schema = load_schema(db)

        assert isinstance(schema, IngestionSchema)
        assert schema.name == "default"
        # Should contain well-known node types
        for nt in ("Knowledge", "Fact", "Entity", "Person", "Document"):
            assert nt in schema.node_types, f"Missing node type {nt}"
        # Edge types should also be populated
        assert len(schema.edge_types) > 0

    def test_load_schema_from_context_meta(self):
        """Graph with schema property on _context_meta loads it."""
        db = self._make_db("custom_schema_graph")

        # Inject a custom schema into _context_meta
        custom = {
            "name": "news",
            "node_types": {
                "Article": {"required": ["title"], "optional": ["url", "summary"]},
            },
            "edge_types": {
                "CITES": {"source": "Article", "target": "Article"},
            },
        }
        import json

        meta = db.get_node("_context_meta")
        assert meta is not None, "_context_meta must exist"
        meta.properties["schema"] = json.dumps(custom)
        db.node_properties["_context_meta"] = meta.properties

        schema = load_schema(db)
        assert schema.name == "news"
        assert "Article" in schema.node_types
        assert schema.node_types["Article"]["required"] == ["title"]
        assert "CITES" in schema.edge_types

    def test_ingestion_schema_has_node_types_and_edge_types(self):
        """Default schema has expected structure with required/optional."""
        schema = IngestionSchema.default()

        assert isinstance(schema.node_types, dict)
        assert isinstance(schema.edge_types, dict)

        # Every node type entry must have 'required' and 'optional' lists
        for nt, spec in schema.node_types.items():
            assert "required" in spec, f"{nt} missing 'required'"
            assert "optional" in spec, f"{nt} missing 'optional'"
            assert isinstance(spec["required"], list)
            assert isinstance(spec["optional"], list)
            # All types require at least 'name'
            assert "name" in spec["required"], f"{nt} must require 'name'"

        # Check that DEFAULT_NODE_TYPES matches
        assert schema.node_types == DEFAULT_NODE_TYPES
        assert schema.edge_types == DEFAULT_EDGE_TYPES

    def test_load_schema_with_content_type_filter(self):
        """load_schema accepts content_type param (future filtering)."""
        db = self._make_db("ct_graph")
        schema = load_schema(db, content_type="news")
        # Should still return a valid schema even if content_type is unused
        assert isinstance(schema, IngestionSchema)
        assert schema.content_type == "news"


# ===========================================================================
# Group B: Extraction
# ===========================================================================

from contextcore.ingestion.schema_extractor import (
    SchemaPatternGenerator, ExtractionPattern,
    regex_extract, build_llm_prompt, llm_extract, merge_extractions,
)


class TestSchemaPatternGenerator:
    def test_generates_name_patterns(self):
        schema = IngestionSchema(
            name="test",
            node_types={"Person": {"required": ["name"], "optional": ["role", "email"]}},
            edge_types={},
        )
        gen = SchemaPatternGenerator(schema)
        patterns = gen.generate()
        person_patterns = [p for p in patterns if p.target_type == "Person"]
        assert len(person_patterns) >= 1

    def test_generates_email_pattern(self):
        schema = IngestionSchema(
            name="test",
            node_types={"Person": {"required": ["name"], "optional": ["email"]}},
            edge_types={},
        )
        gen = SchemaPatternGenerator(schema)
        patterns = gen.generate()
        email_patterns = [p for p in patterns if p.target_field == "email"]
        assert len(email_patterns) >= 1

    def test_generates_date_pattern(self):
        schema = IngestionSchema(
            name="test",
            node_types={"Event": {"required": ["name"], "optional": ["date"]}},
            edge_types={},
        )
        gen = SchemaPatternGenerator(schema)
        patterns = gen.generate()
        date_patterns = [p for p in patterns if p.target_field == "date"]
        assert len(date_patterns) >= 1


class TestRegexExtract:
    def test_extracts_person_from_text(self):
        schema = IngestionSchema(
            name="test",
            node_types={"Person": {"required": ["name"], "optional": ["role", "email"]}},
            edge_types={},
        )
        text = "Alice Smith is the lead engineer. Contact her at alice@example.com"
        nodes = regex_extract(text, schema)
        person_nodes = [n for n in nodes if n["label"] == "Person"]
        assert len(person_nodes) >= 1
        assert "Alice Smith" in person_nodes[0]["properties"].get("name", "")

    def test_extracts_email_field(self):
        schema = IngestionSchema(
            name="test",
            node_types={"Person": {"required": ["name"], "optional": ["email"]}},
            edge_types={},
        )
        text = "Contact Alice Smith at alice@example.com for details."
        nodes = regex_extract(text, schema)
        person_nodes = [n for n in nodes if n["label"] == "Person"]
        emails = [n for n in person_nodes if n["properties"].get("email")]
        assert len(emails) >= 1

    def test_returns_empty_for_no_matches(self):
        schema = IngestionSchema(
            name="test",
            node_types={"Person": {"required": ["name"], "optional": []}},
            edge_types={},
        )
        text = "no entities here at all just lowercase words"
        nodes = regex_extract(text, schema)
        assert len(nodes) == 0


class TestLLMExtraction:
    def test_build_llm_prompt_includes_schema_types(self):
        schema = IngestionSchema(
            name="test",
            node_types={
                "Person": {"required": ["name"], "optional": ["role"]},
                "Decision": {"required": ["name", "statement"], "optional": ["rationale"]},
            },
            edge_types={"DECIDED_BY": {"from": "Decision", "to": "Person"}},
        )
        prompt = build_llm_prompt("Some text about Alice.", schema)
        assert "Person" in prompt
        assert "Decision" in prompt
        assert "DECIDED_BY" in prompt

    def test_build_llm_prompt_has_json_instruction(self):
        schema = IngestionSchema.default()
        prompt = build_llm_prompt("Any text.", schema)
        assert "JSON" in prompt or "json" in prompt

    def test_llm_extract_without_llm_returns_empty(self):
        schema = IngestionSchema.default()
        nodes, edges = llm_extract("Some text", schema, llm_fn=None)
        assert nodes == []
        assert edges == []

    def test_llm_extract_with_mock_llm(self):
        schema = IngestionSchema(
            name="test",
            node_types={"Person": {"required": ["name"], "optional": ["role"]}},
            edge_types={"WORKS_AT": {"from": "Person", "to": "Organization"}},
        )
        import json
        mock_response = json.dumps({
            "entities": [{"type": "Person", "name": "Bob Jones", "role": "CTO"}],
            "relationships": [{"type": "WORKS_AT", "source": "Bob Jones", "target": "Acme Corp"}],
        })
        nodes, edges = llm_extract("Bob Jones is the CTO.", schema, llm_fn=lambda p: mock_response)
        assert len(nodes) == 1
        assert nodes[0]["label"] == "Person"
        assert nodes[0]["properties"]["name"] == "Bob Jones"
        assert len(edges) == 1
        assert edges[0]["label"] == "WORKS_AT"


class TestMergeExtractions:
    def test_merge_fills_gaps(self):
        phase1 = [{"label": "Person", "properties": {"name": "Alice", "confidence": 0.7, "_extraction_method": "regex"}}]
        phase2 = [{"label": "Person", "properties": {"name": "Alice", "role": "CEO", "confidence": 0.85, "_extraction_method": "llm"}}]
        merged = merge_extractions(phase1, phase2)
        alice = [n for n in merged if n["properties"]["name"] == "Alice"]
        assert len(alice) == 1
        assert alice[0]["properties"]["role"] == "CEO"
        assert alice[0]["properties"]["_extraction_method"] == "merged"

    def test_merge_adds_new_from_llm(self):
        phase1 = [{"label": "Person", "properties": {"name": "Alice", "confidence": 0.7}}]
        phase2 = [{"label": "Organization", "properties": {"name": "Acme Corp", "confidence": 0.85}}]
        merged = merge_extractions(phase1, phase2)
        assert len(merged) == 2

    def test_merge_keeps_higher_confidence(self):
        phase1 = [{"label": "Fact", "properties": {"name": "Revenue up", "statement": "Revenue increased 20%", "confidence": 0.7}}]
        phase2 = [{"label": "Fact", "properties": {"name": "Revenue up", "statement": "Revenue increased 20% YoY", "confidence": 0.85}}]
        merged = merge_extractions(phase1, phase2)
        assert len(merged) == 1
        assert "YoY" in merged[0]["properties"]["statement"]


# ===========================================================================
# Group C: Integration
# ===========================================================================

from contextcore.tools.registry import ToolRegistry, ToolContext
from contextcore.adapters._base import AIContextDBConnection


class TestCreateGraphWithSchema:
    def test_create_graph_stores_schema_in_context_meta(self):
        import uuid as _uuid
        unique_name = f"test_schema_graph_{_uuid.uuid4().hex[:8]}"
        registry = GraphRegistry()
        schema_def = {
            "node_types": {
                "Person": {"required": ["name"], "optional": ["role"]},
            },
            "edge_types": {},
        }
        db = registry.create_graph(unique_name, config={"schema": schema_def})
        meta = db.csr_adapter.get_node("_context_meta")
        assert meta is not None, f"No _context_meta node in {unique_name}"
        assert meta.properties.get("schema") is not None, f"No schema property on _context_meta"
        assert "Person" in meta.properties["schema"]["node_types"]


class TestAddKnowledgeValidation:
    @pytest.fixture
    def ctx_with_schema(self):
        registry = GraphRegistry()
        schema_def = {
            "node_types": {
                "Fact": {"required": ["name"], "optional": ["statement"]},
                "Person": {"required": ["name"], "optional": ["role"]},
            },
            "edge_types": {},
        }
        db = registry.create_graph("test_ak_val", config={"schema": schema_def})
        conn = AIContextDBConnection(namespace="test_ak_val", graph_registry=registry, contextcore=db)
        return ToolContext(conn=conn, agent_id="test", agent_name="Test")

    def test_valid_node_gets_valid_status(self, ctx_with_schema):
        result = ToolRegistry.dispatch("add_knowledge", ctx_with_schema, {
            "content": "Redis is fast", "node_type": "Fact"
        })
        assert "created" in result.lower() or "added" in result.lower()


class TestSchemaIngestionIntegration:
    def test_full_pipeline(self):
        """E2E: schema -> extract -> validate -> dedup."""
        from contextcore.ingestion.dedup import dedup_check

        registry = GraphRegistry()
        schema_def = {
            "node_types": {
                "Person": {"required": ["name"], "optional": ["role", "email"]},
                "Fact": {"required": ["name"], "optional": ["statement"]},
            },
            "edge_types": {},
        }
        db = registry.create_graph("test_e2e_schema", config={"schema": schema_def})

        # Load schema from graph
        schema = load_schema(db)
        assert "Person" in schema.node_types

        # Extract from text
        text = "Alice Smith is the CEO of Acme Corp. Revenue increased 35% in Q4 2025."
        nodes = regex_extract(text, schema)
        assert len(nodes) >= 1

        # Validate each node
        from contextcore.ingestion.schema_validator import validate_and_flag
        for node in nodes:
            validate_and_flag(node, schema)
        valid_nodes = [n for n in nodes if n["properties"].get("validation_status") == "valid"]
        assert len(valid_nodes) >= 1

        # Persist first batch
        for node in valid_nodes:
            db.add_node(GraphNode(
                id=f"e2e_{node['properties']['name'][:10].lower().replace(' ', '_')}",
                label=node["label"],
                properties=node["properties"],
            ))

        # Dedup check on second extraction
        nodes2 = regex_extract(text, schema)
        found_dedup = False
        for node in nodes2:
            match = dedup_check(node, db)
            if match is not None:
                found_dedup = True
                assert match["match_type"] in ("exact", "fuzzy")
        # At least one node should be detected as duplicate
        assert found_dedup, "Expected at least one duplicate detected after re-extraction"

        # Verify quality signals on persisted nodes
        all_nodes = db.csr_adapter.get_all_nodes()
        content_nodes = [n for n in all_nodes
                        if getattr(n, 'node_type', getattr(n, 'label', '')) in ("Person", "Fact")]
        assert len(content_nodes) >= 1, "Should have persisted at least one content node"
        for n in content_nodes:
            assert n.properties.get("validation_status") is not None
            assert n.properties.get("confidence") is not None
            assert n.properties.get("_extraction_method") is not None
