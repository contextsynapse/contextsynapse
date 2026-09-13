"""Tests for schema-driven deduplication strategy."""

import pytest

from contextcore.schema.sdl import DedupPolicy
from contextcore.schema.dedup import SchemaDedupStrategy


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def strategies():
    return {
        "Drug": DedupPolicy(key=["name"], merge="latest_wins"),
        "Drug_composite": DedupPolicy(key=["name", "dosage"], merge="latest_wins"),
        "Patient": DedupPolicy(key=["name"], merge="merge_properties"),
        "Event": DedupPolicy(key=["name"], merge="keep_both"),
    }


@pytest.fixture
def dedup(strategies):
    return SchemaDedupStrategy(strategies)


# ── dedup_key tests ──────────────────────────────────────────────────

def test_dedup_key_single_field(dedup):
    key = dedup.dedup_key("Drug", {"name": "Metformin", "category": "diabetes"})
    assert key == "Drug:metformin"


def test_dedup_key_composite():
    strat = SchemaDedupStrategy({
        "Drug": DedupPolicy(key=["name", "dosage"], merge="latest_wins"),
    })
    key = strat.dedup_key("Drug", {"name": "Metformin", "dosage": "500mg"})
    assert key == "Drug:metformin|500mg"


def test_dedup_key_missing_field_uses_empty():
    strat = SchemaDedupStrategy({
        "Drug": DedupPolicy(key=["name", "dosage"], merge="latest_wins"),
    })
    key = strat.dedup_key("Drug", {"name": "Metformin"})
    assert key == "Drug:metformin|"


def test_dedup_key_unknown_type_falls_back(dedup):
    key = dedup.dedup_key("UnknownType", {"name": "Foo", "bar": "baz"})
    assert key == "UnknownType:foo"


def test_dedup_key_unknown_type_no_name(dedup):
    key = dedup.dedup_key("UnknownType", {"bar": "baz"})
    assert key == "UnknownType:"


def test_dedup_key_strips_whitespace(dedup):
    key = dedup.dedup_key("Drug", {"name": "  Metformin  "})
    assert key == "Drug:metformin"


# ── merge tests ──────────────────────────────────────────────────────

def test_merge_latest_wins(dedup):
    existing = {"name": "Metformin", "category": "diabetes", "source": "paper1"}
    incoming = {"name": "Metformin", "category": "antidiabetic"}
    result = dedup.merge("Drug", existing, incoming)
    assert result is not None
    assert result["name"] == "Metformin"
    assert result["category"] == "antidiabetic"  # incoming wins
    assert result["source"] == "paper1"           # existing-only preserved


def test_merge_merge_properties(dedup):
    existing = {"name": "Alice", "age": 30}
    incoming = {"name": "Alice", "age": 31, "role": "nurse"}
    result = dedup.merge("Patient", existing, incoming)
    assert result is not None
    assert result["age"] == 30          # existing preserved
    assert result["role"] == "nurse"    # new field filled in
    assert result["name"] == "Alice"


def test_merge_keep_both_returns_none(dedup):
    result = dedup.merge("Event", {"name": "A"}, {"name": "B"})
    assert result is None


def test_merge_unknown_type_defaults_latest_wins(dedup):
    existing = {"name": "X", "old": 1}
    incoming = {"name": "X", "old": 2, "new": 3}
    result = dedup.merge("Unknown", existing, incoming)
    assert result is not None
    assert result["old"] == 2   # incoming wins
    assert result["new"] == 3


# ── should_dedup tests ───────────────────────────────────────────────

def test_should_dedup_true(dedup):
    assert dedup.should_dedup("Drug") is True
    assert dedup.should_dedup("Patient") is True


def test_should_dedup_keep_both_false(dedup):
    assert dedup.should_dedup("Event") is False


def test_should_dedup_unknown_type_true(dedup):
    # Unknown types use default policy (latest_wins), so dedup is True
    assert dedup.should_dedup("Unknown") is True
