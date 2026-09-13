"""
Jira Connector
================
Create issues, sync status, and track work from the project graph to Jira.

Usage:
    jira = JiraConnector(url="https://your-org.atlassian.net", email="you@co.com", token="...")
    jira.create_issue(project="MYAPP", title="Implement JWT auth", description="...", issue_type="Task")
    jira.update_status(issue_key="MYAPP-42", status="Done")
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class JiraConnector:
    """Jira REST API connector for issue tracking."""

    def __init__(self, url: str, email: str, token: str):
        """
        Args:
            url: Jira instance URL (e.g. https://your-org.atlassian.net)
            email: Jira account email
            token: Jira API token
        """
        self.url = url.rstrip("/")
        self._auth = (email, token)
        self._headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def create_issue(
        self,
        project: str,
        title: str,
        description: str = "",
        issue_type: str = "Task",
        priority: Optional[str] = None,
        labels: Optional[List[str]] = None,
        assignee: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a Jira issue."""
        fields: Dict[str, Any] = {
            "project": {"key": project},
            "summary": title,
            "description": description,
            "issuetype": {"name": issue_type},
        }
        if priority:
            fields["priority"] = {"name": priority.capitalize()}
        if labels:
            fields["labels"] = labels
        if assignee:
            fields["assignee"] = {"accountId": assignee}

        resp = requests.post(
            f"{self.url}/rest/api/3/issue",
            auth=self._auth,
            headers=self._headers,
            json={"fields": fields},
        )
        if resp.status_code == 201:
            data = resp.json()
            return {
                "status": "success",
                "key": data["key"],
                "id": data["id"],
                "url": f"{self.url}/browse/{data['key']}",
            }
        return {"status": "error", "code": resp.status_code, "message": resp.text[:200]}

    def get_issue(self, issue_key: str) -> Dict[str, Any]:
        """Get issue details."""
        resp = requests.get(
            f"{self.url}/rest/api/3/issue/{issue_key}",
            auth=self._auth,
            headers=self._headers,
        )
        if resp.status_code == 200:
            data = resp.json()
            fields = data.get("fields", {})
            return {
                "key": data["key"],
                "summary": fields.get("summary", ""),
                "status": fields.get("status", {}).get("name", ""),
                "assignee": (fields.get("assignee") or {}).get("displayName", "Unassigned"),
                "priority": (fields.get("priority") or {}).get("name", ""),
            }
        return {"status": "error", "code": resp.status_code}

    def add_comment(self, issue_key: str, comment: str) -> Dict[str, Any]:
        """Add a comment to an issue."""
        resp = requests.post(
            f"{self.url}/rest/api/3/issue/{issue_key}/comment",
            auth=self._auth,
            headers=self._headers,
            json={"body": {"type": "doc", "version": 1, "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": comment}]}
            ]}},
        )
        if resp.status_code == 201:
            return {"status": "success", "comment_id": resp.json().get("id")}
        return {"status": "error", "code": resp.status_code}

    def test_connection(self) -> bool:
        """Test if credentials are valid."""
        resp = requests.get(
            f"{self.url}/rest/api/3/myself",
            auth=self._auth,
            headers=self._headers,
        )
        return resp.status_code == 200
