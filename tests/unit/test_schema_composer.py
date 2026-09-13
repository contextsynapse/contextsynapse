"""Tests for SchemaComposer — cross-module schema composition."""

import pytest

from contextcore.schema.composer import SchemaComposer


class TestSchemaComposer:
    def test_list_modules(self):
        composer = SchemaComposer()
        modules = composer.list_modules()
        assert "healthcare" in modules
        assert "finance" in modules
        assert len(modules) >= 20  # 29 builtin schemas

    def test_list_types(self):
        composer = SchemaComposer()
        types = composer.list_types("healthcare")
        assert "Condition" in types["node_types"]
        assert "TREATED_WITH" in types["edge_types"]

    def test_list_types_unknown_module(self):
        composer = SchemaComposer()
        with pytest.raises(ValueError, match="nonexistent"):
            composer.list_types("nonexistent")

    def test_compose_single_module(self):
        composer = SchemaComposer()
        result = composer.compose({
            "name": "test",
            "compose": [{"healthcare": ["Patient", "Condition", "Treatment"]}],
        })
        assert "Patient" in result.node_types
        assert "Condition" in result.node_types
        assert "Treatment" in result.node_types
        # TREATED_WITH should be auto-included (Condition->Treatment both selected)
        assert "TREATED_WITH" in result.edge_types

    def test_compose_cross_module(self):
        composer = SchemaComposer()
        result = composer.compose({
            "name": "claims",
            "compose": [
                {"healthcare": ["Patient", "Condition"]},
                {"finance": ["Risk"]},
            ],
        })
        assert "Patient" in result.node_types
        assert "Risk" in result.node_types

    def test_compose_with_project_types(self):
        composer = SchemaComposer()
        result = composer.compose({
            "name": "custom",
            "compose": [{"healthcare": ["Condition"]}],
            "node_types": {
                "ClaimReview": {"fields": {"reviewer": {"type": "string", "required": True}}},
            },
            "edge_types": {
                "REVIEWS": {"source": "ClaimReview", "target": "Condition"},
            },
        })
        assert "Condition" in result.node_types
        assert "ClaimReview" in result.node_types
        assert "REVIEWS" in result.edge_types

    def test_edge_auto_include_only_if_both_types_selected(self):
        composer = SchemaComposer()
        result = composer.compose({
            "name": "partial",
            "compose": [{"healthcare": ["Condition"]}],  # no Treatment
        })
        # TREATED_WITH needs Condition+Treatment, but Treatment not selected
        assert "TREATED_WITH" not in result.edge_types

    def test_compose_with_derivation_rules(self):
        composer = SchemaComposer()
        result = composer.compose({
            "name": "test",
            "compose": [{"healthcare": ["Condition", "Treatment"]}],
            "derivation_rules": [
                {"when": {"node": "Condition"}, "action": "boost", "params": {"priority": 2.0}},
            ],
        })
        assert len(result.derivation_rules) == 1

    def test_unknown_module_raises(self):
        composer = SchemaComposer()
        with pytest.raises(ValueError, match="nonexistent"):
            composer.compose({"name": "x", "compose": [{"nonexistent": ["Foo"]}]})

    def test_unknown_type_raises(self):
        composer = SchemaComposer()
        with pytest.raises(ValueError, match="NonExistentType"):
            composer.compose({"name": "x", "compose": [{"healthcare": ["NonExistentType"]}]})

    def test_empty_compose(self):
        composer = SchemaComposer()
        result = composer.compose({
            "name": "custom_only",
            "node_types": {"Foo": {"fields": {"name": {"type": "string", "required": True}}}},
        })
        assert "Foo" in result.node_types

    def test_project_type_overrides_module_type(self):
        composer = SchemaComposer()
        result = composer.compose({
            "name": "override",
            "compose": [{"healthcare": ["Patient"]}],
            "node_types": {
                "Patient": {"fields": {"custom_field": {"type": "string", "required": True}}},
            },
        })
        assert "Patient" in result.node_types
        # Project override should have custom_field
        assert "custom_field" in result.node_types["Patient"].fields

    def test_schema_name_set(self):
        composer = SchemaComposer()
        result = composer.compose({
            "name": "my_schema",
            "compose": [{"healthcare": ["Condition"]}],
        })
        assert result.name == "my_schema"

    def test_contraindicated_with_edge_included(self):
        """CONTRAINDICATED_WITH is Treatment->Treatment, so selecting Treatment should include it."""
        composer = SchemaComposer()
        result = composer.compose({
            "name": "test",
            "compose": [{"healthcare": ["Treatment"]}],
        })
        assert "CONTRAINDICATED_WITH" in result.edge_types
