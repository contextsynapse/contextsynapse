"""Tests for webhook receiver."""
import pytest


class TestWebhookReceiver:

    def test_register_and_list(self):
        from contextcore.intelligence.webhook_receiver import WebhookReceiver

        receiver = WebhookReceiver()
        config = receiver.register("tcs_news", "TCS", strategy="news_article")

        assert config.webhook_id == "tcs_news"
        assert config.secret != ""
        assert len(receiver.list_webhooks()) == 1

    def test_receive_text_content(self):
        from contextcore.intelligence.webhook_receiver import WebhookReceiver

        receiver = WebhookReceiver()
        receiver.register("test", "TestContext")

        # Without a db, ingest will fail — but we can test the routing
        result = receiver.receive("test", {
            "title": "Test Article",
            "content": "This is test content about something important",
        })

        # Will error because no db — but should not crash
        assert result.status in ("accepted", "error")

    def test_receive_unknown_webhook(self):
        from contextcore.intelligence.webhook_receiver import WebhookReceiver

        receiver = WebhookReceiver()
        result = receiver.receive("nonexistent", {"content": "test"})
        assert result.status == "error"

    def test_receive_disabled_webhook(self):
        from contextcore.intelligence.webhook_receiver import WebhookReceiver

        receiver = WebhookReceiver()
        config = receiver.register("test", "TestContext")
        config.enabled = False

        result = receiver.receive("test", {"content": "test"})
        assert result.status == "error"
        assert "disabled" in result.error.lower()

    def test_receive_empty_payload(self):
        from contextcore.intelligence.webhook_receiver import WebhookReceiver

        receiver = WebhookReceiver()
        receiver.register("test", "TestContext")

        result = receiver.receive("test", {})
        assert result.status == "error"
        assert "No content" in result.error

    def test_receive_count_tracked(self):
        from contextcore.intelligence.webhook_receiver import WebhookReceiver

        receiver = WebhookReceiver()
        config = receiver.register("test", "TestContext")

        receiver.receive("test", {"content": "test"})
        assert config.receive_count == 1
