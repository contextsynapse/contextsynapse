"""Tests for contextcore.ingestion.universal.parsers.turn_parser."""

import pytest

from contextcore.ingestion.universal.parsers.turn_parser import (
    SessionMeta,
    Turn,
    TurnParser,
)


@pytest.fixture
def parser():
    return TurnParser()


# ── Plain text parsing ──────────────────────────────────────────────────────


class TestParseText:

    def test_user_assistant_format(self, parser):
        text = "User: Hello there\nAssistant: Hi! How can I help?"
        meta, turns = parser.parse(text)
        assert meta.source == "text"
        assert len(turns) == 2
        assert turns[0].role == "user"
        assert turns[0].content == "Hello there"
        assert turns[1].role == "assistant"
        assert turns[1].content == "Hi! How can I help?"

    def test_human_ai_format(self, parser):
        text = "Human: What is Python?\nAI: Python is a programming language."
        meta, turns = parser.parse(text)
        assert len(turns) == 2
        assert turns[0].role == "user"
        assert turns[1].role == "assistant"

    def test_multiline_message(self, parser):
        text = (
            "User: Tell me about databases.\n"
            "There are many kinds.\n"
            "Assistant: Sure! Databases come in many forms:\n"
            "1. Relational\n"
            "2. Document\n"
            "3. Graph"
        )
        meta, turns = parser.parse(text)
        assert len(turns) == 2
        assert "many kinds" in turns[0].content
        assert "Graph" in turns[1].content

    def test_indexes_sequential(self, parser):
        text = "User: A\nAssistant: B\nUser: C\nAssistant: D"
        _, turns = parser.parse(text)
        assert [t.index for t in turns] == [0, 1, 2, 3]


# ── ChatGPT export ─────────────────────────────────────────────────────────


class TestParseChatGPT:

    def _make_chatgpt(self):
        return {
            "title": "Test Chat",
            "id": "conv-123",
            "model": "gpt-4",
            "mapping": {
                "msg-sys": {
                    "message": {
                        "author": {"role": "system"},
                        "content": {"parts": ["You are a helpful assistant."]},
                        "create_time": 1000.0,
                    }
                },
                "msg-1": {
                    "message": {
                        "author": {"role": "user"},
                        "content": {"parts": ["Hello"]},
                        "create_time": 1001.0,
                    }
                },
                "msg-2": {
                    "message": {
                        "author": {"role": "assistant"},
                        "content": {"parts": ["Hi there!"]},
                        "create_time": 1002.0,
                    }
                },
            },
        }

    def test_basic_parse(self, parser):
        data = self._make_chatgpt()
        meta, turns = parser.parse(data)
        assert meta.source == "chatgpt"
        assert meta.title == "Test Chat"
        assert meta.conversation_id == "conv-123"
        # System message filtered
        assert len(turns) == 2
        assert turns[0].role == "user"
        assert turns[1].role == "assistant"

    def test_sorted_by_create_time(self, parser):
        data = self._make_chatgpt()
        _, turns = parser.parse(data)
        assert turns[0].content == "Hello"
        assert turns[1].content == "Hi there!"

    def test_system_filtered(self, parser):
        data = self._make_chatgpt()
        _, turns = parser.parse(data)
        roles = {t.role for t in turns}
        assert "system" not in roles

    def test_empty_parts_skipped(self, parser):
        data = {
            "mapping": {
                "m1": {
                    "message": {
                        "author": {"role": "user"},
                        "content": {"parts": [""]},
                        "create_time": 1.0,
                    }
                },
                "m2": {"message": None},  # no message at all
            }
        }
        _, turns = parser.parse(data)
        assert len(turns) == 0


# ── Claude export ───────────────────────────────────────────────────────────


class TestParseClaude:

    def test_flat_text(self, parser):
        data = {
            "uuid": "claude-1",
            "name": "Claude Chat",
            "chat_messages": [
                {"sender": "human", "text": "Explain quantum computing."},
                {"sender": "assistant", "text": "Quantum computing uses qubits..."},
            ],
        }
        meta, turns = parser.parse(data)
        assert meta.source == "claude"
        assert meta.title == "Claude Chat"
        assert len(turns) == 2
        assert turns[0].role == "user"
        assert turns[1].role == "assistant"

    def test_content_blocks(self, parser):
        data = {
            "chat_messages": [
                {"sender": "human", "text": "Hi"},
                {
                    "sender": "assistant",
                    "text": [
                        {"type": "text", "text": "Hello!"},
                        {"type": "text", "text": "How can I help?"},
                    ],
                },
            ],
        }
        _, turns = parser.parse(data)
        assert len(turns) == 2
        assert "Hello!" in turns[1].content
        assert "How can I help?" in turns[1].content

    def test_system_filtered(self, parser):
        data = {
            "chat_messages": [
                {"sender": "system", "text": "You are helpful."},
                {"sender": "human", "text": "Hi"},
            ],
        }
        _, turns = parser.parse(data)
        assert len(turns) == 1
        assert turns[0].role == "user"


# ── Generic / OpenAI-compatible ─────────────────────────────────────────────


class TestParseGeneric:

    def test_basic_messages(self, parser):
        data = {
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi!"},
            ]
        }
        meta, turns = parser.parse(data)
        assert meta.source == "generic"
        assert len(turns) == 2

    def test_system_filtered(self, parser):
        data = {
            "messages": [
                {"role": "system", "content": "You are an assistant."},
                {"role": "user", "content": "Hi"},
            ]
        }
        _, turns = parser.parse(data)
        assert len(turns) == 1
        assert turns[0].role == "user"

    def test_content_blocks(self, parser):
        data = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Part A"},
                        {"type": "text", "text": "Part B"},
                    ],
                }
            ]
        }
        _, turns = parser.parse(data)
        assert "Part A" in turns[0].content
        assert "Part B" in turns[0].content


# ── Error handling ──────────────────────────────────────────────────────────


class TestErrors:

    def test_unknown_dict_format(self, parser):
        with pytest.raises(ValueError, match="Unknown dict format"):
            parser.parse({"foo": "bar"})

    def test_unsupported_type(self, parser):
        with pytest.raises(ValueError, match="Unsupported input type"):
            parser.parse(42)


# ── to_chunks ───────────────────────────────────────────────────────────────


class TestToChunks:

    def test_converts_turns_to_chunks(self, parser):
        turns = [
            Turn(role="user", content="Hello", index=0),
            Turn(role="assistant", content="Hi!", index=1, model="gpt-4"),
        ]
        chunks = parser.to_chunks(turns)
        assert len(chunks) == 2
        assert chunks[0].chunk_type == "turn"
        assert chunks[0].metadata["role"] == "user"
        assert chunks[1].metadata["model"] == "gpt-4"
        assert "timestamp" not in chunks[0].metadata
