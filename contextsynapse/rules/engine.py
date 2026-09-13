"""UnifiedRulesEngine — orchestrates all rule evaluators.

Routes rules to the correct evaluator (aggregate, temporal, pre_action)
based on rule_type, filters by trigger, and returns sorted results.

Usage:
    engine = UnifiedRulesEngine.from_yaml("config/schemas/pms_rules.yaml")
    results = engine.evaluate(RuleTrigger.ON_TRADE, db, action_context={"stock": "TCS"})
    for r in results:
        if r.action == RuleAction.BLOCK:
            raise ComplianceError(r.message)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .loader import RuleLoader
from .models import RuleAction, RuleDefinition, RuleResult, RuleSeverity, RuleTrigger
from .evaluators.aggregate import AggregateEvaluator
from .evaluators.temporal import TemporalEvaluator
from .evaluators.pre_action import PreActionEvaluator

logger = logging.getLogger(__name__)

_SEVERITY_ORDER = {
    RuleSeverity.CRITICAL: 0,
    RuleSeverity.HIGH: 1,
    RuleSeverity.MEDIUM: 2,
    RuleSeverity.LOW: 3,
    RuleSeverity.INFO: 4,
}


class UnifiedRulesEngine:
    """Orchestrates all rule evaluators — aggregate, temporal, pre-action."""

    def __init__(self, rules: List[RuleDefinition] = None):
        self._rules: List[RuleDefinition] = rules or []
        self._aggregate = AggregateEvaluator()
        self._temporal = TemporalEvaluator()
        self._pre_action = PreActionEvaluator()

    @property
    def rules(self) -> List[RuleDefinition]:
        return list(self._rules)

    @classmethod
    def from_yaml(cls, path: str) -> UnifiedRulesEngine:
        """Create engine from a rules YAML file."""
        loader = RuleLoader()
        rules = loader.load_from_yaml(path)
        return cls(rules=rules)

    def get_rules_for_trigger(
        self, trigger: RuleTrigger
    ) -> List[RuleDefinition]:
        """Get all rules that match the given trigger.

        ON_DEMAND bypasses trigger filtering and returns all rules.
        """
        if trigger == RuleTrigger.ON_DEMAND:
            return list(self._rules)
        return [r for r in self._rules if trigger in r.triggers]

    def evaluate(
        self,
        trigger: RuleTrigger,
        db,
        action_context: Optional[Dict[str, Any]] = None,
    ) -> List[RuleResult]:
        """Evaluate all rules matching the trigger.

        Args:
            trigger: The event that triggered evaluation
            db: Graph adapter (AIContextDB or CSR adapter)
            action_context: For pre-action rules, describes the proposed action

        Returns:
            List of RuleResult sorted by severity (critical first)
        """
        rules = self.get_rules_for_trigger(trigger)
        all_results: List[RuleResult] = []

        for rule in rules:
            try:
                results = self.evaluate_rule(rule, db, action_context)
                all_results.extend(results)
            except Exception as e:
                logger.error(
                    "[RULES] Error evaluating rule %s: %s", rule.name, e
                )

        # Sort by severity (critical first)
        all_results.sort(key=lambda r: _SEVERITY_ORDER.get(r.severity, 99))
        return all_results

    def evaluate_rule(
        self,
        rule: RuleDefinition,
        db,
        action_context: Optional[Dict[str, Any]] = None,
    ) -> List[RuleResult]:
        """Evaluate a single rule against the graph."""
        if rule.rule_type == "aggregate":
            return self._aggregate.evaluate(rule, db)
        elif rule.rule_type == "temporal":
            return self._temporal.evaluate(rule, db)
        elif rule.rule_type == "pre_action":
            return self._pre_action.evaluate(rule, db, action_context or {})
        else:
            logger.warning(
                "[RULES] Unknown rule type %s for rule %s",
                rule.rule_type, rule.name,
            )
            return []
