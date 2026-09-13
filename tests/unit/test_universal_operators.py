"""Tests for universal pipeline core operators."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from contextcore.ingestion.universal.ingest_content import Chunk
from contextcore.ingestion.universal.stage_executor import GraphContext
from contextcore.ingestion.universal.operators.detect_signals import DetectSignalsOperator
from contextcore.ingestion.universal.operators.extract_entities import (
    ExtractEntitiesOperator,
    _regex_extract_entities,
)
from contextcore.ingestion.universal.operators.cluster_topics import ClusterTopicsOperator
from contextcore.ingestion.universal.operators.deduplicate import DeduplicateOperator
from contextcore.ingestion.universal.operators.link_cross_reference import LinkCrossReferenceOperator


def _make_graph_ctx() -> GraphContext:
    """Create a GraphContext with a mocked db."""
    db = MagicMock()
    db.add_node = MagicMock()
    db.add_edge = MagicMock()
    db.get_node = MagicMock(return_value=None)
    return GraphContext(db=db, namespace="test")


def _make_chunk(content: str, index: int = 0, **meta) -> Chunk:
    return Chunk(content=content, index=index, metadata=dict(meta))


# ---------------------------------------------------------------------------
# TestDetectSignals
# ---------------------------------------------------------------------------

class TestDetectSignals:
    def test_detects_decision_in_turn(self):
        op = DetectSignalsOperator()
        ctx = _make_graph_ctx()
        chunks = [
            _make_chunk(
                "We decided to use FastAPI for the backend service.",
                index=0,
                turn_node_id="turn_001",
            ),
        ]

        result = op.process(chunks, ctx)

        assert len(result) == 1
        assert "signals" in result[0].metadata
        signal_types = [s["type"] for s in result[0].metadata["signals"]]
        assert "decision" in signal_types

    def test_creates_signal_nodes(self):
        op = DetectSignalsOperator()
        ctx = _make_graph_ctx()
        chunks = [
            _make_chunk(
                "We decided to go with Redis for caching.",
                index=0,
                turn_node_id="turn_001",
            ),
        ]

        op.process(chunks, ctx)

        assert "signal_node_ids" in chunks[0].metadata
        assert len(chunks[0].metadata["signal_node_ids"]) > 0
        # Nodes and edges should have been created
        assert ctx.db.add_node.call_count > 0
        assert ctx.db.add_edge.call_count > 0

    def test_skips_insignificant_chunks(self):
        op = DetectSignalsOperator(significance_threshold=0.3)
        ctx = _make_graph_ctx()
        chunks = [
            _make_chunk("ok", index=0, turn_node_id="turn_001"),
        ]

        result = op.process(chunks, ctx)

        assert result[0].metadata.get("significance", 0) < 0.3
        assert "signals" not in result[0].metadata

    def test_detects_multiple_signal_types(self):
        op = DetectSignalsOperator()
        ctx = _make_graph_ctx()
        chunks = [
            _make_chunk(
                "There's a bug in the auth module. The fix is to update the token validation.",
                index=0,
                turn_node_id="turn_001",
            ),
        ]

        result = op.process(chunks, ctx)

        signal_types = {s["type"] for s in result[0].metadata.get("signals", [])}
        assert "problem" in signal_types
        assert "solution" in signal_types

    def test_filter_by_signal_types(self):
        op = DetectSignalsOperator(signal_types=["decision"])
        ctx = _make_graph_ctx()
        chunks = [
            _make_chunk(
                "There's a bug. We decided to fix it immediately.",
                index=0,
                turn_node_id="turn_001",
            ),
        ]

        result = op.process(chunks, ctx)

        signal_types = {s["type"] for s in result[0].metadata.get("signals", [])}
        assert "decision" in signal_types
        assert "problem" not in signal_types


# ---------------------------------------------------------------------------
# TestExtractEntities
# ---------------------------------------------------------------------------

class TestExtractEntities:
    def test_basic_regex_extraction(self):
        entities = _regex_extract_entities(
            "John Smith works at Google on the FastAPI project."
        )
        names = [e["name"] for e in entities]
        assert "John Smith" in names

    def test_extracts_technology(self):
        entities = _regex_extract_entities(
            "We built the system using Python and Redis for caching."
        )
        names = [e["name"] for e in entities]
        assert "Python" in names or "Redis" in names

    def test_filters_false_positives(self):
        entities = _regex_extract_entities(
            "The solution is to use However and Then in the code."
        )
        names = [e["name"] for e in entities]
        assert "However" not in names
        assert "Then" not in names

    def test_creates_entity_nodes_and_mentions_edges(self):
        op = ExtractEntitiesOperator(use_llm=False)
        ctx = _make_graph_ctx()
        chunks = [
            _make_chunk(
                "Alice Johnson met with Bob Smith to discuss the Python migration.",
                index=0,
                turn_node_id="turn_001",
            ),
        ]

        result = op.process(chunks, ctx)

        assert "entity_node_ids" in result[0].metadata
        assert len(result[0].metadata["entity_node_ids"]) > 0
        # MENTIONS edges created
        assert ctx.db.add_edge.call_count > 0

    def test_creates_entity_to_entity_edges_from_schema(self):
        """When two entities in the same chunk match a schema edge_type, create that edge."""
        op = ExtractEntitiesOperator(use_llm=False)
        ctx = _make_graph_ctx()

        # Set up compiled_schema with edge_types: USED_WITH(Tool -> Tool)
        compiled_schema = MagicMock()
        compiled_schema.edge_types = {
            "USED_WITH": {"source": "Tool", "target": "Tool"},
            "IMPLEMENTS": {"source": "Tool", "target": "Concept"},
        }
        ctx.compiled_schema = compiled_schema

        # Patch _extract to return two Tool entities in same chunk
        op._extract = MagicMock(return_value=[
            {"label": "Tool", "name": "Redis"},
            {"label": "Tool", "name": "Docker"},
        ])

        chunks = [
            _make_chunk(
                "We use Redis and Docker together.",
                index=0,
                turn_node_id="turn_001",
                significance=0.8,
            ),
        ]

        op.process(chunks, ctx)

        # Should have MENTIONS edges + a USED_WITH edge
        edge_calls = ctx.db.add_edge.call_args_list
        edge_labels = [c.args[0].label for c in edge_calls]
        assert "USED_WITH" in edge_labels

    def test_no_entity_edge_without_matching_schema(self):
        """No entity-to-entity edge when schema has no matching edge_type."""
        op = ExtractEntitiesOperator(use_llm=False)
        ctx = _make_graph_ctx()

        compiled_schema = MagicMock()
        compiled_schema.edge_types = {
            "IMPLEMENTS": {"source": "Tool", "target": "Concept"},
        }
        ctx.compiled_schema = compiled_schema

        # Two Person entities -- no edge_type matches Person->Person
        op._extract = MagicMock(return_value=[
            {"label": "Person", "name": "Alice"},
            {"label": "Person", "name": "Bob"},
        ])

        chunks = [
            _make_chunk(
                "Alice and Bob talked.",
                index=0,
                turn_node_id="turn_001",
                significance=0.8,
            ),
        ]

        op.process(chunks, ctx)

        edge_calls = ctx.db.add_edge.call_args_list
        edge_labels = [c.args[0].label for c in edge_calls]
        # Only MENTIONS edges, no entity-to-entity edges
        assert all(l == "MENTIONS" for l in edge_labels)

    def test_deduplicates_entities(self):
        op = ExtractEntitiesOperator(use_llm=False)
        ctx = _make_graph_ctx()
        chunks = [
            _make_chunk(
                "Alice Johnson explained the design to the team.",
                index=0,
                turn_node_id="turn_001",
            ),
            _make_chunk(
                "Alice Johnson then presented the final architecture.",
                index=1,
                turn_node_id="turn_002",
            ),
        ]

        op.process(chunks, ctx)

        # After processing both chunks, "Alice Johnson" should only be one node
        # The second time it should reuse the existing entity via find_entity
        alice_key = "Person:alice johnson"
        assert alice_key in ctx.entity_ids


# ---------------------------------------------------------------------------
# TestClusterTopics
# ---------------------------------------------------------------------------

class TestClusterTopics:
    def test_keyword_clustering_creates_topics(self):
        op = ClusterTopicsOperator(method="keyword", max_topics=3)
        ctx = _make_graph_ctx()
        chunks = [
            _make_chunk(
                "The database performance is critical for our application scaling.",
                index=0, turn_node_id="turn_001",
            ),
            _make_chunk(
                "Database indexing improves query performance significantly.",
                index=1, turn_node_id="turn_002",
            ),
            _make_chunk(
                "Frontend design needs to follow the new branding guidelines.",
                index=2, turn_node_id="turn_003",
            ),
            _make_chunk(
                "The frontend components should be responsive for mobile users.",
                index=3, turn_node_id="turn_004",
            ),
        ]

        result = op.process(chunks, ctx)

        assert len(result) == 4
        # Topic nodes should have been created
        assert ctx.db.add_node.call_count > 0
        # At least one chunk should have topic_ids
        has_topics = any("topic_ids" in c.metadata for c in result)
        assert has_topics

    def test_skips_with_fewer_than_3_chunks(self):
        op = ClusterTopicsOperator()
        ctx = _make_graph_ctx()
        chunks = [
            _make_chunk("Short conversation.", index=0),
            _make_chunk("Only two turns.", index=1),
        ]

        result = op.process(chunks, ctx)

        assert len(result) == 2
        assert ctx.db.add_node.call_count == 0

    def test_creates_raises_edges(self):
        op = ClusterTopicsOperator(method="keyword", max_topics=3)
        ctx = _make_graph_ctx()
        chunks = [
            _make_chunk(
                "Authentication security is our top priority for the release.",
                index=0, turn_node_id="turn_001",
            ),
            _make_chunk(
                "Security audit found vulnerabilities in the authentication flow.",
                index=1, turn_node_id="turn_002",
            ),
            _make_chunk(
                "We need to patch the authentication security issues before launch.",
                index=2, turn_node_id="turn_003",
            ),
        ]

        op.process(chunks, ctx)

        # RAISES edges from turn -> topic should exist
        edge_labels = [
            call.args[0].label
            for call in ctx.db.add_edge.call_args_list
        ]
        assert "RAISES" in edge_labels


# ---------------------------------------------------------------------------
# TestLinkCrossReference
# ---------------------------------------------------------------------------

class TestLinkCrossReference:
    def test_problem_solution_creates_solved_by(self):
        op = LinkCrossReferenceOperator()
        ctx = _make_graph_ctx()

        chunks = [
            _make_chunk(
                "There is a critical bug in the login flow.",
                index=0,
                turn_node_id="turn_001",
                signals=[{"type": "problem", "text": "bug", "confidence": 0.85}],
                signal_node_ids=["problem_001"],
            ),
            _make_chunk(
                "I fixed it by updating the token validation logic.",
                index=1,
                turn_node_id="turn_002",
                signals=[{"type": "solution", "text": "fixed", "confidence": 0.85}],
                signal_node_ids=["solution_001"],
            ),
        ]

        op.process(chunks, ctx)

        # Should create SOLVED_BY edge from problem to solution
        edge_calls = ctx.db.add_edge.call_args_list
        assert len(edge_calls) >= 1
        solved_by = [c for c in edge_calls if c.args[0].label == "SOLVED_BY"]
        assert len(solved_by) == 1

    def test_no_link_when_solution_too_far(self):
        op = LinkCrossReferenceOperator()
        ctx = _make_graph_ctx()

        chunks = [
            _make_chunk(
                "There is a bug.",
                index=0,
                signals=[{"type": "problem", "text": "bug", "confidence": 0.85}],
                signal_node_ids=["problem_001"],
            ),
            _make_chunk("Unrelated discussion about design.", index=1),
            _make_chunk("More unrelated content here.", index=2),
            _make_chunk("Still talking about other things.", index=3),
            _make_chunk(
                "The fix is to update the config.",
                index=4,
                signals=[{"type": "solution", "text": "fix", "confidence": 0.85}],
                signal_node_ids=["solution_001"],
            ),
        ]

        op.process(chunks, ctx)

        # Solution is 4 turns away (window is 3), no SOLVED_BY
        edge_calls = ctx.db.add_edge.call_args_list
        solved_by = [c for c in edge_calls if c.args[0].label == "SOLVED_BY"]
        assert len(solved_by) == 0

    def test_decision_entity_creates_recommends(self):
        op = LinkCrossReferenceOperator()
        ctx = _make_graph_ctx()

        chunks = [
            _make_chunk(
                "We decided to use Redis for the cache layer.",
                index=0,
                turn_node_id="turn_001",
                signals=[{"type": "decision", "text": "decided to", "confidence": 0.9}],
                signal_node_ids=["decision_001"],
                entity_node_ids=["entity_redis"],
            ),
        ]

        op.process(chunks, ctx)

        edge_calls = ctx.db.add_edge.call_args_list
        recommends = [c for c in edge_calls if c.args[0].label == "RECOMMENDS"]
        assert len(recommends) == 1

    def test_no_recommends_without_entities(self):
        op = LinkCrossReferenceOperator()
        ctx = _make_graph_ctx()

        chunks = [
            _make_chunk(
                "We decided to proceed with the plan.",
                index=0,
                signals=[{"type": "decision", "text": "decided to", "confidence": 0.9}],
                signal_node_ids=["decision_001"],
            ),
        ]

        op.process(chunks, ctx)

        edge_calls = ctx.db.add_edge.call_args_list
        recommends = [c for c in edge_calls if c.args[0].label == "RECOMMENDS"]
        assert len(recommends) == 0


# ---------------------------------------------------------------------------
# TestLinkCrossReferenceSchema — schema-driven edge creation
# ---------------------------------------------------------------------------


def _make_schema_ctx(edge_rules: dict) -> GraphContext:
    """Create a GraphContext with a compiled_schema containing edge_rules."""
    from unittest.mock import MagicMock
    from contextcore.schema.sdl import EdgeTypeDef

    db = MagicMock()
    db.add_node = MagicMock()
    db.add_edge = MagicMock()
    db.get_node = MagicMock(return_value=None)

    compiled_schema = MagicMock()
    compiled_schema.edge_rules = {
        name: EdgeTypeDef(name=name, source=defn["source"], target=defn["target"])
        for name, defn in edge_rules.items()
    }
    ctx = GraphContext(db=db, namespace="test", compiled_schema=compiled_schema)
    return ctx


class TestLinkCrossReferenceSchema:
    """Test that the operator reads edge_types from compiled_schema."""

    def test_schema_uses_edge_between_solution_and_tool(self):
        """Schema defines USES: Solution -> Tool. Operator should create it."""
        op = LinkCrossReferenceOperator()
        ctx = _make_schema_ctx({
            "USES": {"source": "Solution", "target": "Tool"},
            "SOLVED_BY": {"source": "Problem", "target": "Solution"},
        })
        # Register an entity so we can reverse-lookup its label
        ctx.entity_ids["Tool:redis"] = "entity_redis"

        chunks = [
            _make_chunk(
                "The fix uses Redis for caching.",
                index=0,
                signals=[{"type": "solution", "text": "fix", "confidence": 0.8}],
                signal_node_ids=["solution_001"],
                entity_node_ids=["entity_redis"],
            ),
        ]

        op.process(chunks, ctx)

        edge_calls = ctx.db.add_edge.call_args_list
        uses_edges = [c for c in edge_calls if c.args[0].label == "USES"]
        assert len(uses_edges) == 1
        assert uses_edges[0].args[0].source == "solution_001"
        assert uses_edges[0].args[0].target == "entity_redis"

    def test_schema_about_edge_between_problem_and_concept(self):
        """Schema defines ABOUT: Problem -> Concept. Operator should create it."""
        op = LinkCrossReferenceOperator()
        ctx = _make_schema_ctx({
            "ABOUT": {"source": "Problem", "target": "Concept"},
        })
        ctx.entity_ids["Concept:authentication"] = "entity_auth"

        chunks = [
            _make_chunk(
                "There is a bug in the authentication module.",
                index=0,
                signals=[{"type": "problem", "text": "bug", "confidence": 0.85}],
                signal_node_ids=["problem_001"],
                entity_node_ids=["entity_auth"],
            ),
        ]

        op.process(chunks, ctx)

        edge_calls = ctx.db.add_edge.call_args_list
        about_edges = [c for c in edge_calls if c.args[0].label == "ABOUT"]
        assert len(about_edges) == 1
        assert about_edges[0].args[0].source == "problem_001"
        assert about_edges[0].args[0].target == "entity_auth"

    def test_schema_signal_to_signal_proximity(self):
        """Schema defines ADDRESSES: Solution -> Problem across chunks."""
        op = LinkCrossReferenceOperator()
        ctx = _make_schema_ctx({
            "ADDRESSES": {"source": "Solution", "target": "Problem"},
            "SOLVED_BY": {"source": "Problem", "target": "Solution"},
        })

        chunks = [
            _make_chunk(
                "There is a critical bug.",
                index=0,
                signals=[{"type": "problem", "text": "bug", "confidence": 0.85}],
                signal_node_ids=["problem_001"],
            ),
            _make_chunk(
                "Here is the fix for that.",
                index=1,
                signals=[{"type": "solution", "text": "fix", "confidence": 0.85}],
                signal_node_ids=["solution_001"],
            ),
        ]

        op.process(chunks, ctx)

        edge_calls = ctx.db.add_edge.call_args_list
        edge_labels = [c.args[0].label for c in edge_calls]
        # Should have both SOLVED_BY (Problem->Solution) and ADDRESSES (Solution->Problem)
        assert "SOLVED_BY" in edge_labels
        assert "ADDRESSES" in edge_labels

    def test_fallback_without_schema(self):
        """Without compiled_schema, fallback to hardcoded SOLVED_BY + RECOMMENDS."""
        op = LinkCrossReferenceOperator()
        ctx = _make_graph_ctx()  # no compiled_schema

        chunks = [
            _make_chunk(
                "There is a bug.",
                index=0,
                signals=[{"type": "problem", "text": "bug", "confidence": 0.85}],
                signal_node_ids=["problem_001"],
            ),
            _make_chunk(
                "Here is the fix.",
                index=1,
                signals=[{"type": "solution", "text": "fix", "confidence": 0.85}],
                signal_node_ids=["solution_001"],
            ),
        ]

        op.process(chunks, ctx)

        edge_calls = ctx.db.add_edge.call_args_list
        edge_labels = [c.args[0].label for c in edge_calls]
        assert "SOLVED_BY" in edge_labels

    def test_no_duplicate_edges_for_same_pair(self):
        """Should not create duplicate edges for the same source-target pair."""
        op = LinkCrossReferenceOperator()
        ctx = _make_schema_ctx({
            "SOLVED_BY": {"source": "Problem", "target": "Solution"},
        })

        chunks = [
            _make_chunk(
                "There is a bug.",
                index=0,
                signals=[{"type": "problem", "text": "bug", "confidence": 0.85}],
                signal_node_ids=["problem_001"],
            ),
            _make_chunk(
                "Here is the fix.",
                index=1,
                signals=[{"type": "solution", "text": "fix", "confidence": 0.85}],
                signal_node_ids=["solution_001"],
            ),
        ]

        op.process(chunks, ctx)

        edge_calls = ctx.db.add_edge.call_args_list
        solved_by = [c for c in edge_calls if c.args[0].label == "SOLVED_BY"]
        assert len(solved_by) == 1


# ---------------------------------------------------------------------------
# TestDeduplicateOperator
# ---------------------------------------------------------------------------

class TestDeduplicateOperator:
    def test_removes_duplicate_chunks(self):
        op = DeduplicateOperator()
        ctx = _make_graph_ctx()
        chunks = [
            _make_chunk("The database needs an index on the users table.", index=0),
            _make_chunk("The database needs an index on the users table.", index=1),
        ]

        result = op.process(chunks, ctx)

        assert len(result) == 1
        assert result[0].content == "The database needs an index on the users table."

    def test_keeps_similar_but_different(self):
        op = DeduplicateOperator()
        ctx = _make_graph_ctx()
        chunks = [
            _make_chunk("The database needs an index on the users table.", index=0),
            _make_chunk("The database needs an index on the orders table.", index=1),
        ]

        result = op.process(chunks, ctx)

        assert len(result) == 2

    def test_case_insensitive_dedup(self):
        op = DeduplicateOperator()
        ctx = _make_graph_ctx()
        chunks = [
            _make_chunk("Hello World", index=0),
            _make_chunk("hello world", index=1),
        ]

        result = op.process(chunks, ctx)

        assert len(result) == 1
