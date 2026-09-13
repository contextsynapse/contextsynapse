"""
Workspace base class — unified interface for agent file operations.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class Workspace(ABC):
    """Abstract workspace that agents write artifacts to."""

    @abstractmethod
    def write_file(self, filepath: str, content: str) -> str:
        """Write a file. Returns confirmation message."""

    @abstractmethod
    def read_file(self, filepath: str) -> str:
        """Read a file. Returns content or error."""

    @abstractmethod
    def list_files(self, path: str = ".") -> List[str]:
        """List files in the workspace."""

    @abstractmethod
    def delete_file(self, filepath: str) -> str:
        """Delete a file. Returns confirmation."""

    def commit(self, message: str, files: Optional[List[str]] = None) -> str:
        """Commit changes (no-op for non-git workspaces)."""
        return "Commits not supported in this workspace type."

    def push(self, branch: Optional[str] = None) -> str:
        """Push to remote (no-op for non-git workspaces)."""
        return "Push not supported in this workspace type."

    def create_branch(self, branch: str) -> str:
        """Create and switch to a branch."""
        return "Branches not supported in this workspace type."

    def run_command(self, command: str, timeout: int = 60) -> str:
        """Run a shell command in the workspace directory."""
        return "Command execution not supported in this workspace type."

    def get_status(self) -> Dict[str, Any]:
        """Get workspace status (file count, git status, etc.)."""
        return {"type": self.__class__.__name__, "files": len(self.list_files())}

    @staticmethod
    def from_config(config: Dict[str, Any]) -> "Workspace":
        """Factory: create a workspace from a config dict.

        Config keys:
            type: "local" | "git" | "github"
            path: local directory path (for local/git)
            repo_url: git remote URL (for git)
            branch: default branch name
            github_token: GitHub PAT (for github type)
            github_repo: "owner/repo" (for github type)
        """
        ws_type = config.get("type", "local")

        if ws_type == "local":
            from .local import LocalWorkspace
            return LocalWorkspace(base_path=config.get("path", "generated"))

        if ws_type == "git":
            from .git import GitWorkspace
            repo_url = config.get("repo_url", "")
            token = config.get("token", "")
            # Inject token into URL for private repo auth (PAT-based)
            if token and repo_url and "github.com" in repo_url and "@" not in repo_url:
                repo_url = repo_url.replace("https://", f"https://{token}@")
            return GitWorkspace(
                base_path=config.get("path", "generated"),
                repo_url=repo_url,
                branch=config.get("branch", "main"),
                auto_clone=config.get("auto_clone", True),
            )

        if ws_type == "github":
            from .github import GitHubWorkspace
            return GitHubWorkspace(
                token=config["github_token"],
                repo=config["github_repo"],
                branch=config.get("branch", "main"),
                local_path=config.get("path"),
            )

        raise ValueError(f"Unknown workspace type: {ws_type}")
