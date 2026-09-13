# tests/integration/test_conversation_routing.py
"""Integration test: conversation text auto-routes through universal pipeline."""
from __future__ import annotations

import json
import pytest


class TestConversationRouting:
    """Verify conversation detection triggers universal pipeline path."""

    def test_detect_and_route_plain_conversation(self):
        """Plain text conversation detected and routed correctly."""
        from contextcore.ingestion.content_detector import detect_content_type

        conversation = (
            "User: What is machine learning?\n"
            "Assistant: Machine learning is a subset of AI that enables systems to learn from data.\n"
            "User: Can you give me an example?\n"
            "Assistant: Sure! A spam filter is a classic example of machine learning.\n"
        )

        assert detect_content_type(conversation) == "conversation"

        # Verify TurnParser can handle this
        from contextcore.ingestion.universal.parsers.turn_parser import TurnParser
        parser = TurnParser()
        meta, turns = parser.parse(conversation)
        assert len(turns) == 4
        assert turns[0].role == "user"
        assert turns[1].role == "assistant"
        assert meta.message_count == 4

    def test_detect_and_route_chatgpt_export(self):
        """ChatGPT JSON export detected and parseable."""
        from contextcore.ingestion.content_detector import detect_content_type
        from contextcore.ingestion.universal.parsers.turn_parser import TurnParser

        export = {
            "title": "ML Discussion",
            "mapping": {
                "msg1": {"message": {"author": {"role": "user"}, "content": {"parts": ["What is ML?"]}}},
                "msg2": {"message": {"author": {"role": "assistant"}, "content": {"parts": ["ML is..."]}}},
            }
        }
        text = json.dumps(export)
        assert detect_content_type(text) == "conversation"

        parser = TurnParser()
        meta, turns = parser.parse(text)
        assert len(turns) >= 2
        assert meta.source == "chatgpt"

    def test_detect_and_route_claude_export(self):
        """Claude JSON export detected and parseable."""
        from contextcore.ingestion.content_detector import detect_content_type
        from contextcore.ingestion.universal.parsers.turn_parser import TurnParser

        export = {
            "chat_messages": [
                {"sender": "human", "text": "Hello Claude"},
                {"sender": "assistant", "text": "Hello! How can I help?"},
                {"sender": "human", "text": "Tell me about graphs"},
                {"sender": "assistant", "text": "Graphs are data structures..."},
            ]
        }
        text = json.dumps(export)
        assert detect_content_type(text) == "conversation"

        parser = TurnParser()
        meta, turns = parser.parse(text)
        assert len(turns) == 4
        assert meta.source == "claude"

    def test_prose_text_not_routed_as_conversation(self):
        """Regular prose stays on legacy pipeline path."""
        from contextcore.ingestion.content_detector import detect_content_type

        prose = (
            "Machine learning is a rapidly evolving field of artificial intelligence. "
            "It encompasses various techniques including supervised learning, unsupervised "
            "learning, and reinforcement learning. Applications range from natural language "
            "processing to computer vision and beyond."
        )
        assert detect_content_type(prose) == "text"
