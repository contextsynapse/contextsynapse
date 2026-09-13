"""
Git Workspace — local git repo with branch/commit/push.

Agents work like real developers:
1. Clone or init a repo
2. Create a feature branch
3. Write files
4. Commit changes
5. Push to remote
6. (Optionally) create a PR via GitHub API

Usage:
    ws = GitWorkspace(
        base_path="workspaces/abc-website",
        repo_url="https://github.com/user/abc-website.git",
        branch="feat/homepage",
    )
    ws.write_file("src/App.jsx", code)
    ws.commit("Add homepage component")
    ws.push()
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Any, Dict, List, Optional

from .local import LocalWorkspace

logger = logging.getLogger(__name__)


class GitWorkspace(LocalWorkspace):
    """Workspace backed by a local git repo with remote push capability."""

    _git_lock = None  # Thread lock for git operations

    def __init__(
        self,
        base_path: str = "generated",
        repo_url: Optional[str] = None,
        branch: str = "main",
        auto_clone: bool = True,
    ):
        import threading
        if GitWorkspace._git_lock is None:
            GitWorkspace._git_lock = threading.Lock()
        self.repo_url = repo_url
        self.branch = branch
        self._current_branch = branch

        # If repo_url provided and auto_clone, clone into base_path
        if repo_url and auto_clone and not os.path.exists(os.path.join(base_path, ".git")):
            parent = os.path.dirname(os.path.abspath(base_path))
            os.makedirs(parent, exist_ok=True)
            self._run_git(["clone", repo_url, os.path.abspath(base_path)], cwd=parent)

        super().__init__(base_path)

        # Init git if no repo exists
        if not os.path.exists(os.path.join(self.base_path, ".git")):
            self._run_git(["init"])
            self._run_git(["checkout", "-b", branch])

        # Always ensure remote URL is up-to-date (token may have changed)
        if repo_url and os.path.exists(os.path.join(self.base_path, ".git")):
            remotes = self._run_git(["remote"])
            if "origin" in remotes:
                self._run_git(["remote", "set-url", "origin", repo_url])
            else:
                self._run_git(["remote", "add", "origin", repo_url])

        # Configure git user for commits (required in clean environments)
        self._run_git(["config", "user.email", "agent@contextsynapse.com"])
        self._run_git(["config", "user.name", "AIContextDB Agent"])

    def _run_git(self, args: List[str], cwd: Optional[str] = None) -> str:
        """Run a git command with thread-safe locking."""
        cmd = ["git"] + args
        # Acquire lock for mutating operations to prevent index.lock conflicts
        mutating = args[0] in ("add", "commit", "push", "pull", "checkout", "merge", "reset", "stash")
        if mutating and GitWorkspace._git_lock:
            GitWorkspace._git_lock.acquire()
        try:
            # Clean up stale lock file before mutating operations
            if mutating:
                lock_file = os.path.join(cwd or self.base_path, ".git", "index.lock")
                if os.path.exists(lock_file):
                    try:
                        os.remove(lock_file)
                        logger.debug("Removed stale .git/index.lock")
                    except Exception:
                        pass

            result = subprocess.run(
                cmd,
                cwd=cwd or self.base_path,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                error = result.stderr.strip() or result.stdout.strip()
                logger.warning("git %s failed: %s", " ".join(args), error)
                return f"Error: {error}"
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            return "Error: git command timed out"
        except FileNotFoundError:
            return "Error: git not found. Install git to use GitWorkspace."
        finally:
            if mutating and GitWorkspace._git_lock and GitWorkspace._git_lock.locked():
                GitWorkspace._git_lock.release()

    def create_branch(self, branch: str) -> str:
        """Create and switch to a new branch."""
        result = self._run_git(["checkout", "-b", branch])
        if not result.startswith("Error"):
            self._current_branch = branch
            return f"Created and switched to branch: {branch}"
        # Branch might already exist
        result = self._run_git(["checkout", branch])
        if not result.startswith("Error"):
            self._current_branch = branch
            return f"Switched to existing branch: {branch}"
        return result

    def commit(self, message: str, files: Optional[List[str]] = None) -> str:
        """Stage and commit changes."""
        if files:
            for f in files:
                self._run_git(["add", f])
        else:
            self._run_git(["add", "-A"])

        # Check if there's anything to commit
        status = self._run_git(["status", "--porcelain"])
        if not status or status.startswith("Error"):
            return "Nothing to commit."

        result = self._run_git(["commit", "-m", message])
        if result.startswith("Error"):
            return result

        # Get short hash
        sha = self._run_git(["rev-parse", "--short", "HEAD"])
        return f"Committed ({sha}): {message}"

    def push(self, branch: Optional[str] = None) -> str:
        """Push to remote."""
        if not self.repo_url:
            return "No remote configured. Set repo_url to enable push."

        branch = branch or self._current_branch
        logger.info("Pushing branch %s to %s", branch, self.repo_url.split("@")[-1] if "@" in self.repo_url else self.repo_url)

        # Check if there are commits to push
        log = self._run_git(["log", "--oneline", "-1"])
        if not log or log.startswith("Error"):
            return "Nothing to push — no commits yet."

        result = self._run_git(["push", "-u", "origin", branch])
        if result.startswith("Error"):
            # Try with --set-upstream for new branches
            result = self._run_git(["push", "--set-upstream", "origin", branch])
        if result.startswith("Error"):
            logger.error("Git push failed: %s", result)
            return result
        logger.info("Push succeeded: %s", branch)
        return f"Pushed to origin/{branch}"

    def pull(self, branch: Optional[str] = None) -> str:
        """Pull latest from remote."""
        branch = branch or self._current_branch
        return self._run_git(["pull", "origin", branch])

    def git_status(self) -> str:
        """Get git status."""
        return self._run_git(["status", "--short"])

    def git_log(self, n: int = 10) -> str:
        """Get recent git log."""
        return self._run_git(["log", f"--oneline", f"-{n}"])

    def git_diff(self, staged: bool = False) -> str:
        """Get diff of changes."""
        args = ["diff"]
        if staged:
            args.append("--staged")
        return self._run_git(args)

    def run_command(self, command: str, timeout: int = 120) -> str:
        """Run a shell command in the workspace directory."""
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=self.base_path,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = result.stdout
            if result.stderr:
                output += "\n" + result.stderr
            if result.returncode != 0:
                output = f"Exit code {result.returncode}\n{output}"
            return output.strip()[:5000]
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {timeout}s"

    def get_status(self) -> Dict[str, Any]:
        status = super().get_status()
        status["type"] = "git"
        status["branch"] = self._current_branch
        # Strip token from URL for display
        import re
        safe_url = re.sub(r'://[^@]+@', '://', self.repo_url or "")
        status["repo_url"] = safe_url
        status["git_status"] = self.git_status()
        return status

    def write_file(self, filepath: str, content: str) -> str:
        """Write file and report git-relevant info."""
        result = super().write_file(filepath, content)
        return result
