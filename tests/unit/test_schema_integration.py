"""Integration tests for Enhanced SDL v2.0 — full pipeline validation.

Tests the complete flow: YAML parse -> EnhancedSchema -> SchemaCompiler ->
CompiledSchema -> ValidationGate -> SchemaDedupStrategy -> IngestionSchema.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from contextcore.schema.sdl import EnhancedSchema
from contextcore.schema.compiler import SchemaCompiler
from contextcore.schema.dedup import SchemaDedupStrategy
from contextcore.schema.validation_gate import ValidationGate

SCHEMAS_DIR = Path(__file__).resolve().parent.parent.parent / "contextcore" / "config" / "schemas"


class TestFullPipeline:
    """Prove compile -> validate -> dedup -> to_ingestion_schema works end-to-end."""

    def _load_schema(self, name: str) -> EnhancedSchema:
        path = SCHEMAS_DIR / f"{name}.yaml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return EnhancedSchema.from_dict(raw)

    # ── healthcare pipeline ──────────────────────────────────────────

    def test_compile_validate_dedup_derive_healthcare(self):
        schema = self._load_schema("healthcare")
        compiled = SchemaCompiler.compile(schema)

        # 1. Extraction plan has all node types
        assert "Patient" in compiled.extraction_plan.allowed_types
        assert "Condition" in compiled.extraction_plan.allowed_types
        assert "Treatment" in compiled.extraction_plan.allowed_types

        # 2. Validate a batch: valid, invalid (missing required), unknown type
        gate = ValidationGate(compiled)
        nodes = [
            {"type": "Patient", "name": "Alice", "age": 30},
            {"type": "Patient"},  # missing required 'name'
            {"type": "AlienSpecies", "name": "Zorg"},  # unknown type
        ]
        accepted, quarantined, rejected = gate.validate_batch(nodes)
        assert len(accepted) == 1
        assert accepted[0].node["name"] == "Alice"
        assert len(rejected) == 1  # missing required field
        assert len(quarantined) == 1  # unknown type (permissive)

        # 3. Dedup: same name => same key
        dedup = SchemaDedupStrategy(compiled.dedup_strategies)
        key1 = dedup.dedup_key("Patient", {"name": "Alice"})
        key2 = dedup.dedup_key("Patient", {"name": "Alice"})
        key3 = dedup.dedup_key("Patient", {"name": "Bob"})
        assert key1 == key2
        assert key1 != key3

        # 4. to_ingestion_schema() round-trip
        ing = compiled.to_ingestion_schema()
        assert ing.name == "healthcare"
        assert "Patient" in ing.node_types
        assert "DIAGNOSED_WITH" in ing.edge_types

    # ── finance pipeline ─────────────────────────────────────────────

    def test_compile_validate_dedup_derive_finance(self):
        schema = self._load_schema("finance")
        compiled = SchemaCompiler.compile(schema)

        assert "Account" in compiled.extraction_plan.allowed_types
        assert "Risk" in compiled.extraction_plan.allowed_types

        gate = ValidationGate(compiled)
        nodes = [
            {"type": "Risk", "name": "Credit Default"},
            {"type": "Risk"},  # missing required 'name'
            {"type": "CryptoMoon", "name": "Moon"},  # unknown
        ]
        accepted, quarantined, rejected = gate.validate_batch(nodes)
        assert len(accepted) == 1
        assert len(rejected) == 1
        assert len(quarantined) == 1

        dedup = SchemaDedupStrategy(compiled.dedup_strategies)
        key1 = dedup.dedup_key("Risk", {"name": "Credit Default"})
        key2 = dedup.dedup_key("Risk", {"name": "Credit Default"})
        assert key1 == key2

        ing = compiled.to_ingestion_schema()
        assert ing.name == "finance"
        assert "Entity" in ing.node_types
        assert "EXPOSED_TO" in ing.edge_types

    # ── v2.0-specific features ───────────────────────────────────────

    def test_healthcare_v2_features(self):
        schema = self._load_schema("healthcare")

        assert schema.version == "2.0"
        assert len(schema.derivation_rules) == 3

        # Enhanced field types
        patient = schema.node_types["Patient"]
        assert patient.fields["age"].type == "int"
        assert patient.fields["name"].required is True
        assert patient.fields["name"].indexed is True

        # Dedup policies
        assert patient.dedup.key == ["name"]
        assert patient.min_confidence == 0.0

        condition = schema.node_types["Condition"]
        assert condition.min_confidence == 0.3

        # Edge properties
        treated_with = schema.edge_types["TREATED_WITH"]
        assert "efficacy" in treated_with.properties

        # New edge type
        assert "CONTRAINDICATED_WITH" in schema.edge_types

    def test_finance_v2_features(self):
        schema = self._load_schema("finance")

        assert schema.version == "2.0"
        assert len(schema.derivation_rules) == 3

        # Enhanced field types
        account = schema.node_types["Account"]
        assert account.fields["balance"].type == "float"
        assert account.fields["name"].required is True
        assert account.fields["name"].indexed is True

        risk = schema.node_types["Risk"]
        assert risk.min_confidence == 0.2

        # Dedup
        assert account.dedup.key == ["name"]

    # ── backward compat across ALL schemas ───────────────────────────

    def test_backward_compat_all_schemas(self):
        """Load every .yaml in config/schemas/, parse, compile, convert.

        This is the critical test: ALL existing schemas must survive the
        EnhancedSchema parser and SchemaCompiler without exceptions.
        """
        schema_files = sorted(SCHEMAS_DIR.glob("*.yaml"))
        assert len(schema_files) >= 28, (
            f"Expected at least 28 schemas, found {len(schema_files)}"
        )

        # session_graph.yaml uses a different format (NODES/EDGES keys);
        # it is consumed by a separate parser, not the SDL pipeline.
        alt_format_schemas = {"session_graph.yaml"}

        failures = []
        for path in schema_files:
            if path.name in alt_format_schemas:
                continue
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
                schema = EnhancedSchema.from_dict(raw)
                compiled = SchemaCompiler.compile(schema)
                ing = compiled.to_ingestion_schema()
                assert ing.name, f"{path.name}: IngestionSchema.name is empty"
                assert len(ing.node_types) > 0, f"{path.name}: no node types"
            except Exception as exc:
                failures.append(f"{path.name}: {exc}")

        assert not failures, "Schema parsing failures:\n" + "\n".join(failures)
