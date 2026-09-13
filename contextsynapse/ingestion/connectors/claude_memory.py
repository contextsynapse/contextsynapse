"""
Claude Memory Connector — Pull conversations and project context from Anthropic.

Supports:
  1. **Claude.ai export** — parse exported conversation JSON
  2. **Claude Code projects** — read CLAUDE.md + memory files from project dirs
  3. **API conversations** — pull via Anthropic API (message history)

Creates graph nodes: Conversation, Message, Memory, ProjectContext
Creates edges: HAS_MESSAGE, REMEMBERS, HAS_CONTEXT
"""

from __future__ import annotations

import json
import hashlib
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import ConnectorIngestor, ConnectorGraphResult

logger = logging.getLogger(__name__)


class ClaudeMemoryIngestor(ConnectorIngestor):
    """Pull Claude conversations, memories, and project context into the graph."""

    connector_type = "claude"

    def __init__(self, config: Dict[str, Any], credentials: Dict[str, Any]):
        super().__init__(config, credentials)
        self.api_key = credentials.get("api_key", "")
        self.export_file = config.get("export_file", "")
        self.project_dir = config.get("project_dir", "")  # Claude Code project
        self.max_conversations = config.get("max_conversations", 50)

    def test_connection(self) -> Dict[str, Any]:
        if self.export_file:
            if os.path.exists(self.export_file):
                return {"success": True, "message": f"Export file found: {self.export_file}"}
            return {"success": False, "message": f"Export file not found: {self.export_file}"}

        if self.project_dir:
            claude_md = Path(self.project_dir) / "CLAUDE.md"
            if claude_md.exists():
                return {"success": True, "message": f"Claude project found at {self.project_dir}"}
            return {"success": False, "message": f"No CLAUDE.md at {self.project_dir}"}

        if not self.api_key:
            return {"success": False, "message": "Anthropic API key required"}
        try:
            import httpx
            r = httpx.get("https://api.anthropic.com/v1/models",
                          headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
                          timeout=10)
            if r.status_code == 200:
                return {"success": True, "message": "Connected to Anthropic API"}
            return {"success": False, "message": f"API error: {r.status_code}"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def discover_metadata(self, context_id: str) -> ConnectorGraphResult:
        result = ConnectorGraphResult()
        mode = "project" if self.project_dir else ("export" if self.export_file else "api")
        conn_node, conn_edge = self._make_connector_node(
            context_id, name="Claude Memory",
            extra_props={"source": "claude", "mode": mode},
        )
        result.nodes.append(conn_node)
        result.edges.append(conn_edge)
        return result

    def pull_data(self, context_id: str, options: Optional[Dict[str, Any]] = None) -> ConnectorGraphResult:
        if self.project_dir:
            return self._pull_from_project(context_id)
        if self.export_file:
            return self._pull_from_export(context_id)
        return self._pull_from_api(context_id)

    # ------------------------------------------------------------------
    # Claude Code project — CLAUDE.md + memory files
    # ------------------------------------------------------------------

    def _pull_from_project(self, context_id: str) -> ConnectorGraphResult:
        result = ConnectorGraphResult()
        project_path = Path(self.project_dir)

        # Read CLAUDE.md (project instructions)
        claude_md = project_path / "CLAUDE.md"
        if claude_md.exists():
            content = claude_md.read_text(encoding="utf-8")
            node_id = f"claude_project_{hashlib.sha256(str(project_path).encode()).hexdigest()[:12]}"
            result.nodes.append(self._node("ProjectContext", {
                "name": f"CLAUDE.md — {project_path.name}",
                "content": content,
                "source": "claude_code",
                "file_path": str(claude_md),
                "project": project_path.name,
            }, node_id=node_id))
            result.edges.append(self._edge(context_id, node_id, "HAS_CONTEXT"))
            result.data_items.append({
                "text": f"# Claude Code Project Instructions\n\n{content}",
                "metadata": {"source": "claude_code", "file": "CLAUDE.md"},
            })

        # Read memory files (from .claude/projects/*/memory/)
        home = Path.home()
        memory_dirs = [
            home / ".claude" / "projects" / project_path.name / "memory",
            home / ".claude" / "memory",
        ]
        # Also check for project-specific memory based on path hash
        for d in home.glob(".claude/projects/*/memory"):
            if d not in memory_dirs:
                memory_dirs.append(d)

        memories_found = 0
        for mem_dir in memory_dirs:
            if not mem_dir.exists():
                continue
            # Read MEMORY.md index
            memory_index = mem_dir / "MEMORY.md"
            if memory_index.exists():
                idx_content = memory_index.read_text(encoding="utf-8")
                idx_id = f"claude_memidx_{hashlib.sha256(str(mem_dir).encode()).hexdigest()[:12]}"
                result.nodes.append(self._node("Memory", {
                    "name": f"Memory Index — {mem_dir.parent.name}",
                    "content": idx_content,
                    "source": "claude_code",
                    "memory_type": "index",
                }, node_id=idx_id))
                result.edges.append(self._edge(context_id, idx_id, "REMEMBERS"))

            # Read individual memory files
            for f in mem_dir.glob("*.md"):
                if f.name == "MEMORY.md":
                    continue
                content = f.read_text(encoding="utf-8")
                if not content.strip():
                    continue
                mem_id = f"claude_mem_{hashlib.sha256(content.encode()).hexdigest()[:12]}"

                # Parse frontmatter
                mem_type = "note"
                mem_name = f.stem
                if content.startswith("---"):
                    try:
                        _, fm, body = content.split("---", 2)
                        for line in fm.strip().split("\n"):
                            if line.startswith("type:"):
                                mem_type = line.split(":", 1)[1].strip()
                            elif line.startswith("name:"):
                                mem_name = line.split(":", 1)[1].strip()
                        content = body.strip()
                    except ValueError:
                        pass

                result.nodes.append(self._node("Memory", {
                    "name": mem_name,
                    "content": content,
                    "source": "claude_code",
                    "memory_type": mem_type,
                    "file_path": str(f),
                }, node_id=mem_id))
                result.edges.append(self._edge(context_id, mem_id, "REMEMBERS"))
                result.data_items.append({
                    "text": f"Claude Memory ({mem_type}): {mem_name}\n\n{content}",
                    "metadata": {"source": "claude_memory", "type": mem_type},
                })
                memories_found += 1

        result.stats = {
            "memories": memories_found,
            "nodes": len(result.nodes),
            "edges": len(result.edges),
        }
        return result

    # ------------------------------------------------------------------
    # Export file parsing
    # ------------------------------------------------------------------

    def _pull_from_export(self, context_id: str) -> ConnectorGraphResult:
        result = ConnectorGraphResult()
        try:
            with open(self.export_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            result.stats = {"error": str(e)}
            return result

        conversations = data if isinstance(data, list) else data.get("conversations", [])
        conversations = conversations[:self.max_conversations]

        for conv in conversations:
            self._process_conversation(conv, context_id, result)

        result.stats = {
            "conversations": len(conversations),
            "nodes": len(result.nodes),
            "data_items": len(result.data_items),
        }
        return result

    def _process_conversation(self, conv: Dict, context_id: str, result: ConnectorGraphResult):
        title = conv.get("name", conv.get("title", "Untitled"))
        conv_id = conv.get("uuid", conv.get("id", self._uid()))
        conv_node_id = f"claude_conv_{hashlib.sha256(conv_id.encode()).hexdigest()[:12]}"

        result.nodes.append(self._node("Conversation", {
            "name": title,
            "source": "claude",
            "conversation_id": conv_id,
            "created_at": conv.get("created_at", ""),
            "updated_at": conv.get("updated_at", ""),
            "model": conv.get("model", ""),
        }, node_id=conv_node_id))
        result.edges.append(self._edge(context_id, conv_node_id, "HAS_CONVERSATION"))

        # Messages
        messages = conv.get("chat_messages", conv.get("messages", []))
        full_text_parts = [f"# {title}\n"]
        prev_id = None
        for i, msg in enumerate(messages):
            role = msg.get("sender", msg.get("role", "unknown"))
            text_parts = msg.get("text", "")
            if isinstance(text_parts, list):
                text_parts = "\n".join(str(p) for p in text_parts)
            # Handle content blocks
            if not text_parts and "content" in msg:
                content = msg["content"]
                if isinstance(content, list):
                    text_parts = "\n".join(
                        b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
                    )
                elif isinstance(content, str):
                    text_parts = content
            if not text_parts or not text_parts.strip():
                continue

            msg_id = f"claude_msg_{hashlib.sha256(f'{conv_id}_{i}'.encode()).hexdigest()[:12]}"
            result.nodes.append(self._node("Message", {
                "name": f"{role}: {text_parts[:60]}",
                "role": role,
                "content": text_parts,
                "message_index": i,
                "source": "claude",
            }, node_id=msg_id))
            result.edges.append(self._edge(conv_node_id, msg_id, "HAS_MESSAGE", {"index": i}))
            if prev_id:
                result.edges.append(self._edge(prev_id, msg_id, "FOLLOWED_BY"))
            prev_id = msg_id
            full_text_parts.append(f"**{role}**: {text_parts}\n")

        if len(full_text_parts) > 1:
            result.data_items.append({
                "text": "\n".join(full_text_parts),
                "metadata": {"title": title, "source": "claude", "conversation_id": conv_id},
            })

    # ------------------------------------------------------------------
    # API-based pulling
    # ------------------------------------------------------------------

    def _pull_from_api(self, context_id: str) -> ConnectorGraphResult:
        result = ConnectorGraphResult()
        result.stats = {
            "message": "Anthropic API does not expose conversation history. Use Claude.ai export or Claude Code project mode.",
            "hint": "Export from claude.ai Settings, or point project_dir to a Claude Code project.",
        }
        return result
