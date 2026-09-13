"""
AIContextDB MCP Server
==================
Exposes AIContextDB as an MCP (Model Context Protocol) server so that
Claude Code, Copilot, or any MCP-compatible agent can use the shared
graph brain as a tool.
"""

from .server import create_mcp_server

__all__ = ["create_mcp_server"]
