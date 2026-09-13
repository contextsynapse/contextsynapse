"""
AIContextDB OpenAI / Codex Adapter
===================================
Provides OpenAI function-calling tool schemas and a dispatcher so any
OpenAI-powered agent (Codex, GPT-4, Assistants API) can read/write
the same shared graph that Claude accesses via MCP.

Requirements:
    pip install openai  # optional — only needed for the bridge runner
"""

from .tools import (
    configure,
    create_openai_tools,
    dispatch_tool_call,
    init_project,
)

__all__ = [
    "configure",
    "create_openai_tools",
    "dispatch_tool_call",
    "init_project",
]
