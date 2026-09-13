"""Tests for SchemaManager and Schema dataclasses."""
import pytest
import yaml
from pathlib import Path

# We'll import once the module exists
# from contextcore.project.schema_manager import Schema, SchemaManager, get_schema_manager


MINIMAL_SCHEMA = {
    "meta": {"name": "test", "version": "1.0", "description": "Test schema"},
    "node_types": {
        "Document": {
            "description": "A document",
            "properties": {"content": "required", "title": "optional"},
            "id_prefix": "doc",
        },
        "Fact": {
            "description": "A fact",
            "properties": {"content": "required"},
        },
    },
    "relationships": {
        "STATES": {
            "source": "Document",
            "target": "Fact",
            "description": "A document states a fact",
        },
    },
}


class TestSchemaFromDict:
    def test_creates_schema_from_valid_dict(self):
        from contextcore.project.schema_manager import Schema

        schema = Schema.from_dict(MINIMAL_SCHEMA)
        assert schema.meta.name == "test"
        assert schema.meta.version == "1.0"
        assert "Document" in schema.node_types
        assert "Fact" in schema.node_types
        assert "STATES" in schema.relationships

    def test_node_type_has_description_and_properties(self):
        from contextcore.project.schema_manager import Schema

        schema = Schema.from_dict(MINIMAL_SCHEMA)
        doc = schema.node_types["Document"]
        assert doc.description == "A document"
        assert doc.properties == {"content": "required", "title": "optional"}
        assert doc.id_prefix == "doc"

    def test_id_prefix_defaults_to_lowercase_name(self):
        from contextcore.project.schema_manager import Schema

        schema = Schema.from_dict(MINIMAL_SCHEMA)
        fact = schema.node_types["Fact"]
        assert fact.id_prefix == "fact"

    def test_relationship_has_source_target_description(self):
        from contextcore.project.schema_manager import Schema

        schema = Schema.from_dict(MINIMAL_SCHEMA)
        states = schema.relationships["STATES"]
        assert states.source == "Document"
        assert states.target == "Fact"
        assert states.description == "A document states a fact"

    def test_is_valid_node_type(self):
        from contextcore.project.schema_manager import Schema

        schema = Schema.from_dict(MINIMAL_SCHEMA)
        assert schema.is_valid_node_type("Document") is True
        assert schema.is_valid_node_type("CodeModule") is False

    def test_is_valid_edge_type(self):
        from contextcore.project.schema_manager import Schema

        schema = Schema.from_dict(MINIMAL_SCHEMA)
        assert schema.is_valid_edge_type("STATES") is True
        assert schema.is_valid_edge_type("IMPLEMENTS") is False

    def test_validate_node_returns_missing_required_fields(self):
        from contextcore.project.schema_manager import Schema

        schema = Schema.from_dict(MINIMAL_SCHEMA)
        missing = schema.validate_node("Document", {"title": "Hello"})
        assert "content" in missing

    def test_validate_node_returns_empty_when_all_present(self):
        from contextcore.project.schema_manager import Schema

        schema = Schema.from_dict(MINIMAL_SCHEMA)
        missing = schema.validate_node("Document", {"content": "Hello"})
        assert missing == []

    def test_rejects_schema_without_meta(self):
        from contextcore.project.schema_manager import Schema

        bad = {**MINIMAL_SCHEMA}
        del bad["meta"]
        with pytest.raises(ValueError, match="meta"):
            Schema.from_dict(bad)

    def test_rejects_schema_without_node_types(self):
        from contextcore.project.schema_manager import Schema

        bad = {**MINIMAL_SCHEMA}
        del bad["node_types"]
        with pytest.raises(ValueError, match="node_types"):
            Schema.from_dict(bad)

    def test_rejects_schema_without_relationships(self):
        from contextcore.project.schema_manager import Schema

        bad = {**MINIMAL_SCHEMA}
        del bad["relationships"]
        with pytest.raises(ValueError, match="relationships"):
            Schema.from_dict(bad)

    def test_rejects_relationship_referencing_unknown_node_type(self):
        from contextcore.project.schema_manager import Schema

        bad = {**MINIMAL_SCHEMA, "relationships": {
            "BAD": {"source": "Document", "target": "Unknown", "description": "bad"}
        }}
        with pytest.raises(ValueError, match="Unknown"):
            Schema.from_dict(bad)

    def test_wildcard_target_is_allowed(self):
        from contextcore.project.schema_manager import Schema

        data = {**MINIMAL_SCHEMA, "relationships": {
            "HAS": {"source": "Document", "target": "*", "description": "wildcard"}
        }}
        schema = Schema.from_dict(data)
        assert schema.relationships["HAS"].target == "*"

    def test_optional_extensions_default_to_none_or_empty(self):
        from contextcore.project.schema_manager import Schema

        schema = Schema.from_dict(MINIMAL_SCHEMA)
        assert schema.layers is None
        assert schema.sources is None
        assert schema.scanners is None
        assert schema.consumer_hints == {}
        assert schema.processing == {}

    def test_consumer_hint_returns_value_or_default(self):
        from contextcore.project.schema_manager import Schema

        data = {**MINIMAL_SCHEMA, "consumer_hints": {"promotion_labels": ["Fact"]}}
        schema = Schema.from_dict(data)
        assert schema.get_consumer_hint("promotion_labels", []) == ["Fact"]
        assert schema.get_consumer_hint("missing_key", "default") == "default"

    def test_get_processing_returns_section_or_default(self):
        from contextcore.project.schema_manager import Schema

        data = {**MINIMAL_SCHEMA, "processing": {"chunking": {"method": "section"}}}
        schema = Schema.from_dict(data)
        assert schema.get_processing("chunking", {}) == {"method": "section"}
        assert schema.get_processing("missing", {}) == {}

    def test_has_extension(self):
        from contextcore.project.schema_manager import Schema

        data = {**MINIMAL_SCHEMA, "layers": {"intent": {"weight": 0.25}}}
        schema = Schema.from_dict(data)
        assert schema.has_extension("layers") is True
        assert schema.has_extension("scanners") is False


# ── SchemaManager tests ──────────────────────────────────────────────────────

import tempfile
import os


def _write_yaml(dir_path: Path, name: str, data: dict) -> Path:
    """Helper: write a schema YAML to a temp directory."""
    path = dir_path / f"{name}.yaml"
    path.write_text(yaml.dump(data), encoding="utf-8")
    return path


class TestSchemaManager:
    def test_load_schema_from_path(self, tmp_path):
        from contextcore.project.schema_manager import SchemaManager

        _write_yaml(tmp_path, "test", MINIMAL_SCHEMA)
        mgr = SchemaManager(schema_dir=tmp_path)
        schema = mgr.load("test", tmp_path / "test.yaml")
        assert schema.meta.name == "test"
        assert mgr.get("test").meta.name == "test"

    def test_load_all_scans_directory(self, tmp_path):
        from contextcore.project.schema_manager import SchemaManager

        _write_yaml(tmp_path, "alpha", MINIMAL_SCHEMA)
        second = {**MINIMAL_SCHEMA, "meta": {"name": "beta", "version": "1.0", "description": "Beta"}}
        _write_yaml(tmp_path, "beta", second)
        mgr = SchemaManager(schema_dir=tmp_path)
        mgr.load_all()
        assert len(mgr.list_schemas()) == 2

    def test_get_raises_for_unknown_schema(self, tmp_path):
        from contextcore.project.schema_manager import SchemaManager

        mgr = SchemaManager(schema_dir=tmp_path)
        with pytest.raises(KeyError):
            mgr.get("nonexistent")

    def test_bind_and_lookup_namespace(self, tmp_path):
        from contextcore.project.schema_manager import SchemaManager

        _write_yaml(tmp_path, "test", MINIMAL_SCHEMA)
        mgr = SchemaManager(schema_dir=tmp_path)
        mgr.load("test", tmp_path / "test.yaml")
        mgr.bind("my-namespace", "test")
        schema = mgr.for_namespace("my-namespace")
        assert schema is not None
        assert schema.meta.name == "test"

    def test_for_namespace_returns_none_when_unbound(self, tmp_path):
        from contextcore.project.schema_manager import SchemaManager

        mgr = SchemaManager(schema_dir=tmp_path)
        assert mgr.for_namespace("unbound") is None

    def test_unbind_removes_binding(self, tmp_path):
        from contextcore.project.schema_manager import SchemaManager

        _write_yaml(tmp_path, "test", MINIMAL_SCHEMA)
        mgr = SchemaManager(schema_dir=tmp_path)
        mgr.load("test", tmp_path / "test.yaml")
        mgr.bind("ns", "test")
        mgr.unbind("ns")
        assert mgr.for_namespace("ns") is None

    def test_bind_raises_for_unknown_schema(self, tmp_path):
        from contextcore.project.schema_manager import SchemaManager

        mgr = SchemaManager(schema_dir=tmp_path)
        with pytest.raises(KeyError):
            mgr.bind("ns", "nonexistent")

    def test_is_valid_node_type_with_bound_schema(self, tmp_path):
        from contextcore.project.schema_manager import SchemaManager

        _write_yaml(tmp_path, "test", MINIMAL_SCHEMA)
        mgr = SchemaManager(schema_dir=tmp_path)
        mgr.load("test", tmp_path / "test.yaml")
        mgr.bind("ns", "test")
        assert mgr.is_valid_node_type("ns", "Document") is True
        assert mgr.is_valid_node_type("ns", "CodeModule") is False

    def test_is_valid_node_type_true_when_no_schema(self, tmp_path):
        from contextcore.project.schema_manager import SchemaManager

        mgr = SchemaManager(schema_dir=tmp_path)
        # No schema bound = accept anything
        assert mgr.is_valid_node_type("unbound", "Anything") is True

    def test_is_valid_edge_type_with_bound_schema(self, tmp_path):
        from contextcore.project.schema_manager import SchemaManager

        _write_yaml(tmp_path, "test", MINIMAL_SCHEMA)
        mgr = SchemaManager(schema_dir=tmp_path)
        mgr.load("test", tmp_path / "test.yaml")
        mgr.bind("ns", "test")
        assert mgr.is_valid_edge_type("ns", "STATES") is True
        assert mgr.is_valid_edge_type("ns", "IMPLEMENTS") is False

    def test_is_valid_edge_type_true_when_no_schema(self, tmp_path):
        from contextcore.project.schema_manager import SchemaManager

        mgr = SchemaManager(schema_dir=tmp_path)
        assert mgr.is_valid_edge_type("unbound", "ANY_EDGE") is True

    def test_get_consumer_hint(self, tmp_path):
        from contextcore.project.schema_manager import SchemaManager

        data = {**MINIMAL_SCHEMA, "consumer_hints": {"promotion_labels": ["Fact"]}}
        _write_yaml(tmp_path, "test", data)
        mgr = SchemaManager(schema_dir=tmp_path)
        mgr.load("test", tmp_path / "test.yaml")
        mgr.bind("ns", "test")
        assert mgr.get_consumer_hint("ns", "promotion_labels", []) == ["Fact"]
        assert mgr.get_consumer_hint("ns", "missing", "default") == "default"

    def test_get_consumer_hint_returns_default_when_no_schema(self, tmp_path):
        from contextcore.project.schema_manager import SchemaManager

        mgr = SchemaManager(schema_dir=tmp_path)
        assert mgr.get_consumer_hint("unbound", "key", "fallback") == "fallback"

    def test_get_layers(self, tmp_path):
        from contextcore.project.schema_manager import SchemaManager

        data = {**MINIMAL_SCHEMA, "layers": {"intent": {"weight": 0.25, "node_types": ["Document"]}}}
        _write_yaml(tmp_path, "test", data)
        mgr = SchemaManager(schema_dir=tmp_path)
        mgr.load("test", tmp_path / "test.yaml")
        mgr.bind("ns", "test")
        layers = mgr.get_layers("ns")
        assert layers is not None
        assert "intent" in layers

    def test_get_layers_returns_none_when_no_schema(self, tmp_path):
        from contextcore.project.schema_manager import SchemaManager

        mgr = SchemaManager(schema_dir=tmp_path)
        assert mgr.get_layers("unbound") is None

    def test_get_node_types(self, tmp_path):
        from contextcore.project.schema_manager import SchemaManager

        _write_yaml(tmp_path, "test", MINIMAL_SCHEMA)
        mgr = SchemaManager(schema_dir=tmp_path)
        mgr.load("test", tmp_path / "test.yaml")
        mgr.bind("ns", "test")
        types = mgr.get_node_types("ns")
        assert types is not None
        assert "Document" in types
        assert "Fact" in types

    def test_get_node_types_returns_none_when_no_schema(self, tmp_path):
        from contextcore.project.schema_manager import SchemaManager

        mgr = SchemaManager(schema_dir=tmp_path)
        assert mgr.get_node_types("unbound") is None

    def test_get_processing_config(self, tmp_path):
        from contextcore.project.schema_manager import SchemaManager

        data = {**MINIMAL_SCHEMA, "processing": {"search": {"bm25": {"k1": 2.0}}}}
        _write_yaml(tmp_path, "test", data)
        mgr = SchemaManager(schema_dir=tmp_path)
        mgr.load("test", tmp_path / "test.yaml")
        mgr.bind("ns", "test")
        config = mgr.get_processing_config("ns", "search")
        assert config == {"bm25": {"k1": 2.0}}

    def test_reload_picks_up_changes(self, tmp_path):
        from contextcore.project.schema_manager import SchemaManager

        _write_yaml(tmp_path, "test", MINIMAL_SCHEMA)
        mgr = SchemaManager(schema_dir=tmp_path)
        mgr.load("test", tmp_path / "test.yaml")
        assert mgr.get("test").meta.description == "Test schema"

        # Update the file
        updated = {**MINIMAL_SCHEMA, "meta": {**MINIMAL_SCHEMA["meta"], "description": "Updated"}}
        _write_yaml(tmp_path, "test", updated)
        mgr.reload("test")
        assert mgr.get("test").meta.description == "Updated"

    def test_list_schemas_returns_meta(self, tmp_path):
        from contextcore.project.schema_manager import SchemaManager

        _write_yaml(tmp_path, "test", MINIMAL_SCHEMA)
        mgr = SchemaManager(schema_dir=tmp_path)
        mgr.load("test", tmp_path / "test.yaml")
        metas = mgr.list_schemas()
        assert len(metas) == 1
        assert metas[0].name == "test"


# ── Real schema file tests ──────────────────────────────────────────────────

def _real_schema_dir():
    return Path(__file__).resolve().parents[2] / "contextcore" / "project" / "schemas"


class TestKnowledgeSchema:
    def test_knowledge_yaml_loads_successfully(self):
        from contextcore.project.schema_manager import SchemaManager

        mgr = SchemaManager(schema_dir=_real_schema_dir())
        mgr.load_all()
        schema = mgr.get("knowledge")
        assert schema.meta.name == "knowledge"

    def test_knowledge_has_core_node_types(self):
        from contextcore.project.schema_manager import SchemaManager

        mgr = SchemaManager(schema_dir=_real_schema_dir())
        mgr.load_all()
        schema = mgr.get("knowledge")
        for expected in ["Document", "Passage", "Fact", "Entity", "Concept"]:
            assert expected in schema.node_types, f"Missing node type: {expected}"

    def test_knowledge_has_core_relationships(self):
        from contextcore.project.schema_manager import SchemaManager

        mgr = SchemaManager(schema_dir=_real_schema_dir())
        mgr.load_all()
        schema = mgr.get("knowledge")
        for expected in ["HAS_PASSAGE", "STATES", "MENTIONS", "RELATES_TO"]:
            assert expected in schema.relationships, f"Missing relationship: {expected}"

    def test_knowledge_has_consumer_hints(self):
        from contextcore.project.schema_manager import SchemaManager

        mgr = SchemaManager(schema_dir=_real_schema_dir())
        mgr.load_all()
        schema = mgr.get("knowledge")
        assert schema.get_consumer_hint("context_filter_labels", None) is not None
        assert schema.get_consumer_hint("promotion_labels", None) is not None

    def test_sdlc_yaml_still_loads(self):
        from contextcore.project.schema_manager import SchemaManager

        mgr = SchemaManager(schema_dir=_real_schema_dir())
        mgr.load_all()
        schema = mgr.get("sdlc")
        assert schema.meta.name == "sdlc"
        assert "CodeModule" in schema.node_types


class TestSdlcSchemaWrapper:
    """Regression: sdlc_schema.py must export identical values after migration."""

    def test_all_node_types_unchanged(self):
        from contextcore.project.sdlc_schema import ALL_NODE_TYPES
        assert "CodeModule" in ALL_NODE_TYPES
        assert "Requirement" in ALL_NODE_TYPES
        assert "TestCase" in ALL_NODE_TYPES
        assert "ArchDecision" in ALL_NODE_TYPES
        assert "Project" in ALL_NODE_TYPES
        assert len(ALL_NODE_TYPES) >= 15

    def test_sdlc_layers_unchanged(self):
        from contextcore.project.sdlc_schema import SDLC_LAYERS
        assert "intent" in SDLC_LAYERS
        assert "design" in SDLC_LAYERS
        assert "build" in SDLC_LAYERS
        assert "verify" in SDLC_LAYERS
        assert "evolution" in SDLC_LAYERS
        assert SDLC_LAYERS["intent"]["weight"] == 0.25
        assert SDLC_LAYERS["build"]["weight"] == 0.30

    def test_sdlc_edge_types_unchanged(self):
        from contextcore.project.sdlc_schema import SDLC_EDGE_TYPES
        assert "IMPLEMENTS" in SDLC_EDGE_TYPES
        assert "TESTS" in SDLC_EDGE_TYPES
        assert "GOVERNS" in SDLC_EDGE_TYPES
        assert SDLC_EDGE_TYPES["IMPLEMENTS"] == ("CodeModule", "Requirement")

    def test_is_sdlc_node_type_unchanged(self):
        from contextcore.project.sdlc_schema import is_sdlc_node_type
        assert is_sdlc_node_type("CodeModule") is True
        assert is_sdlc_node_type("RandomThing") is False

    def test_validate_node_unchanged(self):
        from contextcore.project.sdlc_schema import validate_node
        missing = validate_node("CodeModule", {"path": "/foo"})
        assert "summary" in missing
        assert "version" in missing

    def test_layer_for_node_type_unchanged(self):
        from contextcore.project.sdlc_schema import layer_for_node_type
        assert layer_for_node_type("CodeModule") == "build"
        assert layer_for_node_type("Requirement") == "intent"
        assert layer_for_node_type("TestCase") == "verify"

    def test_id_prefix_for_unchanged(self):
        from contextcore.project.sdlc_schema import id_prefix_for
        assert id_prefix_for("Requirement") == "req"
        assert id_prefix_for("CodeModule") == "code"

    def test_issue_label_map_unchanged(self):
        from contextcore.project.sdlc_schema import ISSUE_LABEL_MAP
        assert ISSUE_LABEL_MAP.get("bug") == "KnownIssue"
        assert ISSUE_LABEL_MAP.get("feature") == "Requirement"

    def test_get_schema_returns_dict(self):
        from contextcore.project.sdlc_schema import get_schema
        schema = get_schema()
        assert isinstance(schema, dict)
        assert "layers" in schema
        assert "relationships" in schema

    def test_reload_schema_works(self):
        from contextcore.project.sdlc_schema import reload_schema, ALL_NODE_TYPES
        reload_schema()
        assert "CodeModule" in ALL_NODE_TYPES
