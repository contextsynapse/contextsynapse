"""
GitHub Connector
=================
Push code, create PRs, sync issues from the project graph to GitHub.

Usage:
    gh = GitHubConnector(token="ghp_...", repo="user/my-app")
    gh.push_file("auth.py", content, message="Implement JWT auth")
    pr_url = gh.create_pr(title="Add auth", body="Implements JWT auth", branch="feat/auth")
    gh.create_issue(title="Bug: token expiry", body="...", labels=["bug"])
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class GitHubConnector:
    """GitHub API connector for code push, PRs, and issues."""

    def __init__(self, token: str, repo: str, branch: str = "main"):
        """
        Args:
            token: GitHub personal access token (ghp_...)
            repo: Repository in "owner/repo" format
            branch: Default branch
        """
        self.token = token
        self.repo = repo
        self.branch = branch
        self._base = f"https://api.github.com/repos/{repo}"
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def push_file(
        self,
        path: str,
        content: str,
        message: str = "Update file",
        branch: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Push or update a file in the repo.

        Returns commit info or error.
        """
        branch = branch or self.branch
        url = f"{self._base}/contents/{path}"

        # Check if file exists (need SHA for update)
        sha = None
        resp = requests.get(url, headers=self._headers, params={"ref": branch})
        if resp.status_code == 200:
            sha = resp.json().get("sha")

        payload = {
            "message": message,
            "content": base64.b64encode(content.encode()).decode(),
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha

        resp = requests.put(url, headers=self._headers, json=payload)
        if resp.status_code in (200, 201):
            data = resp.json()
            return {
                "status": "success",
                "sha": data.get("commit", {}).get("sha", ""),
                "url": data.get("content", {}).get("html_url", ""),
            }
        return {"status": "error", "code": resp.status_code, "message": resp.text[:200]}

    def create_branch(self, branch_name: str, from_branch: Optional[str] = None) -> Dict[str, Any]:
        """Create a new branch."""
        from_branch = from_branch or self.branch

        # Get SHA of source branch
        resp = requests.get(
            f"{self._base}/git/refs/heads/{from_branch}",
            headers=self._headers,
        )
        if resp.status_code != 200:
            return {"status": "error", "message": f"Branch {from_branch} not found"}

        sha = resp.json()["object"]["sha"]

        resp = requests.post(
            f"{self._base}/git/refs",
            headers=self._headers,
            json={"ref": f"refs/heads/{branch_name}", "sha": sha},
        )
        if resp.status_code == 201:
            return {"status": "success", "branch": branch_name, "sha": sha}
        return {"status": "error", "code": resp.status_code, "message": resp.text[:200]}

    def create_pr(
        self,
        title: str,
        body: str = "",
        head: str = "",
        base: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a pull request."""
        resp = requests.post(
            f"{self._base}/pulls",
            headers=self._headers,
            json={
                "title": title,
                "body": body,
                "head": head,
                "base": base or self.branch,
            },
        )
        if resp.status_code == 201:
            data = resp.json()
            return {
                "status": "success",
                "pr_number": data["number"],
                "url": data["html_url"],
            }
        return {"status": "error", "code": resp.status_code, "message": resp.text[:200]}

    def create_issue(
        self,
        title: str,
        body: str = "",
        labels: Optional[List[str]] = None,
        assignees: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create a GitHub issue."""
        payload: Dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        if assignees:
            payload["assignees"] = assignees

        resp = requests.post(
            f"{self._base}/issues",
            headers=self._headers,
            json=payload,
        )
        if resp.status_code == 201:
            data = resp.json()
            return {
                "status": "success",
                "issue_number": data["number"],
                "url": data["html_url"],
            }
        return {"status": "error", "code": resp.status_code, "message": resp.text[:200]}

    def test_connection(self) -> bool:
        """Test if the token and repo are valid."""
        resp = requests.get(self._base, headers=self._headers)
        return resp.status_code == 200
