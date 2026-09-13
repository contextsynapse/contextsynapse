"""GitHub API scanner — issues (open + closed) with metadata."""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List

from .helpers import _safe_id
from .git import _find_git_dir

logger = logging.getLogger(__name__)

from contextsynapse.project.sdlc_schema import ISSUE_LABEL_MAP as _ISSUE_LABEL_MAP, DEFAULT_ISSUE_TYPE


def _parse_github_remote(repo_path: Path) -> str:
    """Extract 'owner/repo' from .git/config origin URL."""
    git_dir = _find_git_dir(repo_path)
    git_config = git_dir / "config"
    if not git_config.exists():
        return ""
    try:
        text = git_config.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?\s*$', text, re.MULTILINE)
        return m.group(1) if m else ""
    except Exception:
        return ""


def _extract_github_owner_repo(repo_path: Path, source_url: str = "") -> str:
    """Extract owner/repo from GitHub URL or .git/config."""
    if source_url:
        m = re.search(r'github\.com/([^/\s]+/[^/\s]+?)(?:\.git)?(?:/.*)?$', source_url)
        if m:
            return m.group(1)
    return _parse_github_remote(repo_path)


def _issue_to_node(issue: dict) -> dict:
    """Convert a GitHub issue dict to an SDLC node dict."""
    number = issue.get("number", 0)
    title = issue.get("title", "")
    body = (issue.get("body") or "")[:300]
    labels = [l.get("name", "").lower() for l in issue.get("labels", [])]

    node_type = DEFAULT_ISSUE_TYPE
    for label in labels:
        if label in _ISSUE_LABEL_MAP:
            node_type = _ISSUE_LABEL_MAP[label]
            break

    props = {"content": f"{title}. {body}".strip(), "source": f"github:issue:{number}", "version": 1}
    if node_type == "KnownIssue":
        props = {"description": f"{title}. {body}".strip(), "severity": "unknown", "source": f"github:issue:{number}", "version": 1}

    return {"id": f"issue:{number}", "label": node_type, "properties": props}


def scan_github_issues_full(repo_path: Path, source_url: str = "", max_issues: int = 100) -> List[Dict[str, Any]]:
    """Fetch BOTH open and closed GitHub issues with full metadata and pagination."""
    owner_repo = _extract_github_owner_repo(repo_path, source_url)
    if not owner_repo:
        logger.debug("scan_github_issues_full: no GitHub remote — skipping")
        return []

    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    else:
        logger.warning("GITHUB_TOKEN not set — unauthenticated GitHub API (60 req/hour limit)")

    nodes = []
    for state in ("open", "closed"):
        page = 1
        fetched = 0
        while fetched < max_issues:
            per_page = min(30, max_issues - fetched)
            try:
                import requests
                resp = requests.get(
                    f"https://api.github.com/repos/{owner_repo}/issues",
                    params={
                        "state": state, "per_page": per_page,
                        "sort": "updated", "direction": "desc", "page": page,
                    },
                    headers=headers,
                    timeout=15,
                )
                remaining = resp.headers.get("X-RateLimit-Remaining", "?")
                if resp.status_code != 200:
                    logger.warning(
                        "scan_github_issues_full: %s issues — API returned %d "
                        "(rate limit remaining: %s)",
                        state, resp.status_code, remaining,
                    )
                    break

                issues = resp.json()
                if not isinstance(issues, list):
                    break

                issues = [i for i in issues if "pull_request" not in i]
                for issue in issues:
                    node = _issue_to_node(issue)
                    node["properties"]["state"] = state
                    node["properties"]["labels"] = ", ".join(l.get("name", "") for l in issue.get("labels", []))
                    node["properties"]["assignee"] = (issue.get("assignee") or {}).get("login", "")
                    node["properties"]["created_at"] = issue.get("created_at", "")
                    node["properties"]["updated_at"] = issue.get("updated_at", "")
                    node["properties"]["comments"] = issue.get("comments", 0)
                    if state == "closed":
                        node["id"] = f"issue-closed:{issue.get('number', 0)}"
                    nodes.append(node)

                fetched += len(issues)
                if len(issues) < per_page:
                    break  # last page
                page += 1

            except Exception as exc:
                logger.warning("scan_github_issues_full: %s issues failed — %s", state, exc)
                break

    if not nodes:
        logger.warning(
            "scan_github_issues_full: 0 issues from %s — "
            "check GITHUB_TOKEN and repo visibility",
            owner_repo,
        )
    else:
        logger.info("scan_github_issues_full: %d issues from %s", len(nodes), owner_repo)
    return nodes
