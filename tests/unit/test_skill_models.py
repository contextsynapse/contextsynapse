"""Tests for skill engine data models."""
import pytest

from contextcore.skills.models import (
    SkillChainRule, SkillContextRequirement, SkillDefinition,
    SkillResult, SkillStatus, SkillTier,
)
from contextcore.rules.models import RuleAction, RuleResult, RuleSeverity


class TestSkillTier:
    def test_tiers_exist(self):
        assert SkillTier.INSTANT.value == "instant"
        assert SkillTier.FAST.value == "fast"
        assert SkillTier.STANDARD.value == "standard"
        assert SkillTier.DEEP.value == "deep"


class TestSkillStatus:
    def test_statuses_exist(self):
        assert SkillStatus.SUCCESS.value == "success"
        assert SkillStatus.PARTIAL.value == "partial"
        assert SkillStatus.BLOCKED.value == "blocked"
        assert SkillStatus.ERROR.value == "error"


class TestSkillContextRequirement:
    def test_defaults(self):
        req = SkillContextRequirement(type="portfolio")
        assert req.role == "input"
        assert req.required is True
        assert req.source is None


class TestSkillChainRule:
    def test_create(self):
        chain = SkillChainRule(
            skill="/rebalance",
            when="status == BREACH",
            reason="Auto-suggest rebalancing",
        )
        assert chain.skill == "/rebalance"
        assert chain.when == "status == BREACH"


class TestSkillDefinition:

    def test_from_dict_compliance_check(self):
        raw = {
            "name": "compliance-check",
            "description": "Validate portfolio against SEBI rules",
            "version": "1.0",
            "trigger": ["pre_trade", "daily_close", "on_demand"],
            "contexts": {
                "required": [
                    {"type": "portfolio", "role": "input"},
                    {"type": "rules", "role": "input", "source": "schema"},
                ],
                "optional": [
                    {"type": "restricted_list", "role": "input"},
                ],
            },
            "projection": {
                "tier": "fast",
                "priority_labels": ["ComplianceRule", "Holding", "Limit"],
                "max_tokens": 1500,
            },
            "rules": [
                "sebi_pms.single_stock_limit",
                "sebi_pms.related_party_limit",
            ],
            "output": {
                "node_type": "ComplianceResult",
                "properties": {
                    "status": {"type": "enum", "values": ["PASS", "WARNING", "BREACH"]},
                    "violations": {"type": "list"},
                },
            },
            "chains_to": [
                {"skill": "/rebalance", "when": "status == BREACH", "reason": "Fix breach"},
                {"skill": "/alert", "when": "status == BREACH", "params": {"severity": "critical"}},
            ],
            "configurable": ["rules", "thresholds"],
        }
        skill = SkillDefinition.from_dict(raw)
        assert skill.name == "compliance-check"
        assert skill.version == "1.0"
        assert len(skill.triggers) == 3
        assert len(skill.contexts["required"]) == 2
        assert skill.contexts["required"][0].type == "portfolio"
        assert len(skill.contexts["optional"]) == 1
        assert skill.projection["tier"] == "fast"
        assert skill.projection["max_tokens"] == 1500
        assert len(skill.rules) == 2
        assert skill.output["node_type"] == "ComplianceResult"
        assert len(skill.chains_to) == 2
        assert skill.chains_to[0].skill == "/rebalance"
        assert skill.chains_to[0].when == "status == BREACH"
        assert skill.configurable == ["rules", "thresholds"]

    def test_from_dict_minimal(self):
        raw = {
            "name": "simple-skill",
            "description": "A simple skill",
        }
        skill = SkillDefinition.from_dict(raw)
        assert skill.name == "simple-skill"
        assert skill.version == "1.0"
        assert skill.triggers == []
        assert skill.contexts == {"required": [], "optional": []}
        assert skill.projection == {}
        assert skill.rules == []
        assert skill.chains_to == []

    def test_from_dict_with_projection(self):
        raw = {
            "name": "deep-analysis",
            "description": "Deep portfolio analysis",
            "projection": {
                "tier": "deep",
                "max_tokens": 8000,
                "priority_labels": ["Holding", "Signal"],
            },
        }
        skill = SkillDefinition.from_dict(raw)
        assert skill.projection["tier"] == "deep"
        assert skill.projection["max_tokens"] == 8000


class TestSkillResult:

    def test_create_success(self):
        result = SkillResult(
            skill_name="compliance-check",
            status=SkillStatus.SUCCESS,
            output={"status": "PASS", "violations": []},
            rule_results=[],
            message="All checks passed",
        )
        assert result.skill_name == "compliance-check"
        assert result.status == SkillStatus.SUCCESS
        assert result.executed_at != ""

    def test_has_blocks_false(self):
        result = SkillResult(
            skill_name="test",
            status=SkillStatus.SUCCESS,
            output={},
            rule_results=[
                RuleResult(rule_name="r1", action=RuleAction.WARN,
                           severity=RuleSeverity.MEDIUM, message="warn"),
            ],
        )
        assert result.has_blocks() is False

    def test_has_blocks_true(self):
        result = SkillResult(
            skill_name="test",
            status=SkillStatus.BLOCKED,
            output={},
            rule_results=[
                RuleResult(rule_name="r1", action=RuleAction.BLOCK,
                           severity=RuleSeverity.CRITICAL, message="blocked"),
            ],
        )
        assert result.has_blocks() is True

    def test_to_dict(self):
        result = SkillResult(
            skill_name="compliance-check",
            status=SkillStatus.SUCCESS,
            output={"status": "PASS"},
            rule_results=[],
            chain_invocations=["/rebalance"],
            message="done",
        )
        d = result.to_dict()
        assert d["skill_name"] == "compliance-check"
        assert d["status"] == "success"
        assert d["output"]["status"] == "PASS"
        assert d["chain_invocations"] == ["/rebalance"]
        assert "executed_at" in d

    def test_chain_invocations_default_empty(self):
        result = SkillResult(
            skill_name="test",
            status=SkillStatus.SUCCESS,
            output={},
            rule_results=[],
        )
        assert result.chain_invocations == []
