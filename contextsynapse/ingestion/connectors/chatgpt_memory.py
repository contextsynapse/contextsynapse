"""
ChatGPT Memory Connector — Pull conversations and memories from OpenAI.

Supports two modes:
  1. **Conversations** — via OpenAI API (list + retrieve conversations)
  2. **Export file** — parse a ChatGPT data export (conversations.json)

Creates graph nodes: Conversation, Message, Memory, Topic
Creates edges: HAS_MESSAGE, MENTIONS_TOPIC, REMEMBERS
"""

from __future__ import annotations

import json
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .base import ConnectorIngestor, ConnectorGraphResult

logger = logging.getLogger(__name__)


class ChatGPTMemoryIngestor(ConnectorIngestor):
    """Pull ChatGPT conversations and memories into the graph."""

    connector_type = "chatgpt"

    def __init__(self, config: Dict[str, Any], credentials: Dict[str, Any]):
        super().__init__(config, credentials)
        self.api_key = credentials.get("api_key", "")
        self.export_file = config.get("export_file", "")  # path to conversations.json
        self.max_conversations = config.get("max_conversations", 50)
        self.include_system = config.get("include_system_messages", False)

    def test_connection(self) -> Dict[str, Any]:
        if self.export_file:
            import os
            if os.path.exists(self.export_file):
                return {"success": True, "message": f"Export file found: {self.export_file}"}
            return {"success": False, "message": f"Export file not found: {self.export_file}"}

        if not self.api_key:
            return {"success": False, "message": "OpenAI API key required"}
        try:
            import httpx
            r = httpx.get("https://api.openai.com/v1/models",
                          headers={"Authorization": f"Bearer {self.api_key}"}, timeout=10)
            if r.status_code == 200:
                return {"success": True, "message": "Connected to OpenAI API"}
            return {"success": False, "message": f"API error: {r.status_code}"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def discover_metadata(self, context_id: str) -> ConnectorGraphResult:
        result = ConnectorGraphResult()
        conn_node, conn_edge = self._make_connector_node(
            context_id, name="ChatGPT Memory",
            extra_props={"source": "chatgpt", "mode": "export" if self.export_file else "api"},
        )
        result.nodes.append(conn_node)
        result.edges.append(conn_edge)
        return result

    def pull_data(self, context_id: str, options: Optional[Dict[str, Any]] = None) -> ConnectorGraphResult:
        if self.export_file:
            return self._pull_from_export(context_id)
        return self._pull_from_api(context_id)

    # ------------------------------------------------------------------
    # Export file parsing (conversations.json from ChatGPT data export)
    # ------------------------------------------------------------------

    def _pull_from_export(self, context_id: str) -> ConnectorGraphResult:
        result = ConnectorGraphResult()
        try:
            with open(self.export_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            result.stats = {"error": str(e)}
            return result

        conversations = data if isinstance(data, list) else data.get("conversations", data.get("items", []))
        conversations = conversations[:self.max_conversations]

        for conv in conversations:
            self._process_conversation(conv, context_id, result)

        result.stats = {
            "conversations": len(conversations),
            "nodes": len(result.nodes),
            "edges": len(result.edges),
            "data_items": len(result.data_items),
        }
        return result

    # ------------------------------------------------------------------
    # API-based pulling
    # ------------------------------------------------------------------

    def _pull_from_api(self, context_id: str) -> ConnectorGraphResult:
        result = ConnectorGraphResult()
        # OpenAI doesn't have a public conversations API yet,
        # but we can pull from the chat completions history if available
        result.stats = {
            "message": "OpenAI API conversation export not yet available. Use the data export file instead.",
            "hint": "Go to Settings → Data controls → Export data in ChatGPT",
        }
        return result

    # ------------------------------------------------------------------
    # Conversation → Graph nodes
    # ------------------------------------------------------------------

    def _process_conversation(self, conv: Dict, context_id: str, result: ConnectorGraphResult):
        title = conv.get("title", "Untitled conversation")
        conv_id = conv.get("id", self._uid())
        create_time = conv.get("create_time")
        update_time = conv.get("update_time")

        # Conversation node
        conv_node_id = f"chatgpt_conv_{hashlib.sha256(conv_id.encode()).hexdigest()[:12]}"
        result.nodes.append(self._node("Conversation", {
            "name": title,
            "source": "chatgpt",
            "conversation_id": conv_id,
            "created_at": _unix_to_iso(create_time),
            "updated_at": _unix_to_iso(update_time),
        }, node_id=conv_node_id))

        result.edges.append(self._edge(context_id, conv_node_id, "HAS_CONVERSATION"))

        # Extract messages from the mapping structure
        mapping = conv.get("mapping", {})
        messages = []
        for node_id, node_data in mapping.items():
            msg = (node_data or {}).get("message")
            if not msg:
                continue
            role = msg.get("author", {}).get("role", "unknown")
            if role == "system" and not self.include_system:
                continue
            content_parts = msg.get("content", {}).get("parts", [])
            text = "\n".join(str(p) for p in content_parts if isinstance(p, str))
            if not text.strip():
                continue
            messages.append({
                "role": role,
                "text": text,
                "create_time": msg.get("create_time"),
                "msg_id": msg.get("id", node_id),
            })

        # Sort by time
        messages.sort(key=lambda m: m.get("create_time") or 0)

        # Create message nodes and collect full text for extraction
        full_text_parts = [f"# {title}\n"]
        prev_msg_id = None
        for i, msg in enumerate(messages):
            msg_node_id = f"chatgpt_msg_{hashlib.sha256(msg['msg_id'].encode()).hexdigest()[:12]}"
            result.nodes.append(self._node("Message", {
                "name": f"{msg['role']}: {msg['text'][:60]}",
                "role": msg["role"],
                "content": msg["text"],
                "message_index": i,
                "created_at": _unix_to_iso(msg.get("create_time")),
                "source": "chatgpt",
            }, node_id=msg_node_id))
            result.edges.append(self._edge(conv_node_id, msg_node_id, "HAS_MESSAGE",
                                            {"index": i}))
            if prev_msg_id:
                result.edges.append(self._edge(prev_msg_id, msg_node_id, "FOLLOWED_BY"))
            prev_msg_id = msg_node_id

            full_text_parts.append(f"**{msg['role']}**: {msg['text']}\n")

        # Add full conversation as a data_item for extraction pipeline
        if messages:
            result.data_items.append({
                "text": "\n".join(full_text_parts),
                "metadata": {
                    "title": title,
                    "source": "chatgpt",
                    "conversation_id": conv_id,
                    "message_count": len(messages),
                },
            })


class ChatGPTMemoryExtractor:
    """Extract structured memories from ChatGPT's memory.json export."""

    @staticmethod
    def extract_memories(memory_file: str, context_id: str) -> ConnectorGraphResult:
        """Parse ChatGPT's memory.json and create Memory nodes."""
        result = ConnectorGraphResult()
        try:
            with open(memory_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            result.stats = {"error": str(e)}
            return result

        memories = data if isinstance(data, list) else data.get("memories", data.get("items", []))
        for mem in memories:
            content = mem.get("content", mem.get("text", mem.get("value", "")))
            if not content:
                continue
            mem_id = f"chatgpt_mem_{hashlib.sha256(content.encode()).hexdigest()[:12]}"
            result.nodes.append({
                "id": mem_id,
                "label": "Memory",
                "properties": {
                    "name": content[:80],
                    "content": content,
                    "source": "chatgpt",
                    "memory_type": mem.get("type", "user_preference"),
                    "created_at": mem.get("created_at", mem.get("timestamp", "")),
                },
            })
            result.edges.append({
                "id": str(hashlib.sha256(f"mem_edge_{mem_id}".encode()).hexdigest()[:16]),
                "source": context_id,
                "target": mem_id,
                "label": "REMEMBERS",
                "properties": {"source": "chatgpt"},
            })

            # Also as data_item for extraction
            result.data_items.append({
                "text": f"ChatGPT Memory: {content}",
                "metadata": {"source": "chatgpt_memory", "memory_id": mem_id},
            })

        result.stats = {"memories": len(result.nodes)}
        return result


def _unix_to_iso(ts) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except Exception:
        return str(ts)
