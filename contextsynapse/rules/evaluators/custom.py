"""Custom rule evaluator — evaluates user-defined rules stored in graph."""
from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional

from ..models import RuleAction, RuleResult, RuleSeverity

logger = logging.getLogger(__name__)

_OPERATORS = {
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
    "eq": lambda a, b: a == b,
    "neq": lambda a, b: a != b,
}

_FIELDS = {
    "stock_weight": "Max single stock weight (%)",
    "sector_weight": "Max sector weight (%)",
    "top5_concentration": "Top 5 holdings concentration (%)",
    "total_value": "Portfolio total value (₹)",
    "cash_balance_pct": "Cash balance (%)",
    "holding_count": "Number of holdings",
    "avg_cost": "Average cost per holding (₹)",
    "portfolio_drift": "Portfolio drift (%)",
}


def _compute_metrics(holdings: List[Dict]) -> Dict[str, Any]:
    """Compute all portfolio metrics from holdings list."""
    total_value = sum(
        float(h.get("quantity", 0)) * float(h.get("current_price") or h.get("avg_cost", 0))
        for h in holdings
    )

    stock_weights: Dict[str, float] = {}
    sector_weights: Dict[str, float] = {}

    for h in holdings:
        symbol = h.get("stock_symbol", "UNKNOWN")
        sector = h.get("sector", "Unknown")
        qty = float(h.get("quantity", 0))
        price = float(h.get("current_price") or h.get("avg_cost", 0))
        mv = qty * price
        weight = (mv / total_value * 100) if total_value > 0 else 0
        stock_weights[symbol] = stock_weights.get(symbol, 0) + weight
        sector_weights[sector] = sector_weights.get(sector, 0) + weight

    sorted_weights = sorted(stock_weights.values(), reverse=True)
    top5_concentration = sum(sorted_weights[:5])

    return {
        "total_value": total_value,
        "stock_weights": stock_weights,
        "sector_weights": sector_weights,
        "max_stock_weight": max(stock_weights.values()) if stock_weights else 0.0,
        "max_sector_weight": max(sector_weights.values()) if sector_weights else 0.0,
        "top5_concentration": top5_concentration,
        "cash_balance_pct": 0.0,
        "holding_count": float(len(holdings)),
        "portfolio_drift": 0.0,
    }


_FIELD_ALIAS = {
    # UI field name → metrics dict key
    "stock_weight": "max_stock_weight",
    "sector_weight": "max_sector_weight",
}


def _eval_condition(condition: Dict, metrics: Dict) -> bool:
    field = condition.get("field", "")
    operator = condition.get("operator", "gt")
    value = float(condition.get("value", 0))
    # Resolve alias (e.g. "stock_weight" → "max_stock_weight")
    metric_key = _FIELD_ALIAS.get(field, field)
    metric_val = metrics.get(metric_key, 0.0)
    op_fn = _OPERATORS.get(operator)
    if op_fn is None:
        return False
    return op_fn(float(metric_val), value)


def evaluate_custom_rule(
    rule_data: Dict[str, Any],
    holdings: List[Dict[str, Any]],
) -> Optional[RuleResult]:
    """Evaluate a single custom rule against portfolio holdings.

    Returns RuleResult if the rule fires, None otherwise.
    """
    try:
        conditions = rule_data.get("conditions", [])
        conditions_operator = rule_data.get("conditions_operator", "AND")
        actions = rule_data.get("actions", [])

        if not conditions or not actions:
            return None

        metrics = _compute_metrics(holdings)

        cond_results = [_eval_condition(c, metrics) for c in conditions]

        # Per-row connectors: each condition has an optional "connector" field
        # that determines how it joins the NEXT condition.
        # Walk left-to-right: result = c[0], then apply connector[0] with c[1], etc.
        # Falls back to global conditions_operator when connector is missing.
        if len(cond_results) == 0:
            fired = False
        elif len(cond_results) == 1:
            fired = cond_results[0]
        else:
            fired = cond_results[0]
            for i, val in enumerate(cond_results[1:], start=0):
                conn = (conditions[i].get("connector") if isinstance(conditions[i], dict)
                        else getattr(conditions[i], "connector", None)) or conditions_operator
                if conn.upper() == "OR":
                    fired = fired or val
                else:
                    fired = fired and val

        if not fired:
            return None

        action_data = actions[0]
        action_type = action_data.get("type", "alert")
        raw_message = action_data.get("message", rule_data.get("name", "Rule triggered"))

        try:
            action = RuleAction(action_type)
        except ValueError:
            action = RuleAction.ALERT

        try:
            severity = RuleSeverity(rule_data.get("severity", "medium"))
        except ValueError:
            severity = RuleSeverity.MEDIUM

        # Safe template substitution
        fmt_ctx = {
            k: round(v, 2) if isinstance(v, float) else v
            for k, v in metrics.items()
            if not isinstance(v, dict)
        }
        try:
            message = raw_message.format(**fmt_ctx)
        except (KeyError, ValueError):
            message = raw_message

        return RuleResult(
            rule_name=rule_data.get("name", "Custom Rule"),
            action=action,
            severity=severity,
            message=message,
            details={
                "rule_id": rule_data.get("rule_id", ""),
                "category": rule_data.get("category", "custom"),
                "fired_conditions": [
                    conditions[i] for i, r in enumerate(cond_results) if r
                ],
                "metrics": {k: round(v, 2) if isinstance(v, float) else v
                           for k, v in fmt_ctx.items()},
            },
        )
    except Exception as e:
        logger.error("[CUSTOM_RULE] Error evaluating rule %s: %s", rule_data.get("name"), e)
        return None
