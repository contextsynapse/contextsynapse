"""Tests for skill YAML loader."""
import os
import tempfile
import pytest
import yaml

from contextcore.skills.loader import SkillLoader
from contextcore.skills.models import SkillDefinition


SKILLS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "contextcore", "config", "skills"
)


class TestSkillLoader:

    def test_load_from_yaml(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            yaml.dump({
                "name": "test-skill",
                "description": "A test skill",
                "trigger": ["on_demand"],
                "projection": {"tier": "fast"},
            }, f)
            path = f.name
        try:
            loader = SkillLoader()
            skill = loader.load_from_yaml(path)
            assert isinstance(skill, SkillDefinition)
            assert skill.name == "test-skill"
            assert skill.projection["tier"] == "fast"
        finally:
            os.unlink(path)

    def test_load_from_yaml_missing_name_raises(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            yaml.dump({"description": "no name"}, f)
            path = f.name
        try:
            loader = SkillLoader()
            with pytest.raises(ValueError, match="name"):
                loader.load_from_yaml(path)
        finally:
            os.unlink(path)

    def test_load_from_directory(self):
        if not os.path.isdir(SKILLS_DIR):
            pytest.skip("skills config dir not found")
        loader = SkillLoader()
        skills = loader.load_from_directory(SKILLS_DIR)
        assert len(skills) >= 5
        assert "compliance-check" in skills
        assert "pre-trade-check" in skills
        assert "portfolio-health" in skills
        assert "rebalance" in skills
        assert "morning-briefing" in skills

    def test_get_skill_after_load(self):
        if not os.path.isdir(SKILLS_DIR):
            pytest.skip("skills config dir not found")
        loader = SkillLoader()
        loader.load_from_directory(SKILLS_DIR)
        skill = loader.get_skill("compliance-check")
        assert skill is not None
        assert skill.name == "compliance-check"
        assert len(skill.contexts["required"]) >= 1

    def test_get_skill_not_found(self):
        loader = SkillLoader()
        assert loader.get_skill("nonexistent") is None

    def test_compliance_check_skill_structure(self):
        if not os.path.isdir(SKILLS_DIR):
            pytest.skip("skills config dir not found")
        loader = SkillLoader()
        loader.load_from_directory(SKILLS_DIR)
        skill = loader.get_skill("compliance-check")
        assert skill is not None
        assert "pre_trade" in skill.triggers or "daily_close" in skill.triggers
        assert skill.projection.get("tier") == "fast"
        assert len(skill.rules) >= 1
        assert len(skill.chains_to) >= 1

    def test_rebalance_skill_has_chain(self):
        if not os.path.isdir(SKILLS_DIR):
            pytest.skip("skills config dir not found")
        loader = SkillLoader()
        loader.load_from_directory(SKILLS_DIR)
        skill = loader.get_skill("rebalance")
        assert skill is not None
        chain_skills = [c.skill for c in skill.chains_to]
        assert any("compliance" in s or "tax" in s for s in chain_skills)

    def test_morning_briefing_trigger(self):
        if not os.path.isdir(SKILLS_DIR):
            pytest.skip("skills config dir not found")
        loader = SkillLoader()
        loader.load_from_directory(SKILLS_DIR)
        skill = loader.get_skill("morning-briefing")
        assert skill is not None
        assert skill.projection.get("tier") == "deep"
