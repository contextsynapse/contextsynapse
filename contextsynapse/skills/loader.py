"""SkillLoader — loads skill definitions from YAML files.

Usage:
    loader = SkillLoader()
    loader.load_from_directory("config/skills/")
    skill = loader.get_skill("compliance-check")
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

import yaml

from .models import SkillDefinition

logger = logging.getLogger(__name__)


class SkillLoader:
    """Loads SkillDefinition objects from YAML files."""

    def __init__(self):
        self._skills: Dict[str, SkillDefinition] = {}

    def load_from_yaml(self, path: str) -> SkillDefinition:
        """Load a single skill from a YAML file."""
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}

        if not data.get("name"):
            raise ValueError(f"Skill config {path} missing required field: name")

        skill = SkillDefinition.from_dict(data)
        self._skills[skill.name] = skill
        return skill

    def load_from_directory(self, config_dir: str) -> Dict[str, SkillDefinition]:
        """Load all skill YAMLs from a directory. Returns {name: definition}."""
        config_path = Path(config_dir)
        if not config_path.is_dir():
            logger.warning("[SKILLS] Config dir not found: %s", config_dir)
            return {}

        for f in sorted(config_path.iterdir()):
            if f.suffix not in (".yaml", ".yml"):
                continue
            try:
                self.load_from_yaml(str(f))
            except Exception as e:
                logger.warning("[SKILLS] Skipping %s: %s", f.name, e)

        logger.info("[SKILLS] Loaded %d skills from %s", len(self._skills), config_dir)
        return dict(self._skills)

    def get_skill(self, name: str) -> Optional[SkillDefinition]:
        """Get a loaded skill by name."""
        return self._skills.get(name)
