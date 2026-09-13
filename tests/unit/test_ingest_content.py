"""Tests for IngestContent and Chunk dataclasses."""
import pytest
from contextcore.ingestion.universal.ingest_content import IngestContent, Chunk

class TestIngestContent:
    def test_text_content(self):
        c = IngestContent(content_type="text", text="Hello world", title="Test")
        assert c.content_type == "text"
        assert c.text == "Hello world"
        assert c.messages == []
        assert c.records == []

    def test_conversation_content(self):
        msgs = [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello"}]
        c = IngestContent(content_type="conversation", messages=msgs, title="Chat")
        assert c.content_type == "conversation"
        assert len(c.messages) == 2

    def test_structured_content(self):
        rows = [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]
        c = IngestContent(content_type="structured", records=rows, record_type="patient")
        assert len(c.records) == 2
        assert c.record_type == "patient"

class TestChunk:
    def test_text_chunk(self):
        ch = Chunk(content="paragraph text", index=0, chunk_type="paragraph")
        assert ch.chunk_type == "paragraph"
        assert ch.metadata == {}

    def test_turn_chunk(self):
        ch = Chunk(content="I need help", index=0, chunk_type="turn", metadata={"role": "user", "turn_index": 0})
        assert ch.metadata["role"] == "user"

    def test_record_chunk(self):
        ch = Chunk(content="", index=0, chunk_type="record", metadata={"fields": {"name": "Alice"}, "record_type": "patient"})
        assert ch.metadata["fields"]["name"] == "Alice"

    def test_chunk_token_estimate(self):
        ch = Chunk(content="This is about twenty tokens of text for testing purposes here", index=0)
        assert ch.token_estimate > 0
