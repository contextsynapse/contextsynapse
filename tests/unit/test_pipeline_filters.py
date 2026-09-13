"""Tests for pipeline content filters."""
import pytest


def test_junk_line_removal():
    from contextcore.pipelines.filters import remove_junk_lines
    text = """Home | About | Contact
    Real article content about Indian politics and economic reforms.
    Follow us on Twitter
    Copyright 2026 All Rights Reserved
    The RBI announced a new interest rate policy today.
    Share this article
    Read more"""
    cleaned = remove_junk_lines(text)
    assert "Real article content" in cleaned
    assert "RBI announced" in cleaned
    assert "Home | About | Contact" not in cleaned
    assert "Follow us on Twitter" not in cleaned
    assert "Copyright" not in cleaned
    assert "Share this article" not in cleaned


def test_junk_removal_keeps_substantial_content():
    from contextcore.pipelines.filters import remove_junk_lines
    text = "This is a substantial paragraph about the state of Indian economy in 2026."
    cleaned = remove_junk_lines(text)
    assert cleaned.strip() == text.strip()


def test_minimum_content_check():
    from contextcore.pipelines.filters import passes_minimum_content
    assert passes_minimum_content("x" * 200) is True
    assert passes_minimum_content("short") is False
    assert passes_minimum_content("") is False


def test_semantic_dedup_same_content():
    from contextcore.pipelines.filters import SemanticDedup
    dedup = SemanticDedup()
    text = "The Indian economy grew by 7% in the fiscal year 2025-26."
    assert dedup.is_duplicate(text) is False  # first time
    assert dedup.is_duplicate(text) is True   # exact same


def test_semantic_dedup_different_content():
    from contextcore.pipelines.filters import SemanticDedup
    dedup = SemanticDedup()
    dedup.is_duplicate("Article about cricket in India")
    assert dedup.is_duplicate("Quantum computing breakthroughs in 2026") is False


def test_keyword_filter_include():
    from contextcore.pipelines.filters import keyword_filter
    text = "The Indian government announced new economic reforms today."
    assert keyword_filter(text, include=["economy", "reforms"], exclude=[]) is True
    assert keyword_filter(text, include=["cricket", "sports"], exclude=[]) is False


def test_keyword_filter_exclude():
    from contextcore.pipelines.filters import keyword_filter
    text = "India won the cricket world cup in an exciting final."
    assert keyword_filter(text, include=[], exclude=["cricket"]) is False
    assert keyword_filter(text, include=[], exclude=["politics"]) is True


def test_keyword_filter_empty():
    from contextcore.pipelines.filters import keyword_filter
    text = "Any content at all."
    assert keyword_filter(text, include=[], exclude=[]) is True


class TestWordBoundaryFiltering:
    """Keyword filters must use word boundaries, not substring matching."""

    def test_keyword_ai_does_not_match_said(self):
        from contextcore.ingestion.filters import _keyword_include
        result = _keyword_include("He said something important", ["AI"], mode="or")
        assert result is not None  # should NOT match — "said" != "AI"
        assert result.status == "filtered"

    def test_keyword_ai_matches_ai_standalone(self):
        from contextcore.ingestion.filters import _keyword_include
        result = _keyword_include("AI is transforming industries", ["AI"], mode="or")
        assert result is None  # passed — "AI" found as whole word

    def test_phrase_matching(self):
        from contextcore.ingestion.filters import _keyword_include
        result = _keyword_include("Climate change is a topic", ["climate change"], mode="or")
        assert result is None  # passed — phrase found

    def test_phrase_no_partial_match(self):
        from contextcore.ingestion.filters import _keyword_include
        # "climate" alone should not match phrase "climate change"
        result = _keyword_include("The climate is nice today", ["climate change"], mode="or")
        assert result is not None  # filtered — phrase not found
        assert result.status == "filtered"

    def test_exclude_word_boundary(self):
        from contextcore.ingestion.filters import _keyword_exclude
        # "ad" keyword should not match "advertising" or "made"
        result = _keyword_exclude("He made an advertising campaign", ["ad"])
        assert result is None  # passed — "ad" not found as whole word

    def test_exclude_exact_match(self):
        from contextcore.ingestion.filters import _keyword_exclude
        result = _keyword_exclude("This ad is annoying", ["ad"])
        assert result is not None  # filtered — "ad" found as whole word
