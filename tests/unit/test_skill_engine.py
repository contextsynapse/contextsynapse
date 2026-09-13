"""Tests for SkillEngine — orchestrator that invokes skills and evaluates rules."""
import os
import pytest
from unittest.mock import MagicMock
from dataclasses import dataclass, field
from typing import Dict, Any, List

from contextcore.skills.engine import SkillEngine
from contextcore.skills.loader import SkillLoader
from contextcore.skills.models import SkillDefinition, SkillResult, SkillStatus
from contextcore.rules.engine import UnifiedRulesEngine
from contextcore.rules.models import RuleAction, RuleDefinition, RuleSeverity, RuleTrigger


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


SKILLS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "contextcore", "config", "skills"
)
RULES_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "contextcore", "config", "schemas", "pms_rules.yaml"
)


@pytest.fixture
def engine():
    loader = SkillLoader()
    if os.path.isdir(SKILLS_DIR):
        loader.load_from_directory(SKILLS_DIR)
    rules = None
    if os.path.isfile(RULES_PATH):
        rules = UnifiedRulesEngine.from_yaml(RULES_PATH)
    return SkillEngine(skill_loader=loader, rules_engine=rules)


class TestSkillEngineBasic:

    def test_list_skills(self, engine):
        skills = engine.list_skills()
        assert len(skills) >= 5
        assert "compliance-check" in skills

    def test_get_skill(self, engine):
        skill = engine.get_skill("compliance-check")
        assert skill is not None
        assert skill.name == "compliance-check"

    def test_get_skill_not_found(self, engine):
        assert engine.get_skill("nonexistent") is None


class TestSkillInvocation:

    def test_invoke_compliance_check_passes(self, engine):
        """Compliant portfolio should result in SUCCESS."""
        holdings = [
            FakeNode("h1", "Holding", {"stock_symbol": "TCS", "weight": 0.08, "sector": "IT", "related_party_group": "tata"}),
            FakeNode("h2", "Holding", {"stock_symbol": "HDFC", "weight": 0.06, "sector": "Banking", "related_party_group": "hdfc"}),
        ]
        db = make_db(holdings)
        result = engine.invoke("compliance-check", db)
        assert result.status in (SkillStatus.SUCCESS, SkillStatus.PARTIAL)
        assert not result.has_blocks()

    def test_invoke_compliance_check_blocked(self, engine):
        """Portfolio with 12% single stock should be BLOCKED."""
        holdings = [
            FakeNode("h1", "Holding", {"stock_symbol": "TCS", "weight": 0.12, "sector": "IT", "related_party_group": "tata"}),
            FakeNode("h2", "Holding", {"stock_symbol": "HDFC", "weight": 0.06, "sector": "Banking", "related_party_group": "hdfc"}),
        ]
        db = make_db(holdings)
        result = engine.invoke("compliance-check", db)
        assert result.status == SkillStatus.BLOCKED
        assert result.has_blocks()
        assert any("TCS" in r.message for r in result.rule_results)

    def test_invoke_pre_trade_check_with_context(self, engine):
        """Pre-trade check with restricted stock should block."""
        nodes = [
            FakeNode("r1", "RestrictedStock", {"stock_symbol": "BAD.NS", "reason": "SEBI ban"}),
        ]
        db = make_db(nodes)
        result = engine.invoke(
            "pre-trade-check", db,
            action_context={"stock_symbol": "BAD.NS"},
        )
        assert result.has_blocks()

    def test_invoke_unknown_skill_returns_error(self, engine):
        db = make_db([])
        result = engine.invoke("nonexistent-skill", db)
        assert result.status == SkillStatus.ERROR

    def test_invoke_without_rules_engine(self):
        """Engine without rules should still work — just no rule evaluation."""
        loader = SkillLoader()
        skill = SkillDefinition.from_dict({
            "name": "test-skill",
            "description": "test",
            "rules": [],
        })
        loader._skills["test-skill"] = skill
        engine = SkillEngine(skill_loader=loader, rules_engine=None)
        db = make_db([])
        result = engine.invoke("test-skill", db)
        assert result.status == SkillStatus.SUCCESS
        assert len(result.rule_results) == 0

    def test_result_contains_skill_name(self, engine):
        db = make_db([])
        result = engine.invoke("compliance-check", db)
        assert result.skill_name == "compliance-check"
