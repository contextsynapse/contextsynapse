"""Tests for GitHub and Jira connectors."""
from __future__ import annotations
import pytest
from unittest.mock import MagicMock, patch

from contextcore.project.connectors.github_connector import GitHubConnector
from contextcore.project.connectors.jira_connector import JiraConnector


class TestGitHubConnector:
    def test_not_configured_without_token(self):
        gh = GitHubConnector({"repo": "owner/repo"})
        assert not gh.configured

    def test_configured_with_both(self):
        gh = GitHubConnector({"repo": "owner/repo", "token": "ghp_test"})
        assert gh.configured

    def test_create_issue_returns_none_when_not_configured(self):
        gh = GitHubConnector({})
        result = gh.create_issue({"title": "Test"})
        assert result is None

    @patch("requests.post")
    def test_create_issue_success(self, mock_post):
        mock_post.return_value = MagicMock(status_code=201, json=lambda: {"number": 42, "html_url": "https://github.com/o/r/issues/42"})
        gh = GitHubConnector({"repo": "owner/repo", "token": "ghp_test"})
        result = gh.create_issue({"title": "Build auth", "description": "JWT", "priority": "high", "acceptance_criteria": '["Works", "Tests pass"]'})
        assert result["issue_number"] == 42
        assert "github.com" in result["url"]

    @patch("requests.patch")
    def test_close_issue(self, mock_patch):
        mock_patch.return_value = MagicMock(status_code=200)
        gh = GitHubConnector({"repo": "owner/repo", "token": "ghp_test"})
        assert gh.close_issue(42) is True


class TestJiraConnector:
    def test_not_configured_without_fields(self):
        jira = JiraConnector({"url": "https://test.atlassian.net"})
        assert not jira.configured

    def test_configured_with_all_fields(self):
        jira = JiraConnector({"url": "https://test.atlassian.net", "email": "a@b.com", "api_token": "xxx", "project_key": "PROJ"})
        assert jira.configured

    def test_create_ticket_returns_none_when_not_configured(self):
        jira = JiraConnector({})
        result = jira.create_ticket({"title": "Test"})
        assert result is None

    @patch("requests.post")
    def test_create_ticket_success(self, mock_post):
        mock_post.return_value = MagicMock(status_code=201, json=lambda: {"key": "PROJ-123"})
        jira = JiraConnector({"url": "https://test.atlassian.net", "email": "a@b.com", "api_token": "xxx", "project_key": "PROJ"})
        result = jira.create_ticket({"title": "Build API", "priority": "high"})
        assert result["ticket_key"] == "PROJ-123"

    @patch("requests.get")
    def test_import_tickets(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {
            "issues": [{"key": "PROJ-1", "fields": {"summary": "Fix bug", "description": "Details", "priority": {"name": "High"}, "status": {"name": "To Do"}}}]
        })
        jira = JiraConnector({"url": "https://test.atlassian.net", "email": "a@b.com", "api_token": "xxx", "project_key": "PROJ"})
        tasks = jira.import_tickets()
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Fix bug"
        assert tasks[0]["priority"] == "high"
