"""Reactive Pipeline Controller — signals trigger pipeline actions.

When correlation signals fire (sentiment reversal, threshold breach, etc.),
this module reacts by accelerating pipelines, expanding sources, triggering
immediate runs, and emitting alerts.

The reactive loop:
    Ingest → Tag → Signal fires → React → More ingestion → More signals

Auto-deescalation: elevated states expire after a configurable window.

Usage:
    from contextsynapse.intelligence.reactive import ReactiveController, ReactionRule

    controller = ReactiveController()
    controller.add_rule(ReactionRule(
        signal_type="sentiment_reversal",
        actions=["accelerate", "deepen", "alert"],
        accelerate_interval="5m",
        escalation_window_hours=4,
    ))

    # Called automatically after signal processing
    reactions = controller.react(signal, context_name, collector)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ReactionRule:
    """Defines what happens when a specific signal type fires."""
    signal_type: str                       # sentiment_reversal | threshold_breach | co_occurrence
    actions: List[str] = field(default_factory=lambda: ["alert"])
    # Possible actions: accelerate, expand, deepen, alert, correlate

    # Accelerate config
    accelerate_interval: str = "5m"        # polling interval during escalation
    escalation_window_hours: float = 4.0   # how long escalation lasts

    # Expand config (additional sources to add temporarily)
    expand_sources: List[Dict[str, str]] = field(default_factory=list)
    # e.g., [{"source_type": "crawl", "url_template": "https://news.google.com/search?q={entity}"}]

    # Severity filter — only react to signals at or above this severity
    min_severity: str = "info"             # info | warning | alert

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signal_type": self.signal_type,
            "actions": self.actions,
            "accelerate_interval": self.accelerate_interval,
            "escalation_window_hours": self.escalation_window_hours,
            "expand_sources": self.expand_sources,
            "min_severity": self.min_severity,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReactionRule":
        return cls(
            signal_type=data.get("signal_type", ""),
            actions=data.get("actions", ["alert"]),
            accelerate_interval=data.get("accelerate_interval", "5m"),
            escalation_window_hours=data.get("escalation_window_hours", 4.0),
            expand_sources=data.get("expand_sources", []),
            min_severity=data.get("min_severity", "info"),
        )


_SEVERITY_ORDER = {"info": 0, "warning": 1, "alert": 2}

_INTERVAL_MAP = {
    "1m": 1, "2m": 2, "5m": 5, "10m": 10, "15m": 15,
    "30m": 30, "1h": 60, "6h": 360, "daily": 1440, "weekly": 10080,
}


@dataclass
class EscalationState:
    """Tracks an active escalation for a context."""
    context_name: str
    entity_name: str
    signal_type: str
    original_intervals: Dict[str, str] = field(default_factory=dict)  # pipeline_name -> original interval
    escalated_at: str = ""
    deescalate_at: str = ""
    actions_taken: List[str] = field(default_factory=list)

    def is_expired(self) -> bool:
        if not self.deescalate_at:
            return True
        try:
            dt = datetime.fromisoformat(self.deescalate_at.replace("Z", "+00:00"))
            return datetime.now(timezone.utc) >= dt
        except (ValueError, TypeError):
            return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "context_name": self.context_name,
            "entity_name": self.entity_name,
            "signal_type": self.signal_type,
            "escalated_at": self.escalated_at,
            "deescalate_at": self.deescalate_at,
            "actions_taken": self.actions_taken,
        }


@dataclass
class Reaction:
    """A single reaction taken in response to a signal."""
    action: str              # accelerate | expand | deepen | alert | correlate
    context_name: str
    entity_name: str
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "context_name": self.context_name,
            "entity_name": self.entity_name,
            "details": self.details,
            "timestamp": self.timestamp,
        }


class ReactiveController:
    """Connects signals to pipeline actions.

    Manages reaction rules, escalation states, and auto-deescalation.
    """

    def __init__(self, alert_dispatcher=None):
        self._rules: Dict[str, List[ReactionRule]] = {}  # signal_type -> rules
        self._escalations: Dict[str, EscalationState] = {}  # "context:entity" -> state
        self._reaction_log: List[Reaction] = []
        self._alert_dispatcher = alert_dispatcher

    def add_rule(self, rule: ReactionRule):
        """Register a reaction rule for a signal type."""
        self._rules.setdefault(rule.signal_type, []).append(rule)

    def add_default_rules(self):
        """Add sensible default reaction rules."""
        self.add_rule(ReactionRule(
            signal_type="sentiment_reversal",
            actions=["accelerate", "deepen", "alert"],
            accelerate_interval="5m",
            escalation_window_hours=4.0,
            min_severity="warning",
        ))
        self.add_rule(ReactionRule(
            signal_type="threshold_breach",
            actions=["accelerate", "deepen", "alert"],
            accelerate_interval="5m",
            escalation_window_hours=2.0,
            min_severity="alert",
        ))
        self.add_rule(ReactionRule(
            signal_type="co_occurrence",
            actions=["alert", "correlate"],
            min_severity="info",
        ))

    def react(
        self,
        signal,
        context_name: str,
        collector=None,
    ) -> List[Reaction]:
        """React to a signal by executing matching rules.

        Args:
            signal: Signal dataclass from signals.py
            context_name: Which context this signal belongs to
            collector: Optional ContextCollector for pipeline manipulation

        Returns list of Reactions taken.
        """
        # Auto-deescalate expired states first
        self._check_deescalations(collector)

        rules = self._rules.get(signal.type, [])
        if not rules:
            return []

        reactions = []

        for rule in rules:
            # Check severity filter
            signal_sev = _SEVERITY_ORDER.get(signal.severity, 0)
            min_sev = _SEVERITY_ORDER.get(rule.min_severity, 0)
            if signal_sev < min_sev:
                continue

            for action in rule.actions:
                reaction = self._execute_action(
                    action, signal, context_name, rule, collector
                )
                if reaction:
                    reactions.append(reaction)
                    self._reaction_log.append(reaction)

        if reactions:
            logger.info("[REACTIVE] %d reactions for signal '%s' on '%s/%s'",
                         len(reactions), signal.type, context_name, signal.entity_name)

        return reactions

    def _execute_action(
        self, action: str, signal, context_name: str,
        rule: ReactionRule, collector,
    ) -> Optional[Reaction]:
        """Execute a single reaction action."""
        entity = signal.entity_name

        if action == "accelerate":
            return self._accelerate(context_name, entity, rule, collector)

        elif action == "deepen":
            return self._deepen(context_name, entity, collector)

        elif action == "alert":
            return self._alert(context_name, entity, signal)

        elif action == "correlate":
            return Reaction(
                action="correlate",
                context_name=context_name,
                entity_name=entity,
                details={"signal_type": signal.type, "note": "correlation check triggered"},
            )

        elif action == "expand":
            return self._expand(context_name, entity, rule, collector)

        return None

    def _accelerate(
        self, context_name: str, entity: str,
        rule: ReactionRule, collector,
    ) -> Reaction:
        """Accelerate pipeline polling for a context."""
        esc_key = f"{context_name}:{entity}"
        now = datetime.now(timezone.utc)

        # Save original intervals and set escalation
        original_intervals = {}
        if collector:
            plan = collector.get_plan(context_name)
            if plan:
                for p in plan.pipelines:
                    if p.source_type not in ("manual", "upload"):
                        original_intervals[p.name] = p.interval
                        p.interval = rule.accelerate_interval

        deescalate_at = (now + timedelta(hours=rule.escalation_window_hours)).isoformat()

        self._escalations[esc_key] = EscalationState(
            context_name=context_name,
            entity_name=entity,
            signal_type=rule.signal_type,
            original_intervals=original_intervals,
            escalated_at=now.isoformat(),
            deescalate_at=deescalate_at,
            actions_taken=["accelerate"],
        )

        return Reaction(
            action="accelerate",
            context_name=context_name,
            entity_name=entity,
            details={
                "new_interval": rule.accelerate_interval,
                "deescalate_at": deescalate_at,
                "pipelines_affected": list(original_intervals.keys()),
            },
        )

    def _deepen(self, context_name: str, entity: str, collector) -> Reaction:
        """Trigger immediate run of all pipelines for a context."""
        details = {"triggered": False}
        if collector:
            try:
                result = collector.run_all(context_name)
                details = {
                    "triggered": True,
                    "pipelines_run": result.pipelines_run,
                    "items_ingested": result.total_ingested,
                }
            except Exception as exc:
                details = {"triggered": False, "error": str(exc)}

        return Reaction(
            action="deepen",
            context_name=context_name,
            entity_name=entity,
            details=details,
        )

    def _alert(self, context_name: str, entity: str, signal) -> Reaction:
        """Emit an alert for agents/analysts."""
        # 1. In-app propagation (for agents)
        try:
            from ..context.propagation import get_propagator
            propagator = get_propagator()
            propagator.propagate(
                source_agent="reactive_controller",
                event_type=f"signal_{signal.type}",
                content=f"Signal: {signal.type} for {entity} in {context_name} — {signal.details}",
                namespace=context_name.lower().replace(" ", "_"),
                priority="critical" if signal.severity == "alert" else "normal",
            )
        except Exception as exc:
            logger.debug("[REACTIVE] Alert propagation failed: %s", exc)

        # 2. External alert delivery (webhook, email, etc.)
        if self._alert_dispatcher:
            try:
                self._alert_dispatcher.dispatch(
                    signal_type=signal.type,
                    entity_name=entity,
                    context_name=context_name,
                    severity=signal.severity,
                    details=signal.details if hasattr(signal, 'details') else {},
                )
            except Exception as exc:
                logger.debug("[REACTIVE] Alert dispatch failed: %s", exc)

        return Reaction(
            action="alert",
            context_name=context_name,
            entity_name=entity,
            details={
                "signal_type": signal.type,
                "severity": signal.severity,
                "signal_details": signal.details,
            },
        )

    def _expand(
        self, context_name: str, entity: str,
        rule: ReactionRule, collector,
    ) -> Optional[Reaction]:
        """Add temporary sources to expand collection."""
        if not rule.expand_sources or not collector:
            return None

        added = []
        plan = collector.get_plan(context_name)
        if plan:
            for src_template in rule.expand_sources:
                url = src_template.get("url_template", "").replace("{entity}", entity)
                if url:
                    from .collector import PipelineConfig
                    temp_pipeline = PipelineConfig(
                        name=f"_expand_{entity}_{len(plan.pipelines)}",
                        source_type=src_template.get("source_type", "crawl"),
                        sources=[url],
                        interval="10m",
                        enabled=True,
                    )
                    plan.pipelines.append(temp_pipeline)
                    added.append(url)

        if not added:
            return None

        return Reaction(
            action="expand",
            context_name=context_name,
            entity_name=entity,
            details={"sources_added": added},
        )

    def _check_deescalations(self, collector):
        """Auto-deescalate expired escalation states."""
        expired = [k for k, v in self._escalations.items() if v.is_expired()]

        for esc_key in expired:
            state = self._escalations.pop(esc_key)

            # Restore original intervals
            if collector and state.original_intervals:
                plan = collector.get_plan(state.context_name)
                if plan:
                    for p in plan.pipelines:
                        if p.name in state.original_intervals:
                            p.interval = state.original_intervals[p.name]

            logger.info("[REACTIVE] Deescalated '%s' — restoring original intervals",
                         esc_key)

    def get_escalations(self) -> List[Dict[str, Any]]:
        """Get all active escalation states."""
        return [v.to_dict() for v in self._escalations.values() if not v.is_expired()]

    def get_reaction_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent reactions."""
        return [r.to_dict() for r in self._reaction_log[-limit:]]
