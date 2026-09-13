"""
Chat Export Extractor

Extracts conversations from AI chat history exports (ChatGPT, Claude, Gemini).
Handles both ZIP files (containing conversations.json) and raw JSON files.

Each conversation becomes a "page" in the ExtractionResult, following the
same interface as PDF/DOCX/Excel extractors so the rest of the pipeline
(chunk → extract → link → embed → index → CU) runs unchanged.
"""

import json
import logging
import zipfile
from pathlib import Path
from typing import Any, Dict, List

from ..registry import BaseExtractor, ExtractionResult

logger = logging.getLogger(__name__)


class ChatExportExtractor(BaseExtractor):
    """Extract conversations from ChatGPT/Claude/Gemini export files."""

    def __init__(self, config):
        super().__init__(config)
        self.extractor_version = "chat_export_v1"
        self.max_conversations = 200

    def supports_format(self, file_path: str) -> bool:
        ext = Path(file_path).suffix.lower()
        if ext == ".zip":
            try:
                with zipfile.ZipFile(file_path) as zf:
                    return any("conversations" in n.lower() for n in zf.namelist())
            except Exception:
                return False
        if ext == ".json":
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                items = data if isinstance(data, list) else data.get("conversations", [])
                return len(items) > 0 and isinstance(items[0], dict)
            except Exception:
                return False
        return False

    def extract(self, file_path: str) -> ExtractionResult:
        """Extract conversations into pages (one page per conversation).

        Each page contains the full conversation text formatted as:
            # Conversation Title
            **user**: message text
            **assistant**: response text
            ...

        The rest of the pipeline treats each page like a document section.
        """
        result = ExtractionResult(extractor_version=self.extractor_version)

        try:
            conversations = self._load_conversations(file_path)
            if not conversations:
                result.errors.append("No conversations found in file")
                return result

            # Detect source
            sample = conversations[0]
            if "mapping" in sample:
                source = "chatgpt"
            elif "chat_messages" in sample:
                source = "claude"
            else:
                source = "chatgpt"

            result.metadata = {
                "title": f"{source.title()} Chat Export",
                "file_type": "chat_export",
                "source": source,
                "conversation_count": len(conversations),
            }

            # Convert each conversation into a page
            for i, conv in enumerate(conversations[:self.max_conversations]):
                page_text = self._conversation_to_text(conv, source)
                if not page_text.strip():
                    continue

                title = conv.get("title", conv.get("name", f"Conversation {i+1}"))
                result.pages.append({
                    "page_no": i + 1,
                    "text": page_text,
                    "content": page_text,
                    "metadata": {
                        "title": title,
                        "source": source,
                        "conversation_id": conv.get("id", conv.get("uuid", "")),
                    },
                })

            logger.info("[CHAT_EXTRACTOR] Extracted %d conversations from %s export",
                        len(result.pages), source)

        except Exception as e:
            result.errors.append(f"Chat export extraction failed: {e}")
            logger.error("[CHAT_EXTRACTOR] Failed: %s", e)

        return result

    def _load_conversations(self, file_path: str) -> List[Dict]:
        """Load conversations from ZIP or JSON file."""
        ext = Path(file_path).suffix.lower()

        if ext == ".zip":
            with zipfile.ZipFile(file_path) as zf:
                # Find conversations.json in the ZIP
                conv_files = [n for n in zf.namelist()
                              if "conversations" in n.lower() and n.endswith(".json")]
                if not conv_files:
                    return []
                data = json.loads(zf.read(conv_files[0]).decode("utf-8"))
                return data if isinstance(data, list) else data.get("conversations", [])

        # Raw JSON
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else data.get("conversations", data.get("items", []))

    def _conversation_to_text(self, conv: Dict, source: str) -> str:
        """Convert a conversation dict to readable text."""
        title = conv.get("title", conv.get("name", "Untitled"))
        lines = [f"# {title}\n"]

        if source == "chatgpt":
            messages = self._extract_chatgpt_messages(conv)
        else:
            messages = self._extract_claude_messages(conv)

        for msg in messages:
            role = msg.get("role", "unknown")
            text = msg.get("text", "").strip()
            if text:
                # Cap per message to avoid giant prompts
                lines.append(f"**{role}**: {text[:2000]}\n")

        return "\n".join(lines)

    def _extract_chatgpt_messages(self, conv: Dict) -> List[Dict]:
        """Extract messages from ChatGPT's mapping structure."""
        mapping = conv.get("mapping", {})
        messages = []
        for node_data in mapping.values():
            msg = (node_data or {}).get("message")
            if not msg:
                continue
            role = msg.get("author", {}).get("role", "unknown")
            if role == "system":
                continue
            content_parts = msg.get("content", {}).get("parts", [])
            text = "\n".join(str(p) for p in content_parts if isinstance(p, str))
            if text.strip():
                messages.append({
                    "role": role,
                    "text": text,
                    "create_time": msg.get("create_time"),
                })
        messages.sort(key=lambda m: m.get("create_time") or 0)
        return messages

    def _extract_claude_messages(self, conv: Dict) -> List[Dict]:
        """Extract messages from Claude's export format."""
        raw = conv.get("chat_messages", conv.get("messages", []))
        messages = []
        for msg in raw:
            role = msg.get("sender", msg.get("role", "unknown"))
            text = msg.get("text", "")
            if isinstance(text, list):
                text = "\n".join(str(p) for p in text)
            if not text and "content" in msg:
                content = msg["content"]
                if isinstance(content, list):
                    text = "\n".join(
                        b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
            if text and text.strip():
                messages.append({"role": role, "text": text})
        return messages
