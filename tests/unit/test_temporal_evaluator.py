"""Tests for temporal rule evaluator — consecutive days, duration-based rules."""
import pytest
from unittest.mock import MagicMock
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List

from contextcore.rules.models import RuleAction, RuleDefinition, RuleSeverity
from contextcore.rules.evaluators.temporal import TemporalEvaluator


@dataclass
class FakeNode:
    id: str
    label: str
    properties: Dict[str, Any] = field(default_factory=dict)


def make_daily_nodes(label: str, field_name: str, values: List[float], start_days_ago: int = None) -> List[FakeNode]:
    """Create nodes with daily timestamps and a numeric field.
    values[0] is the oldest, values[-1] is today.
    """
    if start_days_ago is None:
        start_days_ago = len(values) - 1
    now = datetime.now(timezone.utc)
    nodes = []
    for i, val in enumerate(values):
        dt = now - timedelta(days=start_days_ago - i)
        nodes.append(FakeNode(
            id=f"n_{i}",
            label=label,
            properties={
                field_name: val,
                "_created_at": dt.isoformat(),
                "date": dt.strftime("%Y-%m-%d"),
            },
        ))
    return nodes


def make_db(nodes: List[FakeNode]):
    db = MagicMock()
    def get_all_nodes(node_type=None):
        if node_type:
            return [n for n in nodes if n.label == node_type]
        return nodes
    db.get_all_nodes = get_all_nodes
    db.csr_adapter = db
    return db


class TestConsecutiveDays:

    def test_3_consecutive_negative_days(self):
        nodes = make_daily_nodes("Flow", "net_value", [-100, -200, -150], start_days_ago=2)
        db = make_db(nodes)
        rule = RuleDefinition.from_dict("fii_selling", {
            "type": "temporal",
            "trigger": ["on_ingest"],
            "condition": {
                "node_type": "Flow",
                "field": "net_value",
                "operator": "<",
                "threshold": 0,
                "consecutive_days": 3,
                "date_field": "date",
            },
            "action": "alert",
            "severity": "medium",
            "message": "FII selling for {days} consecutive days",
        })
        evaluator = TemporalEvaluator()
        results = evaluator.evaluate(rule, db)
        assert len(results) == 1
        assert results[0].action == RuleAction.ALERT
        assert results[0].details["consecutive_days"] >= 3

    def test_streak_broken(self):
        nodes = make_daily_nodes("Flow", "net_value", [-100, 50, -150], start_days_ago=2)
        db = make_db(nodes)
        rule = RuleDefinition.from_dict("fii_selling", {
            "type": "temporal",
            "trigger": ["on_ingest"],
            "condition": {
                "node_type": "Flow",
                "field": "net_value",
                "operator": "<",
                "threshold": 0,
                "consecutive_days": 3,
                "date_field": "date",
            },
            "action": "alert",
            "severity": "medium",
            "message": "FII selling streak",
        })
        evaluator = TemporalEvaluator()
        results = evaluator.evaluate(rule, db)
        assert len(results) == 0  # streak broken by positive day

    def test_longer_streak_detected(self):
        nodes = make_daily_nodes("Flow", "net_value", [-100, -200, -50, -300, -150], start_days_ago=4)
        db = make_db(nodes)
        rule = RuleDefinition.from_dict("selling_5d", {
            "type": "temporal",
            "trigger": ["on_ingest"],
            "condition": {
                "node_type": "Flow",
                "field": "net_value",
                "operator": "<",
                "threshold": 0,
                "consecutive_days": 3,
                "date_field": "date",
            },
            "action": "alert",
            "severity": "high",
            "message": "Selling for {days} days",
        })
        evaluator = TemporalEvaluator()
        results = evaluator.evaluate(rule, db)
        assert len(results) == 1
        assert results[0].details["consecutive_days"] >= 3

    def test_empty_data(self):
        db = make_db([])
        rule = RuleDefinition.from_dict("empty", {
            "type": "temporal",
            "trigger": ["on_ingest"],
            "condition": {
                "node_type": "Flow",
                "field": "net_value",
                "operator": "<",
                "threshold": 0,
                "consecutive_days": 3,
                "date_field": "date",
            },
            "action": "alert",
            "severity": "medium",
            "message": "streak",
        })
        evaluator = TemporalEvaluator()
        results = evaluator.evaluate(rule, db)
        assert len(results) == 0

    def test_not_enough_days(self):
        nodes = make_daily_nodes("Flow", "net_value", [-100, -200], start_days_ago=1)
        db = make_db(nodes)
        rule = RuleDefinition.from_dict("need_3", {
            "type": "temporal",
            "trigger": ["on_ingest"],
            "condition": {
                "node_type": "Flow",
                "field": "net_value",
                "operator": "<",
                "threshold": 0,
                "consecutive_days": 3,
                "date_field": "date",
            },
            "action": "alert",
            "severity": "medium",
            "message": "need 3 days",
        })
        evaluator = TemporalEvaluator()
        results = evaluator.evaluate(rule, db)
        assert len(results) == 0  # only 2 days of data

    def test_invoke_skill_propagated(self):
        nodes = make_daily_nodes("Drift", "drift_pct", [0.06, 0.07, 0.08], start_days_ago=2)
        db = make_db(nodes)
        rule = RuleDefinition.from_dict("drift_persist", {
            "type": "temporal",
            "trigger": ["on_schedule"],
            "condition": {
                "node_type": "Drift",
                "field": "drift_pct",
                "operator": ">",
                "threshold": 0.05,
                "consecutive_days": 3,
                "date_field": "date",
            },
            "action": "escalate",
            "severity": "high",
            "message": "Drift persisted",
            "invoke_skill": "/rebalance",
        })
        evaluator = TemporalEvaluator()
        results = evaluator.evaluate(rule, db)
        assert len(results) == 1
        assert results[0].invoke_skill == "/rebalance"
