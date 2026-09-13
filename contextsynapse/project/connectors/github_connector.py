"""GitHub connector — sync tasks to GitHub Issues."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class GitHubConnector:
    """Sync project tasks to GitHub Issues.

    Config: {"repo": "owner/repo", "token": "ghp_xxx"}
    """

    def __init__(self, config: Dict[str, str]):
        self.repo = config.get("repo", "")
        self.token = config.get("token", "")
        self._base_url = f"https://api.github.com/repos/{self.repo}"

    @property
    def configured(self) -> bool:
        return bool(self.repo and self.token)

    def create_issue(self, task: Dict[str, Any]) -> Optional[Dict]:
        """Create a GitHub Issue from a task dict.

        Returns {"issue_number": N, "url": "..."} on success, None on failure.
        """
        if not self.configured:
            return None

        import requests
        title = task.get("title", "Untitled")
        body_parts = []
        if task.get("description"):
            body_parts.append(task["description"])
        if task.get("acceptance_criteria"):
            criteria = task["acceptance_criteria"]
            if isinstance(criteria, str):
                import json
                try:
                    criteria = json.loads(criteria)
                except Exception:
                    criteria = [criteria]
            if criteria:
                body_parts.append("\n## Acceptance Criteria")
                for c in criteria:
                    body_parts.append(f"- [ ] {c}")
        if task.get("files"):
            files = task["files"]
            if isinstance(files, str):
                import json
                try:
                    files = json.loads(files)
                except Exception:
                    files = [files]
            if files:
                body_parts.append(f"\n## Files\n" + "\n".join(f"- `{f}`" for f in files))

        body = "\n".join(body_parts)
        labels = []
        priority = task.get("priority", "")
        if priority:
            labels.append(f"priority:{priority}")
        tags = task.get("tags", "")
        if isinstance(tags, str) and tags:
            labels.extend(tags.split(","))

        try:
            resp = requests.post(
                f"{self._base_url}/issues",
                headers={
                    "Authorization": f"token {self.token}",
                    "Accept": "application/vnd.github.v3+json",
                },
                json={"title": title, "body": body, "labels": labels[:10]},
                timeout=15,
            )
            if resp.status_code == 201:
                data = resp.json()
                return {"issue_number": data["number"], "url": data["html_url"]}
            else:
                logger.warning("GitHub create_issue failed: %d %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.error("GitHub create_issue error: %s", e)

        return None

    def close_issue(self, issue_number: int) -> bool:
        """Close a GitHub Issue."""
        if not self.configured:
            return False
        import requests
        try:
            resp = requests.patch(
                f"{self._base_url}/issues/{issue_number}",
                headers={
                    "Authorization": f"token {self.token}",
                    "Accept": "application/vnd.github.v3+json",
                },
                json={"state": "closed"},
                timeout=15,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def sync_task_status(self, issue_number: int, status: str) -> bool:
        """Sync task status to GitHub Issue (open/closed)."""
        if status in ("completed", "done"):
            return self.close_issue(issue_number)
        return True  # open issues stay open
