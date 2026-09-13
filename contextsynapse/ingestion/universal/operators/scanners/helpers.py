"""Shared helpers for SDLC scanners."""
from __future__ import annotations

import re
from pathlib import Path
from typing import List

_SKIP_DIRS = {"__pycache__", "node_modules", ".git", "venv", ".venv", ".tox", ".mypy_cache"}
_CODE_EXTENSIONS = (".py", ".ts", ".js", ".go", ".rs", ".java", ".tsx", ".jsx")


def _safe_id(text: str) -> str:
    """Convert text to a safe node ID slug."""
    s = re.sub(r'[^a-zA-Z0-9_-]', '-', text.lower())
    s = re.sub(r'-+', '-', s).strip('-')
    return s[:60]


def _safe_read(path: Path, limit: int = 2000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except Exception:
        return ""


def _find_src_dirs(repo_path: Path) -> List[Path]:
    dirs = []
    for c in ("src", "lib", "app", "pkg", "internal", "cmd"):
        d = repo_path / c
        if d.is_dir():
            dirs.append(d)
    for init in repo_path.glob("*/__init__.py"):
        pkg = init.parent
        if pkg.name not in ("tests", "test", "venv", ".venv", "__pycache__"):
            dirs.append(pkg)
    if not dirs:
        dirs.append(repo_path)
    return dirs


def _extract_summary(text: str, suffix: str) -> str:
    if suffix == ".py":
        m = re.search(r'"""(.*?)"""', text, re.DOTALL)
        if m:
            return m.group(1).strip()[:200]
        m = re.search(r"'''(.*?)'''", text, re.DOTALL)
        if m:
            return m.group(1).strip()[:200]
    elif suffix in (".ts", ".js"):
        m = re.search(r'/\*\*(.*?)\*/', text, re.DOTALL)
        if m:
            return re.sub(r'\n\s*\*\s?', ' ', m.group(1)).strip()[:200]
    elif suffix == ".go":
        lines = text.split("\n")
        comments = []
        for line in lines:
            if line.strip().startswith("//"):
                comments.append(line.strip().lstrip("/").strip())
            elif comments:
                break
        if comments:
            return " ".join(comments)[:200]
    for line in text.split("\n"):
        line = line.strip()
        if line and not line.startswith(("import ", "from ", "#!", "//", "/*", "package ", "require")):
            return line[:200]
    return ""
