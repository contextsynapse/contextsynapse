import pytest
from contextcore.project.sdlc_schema import (
    SDLC_LAYERS,
    ALL_NODE_TYPES,
    SDLC_EDGE_TYPES,
    NODE_REQUIRED_FIELDS,
    layer_for_node_type,
    is_sdlc_node_type,
    validate_node,
)


class TestSDLCLayers:
    def test_five_layers_defined(self):
        assert set(SDLC_LAYERS.keys()) == {"intent", "design", "build", "verify", "evolution"}

    def test_weights_sum_to_one(self):
        total = sum(v["weight"] for v in SDLC_LAYERS.values())
        assert abs(total - 1.0) < 1e-9

    def test_evolution_is_optional(self):
        assert SDLC_LAYERS["evolution"]["optional"] is True

    def test_non_evolution_not_optional(self):
        for name, layer in SDLC_LAYERS.items():
            if name != "evolution":
                assert layer.get("optional", False) is False

    def test_layer_node_type_counts(self):
        assert len(SDLC_LAYERS["intent"]["node_types"]) == 3
        assert len(SDLC_LAYERS["design"]["node_types"]) == 3
        assert len(SDLC_LAYERS["build"]["node_types"]) == 3
        assert len(SDLC_LAYERS["verify"]["node_types"]) == 3
        assert len(SDLC_LAYERS["evolution"]["node_types"]) == 3


class TestAllNodeTypes:
    def test_fifteen_node_types(self):
        assert len(ALL_NODE_TYPES) == 15

    def test_contains_expected_types(self):
        expected = {
            "Requirement", "UserStory", "Goal",
            "ArchDecision", "Pattern", "Constraint",
            "CodeModule", "APIContract", "DataModel",
            "TestCase", "TestResult", "KnownIssue",
            "Preservation", "Migration", "ChangeRecord",
        }
        assert set(ALL_NODE_TYPES) == expected


class TestSDLCEdgeTypes:
    def test_core_edges_defined(self):
        for edge in ("IMPLEMENTS", "SATISFIES", "TESTS", "GOVERNS", "CONSTRAINED_BY",
                     "DEPENDS_ON", "EXPOSES", "USES"):
            assert edge in SDLC_EDGE_TYPES

    def test_orphan_fill_edges_defined(self):
        for edge in ("REFINES", "MOTIVATES", "APPLIES", "VALIDATES", "AFFECTS"):
            assert edge in SDLC_EDGE_TYPES, f"Missing edge type: {edge}"

    def test_brownfield_edges_defined(self):
        for edge in ("PRESERVES", "STRANGLES", "REPLACES", "MODIFIES"):
            assert edge in SDLC_EDGE_TYPES

    def test_edge_has_source_and_target(self):
        for name, (src, tgt) in SDLC_EDGE_TYPES.items():
            assert isinstance(src, str) and len(src) > 0
            assert isinstance(tgt, str) and len(tgt) > 0

    def test_no_orphaned_node_types(self):
        """Every node type must appear in at least one edge."""
        in_edges = set()
        for src, tgt in SDLC_EDGE_TYPES.values():
            in_edges.add(src)
            in_edges.add(tgt)
        orphans = [t for t in ALL_NODE_TYPES if t not in in_edges]
        assert orphans == [], f"Orphaned node types with no edges: {orphans}"

    def test_refines_user_story_to_requirement(self):
        assert SDLC_EDGE_TYPES["REFINES"] == ("UserStory", "Requirement")

    def test_motivates_goal_to_requirement(self):
        assert SDLC_EDGE_TYPES["MOTIVATES"] == ("Goal", "Requirement")

    def test_applies_pattern_to_codemodule(self):
        assert SDLC_EDGE_TYPES["APPLIES"] == ("Pattern", "CodeModule")

    def test_validates_testresult_to_testcase(self):
        assert SDLC_EDGE_TYPES["VALIDATES"] == ("TestResult", "TestCase")

    def test_affects_knownissue_to_codemodule(self):
        assert SDLC_EDGE_TYPES["AFFECTS"] == ("KnownIssue", "CodeModule")


class TestLayerForNodeType:
    def test_requirement_is_intent(self):
        assert layer_for_node_type("Requirement") == "intent"

    def test_archdecision_is_design(self):
        assert layer_for_node_type("ArchDecision") == "design"

    def test_codemodule_is_build(self):
        assert layer_for_node_type("CodeModule") == "build"

    def test_testcase_is_verify(self):
        assert layer_for_node_type("TestCase") == "verify"

    def test_preservation_is_evolution(self):
        assert layer_for_node_type("Preservation") == "evolution"

    def test_unknown_returns_none(self):
        assert layer_for_node_type("UnknownType") is None


class TestIsSDLCNodeType:
    def test_known_types_true(self):
        for t in ALL_NODE_TYPES:
            assert is_sdlc_node_type(t) is True

    def test_unknown_types_false(self):
        assert is_sdlc_node_type("Task") is False
        assert is_sdlc_node_type("") is False


class TestValidateNode:
    def test_requirement_needs_content(self):
        errors = validate_node("Requirement", {})
        assert "content" in errors

    def test_requirement_valid_with_content(self):
        errors = validate_node("Requirement", {"content": "Users can log in"})
        assert errors == []

    def test_archdecision_needs_decision_and_rationale(self):
        errors = validate_node("ArchDecision", {})
        assert "decision" in errors
        assert "rationale" in errors

    def test_archdecision_partial_missing_rationale(self):
        errors = validate_node("ArchDecision", {"decision": "Use JWT"})
        assert "rationale" in errors
        assert "decision" not in errors

    def test_unknown_type_returns_empty(self):
        errors = validate_node("UnknownType", {})
        assert errors == []
