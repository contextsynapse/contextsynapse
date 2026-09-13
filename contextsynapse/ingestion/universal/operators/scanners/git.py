"""Git scanners — history, metadata, git dir discovery."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .helpers import _safe_id, _CODE_EXTENSIONS

logger = logging.getLogger(__name__)

_CHURN_THRESHOLD = 3
_MAX_CHANGE_RECORDS = 15


def _find_git_dir(repo_path: Path) -> Path:
    """Find the .git directory, walking up from repo_path if needed."""
    current = repo_path.resolve()
    for _ in range(10):
        git_dir = current / ".git"
        if git_dir.is_dir():
            return git_dir
        if git_dir.is_file():
            text = git_dir.read_text().strip()
            if text.startswith("gitdir:"):
                return Path(text.split(":", 1)[1].strip())
        parent = current.parent
        if parent == current:
            break
        current = parent
    return repo_path / ".git"


def _git_toplevel(repo_path: Path) -> str:
    """Get the git top-level directory for running git commands."""
    import subprocess
    try:
        r = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    git_dir = _find_git_dir(repo_path)
    if git_dir.is_dir():
        return str(git_dir.parent)
    return str(repo_path)


def scan_git_history(repo_path: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Create ChangeRecord nodes for high-churn files from git log."""
    if not _find_git_dir(repo_path).is_dir():
        return [], []

    try:
        import subprocess
        from collections import defaultdict

        git_root = _git_toplevel(repo_path)
        result = subprocess.run(
            ["git", "-C", git_root, "log", "--format=%H|%an|%s", "--no-merges", "-50", "--name-only"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return [], []

        file_commits: Dict[str, List[Dict]] = defaultdict(list)
        current_hash = current_author = current_message = ""

        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            if "|" in line and len(line.split("|")) == 3:
                parts = line.split("|", 2)
                current_hash, current_author, current_message = parts
            elif current_hash and line:
                if any(line.endswith(ext) for ext in _CODE_EXTENSIONS):
                    file_commits[line].append({
                        "hash": current_hash, "author": current_author, "message": current_message,
                    })

        nodes = []
        edges = []
        for fpath, commits in sorted(file_commits.items(), key=lambda x: -len(x[1])):
            if len(commits) < _CHURN_THRESHOLD:
                continue
            if len(nodes) >= _MAX_CHANGE_RECORDS:
                break

            latest = commits[0]
            node_id = f"churn:{_safe_id(fpath)}"
            nodes.append({
                "id": node_id,
                "label": "ChangeRecord",
                "properties": {
                    "description": f"{fpath}: {latest['message']}",
                    "author": latest["author"],
                    "commit_count": len(commits),
                    "last_commit_hash": latest["hash"][:8],
                    "path": fpath,
                    "version": 1,
                },
            })
            edges.append({"label": "MODIFIES", "source": node_id, "target": f"code:{_safe_id(fpath)}"})

        logger.info("scan_git_history: %d change records, %d edges", len(nodes), len(edges))
        return nodes, edges

    except Exception as exc:
        logger.debug("scan_git_history: failed — %s", exc)
        return [], []


def scan_repo_metadata(repo_path: Path) -> List[Dict[str, Any]]:
    """Extract repository metadata: branches, tags, contributors, commit stats."""
    if not _find_git_dir(repo_path).is_dir():
        return []

    import subprocess
    nodes = []
    git_root = _git_toplevel(repo_path)

    def _git(args: List[str]) -> str:
        try:
            r = subprocess.run(
                ["git", "-C", git_root] + args,
                capture_output=True, text=True, timeout=10,
            )
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    branches_raw = _git(["branch", "-a", "--format=%(refname:short)"])
    branches = [b.strip() for b in branches_raw.split("\n") if b.strip()] if branches_raw else []
    current_branch = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    tags_raw = _git(["tag", "--list"])
    tags = [t.strip() for t in tags_raw.split("\n") if t.strip()] if tags_raw else []
    commit_count = _git(["rev-list", "--count", "HEAD"])
    contributors_raw = _git(["shortlog", "-sn", "--no-merges", "HEAD"])
    contributors = []
    for line in (contributors_raw or "").split("\n"):
        line = line.strip()
        if line:
            parts = line.split("\t", 1)
            if len(parts) == 2:
                contributors.append({"commits": int(parts[0].strip()), "name": parts[1].strip()})
    last_commit = _git(["log", "-1", "--format=%ci"])
    file_count = _git(["ls-files"])
    tracked_files = len(file_count.split("\n")) if file_count else 0

    nodes.append({
        "id": "meta:repository",
        "label": "ChangeRecord",
        "properties": {
            "description": f"Repository: {repo_path.name} — {commit_count or '?'} commits, {len(branches)} branches, {len(tags)} tags, {len(contributors)} contributors, {tracked_files} files",
            "branch_count": len(branches),
            "current_branch": current_branch,
            "branches": ", ".join(branches[:20]),
            "tag_count": len(tags),
            "tags": ", ".join(tags[:10]),
            "commit_count": int(commit_count) if commit_count.isdigit() else 0,
            "contributor_count": len(contributors),
            "top_contributors": ", ".join(c["name"] for c in contributors[:10]),
            "tracked_files": tracked_files,
            "last_commit": last_commit,
            "version": 1,
        },
    })

    for contrib in contributors[:10]:
        nodes.append({
            "id": f"contributor:{_safe_id(contrib['name'])}",
            "label": "Contributor",
            "properties": {
                "name": contrib["name"],
                "commit_count": contrib["commits"],
                "version": 1,
            },
        })

    logger.info("scan_repo_metadata: %d branches, %d tags, %d contributors, %s commits",
                len(branches), len(tags), len(contributors), commit_count)
    return nodes
