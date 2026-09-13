"""
Workspace — where agents produce artifacts.

A workspace gives agents a sandboxed directory to write code, create files,
run commands, and push to Git. Each session/project gets its own workspace.

Backends:
    - LocalWorkspace: filesystem under generated/<project>/
    - GitWorkspace: local git repo with branch/commit/push
    - GitHubWorkspace: remote GitHub repo via API

Usage:
    ws = GitWorkspace.from_config({
        "type": "git",
        "repo_url": "https://github.com/user/my-app.git",
        "branch": "feat/abc-website",
    })
    ws.write_file("src/App.jsx", content)
    ws.commit("Add homepage component")
    ws.push()
"""

from .base import Workspace
from .local import LocalWorkspace
from .git import GitWorkspace

__all__ = ["Workspace", "LocalWorkspace", "GitWorkspace"]
