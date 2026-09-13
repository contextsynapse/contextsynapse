"""Tests for ValidationGate — accept/quarantine/reject enforcement."""

import pytest

from contextcore.schema.sdl import EnhancedSchema
from contextcore.schema.compiler import SchemaCompiler
from contextcore.schema.validation_gate import ValidationGate, ValidationVerdict


SCHEMA_RAW = {
    "name": "test",
    "strict_mode": True,
    "node_types": {
        "Drug": {
            "fields": {
                "name": {"type": "string", "required": True},
                "severity": {"type": "enum", "values": ["mild", "moderate", "severe"]},
                "icd_code": {"type": "string", "pattern": r"^[A-Z]\d{2}"},
                "score": {"type": "float", "range_min": 0, "range_max": 1},
            },
            "min_confidence": 0.4,
        },
    },
}


def _make_gate(raw=None):
    raw = raw or SCHEMA_RAW
    schema = EnhancedSchema.from_dict(raw)
    compiled = SchemaCompiler.compile(schema)
    return ValidationGate(compiled)


class TestValidationGate:
    def test_accept_valid_node(self):
        gate = _make_gate()
        node = {"type": "Drug", "name": "Aspirin", "severity": "mild",
                "icd_code": "A01", "score": 0.8, "confidence": 0.9}
        v = gate.validate_node(node)
        assert v.verdict == "accept"

    def test_reject_missing_required_field(self):
        gate = _make_gate()
        node = {"type": "Drug", "severity": "mild"}  # missing 'name'
        v = gate.validate_node(node)
        assert v.verdict == "reject"
        assert "name" in v.reason

    def test_quarantine_invalid_enum(self):
        gate = _make_gate()
        node = {"type": "Drug", "name": "X", "severity": "extreme", "confidence": 0.9}
        v = gate.validate_node(node)
        assert v.verdict == "quarantine"
        assert "severity" in v.reason

    def test_quarantine_invalid_pattern(self):
        gate = _make_gate()
        node = {"type": "Drug", "name": "X", "icd_code": "123bad", "confidence": 0.9}
        v = gate.validate_node(node)
        assert v.verdict == "quarantine"
        assert "icd_code" in v.reason

    def test_quarantine_out_of_range(self):
        gate = _make_gate()
        node = {"type": "Drug", "name": "X", "score": 1.5, "confidence": 0.9}
        v = gate.validate_node(node)
        assert v.verdict == "quarantine"
        assert "score" in v.reason

    def test_reject_unknown_type_strict(self):
        gate = _make_gate()
        node = {"type": "Unknown", "name": "X"}
        v = gate.validate_node(node)
        assert v.verdict == "reject"
        assert "unknown" in v.reason.lower() or "Unknown" in v.reason

    def test_quarantine_unknown_type_permissive(self):
        raw = dict(SCHEMA_RAW, strict_mode=False)
        gate = _make_gate(raw)
        node = {"type": "Unknown", "name": "X"}
        v = gate.validate_node(node)
        assert v.verdict == "quarantine"

    def test_reject_low_confidence(self):
        gate = _make_gate()
        node = {"type": "Drug", "name": "X", "confidence": 0.2}
        v = gate.validate_node(node)
        assert v.verdict == "reject"
        assert "confidence" in v.reason.lower()

    def test_accept_no_confidence_field(self):
        gate = _make_gate()
        node = {"type": "Drug", "name": "Aspirin"}  # no confidence key
        v = gate.validate_node(node)
        assert v.verdict == "accept"

    def test_batch_validate(self):
        gate = _make_gate()
        nodes = [
            {"type": "Drug", "name": "Good", "confidence": 0.9},       # accept
            {"type": "Drug", "name": "Bad", "severity": "nope", "confidence": 0.9},  # quarantine
            {"type": "Drug", "confidence": 0.9},                        # reject (missing name)
            {"type": "Unknown", "name": "X"},                           # reject (strict)
            {"type": "Drug", "name": "Low", "confidence": 0.1},        # reject (low conf)
        ]
        accepted, quarantined, rejected = gate.validate_batch(nodes)
        assert len(accepted) == 1
        assert accepted[0].node["name"] == "Good"
        assert len(quarantined) == 1
        assert quarantined[0].node["name"] == "Bad"
        assert len(rejected) == 3
