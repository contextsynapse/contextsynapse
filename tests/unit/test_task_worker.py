"""Tests for the standalone task worker (REST client + LLM)."""

import json
import sys
import os
import pytest
from unittest.mock import patch, MagicMock

# Add scripts to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))


class TestAIContextDBClient:

    def test_init_stores_config(self):
        from task_worker import AIContextDBClient
        client = AIContextDBClient("http://localhost:8340", "a1", "key1")
        assert client.server_url == "http://localhost:8340"
        assert client.agent_id == "a1"

    def test_auth_headers(self):
        from task_worker import AIContextDBClient
        client = AIContextDBClient("http://localhost:8340", "a1", "key1")
        headers = client._headers()
        assert headers["X-Agent-ID"] == "a1"
        assert headers["X-API-Key"] == "key1"

    @patch("requests.get")
    def test_my_tasks_calls_correct_endpoint(self, mock_get):
        from task_worker import AIContextDBClient
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"tasks": [{"id": "t1", "title": "Test task", "status": "open"}]},
        )
        mock_get.return_value.raise_for_status = lambda: None
        client = AIContextDBClient("http://localhost:8340", "a1", "key1")
        tasks = client.my_tasks("session1", status="open")
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Test task"
        call_url = mock_get.call_args[0][0]
        assert "/agent/sessions/session1/tasks/mine" in call_url

    @patch("requests.get")
    def test_orient_calls_correct_endpoint_with_tier(self, mock_get):
        from task_worker import AIContextDBClient
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"orient": "PROJECTED CONTEXT...", "mode": "projection"},
        )
        mock_get.return_value.raise_for_status = lambda: None
        client = AIContextDBClient("http://localhost:8340", "a1", "key1")
        result = client.orient("session1", max_tokens=3000, max_time_ms=1000, tier="standard")
        assert "PROJECTED" in result
        call_kwargs = mock_get.call_args[1]
        assert call_kwargs["params"]["tier"] == "standard"
        assert call_kwargs["params"]["max_time_ms"] == 1000

    @patch("requests.post")
    def test_claim_task(self, mock_post):
        from task_worker import AIContextDBClient
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"status": "claimed", "task_id": "t1"},
        )
        mock_post.return_value.raise_for_status = lambda: None
        client = AIContextDBClient("http://localhost:8340", "a1", "key1")
        result = client.claim_task("session1", "t1")
        assert result["status"] == "claimed"

    @patch("requests.post")
    def test_add_finding(self, mock_post):
        from task_worker import AIContextDBClient
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"node_id": "n1", "label": "Finding"},
        )
        mock_post.return_value.raise_for_status = lambda: None
        client = AIContextDBClient("http://localhost:8340", "a1", "key1")
        result = client.add_finding("session1", "Test finding", "Detailed analysis...")
        assert result["label"] == "Finding"
        body = mock_post.call_args[1]["json"]
        assert body["label"] == "Finding"
        assert body["properties"]["name"] == "Test finding"

    @patch("requests.post")
    def test_complete_task(self, mock_post):
        from task_worker import AIContextDBClient
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"status": "completed"},
        )
        mock_post.return_value.raise_for_status = lambda: None
        client = AIContextDBClient("http://localhost:8340", "a1", "key1")
        result = client.complete_task("session1", "t1", summary="Done")
        assert result["status"] == "completed"

    @patch("requests.post")
    def test_heartbeat(self, mock_post):
        from task_worker import AIContextDBClient
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"ack": True},
        )
        mock_post.return_value.raise_for_status = lambda: None
        client = AIContextDBClient("http://localhost:8340", "a1", "key1")
        result = client.heartbeat("session1", "idle")
        assert result["ack"] is True


class TestLLMWorker:

    def test_init_with_known_provider(self):
        from task_worker import LLMWorker
        worker = LLMWorker(provider="openai", model="gpt-4o-mini", api_key="test-key")
        assert worker.provider == "openai"
        assert worker.model == "gpt-4o-mini"

    def test_init_with_custom_provider_file(self, tmp_path):
        from task_worker import LLMWorker
        providers_file = tmp_path / "providers.json"
        providers_file.write_text(json.dumps({
            "xai": {"base_url": "https://api.x.ai/v1", "key_env": "XAI_API_KEY", "default_model": "grok-2"}
        }))
        worker = LLMWorker(provider="xai", api_key="test-key", providers_file=str(providers_file))
        assert worker.provider == "xai"
        assert worker.model == "grok-2"

    def test_unknown_provider_raises(self):
        from task_worker import LLMWorker
        with pytest.raises(ValueError, match="Unknown provider"):
            LLMWorker(provider="nonexistent", api_key="key")

    def test_anthropic_provider(self):
        from task_worker import LLMWorker
        worker = LLMWorker(provider="anthropic", model="claude-sonnet-4-20250514", api_key="test-key")
        assert worker.provider == "anthropic"
        assert worker._is_anthropic is True

    def test_openai_compatible_provider(self):
        from task_worker import LLMWorker
        worker = LLMWorker(provider="groq", api_key="test-key")
        assert worker.provider == "groq"
        assert worker._is_anthropic is False
        assert worker.model == "llama-3.3-70b-versatile"


class TestTaskWorker:

    def _make_worker(self):
        from task_worker import TaskWorker, AIContextDBClient, LLMWorker
        client = MagicMock(spec=AIContextDBClient)
        llm = MagicMock(spec=LLMWorker)
        llm.provider = "test"
        llm.model = "test-model"
        worker = TaskWorker(client=client, llm=llm, session_id="s1",
                           tier="standard", max_tokens=3000, poll_interval=5, once=True)
        return worker, client, llm

    def test_execute_task_full_cycle(self):
        worker, client, llm = self._make_worker()
        client.orient.return_value = "PROJECTED CONTEXT: security analysis..."
        llm.generate.return_value = "The auth module has three vulnerabilities: first, the token storage is insecure..."

        task = {"id": "t1", "title": "Audit auth", "description": "Check security",
                "priority": "high", "status": "open", "tags": "analysis"}
        worker.execute_task(task)

        client.claim_task.assert_called_once_with("s1", "t1")
        client.heartbeat.assert_called()
        client.orient.assert_called_once()
        llm.generate.assert_called_once()
        client.add_finding.assert_called_once()  # analysis task writes finding
        call_args = client.complete_task.call_args
        assert call_args[0] == ("s1", "t1")
        assert len(call_args[1]["summary"]) <= 500

    def test_should_write_finding_analysis_task(self):
        worker, _, _ = self._make_worker()
        task = {"tags": "analysis,security"}
        assert worker.should_write_finding(task, "Detailed analysis of the system with multiple findings and recommendations for improvement") is True

    def test_should_not_write_finding_code_task(self):
        worker, _, _ = self._make_worker()
        task = {"tags": "code,implement"}
        assert worker.should_write_finding(task, "Implemented the feature with full coverage") is False

    def test_should_not_write_finding_short_response(self):
        worker, _, _ = self._make_worker()
        task = {"tags": "analysis"}
        assert worker.should_write_finding(task, "I don't know") is False

    def test_should_not_write_finding_insufficient_context(self):
        worker, _, _ = self._make_worker()
        task = {"tags": "analysis"}
        assert worker.should_write_finding(task, "I don't have enough context to analyze this properly because there is no data") is False

    def test_execute_task_llm_failure_does_not_crash(self):
        import requests as req_mod
        worker, client, llm = self._make_worker()
        client.orient.return_value = "PROJECTED CONTEXT..."
        llm.generate.side_effect = Exception("API timeout")

        task = {"id": "t1", "title": "Audit auth", "description": "",
                "priority": "high", "status": "open", "tags": ""}
        worker.execute_task(task)
        # Task should NOT be completed on LLM failure
        client.complete_task.assert_not_called()

    def test_execute_task_claim_failure_skips(self):
        import requests as req_mod
        worker, client, llm = self._make_worker()
        client.claim_task.side_effect = req_mod.HTTPError("409 Conflict")

        task = {"id": "t1", "title": "Audit auth", "description": "",
                "priority": "high", "status": "open", "tags": ""}
        worker.execute_task(task)
        llm.generate.assert_not_called()
