"""
GitHub Workspace — remote workspace via GitHub API.

For agents that don't have local filesystem access. Files are pushed
directly to GitHub via the Contents API.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .base import Workspace

logger = logging.getLogger(__name__)


class GitHubWorkspace(Workspace):
    """Workspace backed by GitHub API — no local filesystem needed."""

    def __init__(
        self,
        token: str,
        repo: str,
        branch: str = "main",
        local_path: Optional[str] = None,
    ):
        from ..connectors.github import GitHubConnector

        self._gh = GitHubConnector(token=token, repo=repo, branch=branch)
        self.repo = repo
        self.branch = branch
        self._local_path = local_path

    def write_file(self, filepath: str, content: str) -> str:
        result = self._gh.push_file(filepath, content, message=f"Update {filepath}", branch=self.branch)
        if "error" in str(result).lower():
            return f"Error writing {filepath}: {result}"
        sha = result.get("commit", {}).get("sha", "")[:7]
        return f"Pushed {filepath} to {self.repo}@{self.branch} ({sha})"

    def read_file(self, filepath: str) -> str:
        content = self._gh.get_file(filepath, branch=self.branch)
        if content is None:
            return f"File not found: {filepath}"
        return content

    def list_files(self, path: str = ".") -> List[str]:
        tree = self._gh.get_tree(branch=self.branch)
        return [f["path"] for f in tree if f.get("type") == "blob"]

    def delete_file(self, filepath: str) -> str:
        result = self._gh.delete_file(filepath, message=f"Delete {filepath}", branch=self.branch)
        if "error" in str(result).lower():
            return f"Error: {result}"
        return f"Deleted {filepath} from {self.repo}@{self.branch}"

    def create_branch(self, branch: str) -> str:
        result = self._gh.create_branch(branch, from_branch=self.branch)
        if "error" in str(result).lower():
            return f"Error: {result}"
        self.branch = branch
        return f"Created branch {branch} on {self.repo}"

    def commit(self, message: str, files: Optional[List[str]] = None) -> str:
        # GitHub API commits happen per-file in write_file
        return f"Changes committed via GitHub API (commits happen per write_file call)"

    def push(self, branch: Optional[str] = None) -> str:
        # GitHub API pushes happen per-file
        return f"Changes are already pushed via GitHub API"

    def get_status(self) -> Dict[str, Any]:
        return {
            "type": "github",
            "repo": self.repo,
            "branch": self.branch,
        }
