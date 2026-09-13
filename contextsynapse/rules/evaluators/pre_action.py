"""PreActionEvaluator — checks proposed actions against constraint rules.

Validates trades against restricted lists, client constraints, and
other pre-execution checks.

Usage:
    evaluator = PreActionEvaluator()
    results = evaluator.evaluate(rule, db, action_context={"stock_symbol": "TCS.NS"})
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from ..models import RuleDefinition, RuleResult

logger = logging.getLogger(__name__)


class PreActionEvaluator:
    """Evaluates pre-action rules against proposed trade/action context."""

    def evaluate(
        self, rule: RuleDefinition, db, action_context: Dict[str, Any] = None
    ) -> List[RuleResult]:
        """Evaluate a pre-action rule.

        Args:
            rule: The rule definition
            db: Graph adapter
            action_context: Dict describing the proposed action, e.g.:
                {"stock_symbol": "TCS.NS", "action": "buy", "quantity": 100, "client_id": "CLT001"}
        """
        if action_context is None:
            action_context = {}

        condition = rule.condition
        check_type = condition.get("check", "")
        adapter = getattr(db, "csr_adapter", db)

        if check_type == "restricted_list":
            return self._check_restricted_list(rule, adapter, action_context)
        elif check_type == "client_constraint":
            return self._check_client_constraint(rule, adapter, action_context)
        else:
            logger.warning("[RULES] Unknown pre_action check: %s", check_type)
            return []

    def _check_restricted_list(
        self, rule: RuleDefinition, adapter, ctx: Dict[str, Any]
    ) -> List[RuleResult]:
        """Check if the stock in action_context is on a restricted list."""
        cond = rule.condition
        list_node_type = cond.get("list_node_type", "RestrictedStock")
        match_field = cond.get("match_field", "stock_symbol")
        stock = ctx.get("stock_symbol", ctx.get(match_field, ""))

        if not stock:
            return []

        nodes = adapter.get_all_nodes(node_type=list_node_type)
        for node in nodes:
            props = node.properties if hasattr(node, "properties") else {}
            if props.get(match_field) == stock:
                reason = props.get("reason", "restricted")
                msg = rule.message
                try:
                    msg = msg.format(stock=stock, reason=reason, **ctx)
                except (KeyError, ValueError, IndexError):
                    pass
                return [RuleResult(
                    rule_name=rule.name,
                    action=rule.action,
                    severity=rule.severity,
                    message=msg,
                    details={"stock": stock, "reason": reason},
                    invoke_skill=rule.invoke_skill,
                )]
        return []

    def _check_client_constraint(
        self, rule: RuleDefinition, adapter, ctx: Dict[str, Any]
    ) -> List[RuleResult]:
        """Check if a client constraint blocks this trade."""
        cond = rule.condition
        list_node_type = cond.get("list_node_type", "ClientConstraint")
        match_field = cond.get("match_field", "stock_symbol")
        client_field = cond.get("client_field", "client_id")
        constraint_field = cond.get("constraint_field", "constraint")

        stock = ctx.get("stock_symbol", ctx.get(match_field, ""))
        client_id = ctx.get("client_id", ctx.get(client_field, ""))

        if not stock or not client_id:
            return []

        nodes = adapter.get_all_nodes(node_type=list_node_type)
        for node in nodes:
            props = node.properties if hasattr(node, "properties") else {}
            if (props.get(match_field) == stock and
                    props.get(client_field) == client_id):
                constraint = props.get(constraint_field, "blocked")
                msg = rule.message
                try:
                    extra = {k: v for k, v in ctx.items()
                             if k not in ("stock_symbol", "client_id", "constraint")}
                    msg = msg.format(
                        stock=stock, client_id=client_id,
                        constraint=constraint, **extra,
                    )
                except (KeyError, ValueError, IndexError):
                    pass
                return [RuleResult(
                    rule_name=rule.name,
                    action=rule.action,
                    severity=rule.severity,
                    message=msg,
                    details={
                        "stock": stock,
                        "client_id": client_id,
                        "constraint": constraint,
                    },
                    invoke_skill=rule.invoke_skill,
                )]
        return []
