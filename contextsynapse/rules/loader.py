"""RuleLoader — loads rule definitions from YAML schema files.

Parses the 'rules' section of a schema YAML file into RuleDefinition objects.

Usage:
    loader = RuleLoader()
    rules = loader.load_from_yaml("config/schemas/pms_rules.yaml")
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List

import yaml

from .models import RuleDefinition

logger = logging.getLogger(__name__)


class RuleLoader:
    """Loads RuleDefinition objects from YAML files."""

    def load_from_yaml(self, path: str) -> List[RuleDefinition]:
        """Load rules from a YAML file. Returns list of RuleDefinition."""
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}

        rules_dict = data.get("rules", {})
        rules = []
        for name, raw in rules_dict.items():
            try:
                rule = RuleDefinition.from_dict(name, raw)
                rules.append(rule)
            except Exception as e:
                logger.warning("[RULES] Failed to parse rule %s: %s", name, e)

        logger.info("[RULES] Loaded %d rules from %s", len(rules), path)
        return rules

    def load_from_directory(self, config_dir: str) -> List[RuleDefinition]:
        """Load rules from all YAML files in a directory."""
        all_rules = []
        config_path = Path(config_dir)
        if not config_path.is_dir():
            return []

        for f in sorted(config_path.iterdir()):
            if f.suffix in (".yaml", ".yml") and "rules" in f.stem:
                all_rules.extend(self.load_from_yaml(str(f)))
        return all_rules
