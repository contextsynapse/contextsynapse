"""Tests for Mutual Fund domain schema."""
import os
import pytest
import yaml

from contextcore.schema.sdl import EnhancedSchema
from contextcore.schema.compiler import SchemaCompiler
from contextcore.schema.validation_gate import ValidationGate


SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "contextcore", "config", "schemas", "mutual_fund.yaml"
)


@pytest.fixture
def mf_schema():
    with open(SCHEMA_PATH) as f:
        raw = yaml.safe_load(f)
    return EnhancedSchema.from_dict(raw)


@pytest.fixture
def compiled(mf_schema):
    compiler = SchemaCompiler()
    return compiler.compile(mf_schema)


@pytest.fixture
def gate(compiled):
    return ValidationGate(compiled)


class TestMfSchemaLoads:

    def test_schema_file_exists(self):
        assert os.path.isfile(SCHEMA_PATH)

    def test_schema_parses(self, mf_schema):
        assert mf_schema.name == "mutual_fund"
        assert "FundScheme" in mf_schema.node_types
        assert "NAVHistory" in mf_schema.node_types
        assert "FundPerformance" in mf_schema.node_types

    def test_schema_compiles(self, compiled):
        assert compiled is not None
        assert "FundScheme" in compiled.extraction_plan.allowed_types

    def test_fund_scheme_fields(self, mf_schema):
        fs = mf_schema.node_types["FundScheme"]
        assert "scheme_code" in fs.fields
        assert fs.fields["scheme_code"].required is True
        assert "nav" in fs.fields
        assert "expense_ratio" in fs.fields


class TestMfValidation:

    def test_valid_fund_scheme_accepted(self, gate):
        verdict = gate.validate_node({
            "type": "FundScheme",
            "scheme_code": "119551",
            "name": "HDFC Top 100 Fund - Growth",
            "fund_house": "HDFC Mutual Fund",
            "category": "equity",
            "nav": 856.43,
            "aum": 25000.0,
            "expense_ratio": 0.49,
        })
        assert verdict.verdict == "accept"

    def test_fund_missing_code_rejected(self, gate):
        verdict = gate.validate_node({
            "type": "FundScheme",
            "name": "No Code Fund",
        })
        assert verdict.verdict == "reject"
        assert "scheme_code" in verdict.reason

    def test_valid_nav_history_accepted(self, gate):
        verdict = gate.validate_node({
            "type": "NAVHistory",
            "scheme_code": "119551",
            "date": "2026-09-01",
            "nav_value": 856.43,
        })
        assert verdict.verdict == "accept"

    def test_valid_fund_performance_accepted(self, gate):
        verdict = gate.validate_node({
            "type": "FundPerformance",
            "scheme_code": "119551",
            "return_1y": 18.2,
            "return_3y": 16.1,
            "sharpe_ratio": 1.2,
        })
        assert verdict.verdict == "accept"

    def test_invalid_category_quarantined(self, gate):
        verdict = gate.validate_node({
            "type": "FundScheme",
            "scheme_code": "119551",
            "name": "Test Fund",
            "category": "crypto",  # not in enum
        })
        assert verdict.verdict == "quarantine"
