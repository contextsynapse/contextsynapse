"""Tests for the Universal Context Layer."""
import pytest


class TestScoreNode:
    """Gate 1: Node-level quality scoring."""

    def test_high_quality_person(self):
        """Well-connected named entity scores high."""
        from contextcore.context.quality import score_node

        node = {
            "label": "Person",
            "properties": {
                "name": "Benjamin Netanyahu",
                "source_url": "https://timesofindia.com/article",
                "mention_count": 5,
                "confidence": 0.9,
                "created_by": "pipeline:news",
            },
            "edge_count": 23,
        }
        score = score_node(node)
        assert score >= 80, f"High quality person should score >=80, got {score}"

    def test_garbage_person(self):
        """Generic non-name text scores low."""
        from contextcore.context.quality import score_node

        node = {
            "label": "Person",
            "properties": {"name": "Match Results"},
            "edge_count": 0,
        }
        score = score_node(node)
        assert score <= 25, f"Garbage person should score <=25, got {score}"

    def test_noise_fact(self):
        """Website navigation text scores very low."""
        from contextcore.context.quality import score_node

        node = {
            "label": "Fact",
            "properties": {
                "name": "Edition IN IN US GCC English Hindi Marathi",
                "statement": "Edition IN IN US GCC English Hindi Marathi",
            },
            "edge_count": 0,
        }
        score = score_node(node)
        assert score <= 15, f"Nav noise should score <=15, got {score}"

    def test_high_quality_fact(self):
        """Specific factual claim with entities scores high."""
        from contextcore.context.quality import score_node

        node = {
            "label": "Fact",
            "properties": {
                "name": "Iran warns 'fingers on trigger'",
                "statement": "Iran warns 'fingers on trigger'; US says forces 'ready and focused' as ceasefire talks stall",
                "source_url": "https://timesofindia.com/world/iran-us",
                "subject": "Iran",
            },
            "edge_count": 5,
        }
        score = score_node(node)
        assert score >= 75, f"Quality fact should score >=75, got {score}"

    def test_zero_edge_penalty(self):
        """Isolated nodes get penalized."""
        from contextcore.context.quality import score_node

        with_edges = {"label": "Person", "properties": {"name": "Test Person"}, "edge_count": 5}
        without_edges = {"label": "Person", "properties": {"name": "Test Person"}, "edge_count": 0}
        assert score_node(with_edges) > score_node(without_edges)

    def test_duplicate_text_penalty(self):
        """Duplicate detection lowers score."""
        from contextcore.context.quality import score_node

        node = {
            "label": "Fact",
            "properties": {"statement": "Edition IN US GCC"},
            "edge_count": 0,
            "_is_duplicate": True,
        }
        score = score_node(node)
        assert score <= 15

    def test_score_clamped_0_100(self):
        """Score never goes below 0 or above 100."""
        from contextcore.context.quality import score_node

        terrible = {"label": "Fact", "properties": {"name": "x"}, "edge_count": 0, "_is_duplicate": True}
        excellent = {
            "label": "Person",
            "properties": {"name": "Narendra Modi", "source_url": "https://x.com", "confidence": 1.0,
                           "mention_count": 50, "created_by": "pipeline:news", "subject": "India"},
            "edge_count": 30,
        }
        assert 0 <= score_node(terrible) <= 100
        assert 0 <= score_node(excellent) <= 100


class TestEntityIndex:
    """Gate 1: Redis-backed entity index for O(1) dedup."""

    def _make_index(self):
        from contextcore.context.quality import EntityIndex
        return EntityIndex(redis_client=None)

    def test_put_and_get(self):
        idx = self._make_index()
        idx.put("default", "Person", "Benjamin Netanyahu", "node_123")
        result = idx.get("default", "Person", "Benjamin Netanyahu")
        assert result is not None
        assert result["node_id"] == "node_123"
        assert result["mention_count"] == 1

    def test_put_existing_bumps_count(self):
        idx = self._make_index()
        idx.put("default", "Person", "Benjamin Netanyahu", "node_123")
        idx.put("default", "Person", "Benjamin Netanyahu", "node_123")
        result = idx.get("default", "Person", "Benjamin Netanyahu")
        assert result["mention_count"] == 2

    def test_case_insensitive(self):
        idx = self._make_index()
        idx.put("default", "Person", "Benjamin Netanyahu", "node_123")
        result = idx.get("default", "Person", "benjamin netanyahu")
        assert result is not None
        assert result["node_id"] == "node_123"

    def test_normalize_strips_titles(self):
        from contextcore.context.quality import EntityIndex
        assert EntityIndex.normalize_name("Dr. John Smith") == "john smith"
        assert EntityIndex.normalize_name("PM Netanyahu") == "netanyahu"
        assert EntityIndex.normalize_name("President Biden") == "biden"
        assert EntityIndex.normalize_name("Mr. Test Name") == "test name"

    def test_get_missing_returns_none(self):
        idx = self._make_index()
        assert idx.get("default", "Person", "Nobody") is None

    def test_top_entities(self):
        idx = self._make_index()
        idx.put("g1", "Person", "Alice", "n1", quality=90)
        idx.put("g1", "Person", "Bob", "n2", quality=50)
        idx.put("g1", "Person", "Alice", "n1", quality=90)
        top = idx.top("g1", "Person", limit=2)
        assert len(top) == 2
        assert top[0]["name"] == "Alice"

    def test_different_graphs_isolated(self):
        idx = self._make_index()
        idx.put("graph_a", "Person", "Alice", "n1")
        idx.put("graph_b", "Person", "Alice", "n2")
        assert idx.get("graph_a", "Person", "Alice")["node_id"] == "n1"
        assert idx.get("graph_b", "Person", "Alice")["node_id"] == "n2"


class TestQualityFilter:
    """Gate 2: Quality-aware search filtering."""

    def test_filters_low_quality(self):
        from contextcore.context.quality import QualityFilter
        results = [
            {"node_id": "1", "label": "Fact", "name": "Iran warns...", "_quality": 88, "score": 0.9},
            {"node_id": "2", "label": "Fact", "name": "Edition IN US GCC", "_quality": 5, "score": 0.8},
            {"node_id": "3", "label": "Person", "name": "Netanyahu", "_quality": 92, "score": 0.7},
        ]
        filtered = QualityFilter.filter_results(results, context_quality=78)
        ids = [r["node_id"] for r in filtered]
        assert "2" not in ids
        assert "1" in ids
        assert "3" in ids

    def test_dynamic_threshold(self):
        from contextcore.context.quality import QualityFilter
        assert QualityFilter.threshold(92) == 37
        assert QualityFilter.threshold(78) == 31
        assert QualityFilter.threshold(50) == 20
        assert QualityFilter.threshold(30) == 20
        assert QualityFilter.threshold(10) == 20

    def test_dedup_near_identical_facts(self):
        from contextcore.context.quality import QualityFilter
        results = [
            {"node_id": "1", "label": "Fact", "name": "Iran warns fingers on trigger", "_quality": 88, "score": 0.9},
            {"node_id": "2", "label": "Fact", "name": "Iran warns fingers on the trigger", "_quality": 60, "score": 0.85},
            {"node_id": "3", "label": "Person", "name": "Netanyahu", "_quality": 92, "score": 0.7},
        ]
        deduped = QualityFilter.dedup_results(results)
        fact_ids = [r["node_id"] for r in deduped if r["label"] == "Fact"]
        assert len(fact_ids) == 1
        assert fact_ids[0] == "1"

    def test_caps_per_label(self):
        from contextcore.context.quality import QualityFilter
        results = [
            {"node_id": str(i), "label": "Fact", "name": f"Fact {i}", "_quality": 80, "score": 0.9 - i * 0.01}
            for i in range(10)
        ]
        capped = QualityFilter.cap_per_label(results, max_per_label=3)
        assert len(capped) == 3

    def test_rank_by_quality(self):
        from contextcore.context.quality import QualityFilter
        results = [
            {"node_id": "1", "label": "Fact", "_quality": 50, "score": 0.9},
            {"node_id": "2", "label": "Fact", "_quality": 95, "score": 0.6},
        ]
        ranked = QualityFilter.rank(results, content_type="knowledge")
        assert ranked[0]["node_id"] == "2"


class TestManifestBuilder:
    """Gate 3: Pre-computed context manifest."""

    def _make_builder(self):
        from contextcore.context.quality import ManifestBuilder, EntityIndex
        idx = EntityIndex(redis_client=None)
        idx.put("test_graph", "Person", "Benjamin Netanyahu", "n1", quality=92)
        idx.put("test_graph", "Person", "Benjamin Netanyahu", "n1", quality=92)
        idx.put("test_graph", "Person", "Narendra Modi", "n2", quality=88)
        idx.put("test_graph", "Location", "United States", "n3", quality=90)
        idx.put("test_graph", "Organization", "TCS", "n4", quality=82)
        idx.put("test_graph", "Event", "Iran-US ceasefire", "n5", quality=78)
        return ManifestBuilder(entity_index=idx, redis_client=None)

    def test_build_returns_manifest(self):
        builder = self._make_builder()
        manifest = builder.build("test_graph", "ctx_test")
        assert manifest["context_id"] == "ctx_test"
        assert "context_quality" in manifest
        assert "top_entities" in manifest
        assert "tool_hints" in manifest
        assert "stats" in manifest

    def test_top_entities_populated(self):
        builder = self._make_builder()
        manifest = builder.build("test_graph", "ctx_test")
        persons = manifest["top_entities"].get("Person", [])
        assert len(persons) >= 1
        assert persons[0]["name"] == "Benjamin Netanyahu"

    def test_tool_hints_generated(self):
        builder = self._make_builder()
        manifest = builder.build("test_graph", "ctx_test")
        hints = manifest["tool_hints"]
        assert len(hints) >= 1
        hint_tools = [h["tool"] for h in hints]
        assert "search_nodes" in hint_tools

    def test_manifest_to_text(self):
        from contextcore.context.quality import ManifestBuilder
        builder = self._make_builder()
        manifest = builder.build("test_graph", "ctx_test")
        text = ManifestBuilder.to_text(manifest)
        assert "Benjamin Netanyahu" in text
        assert "Person" in text
        assert isinstance(text, str)
        assert len(text) > 50

    def test_content_type_detection(self):
        from contextcore.context.quality import ManifestBuilder
        assert ManifestBuilder.detect_content_type({"Fact": 500, "Document": 200, "Person": 100}) == "news"
        assert ManifestBuilder.detect_content_type({"Feature": 10, "Requirement": 5}) == "code"
        assert ManifestBuilder.detect_content_type({"Person": 500, "Organization": 200, "Fact": 50}) == "knowledge"
