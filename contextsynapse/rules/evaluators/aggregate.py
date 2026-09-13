"""AggregateEvaluator — evaluates portfolio aggregate rules.

Supports:
  sum       — SUM(field) GROUP BY group_by, check each group against threshold
  top_n_sum — Sort by field desc, sum top N, check against threshold
  count_ratio — COUNT(WHERE condition) / COUNT(all), check ratio against threshold

Usage:
    evaluator = AggregateEvaluator()
    results = evaluator.evaluate(rule, db)
"""
from __future__ import annotations

import logging
import operator
from collections import defaultdict
from typing import Any, Callable, Dict, List

from ..models import RuleAction, RuleDefinition, RuleResult, RuleSeverity

logger = logging.getLogger(__name__)

_OP_MAP: Dict[str, Callable] = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "=": operator.eq,
}


class AggregateEvaluator:
    """Evaluates aggregate rules against graph node properties."""

    def evaluate(self, rule: RuleDefinition, db) -> List[RuleResult]:
        """Evaluate an aggregate rule against the graph."""
        condition = rule.condition
        agg_type = condition.get("aggregate", "sum")
        adapter = getattr(db, "csr_adapter", db)

        if agg_type == "sum":
            return self._eval_sum_group_by(rule, adapter)
        elif agg_type == "top_n_sum":
            return self._eval_top_n_sum(rule, adapter)
        elif agg_type == "count_ratio":
            return self._eval_count_ratio(rule, adapter)
        else:
            logger.warning("[RULES] Unknown aggregate type: %s", agg_type)
            return []

    def _eval_sum_group_by(
        self, rule: RuleDefinition, adapter
    ) -> List[RuleResult]:
        """SUM(field) GROUP BY group_by — check each group against threshold."""
        cond = rule.condition
        node_type = cond.get("node_type", "")
        field_name = cond.get("field", "")
        group_by = cond.get("group_by", "")
        op_str = cond.get("operator", ">")
        threshold = float(cond.get("threshold", 0))
        op_fn = _OP_MAP.get(op_str, operator.gt)

        groups: Dict[str, float] = defaultdict(float)
        nodes = adapter.get_all_nodes(node_type=node_type) if node_type else adapter.get_all_nodes()

        for node in nodes:
            props = node.properties if hasattr(node, "properties") else {}
            group_key = props.get(group_by, "unknown")
            value = props.get(field_name, 0)
            if value is None:
                value = 0
            try:
                groups[str(group_key)] += float(value)
            except (TypeError, ValueError):
                continue

        results = []
        for group, value in groups.items():
            if op_fn(value, threshold):
                msg = rule.message
                try:
                    msg = msg.format(
                        group=group, value=value, threshold=threshold,
                    )
                except (KeyError, ValueError, IndexError):
                    pass

                results.append(RuleResult(
                    rule_name=rule.name,
                    action=rule.action,
                    severity=rule.severity,
                    message=msg,
                    details={
                        "group": group,
                        "value": value,
                        "threshold": threshold,
                        "operator": op_str,
                    },
                    invoke_skill=rule.invoke_skill,
                ))
        return results

    def _eval_top_n_sum(
        self, rule: RuleDefinition, adapter
    ) -> List[RuleResult]:
        """Sort nodes by field desc, sum top N, check against threshold."""
        cond = rule.condition
        node_type = cond.get("node_type", "")
        field_name = cond.get("field", "")
        n = int(cond.get("n", 5))
        op_str = cond.get("operator", ">")
        threshold = float(cond.get("threshold", 0))
        op_fn = _OP_MAP.get(op_str, operator.gt)

        nodes = adapter.get_all_nodes(node_type=node_type) if node_type else adapter.get_all_nodes()
        values = []
        for node in nodes:
            props = node.properties if hasattr(node, "properties") else {}
            v = props.get(field_name, 0)
            if v is None:
                v = 0
            try:
                values.append(float(v))
            except (TypeError, ValueError):
                continue

        values.sort(reverse=True)
        top_n_sum = sum(values[:n])

        if op_fn(top_n_sum, threshold):
            msg = rule.message
            try:
                msg = msg.format(value=top_n_sum, threshold=threshold, n=n)
            except (KeyError, ValueError, IndexError):
                pass

            return [RuleResult(
                rule_name=rule.name,
                action=rule.action,
                severity=rule.severity,
                message=msg,
                details={"value": top_n_sum, "threshold": threshold, "n": n},
                invoke_skill=rule.invoke_skill,
            )]
        return []

    def _eval_count_ratio(
        self, rule: RuleDefinition, adapter
    ) -> List[RuleResult]:
        """COUNT(WHERE condition) / COUNT(all), check ratio against threshold."""
        cond = rule.condition
        node_type = cond.get("node_type", "")
        where_field = cond.get("where_field", "")
        where_op_str = cond.get("where_operator", "<=")
        where_threshold = float(cond.get("where_threshold", 0))
        where_op = _OP_MAP.get(where_op_str, operator.le)
        op_str = cond.get("operator", "<")
        threshold = float(cond.get("threshold", 0))
        op_fn = _OP_MAP.get(op_str, operator.lt)

        nodes = adapter.get_all_nodes(node_type=node_type) if node_type else adapter.get_all_nodes()

        total = 0
        matching = 0
        for node in nodes:
            props = node.properties if hasattr(node, "properties") else {}
            total += 1
            field_val = props.get(where_field)
            if field_val is not None:
                try:
                    if where_op(float(field_val), where_threshold):
                        matching += 1
                except (TypeError, ValueError):
                    continue

        if total == 0:
            return []

        ratio = matching / total

        if op_fn(ratio, threshold):
            msg = rule.message
            try:
                msg = msg.format(
                    value=ratio, threshold=threshold,
                    matching=matching, total=total,
                )
            except (KeyError, ValueError, IndexError):
                pass

            return [RuleResult(
                rule_name=rule.name,
                action=rule.action,
                severity=rule.severity,
                message=msg,
                details={
                    "value": ratio,
                    "threshold": threshold,
                    "matching": matching,
                    "total": total,
                },
                invoke_skill=rule.invoke_skill,
            )]
        return []
