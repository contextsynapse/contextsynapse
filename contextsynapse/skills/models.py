"""Skill engine data models — definitions, results, context requirements, chains.

A Skill is a composable capability that agents invoke. Each skill defines:
  - What contexts it needs (boundary template)
  - What projection tier to use
  - What rules to evaluate
  - What output it produces
  - What skills can chain after it
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from contextsynapse.rules.models import RuleAction, RuleResult


class SkillTier(Enum):
    """CGP projection tier for this skill."""
    INSTANT = "instant"      # < 50ms, 500 tokens
    FAST = "fast"            # < 200ms, 1500 tokens
    STANDARD = "standard"   # < 1000ms, 3000 tokens
    DEEP = "deep"            # < 5000ms, 8000 tokens


class SkillStatus(Enum):
    """Outcome of a skill execution."""
    SUCCESS = "success"      # completed, no blocks
    PARTIAL = "partial"      # completed with warnings
    BLOCKED = "blocked"      # blocked by compliance rule
    ERROR = "error"          # failed due to error


@dataclass
class SkillContextRequirement:
    """A context that a skill needs to operate."""
    type: str                           # portfolio | rules | market_data | etc.
    role: str = "input"                 # input (read-only) | output (write)
    source: Optional[str] = None        # schema | connector | specific context id
    required: bool = True


@dataclass
class SkillChainRule:
    """Defines when to invoke another skill after this one."""
    skill: str                          # skill name to invoke, e.g. "/rebalance"
    when: str = ""                      # condition expression, e.g. "status == BREACH"
    reason: str = ""                    # why this chain exists
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillDefinition:
    """A skill loaded from YAML — defines capability, context, rules, chains."""

    name: str
    description: str = ""
    version: str = "1.0"
    triggers: List[str] = field(default_factory=list)
    contexts: Dict[str, List[SkillContextRequirement]] = field(
        default_factory=lambda: {"required": [], "optional": []}
    )
    projection: Dict[str, Any] = field(default_factory=dict)
    rules: List[str] = field(default_factory=list)
    output: Dict[str, Any] = field(default_factory=dict)
    chains_to: List[SkillChainRule] = field(default_factory=list)
    configurable: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> SkillDefinition:
        """Parse a skill definition from YAML dict."""
        # Parse context requirements
        ctx_raw = raw.get("contexts", {})
        contexts: Dict[str, List[SkillContextRequirement]] = {"required": [], "optional": []}
        for key in ("required", "optional"):
            for item in ctx_raw.get(key, []):
                contexts[key].append(SkillContextRequirement(
                    type=item.get("type", ""),
                    role=item.get("role", "input"),
                    source=item.get("source"),
                    required=(key == "required"),
                ))

        # Parse chain rules
        chains = []
        for item in raw.get("chains_to", []):
            chains.append(SkillChainRule(
                skill=item.get("skill", ""),
                when=item.get("when", ""),
                reason=item.get("reason", ""),
                params=item.get("params", {}),
            ))

        return cls(
            name=raw.get("name", ""),
            description=raw.get("description", ""),
            version=raw.get("version", "1.0"),
            triggers=raw.get("trigger", []),
            contexts=contexts,
            projection=raw.get("projection", {}),
            rules=raw.get("rules", []),
            output=raw.get("output", {}),
            chains_to=chains,
            configurable=raw.get("configurable", []),
        )


@dataclass
class SkillResult:
    """Result of executing a skill."""

    skill_name: str
    status: SkillStatus
    output: Dict[str, Any] = field(default_factory=dict)
    rule_results: List[RuleResult] = field(default_factory=list)
    chain_invocations: List[str] = field(default_factory=list)
    message: str = ""
    executed_at: str = ""

    def __post_init__(self):
        if not self.executed_at:
            self.executed_at = datetime.now(timezone.utc).isoformat()

    def has_blocks(self) -> bool:
        """True if any rule result has a BLOCK action."""
        return any(r.action == RuleAction.BLOCK for r in self.rule_results)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_name": self.skill_name,
            "status": self.status.value,
            "output": self.output,
            "rule_results": [r.to_dict() for r in self.rule_results],
            "chain_invocations": self.chain_invocations,
            "message": self.message,
            "executed_at": self.executed_at,
        }
