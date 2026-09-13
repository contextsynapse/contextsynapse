"""GitHub API-only scanner — Quick Scan without cloning.

Fetches everything available via GitHub REST API:
  - README → Requirements
  - Issues (open + closed) → Requirements / KnownIssues
  - Pull requests → ChangeRecords
  - Contributors → ChangeRecords
  - Branches, tags → metadata
  - Repo info (stars, forks, language, license)
  - File tree + source files → CodeModule nodes

No git clone needed. Works in ~15 seconds for any public repo.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from contextlib import contextmanager
from typing import Any, Dict, List

from .helpers import _safe_id
from .github import _issue_to_node

logger = logging.getLogger(__name__)

# Thread-local token override — set by quick_scan() so all sub-calls share it
_tl = threading.local()


@contextmanager
def _token_context(token: str):
    """Set a per-thread GitHub token for the duration of a scan."""
    _tl.github_token = token or ""
    try:
        yield
    finally:
        _tl.github_token = ""


def _github_get(owner_repo: str, endpoint: str, params: Dict = None, token: str = None) -> Any:
    """Call GitHub REST API. Returns parsed JSON or None."""
    import requests
    token = token or getattr(_tl, "github_token", "") or os.environ.get("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    else:
        logger.warning("GITHUB_TOKEN not set — using unauthenticated API (60 req/hour limit)")

    url = f"https://api.github.com/repos/{owner_repo}{endpoint}"
    try:
        resp = requests.get(url, headers=headers, params=params or {}, timeout=15)
        remaining = resp.headers.get("X-RateLimit-Remaining", "?")
        if resp.status_code == 200:
            logger.debug("GitHub API %s OK (rate limit remaining: %s)", url, remaining)
            return resp.json()
        if resp.status_code in (401, 403):
            logger.warning(
                "GitHub API %s returned %d — check GITHUB_TOKEN validity/permissions "
                "(rate limit remaining: %s)",
                url, resp.status_code, remaining,
            )
        elif resp.status_code == 429:
            logger.warning("GitHub API %s rate-limited (429) — add GITHUB_TOKEN or wait", url)
        else:
            logger.warning("GitHub API %s returned %d", url, resp.status_code)
    except Exception as exc:
        logger.warning("GitHub API %s failed: %s", url, exc)
    return None


def _parse_owner_repo(url: str) -> str:
    """Extract 'owner/repo' from a GitHub URL."""
    m = re.search(r'github\.com/([^/\s]+/[^/\s]+?)(?:\.git)?(?:/.*)?$', url)
    return m.group(1) if m else ""


def scan_github_readme(owner_repo: str) -> List[Dict[str, Any]]:
    """Fetch README via API and extract Requirements from sections."""
    data = _github_get(owner_repo, "/readme")
    if not data:
        return []

    import base64
    content = data.get("content", "")
    encoding = data.get("encoding", "")
    if encoding == "base64":
        try:
            text = base64.b64decode(content).decode("utf-8", errors="replace")
        except Exception:
            return []
    else:
        text = content

    nodes = []
    sections = re.split(r'\n#{1,3}\s+', text)
    for section in sections[1:]:
        lines = section.strip().split("\n")
        title = lines[0].strip().rstrip("#").strip()
        body = "\n".join(lines[1:]).strip()[:500]
        if len(body) < 20:
            continue
        nodes.append({
            "id": f"req:{_safe_id(title)}",
            "label": "Requirement",
            "properties": {"content": f"{title}: {body[:300]}", "source": "README.md", "version": 1},
        })
    return nodes


def scan_github_issues_api(owner_repo: str, max_issues: int = 100, github_token: str = None) -> List[Dict[str, Any]]:
    """Fetch open + closed issues via API with pagination."""
    nodes = []
    for state in ("open", "closed"):
        page = 1
        fetched = 0
        while fetched < max_issues:
            per_page = min(30, max_issues - fetched)
            batch = _github_get(owner_repo, "/issues", {
                "state": state, "per_page": per_page,
                "sort": "updated", "direction": "desc", "page": page,
            }, token=github_token)
            if not batch or not isinstance(batch, list):
                if page == 1:
                    logger.warning(
                        "scan_github_issues_api: no %s issues returned from %s "
                        "(API error or empty repo — check GITHUB_TOKEN)",
                        state, owner_repo,
                    )
                break

            for issue in batch:
                if "pull_request" in issue:
                    continue
                node = _issue_to_node(issue)
                node["properties"]["state"] = state
                node["properties"]["labels"] = ", ".join(
                    l.get("name", "") for l in issue.get("labels", [])
                )
                node["properties"]["assignee"] = (issue.get("assignee") or {}).get("login", "")
                node["properties"]["comments"] = issue.get("comments", 0)
                if state == "closed":
                    node["id"] = f"issue-closed:{issue.get('number', 0)}"
                nodes.append(node)

            fetched += len(batch)
            if len(batch) < per_page:
                break  # last page
            page += 1

    if not nodes:
        logger.warning(
            "scan_github_issues_api: 0 issues total from %s — "
            "is GITHUB_TOKEN set? Is the repo public?",
            owner_repo,
        )
    else:
        logger.info("scan_github_issues_api: %d issues from %s", len(nodes), owner_repo)
    return nodes


def scan_github_pulls(owner_repo: str) -> List[Dict[str, Any]]:
    """Fetch recent pull requests as ChangeRecords."""
    nodes = []
    for state in ("open", "closed"):
        pulls = _github_get(owner_repo, "/pulls", {
            "state": state, "per_page": 10, "sort": "updated", "direction": "desc",
        })
        if not pulls or not isinstance(pulls, list):
            continue

        for pr in pulls:
            number = pr.get("number", 0)
            title = pr.get("title", "")
            author = (pr.get("user") or {}).get("login", "")
            labels = ", ".join(l.get("name", "") for l in pr.get("labels", []))

            nodes.append({
                "id": f"pr:{number}",
                "label": "PullRequest",
                "properties": {
                    "description": f"PR #{number}: {title}",
                    "author": author,
                    "state": state,
                    "labels": labels,
                    "source": f"github:pr:{number}",
                    "version": 1,
                },
            })

    logger.info("scan_github_pulls: %d PRs from %s", len(nodes), owner_repo)
    return nodes


def scan_github_contributors(owner_repo: str) -> List[Dict[str, Any]]:
    """Fetch top contributors."""
    contributors = _github_get(owner_repo, "/contributors", {"per_page": 15})
    if not contributors or not isinstance(contributors, list):
        return []

    nodes = []
    for c in contributors:
        login = c.get("login", "")
        commits = c.get("contributions", 0)
        if not login:
            continue
        nodes.append({
            "id": f"contributor:{_safe_id(login)}",
            "label": "Contributor",
            "properties": {
                "name": login,
                "commit_count": commits,
                "version": 1,
            },
        })

    logger.info("scan_github_contributors: %d contributors from %s", len(nodes), owner_repo)
    return nodes


def scan_github_repo_meta(owner_repo: str) -> List[Dict[str, Any]]:
    """Fetch repo metadata — stars, forks, language, license, branches, tags."""
    repo = _github_get(owner_repo, "")
    if not repo:
        return []

    # Branches
    branches = _github_get(owner_repo, "/branches", {"per_page": 30})
    branch_names = [b.get("name", "") for b in (branches or [])]

    # Tags
    tags = _github_get(owner_repo, "/tags", {"per_page": 10})
    tag_names = [t.get("name", "") for t in (tags or [])]

    return [{
        "id": "meta:repository",
        "label": "ChangeRecord",
        "properties": {
            "description": (
                f"Repository: {repo.get('full_name', owner_repo)} — "
                f"{repo.get('stargazers_count', 0)} stars, "
                f"{repo.get('forks_count', 0)} forks, "
                f"language: {repo.get('language', '?')}, "
                f"{len(branch_names)} branches, "
                f"{len(tag_names)} tags"
            ),
            "stars": repo.get("stargazers_count", 0),
            "forks": repo.get("forks_count", 0),
            "language": repo.get("language", ""),
            "license": (repo.get("license") or {}).get("spdx_id", ""),
            "open_issues": repo.get("open_issues_count", 0),
            "default_branch": repo.get("default_branch", "main"),
            "branches": ", ".join(branch_names[:20]),
            "branch_count": len(branch_names),
            "tags": ", ".join(tag_names[:10]),
            "tag_count": len(tag_names),
            "created_at": repo.get("created_at", ""),
            "updated_at": repo.get("updated_at", ""),
            "version": 1,
        },
    }]


# Source file extensions to ingest, in priority order
_SOURCE_EXTENSIONS = (
    ".py", ".ts", ".tsx", ".js", ".jsx",
    ".go", ".rs", ".java", ".rb", ".cs",
    ".cpp", ".c", ".h", ".swift", ".kt",
)
_CONFIG_EXTENSIONS = (".json", ".yaml", ".yml", ".toml", ".env.example")
_DOC_EXTENSIONS = (".md", ".rst", ".txt")

_SKIP_PATH_PARTS = {
    "node_modules", "__pycache__", ".git", "venv", ".venv",
    "dist", "build", ".next", ".nuxt", "coverage", ".tox",
    "vendor", "third_party", "site-packages",
}

_EXT_TO_LANGUAGE = {
    ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".js": "JavaScript", ".jsx": "JavaScript", ".go": "Go",
    ".rs": "Rust", ".java": "Java", ".rb": "Ruby", ".cs": "C#",
    ".cpp": "C++", ".c": "C", ".h": "C/C++ Header",
    ".swift": "Swift", ".kt": "Kotlin",
    ".json": "JSON", ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML",
    ".md": "Markdown", ".rst": "reStructuredText", ".sql": "SQL",
    ".sh": "Shell", ".dockerfile": "Dockerfile",
}

_CHUNK_SIZE = 2500   # chars per CodeModule node
_MAX_FILE_SIZE = 80_000   # skip files larger than this (raw bytes)
_MAX_FILES = 60      # cap total files fetched


def _should_skip_path(path: str) -> bool:
    """Return True if any path component is a known noise directory."""
    parts = set(path.replace("\\", "/").split("/"))
    return bool(parts & _SKIP_PATH_PARTS)


def _file_priority(path: str) -> int:
    """Lower = higher priority. Tests and configs come after main source."""
    lower = path.lower()
    ext = "." + lower.rsplit(".", 1)[-1] if "." in lower else ""
    if ext not in _SOURCE_EXTENSIONS + _CONFIG_EXTENSIONS + _DOC_EXTENSIONS:
        return 99
    if any(p in lower for p in ("test", "spec", "mock", "fixture", "__test__", ".test.", ".spec.")):
        return 3  # tests after main source
    if ext in _CONFIG_EXTENSIONS:
        return 4
    if ext in _DOC_EXTENSIONS:
        return 5
    return 1  # main source files first


def scan_github_file_tree(owner_repo: str) -> List[Dict[str, str]]:
    """Fetch the full recursive file tree and return a prioritised list of source files.

    Returns list of dicts: {path, sha, size, language, ext}
    Capped at _MAX_FILES entries after priority sorting.
    """
    # Get default branch first
    repo_meta = _github_get(owner_repo, "")
    default_branch = (repo_meta or {}).get("default_branch", "main")

    data = _github_get(owner_repo, f"/git/trees/{default_branch}", {"recursive": "1"})
    if not data or "tree" not in data:
        logger.warning("scan_github_file_tree: no tree returned for %s", owner_repo)
        return []

    tree = data["tree"]
    if data.get("truncated"):
        logger.warning("scan_github_file_tree: tree truncated for %s (large repo)", owner_repo)

    files = []
    all_extensions = _SOURCE_EXTENSIONS + _CONFIG_EXTENSIONS + _DOC_EXTENSIONS
    for item in tree:
        if item.get("type") != "blob":
            continue
        path = item.get("path", "")
        if _should_skip_path(path):
            continue
        ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
        if ext not in all_extensions:
            continue
        size = item.get("size", 0)
        if size > _MAX_FILE_SIZE:
            logger.debug("scan_github_file_tree: skipping large file %s (%d bytes)", path, size)
            continue
        files.append({
            "path": path,
            "sha": item.get("sha", ""),
            "size": size,
            "language": _EXT_TO_LANGUAGE.get(ext, ext.lstrip(".").upper()),
            "ext": ext,
        })

    files.sort(key=lambda f: (_file_priority(f["path"]), f["path"]))
    logger.info(
        "scan_github_file_tree: %d eligible files in %s (capped at %d)",
        len(files), owner_repo, _MAX_FILES,
    )
    return files[:_MAX_FILES]


def scan_github_source_files(owner_repo: str, files: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """Fetch content for each file and produce CodeModule nodes.

    Large files are chunked into multiple nodes so each fits LLM context.
    """
    import base64

    nodes = []
    fetched = 0
    skipped = 0

    for f in files:
        path = f["path"]
        data = _github_get(owner_repo, f"/contents/{path}")
        if not data or not isinstance(data, dict):
            skipped += 1
            continue

        content_b64 = data.get("content") or ""
        encoding = data.get("encoding") or ""
        if encoding == "base64":
            try:
                text = base64.b64decode(content_b64).decode("utf-8", errors="replace")
            except Exception:
                skipped += 1
                continue
        else:
            text = content_b64 if isinstance(content_b64, str) else ""

        if not text or not text.strip():
            skipped += 1
            continue

        # Extract a one-line summary from docstring / leading comment
        summary = _extract_file_summary(text, f["ext"])

        # Chunk the file if it's large
        chunks = [text[i:i + _CHUNK_SIZE] for i in range(0, len(text), _CHUNK_SIZE)]
        total = len(chunks)

        for idx, chunk in enumerate(chunks):
            node_id = f"code:{_safe_id(path)}" if total == 1 else f"code:{_safe_id(path)}:{idx}"
            nodes.append({
                "id": node_id,
                "label": "CodeModule",
                "properties": {
                    "path": path,          # used by infer_edges DEPENDS_ON
                    "file_path": path,     # friendly alias
                    "language": f["language"],
                    "content": chunk,
                    "summary": summary if idx == 0 else f"{path} (chunk {idx + 1}/{total})",
                    "chunk_index": idx,
                    "chunk_total": total,
                    "file_size": f["size"],
                    "source": f"github:{path}",
                    "version": 1,
                },
            })

        fetched += 1

    logger.info(
        "scan_github_source_files: %d files → %d CodeModule nodes (%d skipped)",
        fetched, len(nodes), skipped,
    )
    return nodes


def _extract_file_summary(text: str, ext: str) -> str:
    """Extract a short one-line summary from the top of a source file."""
    if ext == ".py":
        m = re.search(r'"""(.*?)"""', text[:500], re.DOTALL)
        if m:
            return m.group(1).strip().split("\n")[0][:150]
        m = re.search(r"'''(.*?)'''", text[:500], re.DOTALL)
        if m:
            return m.group(1).strip().split("\n")[0][:150]
    elif ext in (".ts", ".tsx", ".js", ".jsx"):
        m = re.search(r'/\*\*(.*?)\*/', text[:500], re.DOTALL)
        if m:
            return re.sub(r'\s*\*\s*', ' ', m.group(1)).strip()[:150]
    elif ext == ".go":
        lines = text.split("\n")
        comments = []
        for line in lines[:20]:
            if line.strip().startswith("//"):
                comments.append(line.strip().lstrip("/").strip())
            elif comments:
                break
        if comments:
            return " ".join(comments)[:150]
    elif ext in (".md", ".rst"):
        for line in text.split("\n")[:10]:
            line = line.strip().lstrip("#").strip()
            if line:
                return line[:150]
    # Fallback: first non-import, non-blank line
    for line in text.split("\n")[:30]:
        s = line.strip()
        if s and not s.startswith(("import ", "from ", "#!", "//", "/*", "package ", "require", "use ", "mod ")):
            return s[:150]
    return ""


def quick_scan(github_url: str, github_token: str = None) -> List[Dict[str, Any]]:
    """Quick Scan — API-only, no clone.

    Fetches README, issues, PRs, contributors, repo metadata, and up to
    60 source files (chunked into CodeModule nodes). ~15-30s depending on
    repo size and rate limits.

    Args:
        github_url: GitHub URL like https://github.com/owner/repo
        github_token: Optional GitHub personal access token. Overrides GITHUB_TOKEN
            env var. Required for private repos; increases rate limit to 5000 req/hour.

    Returns:
        List of SDLC node dicts ready for graph ingestion.
    """
    owner_repo = _parse_owner_repo(github_url)
    if not owner_repo:
        logger.warning("quick_scan: cannot parse owner/repo from %s", github_url)
        return []

    logger.info("quick_scan: scanning %s via GitHub API (no clone, token: %s)",
                owner_repo, "provided" if github_token else "env/none")

    with _token_context(github_token):
        nodes = []
        nodes += scan_github_readme(owner_repo)
        nodes += scan_github_issues_api(owner_repo)
        nodes += scan_github_pulls(owner_repo)
        nodes += scan_github_contributors(owner_repo)
        nodes += scan_github_repo_meta(owner_repo)

        # Source files — fetch tree then content
        file_list = scan_github_file_tree(owner_repo)
        if file_list:
            nodes += scan_github_source_files(owner_repo, file_list)

    logger.info("quick_scan: %d nodes from %s", len(nodes), owner_repo)
    return nodes
