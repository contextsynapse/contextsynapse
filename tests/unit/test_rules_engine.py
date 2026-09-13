"""Tests for UnifiedRulesEngine — the orchestrator that ties all evaluators together."""
import os
import pytest
from unittest.mock import MagicMock
from dataclasses import dataclass, field
from typing import Dict, Any, List

from contextcore.rules.engine import UnifiedRulesEngine
from contextcore.rules.models import RuleAction, RuleDefinition, RuleResult, RuleSeverity, RuleTrigger


@dataclass
class FakeNode:
    id: str
    label: str
    properties: Dict[str, Any] = field(default_factory=dict)


def make_db(nodes: List[FakeNode]):
    db = MagicMock()
    def get_all_nodes(node_type=None):
        if node_type:
            return [n for n in nodes if n.label == node_type]
        return nodes
    db.get_all_nodes = get_all_nodes
    db.csr_adapter = db
    return db


class TestEngineInit:

    def test_create_with_rules(self):
        rule = RuleDefinition.from_dict("test", {
            "type": "aggregate",
            "trigger": ["on_trade"],
            "condition": {},
            "action": "log",
            "severity": "info",
            "message": "test",
        })
        engine = UnifiedRulesEngine(rules=[rule])
        assert len(engine.rules) == 1

    def test_from_yaml(self):
        rules_path = os.path.join(
            os.path.dirname(__file__), "..", "..",
            "contextcore", "config", "schemas", "pms_rules.yaml"
        )
        if not os.path.isfile(rules_path):
            pytest.skip("pms_rules.yaml not found")
        engine = UnifiedRulesEngine.from_yaml(rules_path)
        assert len(engine.rules) >= 5

    def test_get_rules_for_trigger(self):
        r1 = RuleDefinition.from_dict("r1", {
            "type": "aggregate", "trigger": ["on_trade"],
            "condition": {}, "action": "block", "severity": "critical", "message": "r1",
        })
        r2 = RuleDefinition.from_dict("r2", {
            "type": "temporal", "trigger": ["on_ingest"],
            "condition": {}, "action": "alert", "severity": "medium", "message": "r2",
        })
        r3 = RuleDefinition.from_dict("r3", {
            "type": "aggregate", "trigger": ["on_trade", "on_schedule"],
            "condition": {}, "action": "warn", "severity": "medium", "message": "r3",
        })
        engine = UnifiedRulesEngine(rules=[r1, r2, r3])
        trade_rules = engine.get_rules_for_trigger(RuleTrigger.ON_TRADE)
        assert len(trade_rules) == 2  # r1 and r3
        ingest_rules = engine.get_rules_for_trigger(RuleTrigger.ON_INGEST)
        assert len(ingest_rules) == 1  # r2 only


class TestEngineEvaluate:

    def test_evaluate_aggregate_on_trade(self):
        holdings = [
            FakeNode("h1", "Holding", {"stock_symbol": "TCS", "weight": 0.12, "sector": "IT"}),
            FakeNode("h2", "Holding", {"stock_symbol": "INFY", "weight": 0.07, "sector": "IT"}),
        ]
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
        engine = UnifiedRulesEngine(rules=[rule])
        results = engine.evaluate(RuleTrigger.ON_TRADE, db)
        assert len(results) == 1
        assert results[0].action == RuleAction.BLOCK
        assert "TCS" in results[0].message

    def test_evaluate_pre_action_with_context(self):
        restricted = [FakeNode("r1", "RestrictedStock", {"stock_symbol": "BAD.NS"})]
        db = make_db(restricted)

        rule = RuleDefinition.from_dict("restricted", {
            "type": "pre_action",
            "trigger": ["on_trade"],
            "condition": {
                "check": "restricted_list",
                "list_node_type": "RestrictedStock",
                "match_field": "stock_symbol",
            },
            "action": "block",
            "severity": "critical",
            "message": "{stock} is restricted",
        })
        engine = UnifiedRulesEngine(rules=[rule])
        results = engine.evaluate(
            RuleTrigger.ON_TRADE, db,
            action_context={"stock_symbol": "BAD.NS"},
        )
        assert len(results) == 1
        assert results[0].action == RuleAction.BLOCK

    def test_evaluate_skips_non_matching_triggers(self):
        holdings = [FakeNode("h1", "Holding", {"stock_symbol": "TCS", "weight": 0.15})]
        db = make_db(holdings)

        rule = RuleDefinition.from_dict("trade_only", {
            "type": "aggregate",
            "trigger": ["on_trade"],
            "condition": {
                "aggregate": "sum", "node_type": "Holding",
                "field": "weight", "group_by": "stock_symbol",
                "operator": ">", "threshold": 0.10,
            },
            "action": "block", "severity": "critical", "message": "blocked",
        })
        engine = UnifiedRulesEngine(rules=[rule])
        # Evaluate with ON_INGEST — should NOT fire the ON_TRADE rule
        results = engine.evaluate(RuleTrigger.ON_INGEST, db)
        assert len(results) == 0

    def test_evaluate_multiple_rules_fire(self):
        holdings = [
            FakeNode("h1", "Holding", {"stock_symbol": "TCS", "weight": 0.12, "sector": "IT"}),
            FakeNode("h2", "Holding", {"stock_symbol": "INFY", "weight": 0.11, "sector": "IT"}),
        ]
        db = make_db(holdings)

        stock_rule = RuleDefinition.from_dict("stock", {
            "type": "aggregate", "trigger": ["on_trade"],
            "condition": {
                "aggregate": "sum", "node_type": "Holding",
                "field": "weight", "group_by": "stock_symbol",
                "operator": ">", "threshold": 0.10,
            },
            "action": "block", "severity": "critical",
            "message": "{group} over limit",
        })
        sector_rule = RuleDefinition.from_dict("sector", {
            "type": "aggregate", "trigger": ["on_trade"],
            "condition": {
                "aggregate": "sum", "node_type": "Holding",
                "field": "weight", "group_by": "sector",
                "operator": ">", "threshold": 0.20,
            },
            "action": "warn", "severity": "high",
            "message": "{group} sector concentrated",
        })
        engine = UnifiedRulesEngine(rules=[stock_rule, sector_rule])
        results = engine.evaluate(RuleTrigger.ON_TRADE, db)
        # 2 stocks breach + 1 sector breach = 3 results
        assert len(results) == 3

    def test_evaluate_on_demand_runs_all(self):
        holdings = [FakeNode("h1", "Holding", {"stock_symbol": "TCS", "weight": 0.15})]
        db = make_db(holdings)

        rule = RuleDefinition.from_dict("scheduled", {
            "type": "aggregate", "trigger": ["on_schedule"],
            "condition": {
                "aggregate": "sum", "node_type": "Holding",
                "field": "weight", "group_by": "stock_symbol",
                "operator": ">", "threshold": 0.10,
            },
            "action": "block", "severity": "critical", "message": "breach",
        })
        engine = UnifiedRulesEngine(rules=[rule])
        # ON_DEMAND evaluates ALL rules regardless of trigger
        results = engine.evaluate(RuleTrigger.ON_DEMAND, db)
        assert len(results) == 1

    def test_results_sorted_by_severity(self):
        holdings = [
            FakeNode("h1", "Holding", {"stock_symbol": "TCS", "weight": 0.15, "sector": "IT"}),
        ]
        db = make_db(holdings)

        warn_rule = RuleDefinition.from_dict("warn_rule", {
            "type": "aggregate", "trigger": ["on_trade"],
            "condition": {
                "aggregate": "sum", "node_type": "Holding",
                "field": "weight", "group_by": "stock_symbol",
                "operator": ">", "threshold": 0.10,
            },
            "action": "warn", "severity": "medium", "message": "warn",
        })
        block_rule = RuleDefinition.from_dict("block_rule", {
            "type": "aggregate", "trigger": ["on_trade"],
            "condition": {
                "aggregate": "sum", "node_type": "Holding",
                "field": "weight", "group_by": "sector",
                "operator": ">", "threshold": 0.10,
            },
            "action": "block", "severity": "critical", "message": "block",
        })
        engine = UnifiedRulesEngine(rules=[warn_rule, block_rule])
        results = engine.evaluate(RuleTrigger.ON_TRADE, db)
        # Critical should come before medium
        assert results[0].severity == RuleSeverity.CRITICAL
