"""Tests for Reactive Pipeline Controller."""
import pytest
from datetime import datetime, timezone, timedelta


def _make_signal(signal_type="sentiment_reversal", entity="Tesla", severity="warning", details=None):
    from contextcore.intelligence.signals import Signal
    return Signal(
        type=signal_type,
        entity_name=entity,
        severity=severity,
        details=details or {"from": "positive", "to": "negative"},
    )


class TestReactionRules:
    """Rule configuration and matching."""

    def test_default_rules(self):
        from contextcore.intelligence.reactive import ReactiveController
        rc = ReactiveController()
        rc.add_default_rules()
        assert len(rc._rules) == 3
        assert "sentiment_reversal" in rc._rules
        assert "threshold_breach" in rc._rules
        assert "co_occurrence" in rc._rules

    def test_rule_roundtrip(self):
        from contextcore.intelligence.reactive import ReactionRule
        rule = ReactionRule(
            signal_type="sentiment_reversal",
            actions=["accelerate", "alert"],
            accelerate_interval="5m",
            escalation_window_hours=4.0,
        )
        d = rule.to_dict()
        restored = ReactionRule.from_dict(d)
        assert restored.signal_type == "sentiment_reversal"
        assert restored.actions == ["accelerate", "alert"]
        assert restored.accelerate_interval == "5m"


class TestReactiveController:
    """Signal → reaction processing."""

    def test_alert_fires_on_signal(self):
        from contextcore.intelligence.reactive import ReactiveController, ReactionRule

        rc = ReactiveController()
        rc.add_rule(ReactionRule(
            signal_type="sentiment_reversal",
            actions=["alert"],
            min_severity="info",
        ))

        signal = _make_signal()
        reactions = rc.react(signal, "TCS Intelligence")

        assert len(reactions) == 1
        assert reactions[0].action == "alert"
        assert reactions[0].entity_name == "Tesla"

    def test_severity_filter(self):
        from contextcore.intelligence.reactive import ReactiveController, ReactionRule

        rc = ReactiveController()
        rc.add_rule(ReactionRule(
            signal_type="threshold_breach",
            actions=["accelerate"],
            min_severity="alert",  # only react to "alert" severity
        ))

        # Warning severity — should NOT trigger
        signal = _make_signal(signal_type="threshold_breach", severity="warning")
        reactions = rc.react(signal, "TCS")
        assert len(reactions) == 0

        # Alert severity — should trigger
        signal = _make_signal(signal_type="threshold_breach", severity="alert")
        reactions = rc.react(signal, "TCS")
        assert len(reactions) == 1

    def test_accelerate_changes_intervals(self):
        from contextcore.intelligence.reactive import ReactiveController, ReactionRule
        from contextcore.intelligence.collector import ContextCollector, CollectionPlan, PipelineConfig

        collector = ContextCollector()
        plan = CollectionPlan(
            context_name="TCS",
            pipelines=[
                PipelineConfig(name="news", source_type="rss", sources=["url1"], interval="30m"),
                PipelineConfig(name="filings", source_type="crawl", sources=["url2"], interval="daily"),
                PipelineConfig(name="earnings", source_type="manual"),
            ],
        )
        collector.register(plan)

        rc = ReactiveController()
        rc.add_rule(ReactionRule(
            signal_type="sentiment_reversal",
            actions=["accelerate"],
            accelerate_interval="5m",
            escalation_window_hours=2.0,
            min_severity="info",
        ))

        signal = _make_signal()
        reactions = rc.react(signal, "TCS", collector=collector)

        assert len(reactions) == 1
        assert reactions[0].action == "accelerate"

        # Check intervals changed
        p = collector.get_plan("TCS")
        assert p.pipelines[0].interval == "5m"      # news: 30m -> 5m
        assert p.pipelines[1].interval == "5m"      # filings: daily -> 5m
        assert p.pipelines[2].interval == "daily"  # manual: unchanged (source_type=manual skipped)

    def test_deescalation(self):
        from contextcore.intelligence.reactive import ReactiveController, ReactionRule, EscalationState
        from contextcore.intelligence.collector import ContextCollector, CollectionPlan, PipelineConfig

        collector = ContextCollector()
        plan = CollectionPlan(
            context_name="TCS",
            pipelines=[
                PipelineConfig(name="news", source_type="rss", sources=["url1"], interval="30m"),
            ],
        )
        collector.register(plan)

        rc = ReactiveController()

        # Manually create an expired escalation
        rc._escalations["TCS:Tesla"] = EscalationState(
            context_name="TCS",
            entity_name="Tesla",
            signal_type="sentiment_reversal",
            original_intervals={"news": "30m"},
            escalated_at=(datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(),
            deescalate_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        )

        # Simulate pipeline was accelerated
        plan.pipelines[0].interval = "5m"

        # Trigger deescalation check via any react call
        signal = _make_signal(signal_type="unknown_signal")  # no rules for this
        rc.react(signal, "TCS", collector=collector)

        # Should have restored original interval
        assert plan.pipelines[0].interval == "30m"
        assert len(rc.get_escalations()) == 0

    def test_multiple_actions(self):
        from contextcore.intelligence.reactive import ReactiveController, ReactionRule

        rc = ReactiveController()
        rc.add_default_rules()

        signal = _make_signal(severity="warning")
        reactions = rc.react(signal, "TCS")

        # Default sentiment_reversal rule has: accelerate, deepen, alert
        actions = [r.action for r in reactions]
        assert "alert" in actions
        assert "accelerate" in actions

    def test_reaction_log(self):
        from contextcore.intelligence.reactive import ReactiveController, ReactionRule

        rc = ReactiveController()
        rc.add_rule(ReactionRule(
            signal_type="sentiment_reversal",
            actions=["alert"],
            min_severity="info",
        ))

        signal = _make_signal()
        rc.react(signal, "TCS")

        log = rc.get_reaction_log()
        assert len(log) >= 1
        assert log[-1]["action"] == "alert"
        assert log[-1]["context_name"] == "TCS"

    def test_no_rules_no_reactions(self):
        from contextcore.intelligence.reactive import ReactiveController

        rc = ReactiveController()
        signal = _make_signal()
        reactions = rc.react(signal, "TCS")
        assert reactions == []

    def test_deepen_triggers_collector_run(self):
        from contextcore.intelligence.reactive import ReactiveController, ReactionRule
        from contextcore.intelligence.collector import ContextCollector, CollectionPlan, PipelineConfig

        runs = []

        collector = ContextCollector()
        plan = CollectionPlan(
            context_name="TCS",
            pipelines=[PipelineConfig(name="news", source_type="manual")],
        )
        collector.register(plan)

        rc = ReactiveController()
        rc.add_rule(ReactionRule(
            signal_type="sentiment_reversal",
            actions=["deepen"],
            min_severity="info",
        ))

        signal = _make_signal()
        reactions = rc.react(signal, "TCS", collector=collector)

        assert len(reactions) == 1
        assert reactions[0].action == "deepen"
        assert reactions[0].details["triggered"] is True
