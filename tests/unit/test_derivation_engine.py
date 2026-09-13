"""Tests for DerivationEngine — evaluates schema rules against graph state."""

import pytest
from unittest.mock import MagicMock
from types import SimpleNamespace

from contextcore.schema.sdl import DerivationRule
from contextcore.schema.derivation import DerivationEngine


def _make_node(node_id, node_type, properties=None):
    """Create a mock node with .id, .node_type, and .properties."""
    n = SimpleNamespace()
    n.id = node_id
    n.node_type = node_type
    n.properties = properties or {}
    return n


def _mock_db(nodes_by_type=None, edges_from=None):
    """Build a mock db with csr_adapter exposing get_all_nodes / get_neighbors."""
    db = MagicMock()
    nodes_by_type = nodes_by_type or {}
    edges_from = edges_from or {}

    def get_all_nodes(node_type=None):
        if node_type and node_type in nodes_by_type:
            return nodes_by_type[node_type]
        return []

    def get_neighbors(node_id, edge_type=None):
        return edges_from.get(node_id, [])

    db.csr_adapter.get_all_nodes = get_all_nodes
    db.csr_adapter.get_neighbors = get_neighbors
    return db


# ── Edge-count / create_cu tests ─────────────────────────────────────

class TestEdgeCountRule:
    def test_create_cu_rule_fires(self):
        """Condition with 4 TREATED_WITH edges fires create_cu."""
        rule = DerivationRule(
            when={"node": "Condition", "has_edge": "TREATED_WITH", "count": ">3"},
            action="create_cu",
            params={"topic_from": "Condition.name"},
        )
        c1 = _make_node("c1", "Condition", {"name": "Diabetes"})
        neighbors = [("t1", None), ("t2", None), ("t3", None), ("t4", None)]
        db = _mock_db(
            nodes_by_type={"Condition": [c1]},
            edges_from={"c1": neighbors},
        )

        engine = DerivationEngine([rule])
        actions = engine.evaluate(db)

        assert len(actions) == 1
        act = actions[0]
        assert act["action"] == "create_cu"
        assert act["node_id"] == "c1"
        assert act["node_type"] == "Condition"
        assert act["topic"] == "Diabetes"
        assert act["edge_count"] == 4

    def test_create_cu_rule_does_not_fire_below_threshold(self):
        """Condition with 2 edges does NOT fire when threshold is >3."""
        rule = DerivationRule(
            when={"node": "Condition", "has_edge": "TREATED_WITH", "count": ">3"},
            action="create_cu",
            params={"topic_from": "Condition.name"},
        )
        c1 = _make_node("c1", "Condition", {"name": "Diabetes"})
        neighbors = [("t1", None), ("t2", None)]
        db = _mock_db(
            nodes_by_type={"Condition": [c1]},
            edges_from={"c1": neighbors},
        )

        engine = DerivationEngine([rule])
        actions = engine.evaluate(db)

        assert len(actions) == 0

    def test_create_cu_gte_operator(self):
        """Test >=3 fires when count is exactly 3."""
        rule = DerivationRule(
            when={"node": "Condition", "has_edge": "TREATED_WITH", "count": ">=3"},
            action="create_cu",
            params={"topic_from": "Condition.name"},
        )
        c1 = _make_node("c1", "Condition", {"name": "Flu"})
        neighbors = [("t1", None), ("t2", None), ("t3", None)]
        db = _mock_db(
            nodes_by_type={"Condition": [c1]},
            edges_from={"c1": neighbors},
        )

        engine = DerivationEngine([rule])
        actions = engine.evaluate(db)

        assert len(actions) == 1


# ── Property-match / boost tests ─────────────────────────────────────

class TestPropertyMatchRule:
    def test_boost_rule_fires(self):
        """Risk with severity=critical fires boost with priority=2.0."""
        rule = DerivationRule(
            when={"node": "Risk", "property": "severity", "equals": "critical"},
            action="boost",
            params={"priority": 2.0},
        )
        r1 = _make_node("r1", "Risk", {"severity": "critical"})
        db = _mock_db(nodes_by_type={"Risk": [r1]})

        engine = DerivationEngine([rule])
        actions = engine.evaluate(db)

        assert len(actions) == 1
        act = actions[0]
        assert act["action"] == "boost"
        assert act["node_id"] == "r1"
        assert act["node_type"] == "Risk"
        assert act["priority"] == 2.0

    def test_boost_rule_no_match(self):
        """Risk with severity=low does not fire."""
        rule = DerivationRule(
            when={"node": "Risk", "property": "severity", "equals": "critical"},
            action="boost",
            params={"priority": 2.0},
        )
        r1 = _make_node("r1", "Risk", {"severity": "low"})
        db = _mock_db(nodes_by_type={"Risk": [r1]})

        engine = DerivationEngine([rule])
        actions = engine.evaluate(db)

        assert len(actions) == 0


# ── Shared-field / create_edge tests ─────────────────────────────────

class TestSharedFieldRule:
    def test_create_edge_rule(self):
        """Two Treatments sharing condition_name creates an edge."""
        rule = DerivationRule(
            when={"node": "Treatment", "shares_field": "condition_name", "with": "Treatment"},
            action="create_edge",
            params={"edge_type": "ALTERNATIVE_TO", "confidence": 0.6},
        )
        t1 = _make_node("t1", "Treatment", {"condition_name": "Diabetes"})
        t2 = _make_node("t2", "Treatment", {"condition_name": "Diabetes"})
        t3 = _make_node("t3", "Treatment", {"condition_name": "Flu"})
        db = _mock_db(nodes_by_type={"Treatment": [t1, t2, t3]})

        engine = DerivationEngine([rule])
        actions = engine.evaluate(db)

        assert len(actions) == 1
        act = actions[0]
        assert act["action"] == "create_edge"
        assert set([act["source"], act["target"]]) == {"t1", "t2"}
        assert act["edge_type"] == "ALTERNATIVE_TO"
        assert act["confidence"] == 0.6
        assert act["shared_value"] == "Diabetes"

    def test_create_edge_three_nodes_same_field(self):
        """Three nodes sharing a field creates 3 edges (all pairs)."""
        rule = DerivationRule(
            when={"node": "Treatment", "shares_field": "condition_name", "with": "Treatment"},
            action="create_edge",
            params={"edge_type": "ALTERNATIVE_TO", "confidence": 0.5},
        )
        t1 = _make_node("t1", "Treatment", {"condition_name": "Diabetes"})
        t2 = _make_node("t2", "Treatment", {"condition_name": "Diabetes"})
        t3 = _make_node("t3", "Treatment", {"condition_name": "Diabetes"})
        db = _mock_db(nodes_by_type={"Treatment": [t1, t2, t3]})

        engine = DerivationEngine([rule])
        actions = engine.evaluate(db)

        assert len(actions) == 3  # t1-t2, t1-t3, t2-t3

    def test_create_edge_no_shared_values(self):
        """Nodes with different field values produce no edges."""
        rule = DerivationRule(
            when={"node": "Treatment", "shares_field": "condition_name", "with": "Treatment"},
            action="create_edge",
            params={"edge_type": "ALTERNATIVE_TO", "confidence": 0.6},
        )
        t1 = _make_node("t1", "Treatment", {"condition_name": "Diabetes"})
        t2 = _make_node("t2", "Treatment", {"condition_name": "Flu"})
        db = _mock_db(nodes_by_type={"Treatment": [t1, t2]})

        engine = DerivationEngine([rule])
        actions = engine.evaluate(db)

        assert len(actions) == 0


# ── Misc tests ────────────────────────────────────────────────────────

class TestMisc:
    def test_no_rules_returns_empty(self):
        db = _mock_db()
        engine = DerivationEngine([])
        assert engine.evaluate(db) == []

    def test_multiple_rules(self):
        """Multiple rules of different types all evaluate."""
        rules = [
            DerivationRule(
                when={"node": "Risk", "property": "severity", "equals": "critical"},
                action="boost",
                params={"priority": 2.0},
            ),
            DerivationRule(
                when={"node": "Condition", "has_edge": "TREATED_WITH", "count": ">1"},
                action="create_cu",
                params={"topic_from": "Condition.name"},
            ),
        ]
        r1 = _make_node("r1", "Risk", {"severity": "critical"})
        c1 = _make_node("c1", "Condition", {"name": "Flu"})
        db = _mock_db(
            nodes_by_type={"Risk": [r1], "Condition": [c1]},
            edges_from={"c1": [("t1", None), ("t2", None)]},
        )

        engine = DerivationEngine(rules)
        actions = engine.evaluate(db)

        assert len(actions) == 2
        action_types = {a["action"] for a in actions}
        assert action_types == {"boost", "create_cu"}
