"""
Tests for text chunking and dedup helpers.
"""

from __future__ import annotations

import pytest

from contextsynapse.ingestion.chunking import chunk_text
from contextsynapse.ingestion.stage_executor import (
    _content_hash,
    _hamming_distance,
    _normalised_hash,
    _simhash,
)


# ── chunk_text ────────────────────────────────────────────────────────────────


class TestChunkTextShort:
    """Short text that fits in a single chunk."""

    def test_returns_single_chunk(self):
        text = "Hello world. This is short."
        result = chunk_text(text)
        assert result == [text]

    def test_exactly_max_chars(self):
        text = "a" * 2000
        result = chunk_text(text)
        assert result == [text]


class TestChunkTextDoubleNewlines:
    """Text with paragraph breaks (double newlines)."""

    def test_splits_on_double_newlines(self):
        para_a = "A" * 800
        para_b = "B" * 800
        para_c = "C" * 800
        text = f"{para_a}\n\n{para_b}\n\n{para_c}"
        chunks = chunk_text(text, max_chars=1800)
        assert len(chunks) >= 2
        # Every paragraph must appear in some chunk
        joined = " ".join(chunks)
        assert para_a in joined
        assert para_b in joined
        assert para_c in joined


class TestChunkTextSingleNewlines:
    """Text with only single newlines (e.g. trafilatura output)."""

    def test_splits_on_single_newlines(self):
        lines = [f"Line {i} " + "x" * 100 for i in range(30)]
        text = "\n".join(lines)
        chunks = chunk_text(text, max_chars=500)
        assert len(chunks) > 1
        # All content preserved
        for line in lines:
            assert any(line.strip() in c for c in chunks)


class TestChunkTextSentenceBoundary:
    """Text with no newlines at all — should split on sentence boundaries."""

    def test_splits_on_sentences(self):
        sentences = [f"Sentence number {i}." for i in range(60)]
        text = " ".join(sentences)
        assert "\n" not in text
        chunks = chunk_text(text, max_chars=500)
        assert len(chunks) > 1


class TestChunkTextMaxChars:
    """No chunk should exceed max_chars."""

    def test_chunks_respect_max_chars(self):
        text = ("word " * 1000).strip()
        for max_c in [200, 500, 1000]:
            chunks = chunk_text(text, max_chars=max_c)
            for chunk in chunks:
                assert len(chunk) <= max_c, (
                    f"Chunk length {len(chunk)} exceeds max_chars={max_c}"
                )

    def test_custom_max_chars(self):
        text = "Hello. " * 200
        chunks = chunk_text(text, max_chars=100)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 100


class TestChunkTextEmpty:
    """Edge case: empty or whitespace-only text."""

    def test_empty_string(self):
        result = chunk_text("")
        assert result == [""]

    def test_whitespace_only(self):
        result = chunk_text("   ")
        assert result == ["   "]


# ── _content_hash ─────────────────────────────────────────────────────────────


class TestContentHash:
    def test_same_text_same_hash(self):
        assert _content_hash("hello") == _content_hash("hello")

    def test_different_text_different_hash(self):
        assert _content_hash("hello") != _content_hash("world")

    def test_returns_hex_string(self):
        h = _content_hash("test")
        assert isinstance(h, str)
        assert len(h) == 64  # SHA-256 hex length
        int(h, 16)  # valid hex


# ── _normalised_hash ──────────────────────────────────────────────────────────


class TestNormalisedHash:
    def test_ignores_case(self):
        assert _normalised_hash("Hello World") == _normalised_hash("hello world")

    def test_ignores_extra_whitespace(self):
        assert _normalised_hash("a  b   c") == _normalised_hash("a b c")

    def test_ignores_leading_trailing_whitespace(self):
        assert _normalised_hash("  hello  ") == _normalised_hash("hello")

    def test_ignores_newlines_vs_spaces(self):
        assert _normalised_hash("hello\nworld") == _normalised_hash("hello world")

    def test_differs_for_different_content(self):
        assert _normalised_hash("alpha") != _normalised_hash("beta")


# ── _simhash ──────────────────────────────────────────────────────────────────


class TestSimhash:
    def test_identical_text_distance_zero(self):
        text = "The quick brown fox jumps over the lazy dog"
        h1 = _simhash(text)
        h2 = _simhash(text)
        assert _hamming_distance(h1, h2) == 0

    def test_similar_text_smaller_than_different(self):
        t1 = "The quick brown fox jumps over the lazy dog"
        t2 = "The quick brown fox leaps over the lazy dog"
        t3 = "Lorem ipsum dolor sit amet consectetur adipiscing elit"
        d_similar = _hamming_distance(_simhash(t1), _simhash(t2))
        d_different = _hamming_distance(_simhash(t1), _simhash(t3))
        assert d_similar < d_different, f"Similar ({d_similar}) should be < different ({d_different})"

    def test_different_text_large_distance(self):
        t1 = "The quick brown fox jumps over the lazy dog"
        t2 = "Lorem ipsum dolor sit amet consectetur adipiscing elit"
        d = _hamming_distance(_simhash(t1), _simhash(t2))
        assert d > 5, f"Expected large distance, got {d}"

    def test_returns_int(self):
        assert isinstance(_simhash("test text here"), int)

    def test_short_text_does_not_crash(self):
        # Short text triggers character-level fallback
        h = _simhash("hi")
        assert isinstance(h, int)


# ── _hamming_distance ─────────────────────────────────────────────────────────


class TestHammingDistance:
    def test_identical_values(self):
        assert _hamming_distance(0b1010, 0b1010) == 0

    def test_one_bit_difference(self):
        assert _hamming_distance(0b1010, 0b1011) == 1

    def test_all_bits_different(self):
        assert _hamming_distance(0b0000, 0b1111) == 4

    def test_known_pattern(self):
        # 0xFF ^ 0x00 = 0xFF → 8 bits set
        assert _hamming_distance(0xFF, 0x00) == 8

    def test_symmetric(self):
        assert _hamming_distance(42, 99) == _hamming_distance(99, 42)

    def test_zero_zero(self):
        assert _hamming_distance(0, 0) == 0
