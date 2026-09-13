"""Multi-provider turn parser.

Supports:
- Plain text  (``User:`` / ``Assistant:`` or ``Human:`` / ``AI:`` markers)
- ChatGPT export  (``mapping`` dict keyed by message id)
- Claude export   (``chat_messages`` list)
- OpenAI-compatible  (``messages`` list with role/content dicts)

All formats are normalised into a flat ``(SessionMeta, List[Turn])`` pair.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

from contextsynapse.ingestion.universal.ingest_content import Chunk

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Turn:
    """A single conversational turn."""
    role: str
    content: str
    index: int
    timestamp: Optional[str] = None
    model: Optional[str] = None


@dataclass
class SessionMeta:
    """Metadata about the parsed conversation session."""
    title: str = ""
    source: str = ""
    model: str = ""
    message_count: int = 0
    created_at: Optional[str] = None
    conversation_id: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ROLE_MAP = {
    "human": "user",
    "ai": "assistant",
    "bot": "assistant",
}

_TEXT_TURN_RE = re.compile(
    r"^(User|Assistant|Human|AI)\s*:\s*",
    re.IGNORECASE | re.MULTILINE,
)


def _normalize_role(raw: str) -> str:
    return _ROLE_MAP.get(raw.lower(), raw.lower())


# ---------------------------------------------------------------------------
# TurnParser
# ---------------------------------------------------------------------------


class TurnParser:
    """Parse various chat export formats into uniform Turn sequences."""

    # -- public interface ---------------------------------------------------

    def parse(self, input_data: Union[str, dict, list]) -> Tuple[SessionMeta, List[Turn]]:
        """Parse *input_data* and return ``(meta, turns)``.

        Handles: single conversation dict, list of conversations (ChatGPT export),
        JSON string, or plain text with role markers.
        Raises ``ValueError`` when the format cannot be determined.
        """
        if isinstance(input_data, str):
            # Try JSON first
            try:
                parsed = json.loads(input_data)
                if isinstance(parsed, dict):
                    return self._dispatch_dict(parsed)
                if isinstance(parsed, list):
                    return self._parse_list(parsed)
            except (json.JSONDecodeError, ValueError):
                pass
            return self._parse_text(input_data)

        if isinstance(input_data, dict):
            return self._dispatch_dict(input_data)

        if isinstance(input_data, list):
            return self._parse_list(input_data)

        raise ValueError(f"Unsupported input type: {type(input_data).__name__}")

    def _parse_list(self, data: list) -> Tuple[SessionMeta, List[Turn]]:
        """Parse a list of conversations (e.g. ChatGPT full export).

        Merges all conversations into a single (meta, turns) result.
        """
        all_turns: List[Turn] = []
        titles: List[str] = []
        source = ""
        model = ""

        for conv in data:
            if not isinstance(conv, dict):
                continue
            try:
                meta, turns = self._dispatch_dict(conv)
                # Re-index turns to be sequential across all conversations
                offset = len(all_turns)
                for t in turns:
                    all_turns.append(Turn(
                        role=t.role, content=t.content,
                        index=offset + t.index,
                        timestamp=t.timestamp, model=t.model,
                    ))
                if meta.title:
                    titles.append(meta.title)
                if meta.source:
                    source = meta.source
                if meta.model:
                    model = meta.model
            except Exception:
                continue

        combined_meta = SessionMeta(
            title=f"{len(titles)} conversations" if titles else "",
            source=source or "chatgpt",
            model=model,
            message_count=len(all_turns),
        )
        return combined_meta, all_turns

    def to_chunks(self, turns: List[Turn], conversation_title: str = "") -> List[Chunk]:
        """Convert Turn objects into Chunk objects suitable for the pipeline."""
        chunks: List[Chunk] = []
        for turn in turns:
            chunks.append(Chunk(
                content=turn.content,
                index=turn.index,
                chunk_type="turn",
                metadata={
                    "role": turn.role,
                    **({"conversation_title": conversation_title} if conversation_title else {}),
                    **({"timestamp": turn.timestamp} if turn.timestamp else {}),
                    **({"model": turn.model} if turn.model else {}),
                },
            ))
        return chunks

    # -- format dispatch ----------------------------------------------------

    def _dispatch_dict(self, data: dict) -> Tuple[SessionMeta, List[Turn]]:
        if "mapping" in data:
            return self._parse_chatgpt(data)
        if "chat_messages" in data:
            return self._parse_claude(data)
        if "messages" in data:
            return self._parse_generic(data)
        raise ValueError(
            "Unknown dict format: expected 'mapping', 'chat_messages', or 'messages' key"
        )

    # -- plain text ---------------------------------------------------------

    def _parse_text(self, text: str) -> Tuple[SessionMeta, List[Turn]]:
        splits = _TEXT_TURN_RE.split(text)
        # splits looks like: [preamble, role1, content1, role2, content2, ...]
        turns: List[Turn] = []
        idx = 0

        # Skip preamble (index 0) then process pairs
        i = 1
        while i < len(splits) - 1:
            raw_role = splits[i].strip()
            content = splits[i + 1].strip()
            role = _normalize_role(raw_role)
            if role != "system" and content:
                turns.append(Turn(role=role, content=content, index=idx))
                idx += 1
            i += 2

        meta = SessionMeta(
            source="text",
            message_count=len(turns),
        )
        return meta, turns

    # -- ChatGPT export (mapping dict) --------------------------------------

    def _parse_chatgpt(self, data: dict) -> Tuple[SessionMeta, List[Turn]]:
        mapping: Dict[str, Any] = data["mapping"]

        # Collect messages with create_time for sorting
        raw: List[Tuple[float, str, str]] = []
        for _id, node in mapping.items():
            msg = node.get("message")
            if not msg:
                continue
            role = _normalize_role(msg.get("author", {}).get("role", ""))
            if role == "system":
                continue
            content_obj = msg.get("content", {})
            parts = content_obj.get("parts", [])
            text = "\n".join(str(p) for p in parts if p).strip()
            if not text:
                continue
            create_time = msg.get("create_time") or 0.0
            raw.append((float(create_time), role, text))

        raw.sort(key=lambda t: t[0])

        turns = [
            Turn(
                role=role,
                content=content,
                index=idx,
                timestamp=datetime.fromtimestamp(ts).isoformat() if ts else None,
            )
            for idx, (ts, role, content) in enumerate(raw)
        ]

        meta = SessionMeta(
            title=data.get("title", ""),
            source="chatgpt",
            model=data.get("model", "") or data.get("default_model_slug", ""),
            message_count=len(turns),
            created_at=data.get("create_time"),
            conversation_id=data.get("id", data.get("conversation_id", "")),
        )
        return meta, turns

    # -- Claude export (chat_messages list) ---------------------------------

    def _parse_claude(self, data: dict) -> Tuple[SessionMeta, List[Turn]]:
        messages: List[Dict[str, Any]] = data["chat_messages"]
        turns: List[Turn] = []
        idx = 0

        for msg in messages:
            role = _normalize_role(msg.get("sender", msg.get("role", "")))
            if role == "system":
                continue

            # Content may be a plain string or a list of content blocks
            raw_content = msg.get("text", msg.get("content", ""))
            if isinstance(raw_content, list):
                # Content blocks: [{type: "text", text: "..."}, ...]
                parts = [
                    block.get("text", "") for block in raw_content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                text = "\n".join(parts).strip()
            else:
                text = str(raw_content).strip()

            if not text:
                continue

            turns.append(Turn(
                role=role,
                content=text,
                index=idx,
                timestamp=msg.get("created_at") or msg.get("timestamp"),
                model=msg.get("model"),
            ))
            idx += 1

        meta = SessionMeta(
            title=data.get("name", data.get("title", "")),
            source="claude",
            model=data.get("model", ""),
            message_count=len(turns),
            created_at=data.get("created_at"),
            conversation_id=data.get("uuid", data.get("id", "")),
        )
        return meta, turns

    # -- Generic / OpenAI-compatible (messages list) ------------------------

    def _parse_generic(self, data: dict) -> Tuple[SessionMeta, List[Turn]]:
        messages: List[Dict[str, Any]] = data["messages"]
        turns: List[Turn] = []
        idx = 0

        for msg in messages:
            role = _normalize_role(msg.get("role", ""))
            if role == "system":
                continue
            content = msg.get("content", "")
            if isinstance(content, list):
                parts = [
                    block.get("text", "") for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                content = "\n".join(parts).strip()
            else:
                content = str(content).strip()
            if not content:
                continue

            turns.append(Turn(
                role=role,
                content=content,
                index=idx,
                timestamp=msg.get("timestamp"),
                model=msg.get("model"),
            ))
            idx += 1

        meta = SessionMeta(
            title=data.get("title", ""),
            source="generic",
            message_count=len(turns),
            model=data.get("model", ""),
            conversation_id=data.get("id", ""),
        )
        return meta, turns
