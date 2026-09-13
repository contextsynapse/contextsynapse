"""Tests for Context Units — self-discoverable intelligence layer."""
import pytest


class TestBuildContextUnit:
    def test_build_cu_from_facts(self):
        from contextcore.context.context_units import build_context_unit
        facts = [
            {"id": "f1", "label": "Fact", "properties": {"statement": "Iran warns 'fingers on trigger'", "name": "Iran warns"}},
            {"id": "f2", "label": "Fact", "properties": {"statement": "US says forces ready and focused as ceasefire deadline looms", "name": "US forces ready"}},
        ]
        entities = [
            {"id": "p1", "label": "Person", "properties": {"name": "Pezeshkian"}},
            {"id": "o1", "label": "Organization", "properties": {"name": "Revolutionary Guards"}},
        ]
        cu = build_context_unit(topic="Iran-US Tensions", facts=facts, entities=entities)
        assert cu["label"] == "ContextUnit"
        assert cu["properties"]["topic"] == "Iran-US Tensions"
        assert "claim" in cu["properties"]
        assert len(cu["properties"]["claim"]) > 20
        assert "questions_answered" in cu["properties"]
        assert "next_clues" in cu["properties"]
        assert "evidence_ids" in cu
        assert "actor_ids" in cu

    def test_cu_has_confidence(self):
        from contextcore.context.context_units import build_context_unit
        cu = build_context_unit(topic="Test", facts=[{"id": "f1", "label": "Fact", "properties": {"statement": "Test fact about something specific"}}], entities=[])
        assert 0.0 <= cu["properties"]["confidence"] <= 1.0

    def test_cu_without_llm_uses_heuristic(self):
        from contextcore.context.context_units import build_context_unit
        cu = build_context_unit(topic="Heuristic Topic", facts=[
            {"id": "f1", "label": "Fact", "properties": {"statement": "Iran threatens military action"}},
            {"id": "f2", "label": "Fact", "properties": {"statement": "US deploys navy to Gulf region"}},
        ], entities=[{"id": "p1", "label": "Person", "properties": {"name": "Trump"}}], use_llm=False)
        assert "Iran" in cu["properties"]["claim"] or "military" in cu["properties"]["claim"] or "US" in cu["properties"]["claim"]
        assert len(cu["properties"]["questions_answered"]) >= 1


class TestIntentMatching:
    def test_match_by_question(self):
        from contextcore.context.context_units import match_intent
        cus = [
            {"id": "cu1", "properties": {"topic": "Iran-US Tensions", "questions_answered": ["What is the Iran-US situation?", "Is there a ceasefire?"]}},
            {"id": "cu2", "properties": {"topic": "Ukraine-Russia War", "questions_answered": ["What is happening in Ukraine?", "Is Russia attacking?"]}},
        ]
        matches = match_intent("What's happening with Iran?", cus)
        assert len(matches) >= 1
        assert matches[0]["id"] == "cu1"

    def test_match_returns_empty_for_no_match(self):
        from contextcore.context.context_units import match_intent
        cus = [{"id": "cu1", "properties": {"topic": "Iran-US Tensions", "questions_answered": ["What is the Iran-US situation?"]}}]
        matches = match_intent("What is the weather in Tokyo?", cus)
        assert len(matches) == 0


class TestClusterFacts:
    def test_cluster_by_shared_entities(self):
        from contextcore.context.context_units import cluster_facts_for_cus
        facts = [
            {"id": "f1", "label": "Fact", "properties": {"statement": "Iran warns about trigger", "name": "Iran warns"}},
            {"id": "f2", "label": "Fact", "properties": {"statement": "Iran ceasefire deadline", "name": "Iran ceasefire"}},
            {"id": "f3", "label": "Fact", "properties": {"statement": "Ukraine drone attack", "name": "Ukraine drones"}},
        ]
        clusters = cluster_facts_for_cus(facts, min_cluster_size=2)
        assert len(clusters) >= 1
        iran_cluster = [c for c in clusters if any("iran" in f["properties"]["statement"].lower() for f in c["facts"])]
        assert len(iran_cluster) >= 1


class TestCUStorage:
    def test_store_and_get_cu(self):
        import tempfile, os
        from contextcore.search.lmdb_index import LMDBIndex
        idx = LMDBIndex(os.path.join(tempfile.mkdtemp(), "test"))
        idx.index_node("cu_test123", "ContextUnit", {
            "topic": "Iran-US Tensions", "name": "Iran-US Tensions",
            "claim": "Military standoff escalating",
            "confidence": 0.87,
            "questions_answered": "What is Iran-US situation?",
            "next_clues": "Explore ceasefire",
        })
        node = idx.get_node("cu_test123")
        assert node is not None
        assert node["label"] == "ContextUnit"

    def test_search_cu_by_intent(self):
        import tempfile, os
        from contextcore.search.lmdb_index import LMDBIndex
        idx = LMDBIndex(os.path.join(tempfile.mkdtemp(), "test"))
        idx.index_node("cu_1", "ContextUnit", {
            "name": "Iran-US Tensions", "topic": "Iran-US Tensions",
            "claim": "Military standoff with ceasefire deadline",
            "statement": "Iran warns fingers on trigger. US forces ready.",
        })
        idx.index_node("cu_2", "ContextUnit", {
            "name": "Ukraine-Russia War", "topic": "Ukraine-Russia War",
            "claim": "Ongoing conflict with drone strikes",
            "statement": "Russia fires missiles on Ukraine.",
        })
        results = idx.search_bm25("iran ceasefire", limit=5, label_filter="ContextUnit")
        assert len(results) >= 1
        assert results[0]["name"] == "Iran-US Tensions"
