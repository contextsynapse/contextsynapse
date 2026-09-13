"""Tests for rule engine data models."""
import pytest
from datetime import datetime, timezone

from contextcore.rules.models import (
    RuleAction, RuleDefinition, RuleResult, RuleSeverity, RuleTrigger,
)


class TestRuleTrigger:

    def test_all_triggers_exist(self):
        assert RuleTrigger.ON_INGEST.value == "on_ingest"
        assert RuleTrigger.ON_TRADE.value == "on_trade"
        assert RuleTrigger.ON_SIGNAL.value == "on_signal"
        assert RuleTrigger.ON_SCHEDULE.value == "on_schedule"
        assert RuleTrigger.ON_SKILL.value == "on_skill"
        assert RuleTrigger.ON_CHANGE.value == "on_change"
        assert RuleTrigger.ON_DEMAND.value == "on_demand"

    def test_trigger_from_string(self):
        assert RuleTrigger("on_trade") == RuleTrigger.ON_TRADE


class TestRuleAction:

    def test_all_actions_exist(self):
        assert RuleAction.ALLOW.value == "allow"
        assert RuleAction.BLOCK.value == "block"
        assert RuleAction.WARN.value == "warn"
        assert RuleAction.ALERT.value == "alert"
        assert RuleAction.ESCALATE.value == "escalate"
        assert RuleAction.INVOKE.value == "invoke"
        assert RuleAction.BOOST.value == "boost"
        assert RuleAction.LOG.value == "log"


class TestRuleSeverity:

    def test_ordering(self):
        assert RuleSeverity.CRITICAL.value == "critical"
        assert RuleSeverity.HIGH.value == "high"
        assert RuleSeverity.MEDIUM.value == "medium"
        assert RuleSeverity.LOW.value == "low"
        assert RuleSeverity.INFO.value == "info"


class TestRuleDefinition:

    def test_from_dict_aggregate(self):
        raw = {
            "type": "aggregate",
            "trigger": ["on_trade", "on_schedule"],
            "condition": {
                "aggregate": "sum",
                "field": "weight",
                "group_by": "stock_id",
                "operator": ">",
                "threshold": 0.10,
            },
            "action": "block",
            "severity": "critical",
            "message": "SEBI limit: Single stock cannot exceed 10%",
        }
        rule = RuleDefinition.from_dict("sebi_single_stock", raw)
        assert rule.name == "sebi_single_stock"
        assert rule.rule_type == "aggregate"
        assert RuleTrigger.ON_TRADE in rule.triggers
        assert RuleTrigger.ON_SCHEDULE in rule.triggers
        assert rule.action == RuleAction.BLOCK
        assert rule.severity == RuleSeverity.CRITICAL
        assert rule.condition["aggregate"] == "sum"
        assert rule.condition["threshold"] == 0.10

    def test_from_dict_temporal(self):
        raw = {
            "type": "temporal",
            "trigger": ["on_ingest"],
            "condition": {
                "sensor": "fii_flows",
                "field": "net_value",
                "operator": "<",
                "threshold": 0,
                "consecutive_days": 3,
            },
            "action": "alert",
            "severity": "medium",
            "message": "FII selling for {days} consecutive days",
        }
        rule = RuleDefinition.from_dict("fii_selling_streak", raw)
        assert rule.rule_type == "temporal"
        assert rule.condition["consecutive_days"] == 3
        assert rule.action == RuleAction.ALERT

    def test_from_dict_pre_action(self):
        raw = {
            "type": "pre_action",
            "trigger": ["on_trade"],
            "condition": {
                "check": "restricted_list",
                "field": "stock_id",
            },
            "action": "block",
            "severity": "critical",
            "message": "Stock is on restricted list",
        }
        rule = RuleDefinition.from_dict("pre_trade_restricted", raw)
        assert rule.rule_type == "pre_action"
        assert rule.action == RuleAction.BLOCK

    def test_from_dict_with_invoke_skill(self):
        raw = {
            "type": "aggregate",
            "trigger": ["on_change"],
            "condition": {"aggregate": "max_drift", "operator": ">", "threshold": 0.05},
            "action": "alert",
            "severity": "high",
            "message": "Drift exceeds 5%",
            "invoke_skill": "/rebalance",
        }
        rule = RuleDefinition.from_dict("drift_alert", raw)
        assert rule.invoke_skill == "/rebalance"

    def test_from_dict_with_override(self):
        raw = {
            "type": "aggregate",
            "trigger": ["on_trade"],
            "condition": {"aggregate": "sum", "field": "weight", "group_by": "stock_id", "operator": ">", "threshold": 0.10},
            "action": "block",
            "severity": "critical",
            "message": "SEBI breach",
            "override": "compliance_officer_only",
        }
        rule = RuleDefinition.from_dict("sebi_limit", raw)
        assert rule.override == "compliance_officer_only"

    def test_from_dict_defaults(self):
        raw = {
            "type": "aggregate",
            "trigger": ["on_demand"],
            "condition": {},
            "action": "log",
            "severity": "info",
            "message": "test",
        }
        rule = RuleDefinition.from_dict("minimal", raw)
        assert rule.invoke_skill is None
        assert rule.override is None
        assert rule.configurable == []


class TestRuleResult:

    def test_create_result(self):
        result = RuleResult(
            rule_name="sebi_single_stock",
            action=RuleAction.BLOCK,
            severity=RuleSeverity.CRITICAL,
            message="TCS weight 12% exceeds 10% limit",
            details={"stock": "TCS", "weight": 0.12, "limit": 0.10},
        )
        assert result.rule_name == "sebi_single_stock"
        assert result.action == RuleAction.BLOCK
        assert result.triggered_at != ""

    def test_to_dict(self):
        result = RuleResult(
            rule_name="test",
            action=RuleAction.WARN,
            severity=RuleSeverity.MEDIUM,
            message="warning",
            details={"key": "value"},
        )
        d = result.to_dict()
        assert d["rule_name"] == "test"
        assert d["action"] == "warn"
        assert d["severity"] == "medium"
        assert d["details"]["key"] == "value"
        assert "triggered_at" in d

    def test_result_with_invoke_skill(self):
        result = RuleResult(
            rule_name="drift",
            action=RuleAction.INVOKE,
            severity=RuleSeverity.HIGH,
            message="drift detected",
            details={},
            invoke_skill="/rebalance",
        )
        d = result.to_dict()
        assert d["invoke_skill"] == "/rebalance"
