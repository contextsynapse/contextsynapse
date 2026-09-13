"""
DerivationEngine
================
Evaluates schema-defined derivation rules against live graph state
and returns a list of action dicts describing what should happen.

The engine does NOT execute actions — it only evaluates conditions.

Supported rule types:
  - Edge count  (action: create_cu)   — fire when a node has N+ edges of a type
  - Property match (action: boost)    — fire when a node property equals a value
  - Shared field (action: create_edge) — fire when nodes share a field value
"""

from __future__ import annotations

import operator
import re
from collections import defaultdict
from itertools import combinations
from typing import Any, Callable, Dict, List, Tuple

from .sdl import DerivationRule


# ── Count condition parser ───────────────────────────────────────────

_OP_MAP = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "=": operator.eq,
}

_COUNT_RE = re.compile(r"^(>=|<=|>|<|==|=)(\d+)$")


def _parse_count(expr: str) -> Tuple[Callable, int]:
    """Parse a count expression like '>3' into (operator_fn, threshold)."""
    m = _COUNT_RE.match(expr.strip())
    if not m:
        raise ValueError(f"Invalid count expression: {expr!r}")
    op_str, val_str = m.group(1), m.group(2)
    return _OP_MAP[op_str], int(val_str)


# ── DerivationEngine ─────────────────────────────────────────────────

class DerivationEngine:
    """Evaluate derivation rules against a graph and return action dicts."""

    def __init__(self, rules: List[DerivationRule]):
        self.rules = rules

    def evaluate(self, db) -> List[Dict[str, Any]]:
        """Evaluate all rules against *db* and return action dicts.

        Args:
            db: A graph object (AIContextDB or csr_adapter).  The engine
                accesses ``db.csr_adapter`` if present, otherwise uses *db*
                directly.

        Returns:
            List of action dicts — each describes a single derived action.
        """
        actions: List[Dict[str, Any]] = []
        adapter = getattr(db, "csr_adapter", db)

        for rule in self.rules:
            when = rule.when

            if rule.action == "create_cu" and "has_edge" in when:
                actions.extend(self._eval_edge_count(adapter, rule))
            elif rule.action == "boost" and "property" in when:
                actions.extend(self._eval_property_match(adapter, rule))
            elif rule.action == "create_edge" and "shares_field" in when:
                actions.extend(self._eval_shared_field(adapter, rule))

        return actions

    # ── private evaluators ───────────────────────────────────────────

    def _eval_edge_count(
        self, adapter, rule: DerivationRule
    ) -> List[Dict[str, Any]]:
        """Evaluate edge-count condition (create_cu action)."""
        when = rule.when
        node_type = when.get("node", "")
        edge_type = when.get("has_edge", "")
        count_expr = when.get("count", ">0")
        op_fn, threshold = _parse_count(str(count_expr))

        # Resolve topic_from — e.g. "Condition.name" → field "name"
        topic_field = self._resolve_topic_field(rule.params.get("topic_from", ""))

        results: List[Dict[str, Any]] = []
        for node in adapter.get_all_nodes(node_type=node_type):
            neighbors = adapter.get_neighbors(node.id, edge_type=edge_type)
            count = len(neighbors)
            if op_fn(count, threshold):
                topic = node.properties.get(topic_field, "") if topic_field else ""
                results.append({
                    "action": "create_cu",
                    "node_id": node.id,
                    "node_type": node_type,
                    "topic": topic,
                    "edge_count": count,
                })
        return results

    def _eval_property_match(
        self, adapter, rule: DerivationRule
    ) -> List[Dict[str, Any]]:
        """Evaluate property-match condition (boost action)."""
        when = rule.when
        node_type = when.get("node", "")
        prop_name = when.get("property", "")
        expected = when.get("equals")

        results: List[Dict[str, Any]] = []
        for node in adapter.get_all_nodes(node_type=node_type):
            actual = node.properties.get(prop_name)
            if actual == expected:
                action_dict: Dict[str, Any] = {
                    "action": "boost",
                    "node_id": node.id,
                    "node_type": node_type,
                }
                action_dict.update(rule.params)
                results.append(action_dict)
        return results

    def _eval_shared_field(
        self, adapter, rule: DerivationRule
    ) -> List[Dict[str, Any]]:
        """Evaluate shared-field condition (create_edge action)."""
        when = rule.when
        node_type = when.get("node", "")
        field_name = when.get("shares_field", "")

        # Group nodes by their field value
        groups: Dict[Any, List] = defaultdict(list)
        for node in adapter.get_all_nodes(node_type=node_type):
            val = node.properties.get(field_name)
            if val is not None:
                groups[val].append(node)

        results: List[Dict[str, Any]] = []
        for shared_value, nodes in groups.items():
            if len(nodes) < 2:
                continue
            for a, b in combinations(nodes, 2):
                action_dict: Dict[str, Any] = {
                    "action": "create_edge",
                    "source": a.id,
                    "target": b.id,
                    "shared_value": shared_value,
                }
                action_dict.update(rule.params)
                results.append(action_dict)
        return results

    # ── helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _resolve_topic_field(topic_from: str) -> str:
        """Extract field name from 'NodeType.field' notation."""
        if "." in topic_from:
            return topic_from.split(".", 1)[1]
        return topic_from
