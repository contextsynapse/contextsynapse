"""Rule engine data models — definitions, results, triggers, actions.

These are the shared types used by all evaluators and the unified engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class RuleTrigger(Enum):
    """When a rule should be evaluated."""
    ON_INGEST = "on_ingest"
    ON_TRADE = "on_trade"
    ON_SIGNAL = "on_signal"
    ON_SCHEDULE = "on_schedule"
    ON_SKILL = "on_skill"
    ON_CHANGE = "on_change"
    ON_DEMAND = "on_demand"


class RuleAction(Enum):
    """What happens when a rule fires."""
    ALLOW = "allow"
    BLOCK = "block"
    WARN = "warn"
    ALERT = "alert"
    ESCALATE = "escalate"
    INVOKE = "invoke"
    BOOST = "boost"
    ACCELERATE = "accelerate"
    LOG = "log"


class RuleSeverity(Enum):
    """Severity of a rule violation."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


@dataclass
class RuleDefinition:
    """A rule loaded from YAML schema — defines condition + action."""

    name: str
    rule_type: str  # aggregate | temporal | pre_action | node | graph | cross_context
    triggers: List[RuleTrigger] = field(default_factory=list)
    condition: Dict[str, Any] = field(default_factory=dict)
    action: RuleAction = RuleAction.LOG
    severity: RuleSeverity = RuleSeverity.INFO
    message: str = ""
    invoke_skill: Optional[str] = None
    override: Optional[str] = None
    configurable: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, name: str, raw: Dict[str, Any]) -> RuleDefinition:
        """Parse a rule definition from YAML dict."""
        triggers = []
        for t in raw.get("trigger", []):
            # Handle schedule variants like "on_schedule:daily_close"
            trigger_key = t.split(":")[0] if ":" in t else t
            try:
                triggers.append(RuleTrigger(trigger_key))
            except ValueError:
                pass

        try:
            action = RuleAction(raw.get("action", "log"))
        except ValueError:
            action = RuleAction.LOG

        try:
            severity = RuleSeverity(raw.get("severity", "info"))
        except ValueError:
            severity = RuleSeverity.INFO

        return cls(
            name=name,
            rule_type=raw.get("type", ""),
            triggers=triggers,
            condition=raw.get("condition", {}),
            action=action,
            severity=severity,
            message=raw.get("message", ""),
            invoke_skill=raw.get("invoke_skill"),
            override=raw.get("override"),
            configurable=raw.get("configurable", []),
        )


@dataclass
class RuleResult:
    """Result of evaluating a single rule — the action to take."""

    rule_name: str
    action: RuleAction
    severity: RuleSeverity
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    triggered_at: str = ""
    invoke_skill: Optional[str] = None

    def __post_init__(self):
        if not self.triggered_at:
            self.triggered_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "rule_name": self.rule_name,
            "action": self.action.value,
            "severity": self.severity.value,
            "message": self.message,
            "details": self.details,
            "triggered_at": self.triggered_at,
        }
        if self.invoke_skill:
            d["invoke_skill"] = self.invoke_skill
        return d
