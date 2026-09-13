"""
Workspace tool schemas and dispatch for OpenAI function-calling.

These tools let agents interact with their workspace (write code, commit, push, run tests).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from .base import Workspace

logger = logging.getLogger(__name__)

# Module-level workspace (set by the pipeline before each agent run)
_workspace: Optional[Workspace] = None


def set_workspace(ws: Workspace) -> None:
    global _workspace
    _workspace = ws


def _ws_write_file(filepath: str, content: str) -> str:
    if not _workspace:
        return "Error: No workspace configured."
    return _workspace.write_file(filepath, content)


def _ws_read_file(filepath: str) -> str:
    if not _workspace:
        return "Error: No workspace configured."
    return _workspace.read_file(filepath)


def _ws_list_files(path: str = ".") -> str:
    if not _workspace:
        return "Error: No workspace configured."
    files = _workspace.list_files(path)
    if not files:
        return "No files in workspace."
    return f"Files ({len(files)}):\n" + "\n".join(f"  {f}" for f in files)


def _ws_commit(message: str) -> str:
    if not _workspace:
        return "Error: No workspace configured."
    return _workspace.commit(message)


def _ws_push(branch: str = "") -> str:
    if not _workspace:
        return "Error: No workspace configured."
    return _workspace.push(branch or None)


def _ws_create_branch(branch: str) -> str:
    if not _workspace:
        return "Error: No workspace configured."
    return _workspace.create_branch(branch)


def _ws_git_status() -> str:
    if not _workspace:
        return "Error: No workspace configured."
    status = _workspace.get_status()
    lines = [f"Workspace: {status.get('type', 'unknown')}"]
    if status.get("path"):
        lines.append(f"Path: {status['path']}")
    if status.get("branch"):
        lines.append(f"Branch: {status['branch']}")
    if status.get("repo_url"):
        lines.append(f"Remote: {status['repo_url']}")
    if status.get("git_status"):
        lines.append(f"Changes:\n{status['git_status']}")
    lines.append(f"Files: {status.get('file_count', '?')}")
    return "\n".join(lines)


def _ws_run_command(command: str) -> str:
    if not _workspace:
        return "Error: No workspace configured."
    return _workspace.run_command(command)


# ── Tool schemas ────────────────────────────────────────────────────

WORKSPACE_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "ws_write_file",
            "description": "Write a file to the project workspace. Use for creating source code, configs, docs, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Relative file path (e.g. 'src/App.jsx', 'README.md')"},
                    "content": {"type": "string", "description": "Full file content"},
                },
                "required": ["filepath", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ws_read_file",
            "description": "Read a file from the project workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Relative file path to read"},
                },
                "required": ["filepath"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ws_list_files",
            "description": "List all files in the project workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Subdirectory to list (default: root)", "default": "."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ws_git_status",
            "description": "Get workspace status — type, branch, file count, pending changes.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ws_commit",
            "description": "Commit all pending changes in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Commit message describing the changes"},
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ws_push",
            "description": "Push committed changes to the remote repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "branch": {"type": "string", "description": "Branch to push (default: current branch)", "default": ""},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ws_create_branch",
            "description": "Create and switch to a new git branch for your work.",
            "parameters": {
                "type": "object",
                "properties": {
                    "branch": {"type": "string", "description": "Branch name (e.g. 'feat/homepage', 'fix/auth-bug')"},
                },
                "required": ["branch"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ws_run_command",
            "description": "Run a shell command in the workspace (e.g. 'npm install', 'npm test', 'python -m pytest').",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                },
                "required": ["command"],
            },
        },
    },
]


# ── Dispatch ────────────────────────────────────────────────────────

_WORKSPACE_DISPATCH = {
    "ws_write_file": _ws_write_file,
    "ws_read_file": _ws_read_file,
    "ws_list_files": _ws_list_files,
    "ws_git_status": _ws_git_status,
    "ws_commit": _ws_commit,
    "ws_push": _ws_push,
    "ws_create_branch": _ws_create_branch,
    "ws_run_command": _ws_run_command,
}


def dispatch_workspace_tool(function_name: str, arguments: str) -> str:
    """Dispatch a workspace tool call."""
    handler = _WORKSPACE_DISPATCH.get(function_name)
    if not handler:
        return f"Error: Unknown workspace tool '{function_name}'"
    try:
        args = json.loads(arguments) if arguments else {}
        return handler(**args)
    except Exception as e:
        return f"Error executing {function_name}: {e}"
