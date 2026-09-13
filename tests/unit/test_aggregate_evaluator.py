"""Tests for aggregate rule evaluator — SUM, GROUP BY, TOP-N portfolio rules."""
import pytest
from unittest.mock import MagicMock
from dataclasses import dataclass, field
from typing import Dict, Any, List

from contextcore.rules.models import RuleAction, RuleDefinition, RuleSeverity
from contextcore.rules.evaluators.aggregate import AggregateEvaluator


@dataclass
class FakeNode:
    id: str
    label: str
    properties: Dict[str, Any] = field(default_factory=dict)


def make_holdings(weights_by_stock: Dict[str, Dict[str, Any]]) -> List[FakeNode]:
    """Create Holding nodes with given weights and sectors."""
    nodes = []
    for symbol, props in weights_by_stock.items():
        nodes.append(FakeNode(
            id=f"h_{symbol}",
            label="Holding",
            properties={"stock_symbol": symbol, **props},
        ))
    return nodes


def make_db(nodes: List[FakeNode]):
    """Create a mock db adapter with get_all_nodes."""
    db = MagicMock()
    def get_all_nodes(node_type=None):
        if node_type:
            return [n for n in nodes if n.label == node_type]
        return nodes
    db.get_all_nodes = get_all_nodes
    # Also handle csr_adapter pattern
    db.csr_adapter = db
    return db


class TestSumGroupBy:
    """Test SUM with GROUP BY — e.g., single stock weight limit."""

    def test_no_breach(self):
        holdings = make_holdings({
            "TCS": {"weight": 0.08, "sector": "IT"},
            "INFY": {"weight": 0.07, "sector": "IT"},
            "HDFC": {"weight": 0.06, "sector": "Banking"},
        })
        db = make_db(holdings)
        rule = RuleDefinition.from_dict("sebi_stock", {
            "type": "aggregate",
            "trigger": ["on_trade"],
            "condition": {
                "aggregate": "sum",
                "node_type": "Holding",
                "field": "weight",
                "group_by": "stock_symbol",
                "operator": ">",
                "threshold": 0.10,
            },
            "action": "block",
            "severity": "critical",
            "message": "Single stock {group} at {value:.1%} exceeds {threshold:.0%}",
        })
        evaluator = AggregateEvaluator()
        results = evaluator.evaluate(rule, db)
        assert len(results) == 0  # no stock exceeds 10%

    def test_single_stock_breach(self):
        holdings = make_holdings({
            "TCS": {"weight": 0.12, "sector": "IT"},
            "INFY": {"weight": 0.07, "sector": "IT"},
            "HDFC": {"weight": 0.06, "sector": "Banking"},
        })
        db = make_db(holdings)
        rule = RuleDefinition.from_dict("sebi_stock", {
            "type": "aggregate",
            "trigger": ["on_trade"],
            "condition": {
                "aggregate": "sum",
                "node_type": "Holding",
                "field": "weight",
                "group_by": "stock_symbol",
                "operator": ">",
                "threshold": 0.10,
            },
            "action": "block",
            "severity": "critical",
            "message": "Stock {group} at {value:.1%} exceeds {threshold:.0%}",
        })
        evaluator = AggregateEvaluator()
        results = evaluator.evaluate(rule, db)
        assert len(results) == 1
        assert results[0].action == RuleAction.BLOCK
        assert results[0].severity == RuleSeverity.CRITICAL
        assert "TCS" in results[0].message
        assert results[0].details["group"] == "TCS"
        assert results[0].details["value"] == 0.12

    def test_sector_breach(self):
        holdings = make_holdings({
            "TCS": {"weight": 0.10, "sector": "IT"},
            "INFY": {"weight": 0.09, "sector": "IT"},
            "WIPRO": {"weight": 0.08, "sector": "IT"},
            "HDFC": {"weight": 0.06, "sector": "Banking"},
        })
        db = make_db(holdings)
        rule = RuleDefinition.from_dict("sebi_sector", {
            "type": "aggregate",
            "trigger": ["on_trade"],
            "condition": {
                "aggregate": "sum",
                "node_type": "Holding",
                "field": "weight",
                "group_by": "sector",
                "operator": ">",
                "threshold": 0.25,
            },
            "action": "block",
            "severity": "critical",
            "message": "Sector {group} at {value:.1%} exceeds {threshold:.0%}",
        })
        evaluator = AggregateEvaluator()
        results = evaluator.evaluate(rule, db)
        assert len(results) == 1
        assert results[0].details["group"] == "IT"
        assert abs(results[0].details["value"] - 0.27) < 0.01

    def test_multiple_breaches(self):
        holdings = make_holdings({
            "TCS": {"weight": 0.15, "sector": "IT"},
            "RELIANCE": {"weight": 0.12, "sector": "Energy"},
        })
        db = make_db(holdings)
        rule = RuleDefinition.from_dict("stock_limit", {
            "type": "aggregate",
            "trigger": ["on_trade"],
            "condition": {
                "aggregate": "sum",
                "node_type": "Holding",
                "field": "weight",
                "group_by": "stock_symbol",
                "operator": ">",
                "threshold": 0.10,
            },
            "action": "block",
            "severity": "critical",
            "message": "{group} at {value:.1%}",
        })
        evaluator = AggregateEvaluator()
        results = evaluator.evaluate(rule, db)
        assert len(results) == 2


class TestTopNSum:
    """Test TOP-N aggregation — e.g., top 5 holdings concentration."""

    def test_top5_no_breach(self):
        holdings = make_holdings({
            "A": {"weight": 0.07}, "B": {"weight": 0.07},
            "C": {"weight": 0.06}, "D": {"weight": 0.06},
            "E": {"weight": 0.05}, "F": {"weight": 0.05},
        })
        db = make_db(holdings)
        rule = RuleDefinition.from_dict("top5", {
            "type": "aggregate",
            "trigger": ["on_trade"],
            "condition": {
                "aggregate": "top_n_sum",
                "node_type": "Holding",
                "field": "weight",
                "n": 5,
                "operator": ">",
                "threshold": 0.40,
            },
            "action": "warn",
            "severity": "medium",
            "message": "Top 5 concentration at {value:.1%}",
        })
        evaluator = AggregateEvaluator()
        results = evaluator.evaluate(rule, db)
        assert len(results) == 0  # 0.07+0.07+0.06+0.06+0.05 = 0.31 < 0.40

    def test_top5_breach(self):
        holdings = make_holdings({
            "A": {"weight": 0.12}, "B": {"weight": 0.10},
            "C": {"weight": 0.09}, "D": {"weight": 0.08},
            "E": {"weight": 0.07}, "F": {"weight": 0.04},
        })
        db = make_db(holdings)
        rule = RuleDefinition.from_dict("top5", {
            "type": "aggregate",
            "trigger": ["on_trade"],
            "condition": {
                "aggregate": "top_n_sum",
                "node_type": "Holding",
                "field": "weight",
                "n": 5,
                "operator": ">",
                "threshold": 0.40,
            },
            "action": "warn",
            "severity": "medium",
            "message": "Top 5 at {value:.1%}",
        })
        evaluator = AggregateEvaluator()
        results = evaluator.evaluate(rule, db)
        assert len(results) == 1  # 0.12+0.10+0.09+0.08+0.07 = 0.46 > 0.40
        assert abs(results[0].details["value"] - 0.46) < 0.01


class TestCount:
    """Test COUNT aggregation — e.g., large cap ratio."""

    def test_count_ratio(self):
        holdings = make_holdings({
            "A": {"weight": 0.10, "market_cap_rank": 50},
            "B": {"weight": 0.10, "market_cap_rank": 80},
            "C": {"weight": 0.10, "market_cap_rank": 150},
            "D": {"weight": 0.10, "market_cap_rank": 200},
        })
        db = make_db(holdings)
        rule = RuleDefinition.from_dict("large_cap_ratio", {
            "type": "aggregate",
            "trigger": ["on_schedule"],
            "condition": {
                "aggregate": "count_ratio",
                "node_type": "Holding",
                "where_field": "market_cap_rank",
                "where_operator": "<=",
                "where_threshold": 100,
                "operator": "<",
                "threshold": 0.80,
            },
            "action": "warn",
            "severity": "high",
            "message": "Large cap ratio {value:.0%} below {threshold:.0%}",
        })
        evaluator = AggregateEvaluator()
        results = evaluator.evaluate(rule, db)
        assert len(results) == 1  # 2/4 = 50% < 80%
        assert abs(results[0].details["value"] - 0.50) < 0.01


class TestEdgeCases:

    def test_empty_graph(self):
        db = make_db([])
        rule = RuleDefinition.from_dict("empty", {
            "type": "aggregate",
            "trigger": ["on_demand"],
            "condition": {
                "aggregate": "sum",
                "node_type": "Holding",
                "field": "weight",
                "group_by": "stock_symbol",
                "operator": ">",
                "threshold": 0.10,
            },
            "action": "block",
            "severity": "critical",
            "message": "breach",
        })
        evaluator = AggregateEvaluator()
        results = evaluator.evaluate(rule, db)
        assert len(results) == 0

    def test_missing_field_skipped(self):
        nodes = [FakeNode(id="h1", label="Holding", properties={"stock_symbol": "TCS"})]
        db = make_db(nodes)
        rule = RuleDefinition.from_dict("missing", {
            "type": "aggregate",
            "trigger": ["on_demand"],
            "condition": {
                "aggregate": "sum",
                "node_type": "Holding",
                "field": "weight",
                "group_by": "stock_symbol",
                "operator": ">",
                "threshold": 0.10,
            },
            "action": "block",
            "severity": "critical",
            "message": "breach",
        })
        evaluator = AggregateEvaluator()
        results = evaluator.evaluate(rule, db)
        assert len(results) == 0  # weight is missing, treated as 0

    def test_invoke_skill_in_result(self):
        holdings = make_holdings({"TCS": {"weight": 0.15}})
        db = make_db(holdings)
        rule = RuleDefinition.from_dict("with_skill", {
            "type": "aggregate",
            "trigger": ["on_change"],
            "condition": {
                "aggregate": "sum",
                "node_type": "Holding",
                "field": "weight",
                "group_by": "stock_symbol",
                "operator": ">",
                "threshold": 0.10,
            },
            "action": "alert",
            "severity": "high",
            "message": "{group} overweight",
            "invoke_skill": "/rebalance",
        })
        evaluator = AggregateEvaluator()
        results = evaluator.evaluate(rule, db)
        assert len(results) == 1
        assert results[0].invoke_skill == "/rebalance"
