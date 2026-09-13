"""ValidationGate — accept/quarantine/reject enforcement for nodes.

Validates individual nodes against a CompiledSchema, producing a verdict:
- accept: node passes all checks
- quarantine: node has data issues (invalid enum/pattern/range, unknown type in permissive mode)
- reject: node is structurally invalid (missing required fields, unknown type in strict mode, low confidence)

Usage:
    from contextsynapse.schema.validation_gate import ValidationGate

    gate = ValidationGate(compiled_schema)
    verdict = gate.validate_node({"type": "Drug", "name": "Aspirin"})
    accepted, quarantined, rejected = gate.validate_batch(nodes)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

from .compiler import CompiledSchema


@dataclass
class ValidationVerdict:
    """Result of validating a single node."""

    verdict: str  # "accept", "quarantine", "reject"
    reason: str = ""
    node: Dict = field(default_factory=dict)


class ValidationGate:
    """Validates nodes against a compiled schema."""

    def __init__(self, compiled: CompiledSchema):
        self._compiled = compiled
        self._rules = compiled.validation_rules
        self._plan = compiled.extraction_plan

    def validate_node(self, node: Dict[str, Any]) -> ValidationVerdict:
        """Validate a single node and return a verdict.

        Validation order:
        1. Unknown type + strict → reject
        2. Unknown type + permissive → quarantine
        3. Confidence below min_confidence → reject
        4. Missing required field → reject
        5. Invalid enum/pattern/range → quarantine
        6. All OK → accept
        """
        node_type = node.get("type", "")

        # 1-2: Unknown type check
        if node_type not in self._plan.allowed_types:
            if self._rules.strict_mode:
                return ValidationVerdict(
                    verdict="reject",
                    reason=f"Unknown type '{node_type}' in strict mode",
                    node=node,
                )
            else:
                return ValidationVerdict(
                    verdict="quarantine",
                    reason=f"Unknown type '{node_type}' in permissive mode",
                    node=node,
                )

        # 3: Confidence check
        min_conf = self._rules.min_confidence.get(node_type, 0.0)
        if min_conf > 0.0 and "confidence" in node:
            if node["confidence"] < min_conf:
                return ValidationVerdict(
                    verdict="reject",
                    reason=f"Confidence {node['confidence']} below min {min_conf}",
                    node=node,
                )

        # 4: Required fields
        validators = self._rules.field_validators.get(node_type, {})
        for field_name, field_def in validators.items():
            if field_def.required and field_name not in node:
                return ValidationVerdict(
                    verdict="reject",
                    reason=f"Missing required field '{field_name}'",
                    node=node,
                )

        # 5: Field value validation (enum, pattern, range)
        for field_name, field_def in validators.items():
            if field_name not in node:
                continue
            value = node[field_name]
            if not field_def.validate(value):
                return ValidationVerdict(
                    verdict="quarantine",
                    reason=f"Invalid value for field '{field_name}': {value!r}",
                    node=node,
                )

        # 6: All OK
        return ValidationVerdict(verdict="accept", node=node)

    def validate_batch(
        self, nodes: List[Dict[str, Any]]
    ) -> Tuple[List[ValidationVerdict], List[ValidationVerdict], List[ValidationVerdict]]:
        """Validate a batch of nodes.

        Returns (accepted, quarantined, rejected) lists of ValidationVerdict.
        """
        accepted: List[ValidationVerdict] = []
        quarantined: List[ValidationVerdict] = []
        rejected: List[ValidationVerdict] = []

        for node in nodes:
            v = self.validate_node(node)
            if v.verdict == "accept":
                accepted.append(v)
            elif v.verdict == "quarantine":
                quarantined.append(v)
            else:
                rejected.append(v)

        return accepted, quarantined, rejected
