"""Jira connector — sync tasks to Jira tickets."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class JiraConnector:
    """Sync project tasks to Jira tickets.

    Config: {"url": "https://company.atlassian.net", "email": "...", "api_token": "...", "project_key": "PROJ"}
    """

    def __init__(self, config: Dict[str, str]):
        self.url = config.get("url", "").rstrip("/")
        self.email = config.get("email", "")
        self.api_token = config.get("api_token", "")
        self.project_key = config.get("project_key", "")

    @property
    def configured(self) -> bool:
        return bool(self.url and self.email and self.api_token and self.project_key)

    def create_ticket(self, task: Dict[str, Any]) -> Optional[Dict]:
        """Create a Jira ticket from a task dict.

        Returns {"ticket_key": "PROJ-123", "url": "..."} on success.
        """
        if not self.configured:
            return None

        import requests
        from requests.auth import HTTPBasicAuth

        title = task.get("title", "Untitled")
        description = task.get("description", "")

        # Add acceptance criteria to description
        criteria = task.get("acceptance_criteria", [])
        if isinstance(criteria, str):
            import json
            try:
                criteria = json.loads(criteria)
            except Exception:
                criteria = []
        if criteria:
            description += "\n\nh3. Acceptance Criteria\n" + "\n".join(f"* {c}" for c in criteria)

        # Map priority
        priority_map = {"high": "High", "medium": "Medium", "low": "Low"}
        priority = priority_map.get(task.get("priority", "medium"), "Medium")

        payload = {
            "fields": {
                "project": {"key": self.project_key},
                "summary": title,
                "description": description,
                "issuetype": {"name": "Task"},
                "priority": {"name": priority},
            }
        }

        try:
            resp = requests.post(
                f"{self.url}/rest/api/2/issue",
                auth=HTTPBasicAuth(self.email, self.api_token),
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=15,
            )
            if resp.status_code == 201:
                data = resp.json()
                key = data.get("key", "")
                return {"ticket_key": key, "url": f"{self.url}/browse/{key}"}
            else:
                logger.warning("Jira create_ticket failed: %d %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.error("Jira create_ticket error: %s", e)

        return None

    def transition_ticket(self, ticket_key: str, status: str) -> bool:
        """Transition a Jira ticket to a new status."""
        if not self.configured:
            return False
        # Jira transitions are complex (need transition IDs). Simplified version:
        import requests
        from requests.auth import HTTPBasicAuth
        try:
            # Get available transitions
            resp = requests.get(
                f"{self.url}/rest/api/2/issue/{ticket_key}/transitions",
                auth=HTTPBasicAuth(self.email, self.api_token),
                timeout=15,
            )
            if resp.status_code != 200:
                return False

            transitions = resp.json().get("transitions", [])
            # Find matching transition
            target = {"completed": "Done", "in_progress": "In Progress", "open": "To Do"}.get(status, status)
            transition_id = None
            for t in transitions:
                if t["name"].lower() == target.lower():
                    transition_id = t["id"]
                    break

            if not transition_id:
                return False

            resp = requests.post(
                f"{self.url}/rest/api/2/issue/{ticket_key}/transitions",
                auth=HTTPBasicAuth(self.email, self.api_token),
                json={"transition": {"id": transition_id}},
                timeout=15,
            )
            return resp.status_code == 204
        except Exception:
            return False

    def import_tickets(self, jql: str = "") -> List[Dict]:
        """Import Jira tickets as task dicts.

        Returns list of task-compatible dicts.
        """
        if not self.configured:
            return []
        import requests
        from requests.auth import HTTPBasicAuth

        if not jql:
            jql = f"project = {self.project_key} AND status != Done ORDER BY priority DESC"

        try:
            resp = requests.get(
                f"{self.url}/rest/api/2/search",
                auth=HTTPBasicAuth(self.email, self.api_token),
                params={"jql": jql, "maxResults": 50},
                timeout=15,
            )
            if resp.status_code != 200:
                return []

            issues = resp.json().get("issues", [])
            tasks = []
            for issue in issues:
                fields = issue.get("fields", {})
                tasks.append({
                    "title": fields.get("summary", ""),
                    "description": fields.get("description", "") or "",
                    "priority": (fields.get("priority", {}) or {}).get("name", "Medium").lower(),
                    "status": (fields.get("status", {}) or {}).get("name", "To Do").lower().replace(" ", "_"),
                    "external_id": issue.get("key", ""),
                    "external_url": f"{self.url}/browse/{issue.get('key', '')}",
                })
            return tasks
        except Exception:
            return []
