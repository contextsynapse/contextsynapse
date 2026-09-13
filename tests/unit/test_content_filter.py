"""Tests for unified content filter."""


def test_exclude_blocks():
    from contextcore.pipelines.filters import content_filter
    result = content_filter("India wins cricket world cup", keyword_exclude=["cricket"])
    assert result["passed"] is False
    assert "cricket" in result["reason"]


def test_include_passes():
    from contextcore.pipelines.filters import content_filter
    result = content_filter("RBI announces rate cut", keyword_include=["RBI", "economy"])
    assert result["passed"] is True


def test_no_filters_passes_all():
    from contextcore.pipelines.filters import content_filter
    result = content_filter("Anything goes here")
    assert result["passed"] is True
    assert "no filters" in result["reason"]


def test_include_no_match_rejects():
    from contextcore.pipelines.filters import content_filter
    result = content_filter("Weather forecast for tomorrow", keyword_include=["politics", "economy"])
    assert result["passed"] is False


def test_exclude_overrides_include():
    from contextcore.pipelines.filters import content_filter
    result = content_filter("Cricket economy report", keyword_include=["economy"], keyword_exclude=["cricket"])
    assert result["passed"] is False


def test_empty_lists_passes_all():
    from contextcore.pipelines.filters import content_filter
    result = content_filter("Anything", keyword_include=[], keyword_exclude=[], semantic_rules=[])
    assert result["passed"] is True
