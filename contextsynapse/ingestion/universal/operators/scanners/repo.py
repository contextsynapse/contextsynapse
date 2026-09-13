"""Repo file scanners — README, source, tests, docs, API routes."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

from .helpers import _safe_id, _safe_read, _find_src_dirs, _extract_summary, _SKIP_DIRS, _CODE_EXTENSIONS


def scan_readme(repo_path: Path) -> List[Dict[str, Any]]:
    nodes = []
    for name in ("README.md", "README.rst", "readme.md"):
        readme = repo_path / name
        if readme.exists():
            text = readme.read_text(encoding="utf-8", errors="replace")
            break
    else:
        return nodes

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


def scan_source_modules(repo_path: Path) -> List[Dict[str, Any]]:
    nodes = []
    for src_dir in _find_src_dirs(repo_path):
        for fpath in sorted(src_dir.rglob("*")):
            if not fpath.is_file() or fpath.suffix not in _CODE_EXTENSIONS:
                continue
            if any(p in fpath.parts for p in _SKIP_DIRS):
                continue
            if fpath.name.startswith("_") and fpath.name != "__init__.py":
                continue
            rel = fpath.relative_to(repo_path)
            text = _safe_read(fpath, limit=2000)
            if not text or len(text) < 50:
                continue
            summary = _extract_summary(text, fpath.suffix) or f"Module at {rel}"
            nodes.append({
                "id": f"code:{_safe_id(str(rel))}",
                "label": "CodeModule",
                "properties": {"summary": summary[:400], "path": str(rel), "version": 1},
            })
    return nodes


def scan_tests(repo_path: Path) -> List[Dict[str, Any]]:
    nodes = []
    test_dirs = []
    for d in ("tests", "test", "spec", "specs", "__tests__"):
        td = repo_path / d
        if td.is_dir():
            test_dirs.append(td)

    seen = set()
    for td in test_dirs:
        for fpath in sorted(td.rglob("*")):
            if not fpath.is_file() or fpath.suffix not in _CODE_EXTENSIONS:
                continue
            if fpath in seen:
                continue
            seen.add(fpath)
            rel = fpath.relative_to(repo_path)
            text = _safe_read(fpath, limit=1500)
            if not text:
                continue
            test_funcs = re.findall(r'(?:def test_|it\(|test\(|func Test)', text)
            if not test_funcs:
                continue
            summary = _extract_summary(text, fpath.suffix)
            nodes.append({
                "id": f"tc:{_safe_id(str(rel))}",
                "label": "TestCase",
                "properties": {
                    "what": summary[:200] if summary else f"Tests in {rel}",
                    "how": f"{len(test_funcs)} test function(s) in {rel}",
                    "version": 1,
                },
            })
    return nodes


def scan_docs(repo_path: Path) -> List[Dict[str, Any]]:
    nodes = []
    for d in ("docs", "doc", "documentation", "adr", "adrs", "decisions"):
        dd = repo_path / d
        if not dd.is_dir():
            continue
        for fpath in sorted(dd.rglob("*.md")):
            if any(p in fpath.parts for p in _SKIP_DIRS):
                continue
            rel = fpath.relative_to(repo_path)
            text = _safe_read(fpath, limit=2000)
            if not text or len(text) < 50:
                continue
            paragraphs = text.split("\n\n")
            title = paragraphs[0].strip().lstrip("#").strip()
            body = paragraphs[1].strip() if len(paragraphs) > 1 else ""
            nodes.append({
                "id": f"arch:{_safe_id(str(rel))}",
                "label": "ArchDecision",
                "properties": {"decision": title[:200], "rationale": body[:300], "source": str(rel), "version": 1},
            })
    return nodes


def scan_api_routes(repo_path: Path) -> List[Dict[str, Any]]:
    nodes = []
    patterns = [
        r'@\w+\.(get|post|put|delete|patch)\(["\']([^"\']+)',
        r'router\.(get|post|put|delete|patch)\(["\']([^"\']+)',
        r'@(Get|Post|Put|Delete|Patch)Mapping\(["\']([^"\']+)',
    ]
    seen_ids = set()
    for fpath in repo_path.rglob("*"):
        if not fpath.is_file() or fpath.suffix not in (".py", ".ts", ".js", ".java"):
            continue
        if any(p in fpath.parts for p in _SKIP_DIRS):
            continue
        text = _safe_read(fpath, limit=5000)
        if not text:
            continue
        rel = fpath.relative_to(repo_path)
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                method = match.group(1).upper()
                path = match.group(2)
                node_id = f"api:{_safe_id(f'{method}-{path}')}"
                if node_id in seen_ids:
                    continue
                seen_ids.add(node_id)
                nodes.append({
                    "id": node_id,
                    "label": "APIContract",
                    "properties": {"method": method, "path": path, "source": str(rel), "version": 1},
                })
    return nodes
