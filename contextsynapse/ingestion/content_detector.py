# contextcore/ingestion/content_detector.py
"""Lightweight content type detection for ingestion routing.

Detects whether text is a conversation export (ChatGPT, Claude, plain text markers)
or regular prose. No LLM needed — pure regex/JSON heuristics (< 1ms).
"""
from __future__ import annotations

import json
import re

# Minimum number of turn markers to classify as conversation
_MIN_TURNS = 2

# Role markers at the start of lines
_ROLE_MARKER_RE = re.compile(
    r"^(User|Assistant|Human|Claude|AI|System)\s*:\s*",
    re.IGNORECASE | re.MULTILINE,
)


def detect_content_type(text: str) -> str:
    """Detect whether text is a conversation or regular prose.

    Returns: "conversation" or "text"
    """
    if not text or len(text.strip()) < 20:
        return "text"

    # Try JSON-based detection first (exports are always JSON)
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            data = json.loads(stripped)
            if _is_conversation_json(data):
                return "conversation"
        except (json.JSONDecodeError, ValueError):
            pass

    # Plain text role markers
    markers = _ROLE_MARKER_RE.findall(text)
    if len(markers) >= _MIN_TURNS:
        # Must have at least one user AND one assistant-type marker
        roles = {m.lower() for m in markers}
        user_roles = roles & {"user", "human"}
        assistant_roles = roles & {"assistant", "claude", "ai"}
        if user_roles and assistant_roles:
            return "conversation"

    return "text"


def _is_conversation_json(data) -> bool:
    """Check if parsed JSON matches known conversation export formats."""
    if isinstance(data, dict):
        # ChatGPT export: {"mapping": {"id": {"message": {...}}}}
        if "mapping" in data:
            mapping = data["mapping"]
            if isinstance(mapping, dict) and len(mapping) > 0:
                first_val = next(iter(mapping.values()), {})
                if isinstance(first_val, dict) and "message" in first_val:
                    return True

        # Claude export: {"chat_messages": [...]}
        if "chat_messages" in data and isinstance(data["chat_messages"], list):
            if len(data["chat_messages"]) >= _MIN_TURNS:
                return True

    # ChatGPT full export: [{mapping: {...}, title: ...}, ...]  (array of conversations)
    if isinstance(data, list) and len(data) >= 1:
        first = data[0] if data else {}
        if isinstance(first, dict) and "mapping" in first:
            return True

    # OpenAI-compatible: [{"role": "user", "content": "..."}, ...]
    if isinstance(data, list) and len(data) >= _MIN_TURNS:
        if all(isinstance(m, dict) and "role" in m and "content" in m for m in data[:5]):
            return True

    return False
