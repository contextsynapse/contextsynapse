"""Tests for connector registry and LLM memory connectors."""

import json
import os
import tempfile
import pytest

from contextsynapse.ingestion.connectors import get_ingestor, CONNECTOR_INGEST_MAP
from contextsynapse.ingestion.connectors.base import ConnectorIngestor, ConnectorGraphResult


class TestConnectorRegistry:
    def test_all_connectors_registered(self):
        expected = [
            "database", "github", "jira", "rest_api", "salesforce", "sap",
            "servicenow", "chatgpt", "claude", "gemini", "browser",
            "chrome", "edge", "firefox", "safari",
        ]
        for name in expected:
            assert name in CONNECTOR_INGEST_MAP, f"Connector '{name}' not registered"

    def test_get_ingestor_returns_instance(self):
        ingestor = get_ingestor("chatgpt", {"export_file": "/tmp/fake.json"}, {})
        assert ingestor is not None
        assert isinstance(ingestor, ConnectorIngestor)
        assert ingestor.connector_type == "chatgpt"

    def test_get_ingestor_unknown_returns_none(self):
        assert get_ingestor("nonexistent", {}, {}) is None


class TestChatGPTConnector:
    def test_test_connection_no_key_no_file(self):
        ingestor = get_ingestor("chatgpt", {}, {})
        result = ingestor.test_connection()
        assert result["success"] is False

    def test_test_connection_with_export_file(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            json.dump([], f)
            f.flush()
            path = f.name
        try:
            ingestor = get_ingestor("chatgpt", {"export_file": path}, {})
            result = ingestor.test_connection()
            assert result["success"] is True
        finally:
            os.unlink(path)

    def test_pull_from_export_empty(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            json.dump([], f)
            f.flush()
            path = f.name
        try:
            ingestor = get_ingestor("chatgpt", {"export_file": path}, {})
            result = ingestor.pull_data("ctx_123")
            assert isinstance(result, ConnectorGraphResult)
            assert result.stats["conversations"] == 0
        finally:
            os.unlink(path)

    def test_pull_from_export_with_conversation(self):
        conv = {
            "id": "conv_1",
            "title": "Test Chat",
            "create_time": 1700000000,
            "mapping": {
                "msg1": {
                    "message": {
                        "id": "m1",
                        "author": {"role": "user"},
                        "content": {"parts": ["Hello, how are you?"]},
                        "create_time": 1700000001,
                    }
                },
                "msg2": {
                    "message": {
                        "id": "m2",
                        "author": {"role": "assistant"},
                        "content": {"parts": ["I'm doing well!"]},
                        "create_time": 1700000002,
                    }
                },
            },
        }
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            json.dump([conv], f)
            f.flush()
            path = f.name
        try:
            ingestor = get_ingestor("chatgpt", {"export_file": path}, {})
            result = ingestor.pull_data("ctx_123")
            assert result.stats["conversations"] == 1
            # Should have: 1 Conversation + 2 Message nodes
            labels = [n["label"] for n in result.nodes]
            assert "Conversation" in labels
            assert labels.count("Message") == 2
            # Should have data_items for extraction
            assert len(result.data_items) >= 1
        finally:
            os.unlink(path)


class TestClaudeConnector:
    def test_test_connection_no_config(self):
        ingestor = get_ingestor("claude", {}, {})
        result = ingestor.test_connection()
        assert result["success"] is False

    def test_project_mode_with_claude_md(self, tmp_path):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Project instructions\nUse TDD.", encoding="utf-8")

        ingestor = get_ingestor("claude", {"project_dir": str(tmp_path)}, {})
        result = ingestor.test_connection()
        assert result["success"] is True

        data = ingestor.pull_data("ctx_123")
        labels = [n["label"] for n in data.nodes]
        assert "ProjectContext" in labels
        assert len(data.data_items) >= 1


class TestGeminiConnector:
    def test_test_connection_no_config(self):
        ingestor = get_ingestor("gemini", {}, {})
        result = ingestor.test_connection()
        assert result["success"] is False

    def test_pull_from_export(self):
        conv = {
            "title": "Gemini Chat",
            "id": "g1",
            "messages": [
                {"role": "user", "parts": [{"text": "What is Python?"}]},
                {"role": "model", "parts": [{"text": "Python is a programming language."}]},
            ],
        }
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            json.dump([conv], f)
            f.flush()
            path = f.name
        try:
            ingestor = get_ingestor("gemini", {"export_file": path}, {})
            result = ingestor.pull_data("ctx_123")
            assert result.stats["conversations"] == 1
            labels = [n["label"] for n in result.nodes]
            assert "Conversation" in labels
            assert "Message" in labels
        finally:
            os.unlink(path)


class TestBrowserConnector:
    def test_chrome_path_detection(self):
        ingestor = get_ingestor("chrome", {"browser": "chrome"}, {})
        result = ingestor.test_connection()
        # Should either find Chrome or report it missing — not crash
        assert "success" in result

    def test_unknown_browser(self):
        ingestor = get_ingestor("browser", {"browser": "netscape"}, {})
        result = ingestor.test_connection()
        assert result["success"] is False

    def test_discover_metadata(self):
        ingestor = get_ingestor("browser", {"browser": "chrome"}, {})
        result = ingestor.discover_metadata("ctx_123")
        assert len(result.nodes) == 1
        assert result.nodes[0]["label"] == "Connector"
