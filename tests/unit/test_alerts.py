"""Tests for alert delivery."""
import pytest


class TestAlertDispatcher:

    def test_dispatch_to_log_channel(self):
        from contextcore.intelligence.alerts import AlertDispatcher, AlertChannel

        dispatcher = AlertDispatcher()
        dispatcher.add_channel(AlertChannel(
            name="test_log", channel_type="log", min_severity="info",
        ))

        results = dispatcher.dispatch(
            signal_type="sentiment_reversal",
            entity_name="TCS",
            context_name="TCS Intelligence",
            severity="warning",
        )

        assert len(results) == 1
        assert results[0].status == "sent"

    def test_severity_filter(self):
        from contextcore.intelligence.alerts import AlertDispatcher, AlertChannel

        dispatcher = AlertDispatcher()
        dispatcher.add_channel(AlertChannel(
            name="alerts_only", channel_type="log", min_severity="alert",
        ))

        # Warning — should be skipped
        results = dispatcher.dispatch("test", "X", "Y", severity="warning")
        assert results[0].status == "skipped"

        # Alert — should be sent
        results = dispatcher.dispatch("test", "X", "Y", severity="alert")
        assert results[0].status == "sent"

    def test_delivery_log(self):
        from contextcore.intelligence.alerts import AlertDispatcher, AlertChannel

        dispatcher = AlertDispatcher()
        dispatcher.add_channel(AlertChannel(name="log", channel_type="log"))
        dispatcher.dispatch("test", "TCS", "Context", severity="info")

        log = dispatcher.get_delivery_log()
        assert len(log) == 1
        assert log[0]["payload"]["entity"] == "TCS"

    def test_disabled_channel_skipped(self):
        from contextcore.intelligence.alerts import AlertDispatcher, AlertChannel

        dispatcher = AlertDispatcher()
        dispatcher.add_channel(AlertChannel(
            name="disabled", channel_type="log", enabled=False,
        ))

        results = dispatcher.dispatch("test", "X", "Y", severity="alert")
        assert len(results) == 0  # disabled channels not even in results

    def test_channel_roundtrip(self):
        from contextcore.intelligence.alerts import AlertChannel

        ch = AlertChannel(
            name="slack", channel_type="webhook",
            config={"url": "https://hooks.slack.com/test"},
            min_severity="warning",
        )

        d = ch.to_dict()
        restored = AlertChannel.from_dict(d)
        assert restored.name == "slack"
        assert restored.channel_type == "webhook"
        assert restored.min_severity == "warning"
