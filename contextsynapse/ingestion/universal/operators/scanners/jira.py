"""Jira scanner — fetch tickets and map to SDLC nodes."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

from .helpers import _safe_id

logger = logging.getLogger(__name__)

from contextsynapse.project.sdlc_schema import JIRA_TYPE_MAP as _JIRA_TYPE_MAP


def scan_jira_issues() -> List[Dict[str, Any]]:
    """Fetch Jira tickets and map them to SDLC nodes.

    Requires env vars: JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY.
    """
    url = os.environ.get("JIRA_URL", "")
    email = os.environ.get("JIRA_EMAIL", "")
    token = os.environ.get("JIRA_API_TOKEN", "")
    project = os.environ.get("JIRA_PROJECT_KEY", "")

    if not all([url, email, token, project]):
        logger.debug("scan_jira_issues: JIRA env vars not set — skipping")
        return []

    try:
        import requests
        from requests.auth import HTTPBasicAuth

        resp = requests.get(
            f"{url.rstrip('/')}/rest/api/2/search",
            auth=HTTPBasicAuth(email, token),
            params={"jql": f"project = {project} AND status != Done ORDER BY priority DESC", "maxResults": 30},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.debug("scan_jira_issues: Jira API returned %d — skipping", resp.status_code)
            return []

        issues = resp.json().get("issues", [])
        nodes = []
        for issue in issues:
            fields = issue.get("fields", {})
            key = issue.get("key", "")
            summary = fields.get("summary", "")
            description = (fields.get("description") or "")[:300]
            issue_type = (fields.get("issuetype", {}) or {}).get("name", "").lower()
            node_type = _JIRA_TYPE_MAP.get(issue_type, "Requirement")

            props = {"content": f"{summary}. {description}".strip(), "source": f"jira:{key}", "version": 1}
            if node_type == "KnownIssue":
                priority = (fields.get("priority", {}) or {}).get("name", "Medium")
                props = {"description": f"{summary}. {description}".strip(), "severity": priority.lower(), "source": f"jira:{key}", "version": 1}

            nodes.append({"id": f"jira:{_safe_id(key)}", "label": node_type, "properties": props})

        logger.info("scan_jira_issues: %d tickets from %s/%s", len(nodes), url, project)
        return nodes

    except Exception as exc:
        logger.debug("scan_jira_issues: failed — %s", exc)
        return []
