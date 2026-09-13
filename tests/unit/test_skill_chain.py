"""Tests for skill chain evaluation — when one skill triggers another."""
import pytest

from contextcore.skills.engine import SkillEngine
from contextcore.skills.loader import SkillLoader
from contextcore.skills.models import (
    SkillChainRule, SkillDefinition, SkillResult, SkillStatus,
)
from contextcore.rules.models import RuleAction, RuleResult, RuleSeverity


@pytest.fixture
def engine_with_skills():
    loader = SkillLoader()

    # Compliance skill that chains to /rebalance on BREACH
    compliance = SkillDefinition.from_dict({
        "name": "compliance-check",
        "description": "Check compliance",
        "chains_to": [
            {"skill": "/rebalance", "when": "status == BLOCKED", "reason": "Fix breach"},
            {"skill": "/alert", "when": "status == BLOCKED", "params": {"severity": "critical"}},
        ],
    })

    # Rebalance skill that chains to /tax-impact and /compliance-check
    rebalance = SkillDefinition.from_dict({
        "name": "rebalance",
        "description": "Rebalance portfolio",
        "chains_to": [
            {"skill": "/tax-impact", "when": "always", "reason": "Calculate tax"},
            {"skill": "/compliance-check", "when": "always", "reason": "Verify compliance"},
        ],
    })

    # Simple skill with no chains
    simple = SkillDefinition.from_dict({
        "name": "simple",
        "description": "No chains",
    })

    loader._skills = {
        "compliance-check": compliance,
        "rebalance": rebalance,
        "simple": simple,
    }
    return SkillEngine(skill_loader=loader, rules_engine=None)


class TestChainEvaluation:

    def test_chains_fire_on_blocked(self, engine_with_skills):
        skill = engine_with_skills.get_skill("compliance-check")
        result = SkillResult(
            skill_name="compliance-check",
            status=SkillStatus.BLOCKED,
            output={},
            rule_results=[
                RuleResult(rule_name="r1", action=RuleAction.BLOCK,
                           severity=RuleSeverity.CRITICAL, message="blocked"),
            ],
        )
        chains = engine_with_skills.evaluate_chains(skill, result)
        assert "/rebalance" in chains
        assert "/alert" in chains

    def test_chains_dont_fire_on_success(self, engine_with_skills):
        skill = engine_with_skills.get_skill("compliance-check")
        result = SkillResult(
            skill_name="compliance-check",
            status=SkillStatus.SUCCESS,
            output={},
            rule_results=[],
        )
        chains = engine_with_skills.evaluate_chains(skill, result)
        assert "/rebalance" not in chains
        assert "/alert" not in chains

    def test_always_chains_fire(self, engine_with_skills):
        skill = engine_with_skills.get_skill("rebalance")
        result = SkillResult(
            skill_name="rebalance",
            status=SkillStatus.SUCCESS,
            output={},
            rule_results=[],
        )
        chains = engine_with_skills.evaluate_chains(skill, result)
        assert "/tax-impact" in chains
        assert "/compliance-check" in chains

    def test_no_chains_empty_list(self, engine_with_skills):
        skill = engine_with_skills.get_skill("simple")
        result = SkillResult(
            skill_name="simple",
            status=SkillStatus.SUCCESS,
            output={},
            rule_results=[],
        )
        chains = engine_with_skills.evaluate_chains(skill, result)
        assert chains == []

    def test_chain_with_partial_status(self, engine_with_skills):
        skill = engine_with_skills.get_skill("compliance-check")
        result = SkillResult(
            skill_name="compliance-check",
            status=SkillStatus.PARTIAL,
            output={},
            rule_results=[
                RuleResult(rule_name="r1", action=RuleAction.WARN,
                           severity=RuleSeverity.MEDIUM, message="warning"),
            ],
        )
        # PARTIAL != BLOCKED, so chains with "status == BLOCKED" should NOT fire
        chains = engine_with_skills.evaluate_chains(skill, result)
        assert "/rebalance" not in chains
