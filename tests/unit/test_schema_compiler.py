"""Tests for SchemaCompiler — compiles EnhancedSchema into stage-specific rule sets."""

import pytest

from contextcore.schema.sdl import EnhancedSchema
from contextcore.schema.compiler import (
    CompiledSchema,
    ExtractionPlan,
    SchemaCompiler,
    ValidationRules,
)


HEALTHCARE_RAW = {
    "name": "healthcare",
    "version": "2.0",
    "node_types": {
        "Condition": {
            "fields": {
                "name": {"type": "string", "required": True, "indexed": True},
                "severity": {
                    "type": "enum",
                    "values": ["mild", "moderate", "severe", "critical"],
                },
            },
            "dedup": {"key": ["name"], "merge": "merge_properties"},
            "min_confidence": 0.3,
        },
        "Treatment": {
            "fields": {
                "name": {"type": "string", "required": True, "indexed": True},
                "dosage": {"type": "string"},
                "evidence_level": {
                    "type": "enum",
                    "values": [
                        "meta_analysis",
                        "clinical_trial",
                        "case_study",
                        "anecdotal",
                    ],
                },
            },
            "dedup": {"key": ["name", "dosage"], "merge": "latest_wins"},
            "min_confidence": 0.4,
        },
    },
    "edge_types": {
        "TREATED_WITH": {
            "source": "Condition",
            "target": "Treatment",
            "cardinality": "many_to_many",
            "properties": {"efficacy": {"type": "float", "range": [0, 1]}},
        },
    },
    "derivation_rules": [
        {
            "when": {
                "node": "Condition",
                "has_edge": "TREATED_WITH",
                "count": ">3",
            },
            "action": "create_cu",
            "params": {"topic_from": "Condition.name"},
        },
    ],
}


@pytest.fixture
def healthcare_schema():
    return EnhancedSchema.from_dict(HEALTHCARE_RAW)


@pytest.fixture
def compiled(healthcare_schema):
    return SchemaCompiler.compile(healthcare_schema)


# ── Basic compilation ─────────────────────────────────────────────────


def test_compile_returns_compiled_schema(compiled):
    assert isinstance(compiled, CompiledSchema)
    assert compiled.name == "healthcare"
    assert compiled.version == "2.0"


# ── ExtractionPlan ────────────────────────────────────────────────────


def test_extraction_plan_has_node_types(compiled):
    plan = compiled.extraction_plan
    assert isinstance(plan, ExtractionPlan)
    assert "Condition" in plan.allowed_types
    assert "Treatment" in plan.allowed_types
    assert len(plan.allowed_types) == 2


def test_extraction_plan_has_required_fields(compiled):
    plan = compiled.extraction_plan
    assert "Condition" in plan.required_fields
    assert "name" in plan.required_fields["Condition"]
    assert "Treatment" in plan.required_fields
    assert "name" in plan.required_fields["Treatment"]


def test_extraction_plan_has_optional_fields(compiled):
    plan = compiled.extraction_plan
    assert "severity" in plan.optional_fields["Condition"]
    assert "dosage" in plan.optional_fields["Treatment"]
    assert "evidence_level" in plan.optional_fields["Treatment"]


def test_extraction_plan_has_edges(compiled):
    plan = compiled.extraction_plan
    assert "TREATED_WITH" in plan.allowed_edges
    assert plan.edge_source_target["TREATED_WITH"] == ("Condition", "Treatment")


# ── ValidationRules ───────────────────────────────────────────────────


def test_validation_rules_has_field_validators(compiled):
    rules = compiled.validation_rules
    assert isinstance(rules, ValidationRules)
    assert "Condition" in rules.field_validators
    assert "name" in rules.field_validators["Condition"]
    field_def = rules.field_validators["Condition"]["name"]
    assert field_def.type == "string"
    assert field_def.required is True


def test_validation_rules_has_min_confidence(compiled):
    rules = compiled.validation_rules
    assert rules.min_confidence["Condition"] == 0.3
    assert rules.min_confidence["Treatment"] == 0.4


def test_validation_rules_strict_mode_default(compiled):
    assert compiled.validation_rules.strict_mode is False


def test_validation_rules_strict_mode_enabled():
    raw = dict(HEALTHCARE_RAW, strict_mode=True)
    schema = EnhancedSchema.from_dict(raw)
    compiled = SchemaCompiler.compile(schema)
    assert compiled.validation_rules.strict_mode is True


# ── Dedup strategies ──────────────────────────────────────────────────


def test_dedup_strategies_per_type(compiled):
    assert "Condition" in compiled.dedup_strategies
    assert compiled.dedup_strategies["Condition"].key == ["name"]
    assert compiled.dedup_strategies["Condition"].merge == "merge_properties"

    assert "Treatment" in compiled.dedup_strategies
    assert compiled.dedup_strategies["Treatment"].key == ["name", "dosage"]
    assert compiled.dedup_strategies["Treatment"].merge == "latest_wins"


# ── Edge rules ────────────────────────────────────────────────────────


def test_edge_rules_compiled(compiled):
    assert "TREATED_WITH" in compiled.edge_rules
    edge = compiled.edge_rules["TREATED_WITH"]
    assert edge.source == "Condition"
    assert edge.target == "Treatment"
    assert edge.cardinality == "many_to_many"
    assert "efficacy" in edge.properties


# ── Derivation rules ─────────────────────────────────────────────────


def test_derivation_rules_preserved(compiled):
    assert len(compiled.derivation_rules) == 1
    rule = compiled.derivation_rules[0]
    assert rule.action == "create_cu"
    assert rule.when["node"] == "Condition"
    assert rule.params["topic_from"] == "Condition.name"


# ── Backward compatibility ───────────────────────────────────────────


def test_backward_compat_legacy_schema():
    """Legacy flat schemas (fields as string lists) should compile correctly."""
    legacy_raw = {
        "name": "legacy_test",
        "version": "1.0",
        "node_types": {
            "Person": {
                "fields": ["name", "age", "role"],
                "required": ["name"],
            },
        },
        "edge_types": {
            "KNOWS": {"source": "Person", "target": "Person"},
        },
    }
    schema = EnhancedSchema.from_dict(legacy_raw)
    compiled = SchemaCompiler.compile(schema)

    assert compiled.name == "legacy_test"
    assert "Person" in compiled.extraction_plan.allowed_types
    assert "name" in compiled.extraction_plan.required_fields["Person"]
    assert "age" in compiled.extraction_plan.optional_fields["Person"]
    assert "role" in compiled.extraction_plan.optional_fields["Person"]
    assert "KNOWS" in compiled.extraction_plan.allowed_edges


def test_to_ingestion_schema(compiled):
    """CompiledSchema.to_ingestion_schema() produces a valid IngestionSchema."""
    from contextcore.ingestion.schema_extractor import IngestionSchema

    ing = compiled.to_ingestion_schema()
    assert isinstance(ing, IngestionSchema)
    assert ing.name == "healthcare"
    assert "Condition" in ing.node_types
    assert "Treatment" in ing.node_types
    assert "name" in ing.node_types["Condition"]["required"]
    assert "severity" in ing.node_types["Condition"]["optional"]
    assert "TREATED_WITH" in ing.edge_types
    assert ing.edge_types["TREATED_WITH"]["from"] == "Condition"
    assert ing.edge_types["TREATED_WITH"]["to"] == "Treatment"


# ── Edge cases ────────────────────────────────────────────────────────


def test_compile_empty_schema():
    """Empty schema should compile without errors."""
    schema = EnhancedSchema.from_dict({"name": "empty", "version": "0.1"})
    compiled = SchemaCompiler.compile(schema)
    assert compiled.name == "empty"
    assert compiled.extraction_plan.allowed_types == []
    assert compiled.derivation_rules == []


def test_extraction_plan_descriptions():
    """Node type descriptions should appear in extraction plan."""
    raw = {
        "name": "desc_test",
        "node_types": {
            "Widget": {
                "description": "A mechanical widget",
                "fields": {"name": {"type": "string", "required": True}},
            },
        },
    }
    schema = EnhancedSchema.from_dict(raw)
    compiled = SchemaCompiler.compile(schema)
    assert compiled.extraction_plan.descriptions["Widget"] == "A mechanical widget"
