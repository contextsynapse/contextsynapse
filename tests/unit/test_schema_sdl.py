"""Tests for the Enhanced Schema Definition Language (SDL).

Covers FieldDef validation, NodeTypeDef, EdgeTypeDef, DerivationRule,
and EnhancedSchema.from_dict with both legacy flat and enhanced dict formats.
"""

import pytest

from contextcore.schema.sdl import (
    DedupPolicy,
    DerivationRule,
    EdgeTypeDef,
    EnhancedSchema,
    FieldDef,
    NodeTypeDef,
)


# ── FieldDef ──────────────────────────────────────────────────────────

class TestFieldDef:

    def test_defaults(self):
        f = FieldDef(name="x")
        assert f.type == "string"
        assert f.required is False
        assert f.indexed is False

    # -- type: string --
    def test_validate_string_accepts_str(self):
        f = FieldDef(name="x", type="string")
        assert f.validate("hello") is True

    def test_validate_string_coerces_non_str(self):
        """Non-string values should still pass basic string validation."""
        f = FieldDef(name="x", type="string")
        assert f.validate(123) is True  # coerced / tolerated

    # -- type: int --
    def test_validate_int(self):
        f = FieldDef(name="age", type="int")
        assert f.validate(30) is True

    def test_validate_int_rejects_string(self):
        f = FieldDef(name="age", type="int")
        assert f.validate("thirty") is False

    # -- type: float --
    def test_validate_float(self):
        f = FieldDef(name="score", type="float")
        assert f.validate(3.14) is True
        assert f.validate(3) is True  # int is acceptable as float

    def test_validate_float_rejects_string(self):
        f = FieldDef(name="score", type="float")
        assert f.validate("abc") is False

    # -- type: bool --
    def test_validate_bool(self):
        f = FieldDef(name="active", type="bool")
        assert f.validate(True) is True
        assert f.validate(False) is True

    def test_validate_bool_rejects_string(self):
        f = FieldDef(name="active", type="bool")
        assert f.validate("yes") is False

    # -- type: enum --
    def test_validate_enum_pass(self):
        f = FieldDef(name="severity", type="enum", enum_values=["mild", "moderate", "severe"])
        assert f.validate("mild") is True
        assert f.validate("severe") is True

    def test_validate_enum_fail(self):
        f = FieldDef(name="severity", type="enum", enum_values=["mild", "moderate", "severe"])
        assert f.validate("critical") is False

    def test_validate_enum_no_values_always_fails(self):
        f = FieldDef(name="severity", type="enum")
        assert f.validate("anything") is False

    # -- pattern --
    def test_validate_pattern_pass(self):
        f = FieldDef(name="email", type="string", pattern=r"^[^@]+@[^@]+\.[^@]+$")
        assert f.validate("user@example.com") is True

    def test_validate_pattern_fail(self):
        f = FieldDef(name="email", type="string", pattern=r"^[^@]+@[^@]+\.[^@]+$")
        assert f.validate("not-an-email") is False

    # -- range --
    def test_validate_range_pass(self):
        f = FieldDef(name="age", type="int", range_min=0, range_max=150)
        assert f.validate(30) is True

    def test_validate_range_too_low(self):
        f = FieldDef(name="age", type="int", range_min=0, range_max=150)
        assert f.validate(-1) is False

    def test_validate_range_too_high(self):
        f = FieldDef(name="age", type="int", range_min=0, range_max=150)
        assert f.validate(200) is False

    def test_validate_range_min_only(self):
        f = FieldDef(name="score", type="float", range_min=0.0)
        assert f.validate(0.0) is True
        assert f.validate(-0.1) is False

    def test_validate_range_max_only(self):
        f = FieldDef(name="score", type="float", range_max=100.0)
        assert f.validate(100.0) is True
        assert f.validate(100.1) is False

    # -- None value --
    def test_validate_none(self):
        f = FieldDef(name="x", type="string")
        assert f.validate(None) is True  # None is acceptable (field is optional)

    # -- default --
    def test_default_value(self):
        f = FieldDef(name="status", type="string", default="active")
        assert f.default == "active"

    # -- date type --
    def test_validate_date(self):
        f = FieldDef(name="created", type="date")
        assert f.validate("2026-01-01") is True
        assert f.validate("not-a-date") is False


# ── DedupPolicy ───────────────────────────────────────────────────────

class TestDedupPolicy:

    def test_defaults(self):
        d = DedupPolicy()
        assert d.key == ["name"]
        assert d.merge == "latest_wins"

    def test_custom(self):
        d = DedupPolicy(key=["name", "type"], merge="merge_properties")
        assert d.key == ["name", "type"]
        assert d.merge == "merge_properties"


# ── NodeTypeDef ───────────────────────────────────────────────────────

class TestNodeTypeDef:

    def test_required_fields_property(self):
        fields = {
            "name": FieldDef(name="name", required=True),
            "age": FieldDef(name="age", required=False),
            "role": FieldDef(name="role", required=True),
        }
        nt = NodeTypeDef(name="Person", fields=fields, dedup=DedupPolicy())
        assert set(nt.required_fields) == {"name", "role"}

    def test_indexed_fields_property(self):
        fields = {
            "name": FieldDef(name="name", indexed=True),
            "age": FieldDef(name="age"),
            "bio": FieldDef(name="bio", indexed=True),
        }
        nt = NodeTypeDef(name="Person", fields=fields, dedup=DedupPolicy())
        assert set(nt.indexed_fields) == {"name", "bio"}

    def test_min_confidence_default(self):
        nt = NodeTypeDef(name="X", fields={}, dedup=DedupPolicy())
        assert nt.min_confidence == 0.0

    def test_description(self):
        nt = NodeTypeDef(name="X", fields={}, dedup=DedupPolicy(), description="A thing")
        assert nt.description == "A thing"

    def test_dedup_policy(self):
        dedup = DedupPolicy(key=["name", "type"], merge="keep_both")
        nt = NodeTypeDef(name="X", fields={}, dedup=dedup)
        assert nt.dedup.key == ["name", "type"]
        assert nt.dedup.merge == "keep_both"


# ── EdgeTypeDef ───────────────────────────────────────────────────────

class TestEdgeTypeDef:

    def test_defaults(self):
        e = EdgeTypeDef(name="RELATES_TO")
        assert e.source == ""
        assert e.target == ""
        assert e.cardinality == "many_to_many"
        assert e.properties == {}

    def test_with_properties(self):
        props = {"weight": FieldDef(name="weight", type="float")}
        e = EdgeTypeDef(name="WORKS_AT", source="Person", target="Org", properties=props)
        assert e.source == "Person"
        assert e.target == "Org"
        assert "weight" in e.properties
        assert e.properties["weight"].type == "float"

    def test_cardinality(self):
        e = EdgeTypeDef(name="HAS_ONE", cardinality="one_to_one")
        assert e.cardinality == "one_to_one"


# ── DerivationRule ────────────────────────────────────────────────────

class TestDerivationRule:

    def test_create_cu_rule(self):
        rule = DerivationRule(
            when={"node_type": "Condition", "min_facts": 2},
            action="create_cu",
            params={"boost": 1.5},
        )
        assert rule.action == "create_cu"
        assert rule.when["min_facts"] == 2
        assert rule.params["boost"] == 1.5

    def test_boost_rule(self):
        rule = DerivationRule(
            when={"edge_type": "DIAGNOSED_WITH"},
            action="boost",
            params={"factor": 2.0},
        )
        assert rule.action == "boost"

    def test_create_edge_rule(self):
        rule = DerivationRule(
            when={"co_occurrence": ["Treatment", "Condition"]},
            action="create_edge",
            params={"edge_type": "TREATS"},
        )
        assert rule.action == "create_edge"

    def test_alert_rule(self):
        rule = DerivationRule(
            when={"field": "severity", "value": "severe"},
            action="alert",
            params={"channel": "ops"},
        )
        assert rule.action == "alert"


# ── EnhancedSchema ────────────────────────────────────────────────────

class TestEnhancedSchema:

    def test_from_dict_enhanced_format(self):
        raw = {
            "name": "healthcare_v2",
            "version": "2.0",
            "ontology": "medical",
            "strict_mode": True,
            "node_types": {
                "Condition": {
                    "fields": {
                        "name": {"type": "string", "required": True, "indexed": True},
                        "severity": {"type": "enum", "values": ["mild", "moderate", "severe"]},
                        "icd_code": {"type": "string", "pattern": r"^[A-Z]\d{2}"},
                    },
                    "dedup": {"key": ["name"], "merge": "merge_properties"},
                    "min_confidence": 0.3,
                    "description": "Medical condition",
                },
                "Treatment": {
                    "fields": {
                        "name": {"type": "string", "required": True},
                        "dosage": {"type": "float", "range_min": 0},
                    },
                },
            },
            "edge_types": {
                "TREATED_WITH": {
                    "source": "Condition",
                    "target": "Treatment",
                    "cardinality": "many_to_many",
                    "properties": {
                        "effectiveness": {"type": "float", "range_min": 0, "range_max": 1.0},
                    },
                },
            },
            "derivation_rules": [
                {
                    "when": {"node_type": "Condition", "min_facts": 2},
                    "action": "create_cu",
                    "params": {"boost": 1.5},
                },
            ],
        }

        schema = EnhancedSchema.from_dict(raw)

        assert schema.name == "healthcare_v2"
        assert schema.version == "2.0"
        assert schema.ontology == "medical"
        assert schema.strict_mode is True

        # Node types
        assert "Condition" in schema.node_types
        cond = schema.node_types["Condition"]
        assert cond.fields["name"].type == "string"
        assert cond.fields["name"].required is True
        assert cond.fields["name"].indexed is True
        assert cond.fields["severity"].type == "enum"
        assert cond.fields["severity"].enum_values == ["mild", "moderate", "severe"]
        assert cond.fields["icd_code"].pattern == r"^[A-Z]\d{2}"
        assert cond.dedup.key == ["name"]
        assert cond.dedup.merge == "merge_properties"
        assert cond.min_confidence == 0.3
        assert cond.description == "Medical condition"
        assert "name" in cond.required_fields

        # Treatment
        treat = schema.node_types["Treatment"]
        assert treat.fields["dosage"].range_min == 0
        assert treat.dedup.key == ["name"]  # default

        # Edge types
        assert "TREATED_WITH" in schema.edge_types
        tw = schema.edge_types["TREATED_WITH"]
        assert tw.source == "Condition"
        assert tw.target == "Treatment"
        assert tw.cardinality == "many_to_many"
        assert "effectiveness" in tw.properties
        assert tw.properties["effectiveness"].type == "float"

        # Derivation rules
        assert len(schema.derivation_rules) == 1
        assert schema.derivation_rules[0].action == "create_cu"

    def test_from_dict_legacy_flat_format(self):
        """Legacy schemas with fields as a list of strings MUST parse correctly."""
        raw = {
            "name": "healthcare",
            "node_types": {
                "Patient": {
                    "fields": ["name", "age", "gender"],
                    "required": ["name"],
                    "description": "A patient",
                },
                "Condition": {
                    "fields": ["name", "severity", "icd_code"],
                    "required": ["name"],
                },
            },
            "edge_types": {
                "DIAGNOSED_WITH": {
                    "source": "Patient",
                    "target": "Condition",
                },
            },
        }

        schema = EnhancedSchema.from_dict(raw)

        assert schema.name == "healthcare"
        assert schema.version == "1.0"  # default

        # Patient
        patient = schema.node_types["Patient"]
        assert "name" in patient.fields
        assert "age" in patient.fields
        assert patient.fields["name"].type == "string"
        assert patient.fields["name"].required is True
        assert patient.fields["age"].required is False
        assert patient.description == "A patient"

        # Condition
        cond = schema.node_types["Condition"]
        assert cond.fields["name"].required is True
        assert cond.fields["severity"].required is False

        # Edge
        assert "DIAGNOSED_WITH" in schema.edge_types
        assert schema.edge_types["DIAGNOSED_WITH"].source == "Patient"

    def test_from_dict_minimal(self):
        """Minimal schema with just a name and empty node_types."""
        raw = {"name": "empty"}
        schema = EnhancedSchema.from_dict(raw)
        assert schema.name == "empty"
        assert schema.node_types == {}
        assert schema.edge_types == {}
        assert schema.derivation_rules == []

    def test_from_dict_legacy_edge_no_dict(self):
        """Edge types without dict value (just the name)."""
        raw = {
            "name": "test",
            "node_types": {},
            "edge_types": {
                "RELATES_TO": None,
            },
        }
        schema = EnhancedSchema.from_dict(raw)
        assert "RELATES_TO" in schema.edge_types
        assert schema.edge_types["RELATES_TO"].source == ""

    def test_from_dict_edge_with_inference_rules(self):
        raw = {
            "name": "test",
            "edge_types": {
                "TREATS": {
                    "source": "Treatment",
                    "target": "Condition",
                    "inference_rules": [{"pattern": "treats", "confidence": 0.8}],
                },
            },
        }
        schema = EnhancedSchema.from_dict(raw)
        assert len(schema.edge_types["TREATS"].inference_rules) == 1

    def test_from_dict_field_with_default(self):
        raw = {
            "name": "test",
            "node_types": {
                "Item": {
                    "fields": {
                        "status": {"type": "string", "default": "active"},
                    },
                },
            },
        }
        schema = EnhancedSchema.from_dict(raw)
        assert schema.node_types["Item"].fields["status"].default == "active"

    def test_from_dict_no_derivation_rules(self):
        raw = {"name": "test", "node_types": {}}
        schema = EnhancedSchema.from_dict(raw)
        assert schema.derivation_rules == []

    def test_strict_mode_defaults_false(self):
        raw = {"name": "test"}
        schema = EnhancedSchema.from_dict(raw)
        assert schema.strict_mode is False
