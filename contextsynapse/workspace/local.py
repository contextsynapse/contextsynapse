"""
Local filesystem workspace — simplest backend.
Files are written to a directory on disk.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from .base import Workspace

logger = logging.getLogger(__name__)


class LocalWorkspace(Workspace):
    """Workspace backed by a local directory."""

    def __init__(self, base_path: str = "generated"):
        self.base_path = os.path.abspath(base_path)
        os.makedirs(self.base_path, exist_ok=True)

    def _safe_path(self, filepath: str) -> str:
        full = os.path.normpath(os.path.join(self.base_path, filepath))
        if not full.startswith(self.base_path):
            raise ValueError("Path traversal not allowed")
        return full

    def write_file(self, filepath: str, content: str) -> str:
        full_path = self._safe_path(filepath)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        rel = os.path.relpath(full_path, self.base_path)
        return f"Written: {rel} ({len(content)} chars)"

    def read_file(self, filepath: str) -> str:
        full_path = self._safe_path(filepath)
        if not os.path.exists(full_path):
            return f"File not found: {filepath}"
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
        if len(content) > 10000:
            return content[:10000] + f"\n\n... (truncated, {len(content)} total chars)"
        return content

    def list_files(self, path: str = ".") -> List[str]:
        target = self._safe_path(path)
        if not os.path.isdir(target):
            return []
        files = []
        for root, dirs, filenames in os.walk(target):
            dirs[:] = [d for d in dirs if d not in ("node_modules", ".git", "__pycache__", ".venv")]
            for fn in filenames:
                rel = os.path.relpath(os.path.join(root, fn), self.base_path)
                files.append(rel.replace("\\", "/"))
        return sorted(files)

    def delete_file(self, filepath: str) -> str:
        full_path = self._safe_path(filepath)
        if not os.path.exists(full_path):
            return f"File not found: {filepath}"
        os.remove(full_path)
        return f"Deleted: {filepath}"

    def get_status(self) -> Dict[str, Any]:
        files = self.list_files()
        return {
            "type": "local",
            "path": self.base_path,
            "file_count": len(files),
        }
