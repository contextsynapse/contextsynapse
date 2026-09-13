"""TemporalEvaluator — evaluates time-based rules.

Supports:
  consecutive_days — check if a condition held for N consecutive days
                     (most recent N days all satisfy the operator/threshold)

Usage:
    evaluator = TemporalEvaluator()
    results = evaluator.evaluate(rule, db)
"""
from __future__ import annotations

import logging
import operator
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from ..models import RuleDefinition, RuleResult

logger = logging.getLogger(__name__)

_OP_MAP: Dict[str, Callable] = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "=": operator.eq,
}


def _parse_date(value: Any) -> Optional[str]:
    """Extract a date string (YYYY-MM-DD) from various formats."""
    if not value:
        return None
    s = str(value)
    # Handle ISO datetime: take just the date part
    if "T" in s:
        s = s.split("T")[0]
    # Validate it looks like a date
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return None


class TemporalEvaluator:
    """Evaluates temporal rules against time-stamped graph nodes."""

    def evaluate(self, rule: RuleDefinition, db) -> List[RuleResult]:
        """Evaluate a temporal rule against the graph."""
        condition = rule.condition
        adapter = getattr(db, "csr_adapter", db)

        if "consecutive_days" in condition:
            return self._eval_consecutive(rule, adapter)
        else:
            logger.warning("[RULES] Temporal rule %s has no recognized pattern", rule.name)
            return []

    def _eval_consecutive(
        self, rule: RuleDefinition, adapter
    ) -> List[RuleResult]:
        """Check if condition held for N consecutive most-recent days."""
        cond = rule.condition
        node_type = cond.get("node_type", "")
        field_name = cond.get("field", "")
        op_str = cond.get("operator", ">")
        threshold = float(cond.get("threshold", 0))
        required_days = int(cond.get("consecutive_days", 1))
        date_field = cond.get("date_field", "date")
        op_fn = _OP_MAP.get(op_str, operator.gt)

        # Gather all nodes with dates
        nodes = adapter.get_all_nodes(node_type=node_type) if node_type else adapter.get_all_nodes()

        # Group by date, take latest value per date
        by_date: Dict[str, float] = {}
        for node in nodes:
            props = node.properties if hasattr(node, "properties") else {}
            date_str = _parse_date(props.get(date_field) or props.get("_created_at"))
            if not date_str:
                continue
            value = props.get(field_name, 0)
            if value is None:
                value = 0
            try:
                by_date[date_str] = float(value)
            except (TypeError, ValueError):
                continue

        if not by_date:
            return []

        # Sort dates descending (most recent first)
        sorted_dates = sorted(by_date.keys(), reverse=True)

        # Count consecutive days from most recent where condition holds
        streak = 0
        for date_str in sorted_dates:
            value = by_date[date_str]
            if op_fn(value, threshold):
                streak += 1
            else:
                break

        if streak >= required_days:
            msg = rule.message
            try:
                msg = msg.format(
                    days=streak, threshold=threshold,
                    required=required_days,
                )
            except (KeyError, ValueError, IndexError):
                pass

            return [RuleResult(
                rule_name=rule.name,
                action=rule.action,
                severity=rule.severity,
                message=msg,
                details={
                    "consecutive_days": streak,
                    "required_days": required_days,
                    "threshold": threshold,
                    "operator": op_str,
                },
                invoke_skill=rule.invoke_skill,
            )]
        return []
