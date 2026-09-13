"""
Workspace Tools — file operations, git, shell commands.

8 tools: ws_write_file, ws_read_file, ws_list_files, ws_git_status,
         ws_commit, ws_push, ws_create_branch, ws_run_command
"""

from __future__ import annotations

import logging

from .registry import ToolContext, ToolParam, tool

logger = logging.getLogger(__name__)


def _require_workspace(ctx: ToolContext) -> str:
    if not ctx.workspace:
        return "Error: No workspace configured."
    return ""


@tool("ws_write_file", "workspace",
      "Write a file to the project workspace. Use for creating source code, configs, docs.",
      params=[
          ToolParam("filepath", "string", "Relative file path (e.g. 'src/App.jsx')"),
          ToolParam("content", "string", "Full file content"),
      ],
      requires_workspace=True)
def _ws_write_file(ctx: ToolContext, filepath: str, content: str) -> str:
    err = _require_workspace(ctx)
    if err:
        return err
    return ctx.workspace.write_file(filepath, content)


@tool("ws_read_file", "workspace",
      "Read a file from the project workspace.",
      params=[ToolParam("filepath", "string", "Relative file path to read")],
      requires_workspace=True)
def _ws_read_file(ctx: ToolContext, filepath: str) -> str:
    err = _require_workspace(ctx)
    if err:
        return err
    return ctx.workspace.read_file(filepath)


@tool("ws_list_files", "workspace",
      "List all files in the project workspace.",
      params=[ToolParam("path", "string", "Subdirectory to list (default: root)", required=False, default=".")],
      requires_workspace=True)
def _ws_list_files(ctx: ToolContext, path: str = ".") -> str:
    err = _require_workspace(ctx)
    if err:
        return err
    files = ctx.workspace.list_files(path)
    if not files:
        return "No files in workspace."
    return f"Files ({len(files)}):\n" + "\n".join(f"  {f}" for f in files)


@tool("ws_git_status", "workspace",
      "Get workspace status — type, branch, file count, pending changes.",
      requires_workspace=True)
def _ws_git_status(ctx: ToolContext) -> str:
    err = _require_workspace(ctx)
    if err:
        return err
    status = ctx.workspace.get_status()
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


@tool("ws_commit", "workspace",
      "Commit all pending changes in the workspace.",
      params=[ToolParam("message", "string", "Commit message")],
      requires_workspace=True)
def _ws_commit(ctx: ToolContext, message: str) -> str:
    err = _require_workspace(ctx)
    if err:
        return err
    return ctx.workspace.commit(message)


@tool("ws_push", "workspace",
      "Push committed changes to the remote repository.",
      params=[ToolParam("branch", "string", "Branch to push (default: current)", required=False, default="")],
      requires_workspace=True)
def _ws_push(ctx: ToolContext, branch: str = "") -> str:
    err = _require_workspace(ctx)
    if err:
        return err
    return ctx.workspace.push(branch or None)


@tool("ws_create_branch", "workspace",
      "Create and switch to a new git branch.",
      params=[ToolParam("branch", "string", "Branch name (e.g. 'feat/homepage')")],
      requires_workspace=True)
def _ws_create_branch(ctx: ToolContext, branch: str) -> str:
    err = _require_workspace(ctx)
    if err:
        return err
    return ctx.workspace.create_branch(branch)


@tool("ws_run_command", "workspace",
      "Run a shell command in the workspace (e.g. 'npm install', 'npm test').",
      params=[ToolParam("command", "string", "Shell command to execute")],
      requires_workspace=True)
def _ws_run_command(ctx: ToolContext, command: str) -> str:
    err = _require_workspace(ctx)
    if err:
        return err
    return ctx.workspace.run_command(command)
