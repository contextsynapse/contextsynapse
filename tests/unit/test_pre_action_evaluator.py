"""Tests for pre-action evaluator — pre-trade compliance checks."""
import pytest
from unittest.mock import MagicMock
from dataclasses import dataclass, field
from typing import Dict, Any, List

from contextcore.rules.models import RuleAction, RuleDefinition, RuleSeverity
from contextcore.rules.evaluators.pre_action import PreActionEvaluator


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


class TestRestrictedList:

    def test_stock_on_restricted_list(self):
        restricted = [
            FakeNode("r1", "RestrictedStock", {"stock_symbol": "ADANI.NS", "reason": "SEBI investigation"}),
            FakeNode("r2", "RestrictedStock", {"stock_symbol": "DHFL.NS", "reason": "Suspended"}),
        ]
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
            "message": "Stock {stock} is on restricted list: {reason}",
        })
        evaluator = PreActionEvaluator()
        action_context = {"stock_symbol": "ADANI.NS", "action": "buy", "quantity": 100}
        results = evaluator.evaluate(rule, db, action_context)
        assert len(results) == 1
        assert results[0].action == RuleAction.BLOCK
        assert "ADANI.NS" in results[0].message

    def test_stock_not_on_restricted_list(self):
        restricted = [
            FakeNode("r1", "RestrictedStock", {"stock_symbol": "ADANI.NS"}),
        ]
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
            "message": "blocked",
        })
        evaluator = PreActionEvaluator()
        action_context = {"stock_symbol": "TCS.NS", "action": "buy"}
        results = evaluator.evaluate(rule, db, action_context)
        assert len(results) == 0


class TestClientConstraint:

    def test_client_constraint_blocks(self):
        constraints = [
            FakeNode("c1", "ClientConstraint", {
                "client_id": "CLT001",
                "stock_symbol": "RELIANCE.NS",
                "constraint": "do_not_sell",
            }),
        ]
        db = make_db(constraints)
        rule = RuleDefinition.from_dict("client_constraint", {
            "type": "pre_action",
            "trigger": ["on_trade"],
            "condition": {
                "check": "client_constraint",
                "list_node_type": "ClientConstraint",
                "match_field": "stock_symbol",
                "client_field": "client_id",
                "constraint_field": "constraint",
            },
            "action": "block",
            "severity": "high",
            "message": "Client {client_id} has constraint: {constraint}",
        })
        evaluator = PreActionEvaluator()
        action_context = {
            "stock_symbol": "RELIANCE.NS",
            "action": "sell",
            "client_id": "CLT001",
        }
        results = evaluator.evaluate(rule, db, action_context)
        assert len(results) == 1
        assert results[0].action == RuleAction.BLOCK

    def test_different_client_not_blocked(self):
        constraints = [
            FakeNode("c1", "ClientConstraint", {
                "client_id": "CLT001",
                "stock_symbol": "RELIANCE.NS",
                "constraint": "do_not_sell",
            }),
        ]
        db = make_db(constraints)
        rule = RuleDefinition.from_dict("client_constraint", {
            "type": "pre_action",
            "trigger": ["on_trade"],
            "condition": {
                "check": "client_constraint",
                "list_node_type": "ClientConstraint",
                "match_field": "stock_symbol",
                "client_field": "client_id",
                "constraint_field": "constraint",
            },
            "action": "block",
            "severity": "high",
            "message": "blocked",
        })
        evaluator = PreActionEvaluator()
        action_context = {
            "stock_symbol": "RELIANCE.NS",
            "action": "sell",
            "client_id": "CLT002",  # different client
        }
        results = evaluator.evaluate(rule, db, action_context)
        assert len(results) == 0
