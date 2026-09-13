# tests/unit/test_content_detector.py
"""Tests for content type auto-detection."""
import json
import pytest


class TestContentDetector:
    def test_plain_text_detected_as_text(self):
        from contextcore.ingestion.content_detector import detect_content_type
        result = detect_content_type("This is a regular article about machine learning.")
        assert result == "text"

    def test_role_markers_detected_as_conversation(self):
        from contextcore.ingestion.content_detector import detect_content_type
        text = "User: Hello\nAssistant: Hi there!\nUser: How are you?"
        result = detect_content_type(text)
        assert result == "conversation"

    def test_human_claude_markers_detected(self):
        from contextcore.ingestion.content_detector import detect_content_type
        text = "Human: What is Python?\nClaude: Python is a programming language."
        result = detect_content_type(text)
        assert result == "conversation"

    def test_chatgpt_export_json_detected(self):
        from contextcore.ingestion.content_detector import detect_content_type
        export = json.dumps({"mapping": {"id1": {"message": {"role": "user", "content": {"parts": ["hi"]}}}}})
        result = detect_content_type(export)
        assert result == "conversation"

    def test_claude_export_json_detected(self):
        from contextcore.ingestion.content_detector import detect_content_type
        export = json.dumps({"chat_messages": [{"sender": "human", "text": "hello"}, {"sender": "human", "text": "world"}]})
        result = detect_content_type(export)
        assert result == "conversation"

    def test_openai_messages_json_detected(self):
        from contextcore.ingestion.content_detector import detect_content_type
        messages = json.dumps([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}])
        result = detect_content_type(messages)
        assert result == "conversation"

    def test_short_text_not_conversation(self):
        from contextcore.ingestion.content_detector import detect_content_type
        result = detect_content_type("User: just one line")
        assert result == "text"  # too few turns

    def test_regular_json_not_conversation(self):
        from contextcore.ingestion.content_detector import detect_content_type
        result = detect_content_type(json.dumps({"name": "Alice", "age": 30}))
        assert result == "text"

    def test_empty_text(self):
        from contextcore.ingestion.content_detector import detect_content_type
        result = detect_content_type("")
        assert result == "text"

    def test_chatgpt_full_export_array(self):
        """ChatGPT exports an ARRAY of conversations, each with a mapping dict."""
        from contextcore.ingestion.content_detector import detect_content_type
        export = json.dumps([
            {"title": "Chat 1", "mapping": {"id1": {"message": {"author": {"role": "user"}, "content": {"parts": ["hi"]}}}}},
            {"title": "Chat 2", "mapping": {"id2": {"message": {"author": {"role": "assistant"}, "content": {"parts": ["hello"]}}}}},
        ])
        result = detect_content_type(export)
        assert result == "conversation"

    def test_turn_parser_handles_chatgpt_array(self):
        """TurnParser should parse a list of ChatGPT conversations."""
        from contextcore.ingestion.universal.parsers.turn_parser import TurnParser
        export = json.dumps([
            {
                "title": "Chat 1",
                "mapping": {
                    "m1": {"message": {"author": {"role": "user"}, "content": {"parts": ["What is AI?"]}, "create_time": 1000}},
                    "m2": {"message": {"author": {"role": "assistant"}, "content": {"parts": ["AI is..."]}, "create_time": 1001}},
                },
            },
            {
                "title": "Chat 2",
                "mapping": {
                    "m3": {"message": {"author": {"role": "user"}, "content": {"parts": ["Tell me more"]}, "create_time": 2000}},
                    "m4": {"message": {"author": {"role": "assistant"}, "content": {"parts": ["Sure..."]}, "create_time": 2001}},
                },
            },
        ])
        parser = TurnParser()
        meta, turns = parser.parse(export)
        assert meta.source == "chatgpt"
        assert len(turns) == 4
        assert "2 conversations" in meta.title
