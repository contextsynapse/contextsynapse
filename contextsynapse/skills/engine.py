"""SkillEngine — orchestrates skill invocation, rule evaluation, and chaining.

The engine:
1. Loads skill definition by name
2. Evaluates applicable rules against the graph
3. Determines skill status (SUCCESS/BLOCKED/PARTIAL) from rule results
4. Evaluates chain conditions to determine next skills
5. Returns SkillResult with output + chains

The engine does NOT:
- Call LLMs
- Execute trades
- Modify the graph
- Invoke chained skills (caller does that)

Usage:
    engine = SkillEngine(skill_loader=loader, rules_engine=rules)
    result = engine.invoke("compliance-check", db)
    if result.has_blocks():
        raise ComplianceError(result.message)
    for chain in result.chain_invocations:
        engine.invoke(chain, db)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .loader import SkillLoader
from .models import SkillChainRule, SkillDefinition, SkillResult, SkillStatus

logger = logging.getLogger(__name__)


class SkillEngine:
    """Orchestrates skill invocation — rules + chains."""

    def __init__(
        self,
        skill_loader: SkillLoader,
        rules_engine=None,
    ):
        self._loader = skill_loader
        self._rules_engine = rules_engine

    def get_skill(self, name: str) -> Optional[SkillDefinition]:
        """Get a skill definition by name."""
        return self._loader.get_skill(name)

    def list_skills(self) -> List[str]:
        """List all loaded skill names."""
        return list(self._loader._skills.keys())

    def invoke(
        self,
        skill_name: str,
        db,
        action_context: Optional[Dict[str, Any]] = None,
    ) -> SkillResult:
        """Invoke a skill — evaluate rules and determine chains.

        Args:
            skill_name: Name of the skill to invoke
            db: Graph adapter
            action_context: For pre-action skills, the proposed action details

        Returns:
            SkillResult with status, rule results, and chain invocations
        """
        skill = self.get_skill(skill_name)
        if skill is None:
            return SkillResult(
                skill_name=skill_name,
                status=SkillStatus.ERROR,
                message=f"Skill '{skill_name}' not found",
            )

        # Evaluate rules
        rule_results = []
        if self._rules_engine and skill.rules:
            rule_results = self._evaluate_skill_rules(skill, db, action_context)

        # Determine status from rule results
        status = self._determine_status(rule_results)

        # Build result
        result = SkillResult(
            skill_name=skill_name,
            status=status,
            output=self._build_output(skill, rule_results),
            rule_results=rule_results,
            message=self._build_message(skill, status, rule_results),
        )

        # Evaluate chains
        result.chain_invocations = self.evaluate_chains(skill, result)

        return result

    def evaluate_chains(
        self, skill_def: SkillDefinition, result: SkillResult
    ) -> List[str]:
        """Evaluate chain conditions and return list of skills to invoke next."""
        chains = []
        for chain_rule in skill_def.chains_to:
            if self._chain_condition_met(chain_rule, result):
                chains.append(chain_rule.skill)
        return chains

    def _evaluate_skill_rules(
        self, skill: SkillDefinition, db, action_context: Optional[Dict]
    ) -> list:
        """Evaluate the rules specified by this skill."""
        from contextsynapse.rules.models import RuleTrigger

        # Determine trigger type based on skill triggers
        trigger = RuleTrigger.ON_DEMAND
        if "pre_trade" in skill.triggers:
            trigger = RuleTrigger.ON_TRADE
        elif "on_schedule" in skill.triggers or "daily_close" in skill.triggers:
            trigger = RuleTrigger.ON_SCHEDULE

        # Get all rule results for the trigger
        all_results = self._rules_engine.evaluate(
            trigger, db, action_context=action_context
        )

        # Filter to only rules this skill cares about
        if skill.rules:
            skill_rule_names = set(skill.rules)
            filtered = [r for r in all_results if r.rule_name in skill_rule_names]
            return filtered

        return all_results

    def _determine_status(self, rule_results: list) -> SkillStatus:
        """Determine skill status from rule evaluation results."""
        from contextsynapse.rules.models import RuleAction

        if not rule_results:
            return SkillStatus.SUCCESS

        has_blocks = any(r.action == RuleAction.BLOCK for r in rule_results)
        has_warns = any(r.action == RuleAction.WARN for r in rule_results)

        if has_blocks:
            return SkillStatus.BLOCKED
        elif has_warns:
            return SkillStatus.PARTIAL
        else:
            return SkillStatus.SUCCESS

    def _build_output(self, skill: SkillDefinition, rule_results: list) -> Dict:
        """Build skill output from rule results."""
        from contextsynapse.rules.models import RuleAction

        violations = [r.to_dict() for r in rule_results if r.action == RuleAction.BLOCK]
        warnings = [r.to_dict() for r in rule_results if r.action == RuleAction.WARN]

        status = "PASS"
        if violations:
            status = "BREACH"
        elif warnings:
            status = "WARNING"

        return {
            "status": status,
            "violations": violations,
            "warnings": warnings,
            "rules_evaluated": len(rule_results),
        }

    def _build_message(
        self, skill: SkillDefinition, status: SkillStatus, rule_results: list
    ) -> str:
        """Build a human-readable message from the skill result."""
        from contextsynapse.rules.models import RuleAction

        if status == SkillStatus.BLOCKED:
            blocks = [r.message for r in rule_results if r.action == RuleAction.BLOCK]
            return f"BLOCKED: {'; '.join(blocks)}" if blocks else "BLOCKED by compliance rules"
        elif status == SkillStatus.PARTIAL:
            return f"Completed with {len(rule_results)} warning(s)"
        elif status == SkillStatus.ERROR:
            return "Skill execution failed"
        else:
            return f"Completed successfully ({len(rule_results)} rules evaluated)"

    def _chain_condition_met(
        self, chain_rule: SkillChainRule, result: SkillResult
    ) -> bool:
        """Evaluate whether a chain condition is met."""
        condition = chain_rule.when.strip()

        if not condition:
            return False

        if condition == "always":
            return True

        # Simple condition parsing: "field == VALUE"
        if "==" in condition:
            parts = condition.split("==")
            if len(parts) == 2:
                field_name = parts[0].strip()
                expected = parts[1].strip()

                if field_name == "status":
                    # Match against status enum name or value
                    return (
                        result.status.value.upper() == expected.upper()
                        or result.status.name == expected.upper()
                    )

                # Check output fields
                actual = result.output.get(field_name)
                if actual is not None:
                    return str(actual).upper() == expected.upper()

        # Simple condition: "field > threshold"
        if ">" in condition and "==" not in condition:
            parts = condition.split(">")
            if len(parts) == 2:
                field_name = parts[0].strip()
                threshold = parts[1].strip()
                actual = result.output.get(field_name)
                if actual is not None:
                    try:
                        return float(actual) > float(threshold)
                    except (TypeError, ValueError):
                        pass

        return False
