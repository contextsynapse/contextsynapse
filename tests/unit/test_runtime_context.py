"""Tests for Runtime Context Assembler."""
import pytest


class TestRuntimeContextAssembler:

    def _setup_contexts(self):
        """Create two atomic contexts with different data."""
        from contextcore import AIContextDB
        from contextcore.ingestion.smart_ingest import ingest_text
        from contextcore.core.registry import GraphRegistry

        registry = GraphRegistry()

        # Atomic context 1: TCS
        tcs_db = registry.create_graph("tcs")
        ingest_text(
            "TCS reported Q2 revenue of $25.5 billion beating expectations. "
            "CEO praised strong deal pipeline in Europe.",
            tcs_db, title="TCS Q2", context_purpose="TCS financial performance",
        )

        # Atomic context 2: India Macro
        macro_db = registry.create_graph("india_economy")
        ingest_text(
            "RBI held repo rate steady at 6.5% citing stable inflation. "
            "India GDP growth projected at 7.2% for FY27.",
            macro_db, title="India Macro", context_purpose="India macroeconomic indicators",
        )

        return registry

    def test_assemble_single_context(self):
        from contextcore.intelligence.runtime_context import RuntimeContextAssembler

        registry = self._setup_contexts()
        assembler = RuntimeContextAssembler(graph_registry=registry)

        view = assembler.assemble(contexts=["tcs"])
        assert len(view.entities) > 0
        assert all(e.source_context == "tcs" for e in view.entities)

    def test_assemble_multiple_contexts(self):
        from contextcore.intelligence.runtime_context import RuntimeContextAssembler

        registry = self._setup_contexts()
        assembler = RuntimeContextAssembler(graph_registry=registry)

        view = assembler.assemble(contexts=["tcs", "india_economy"])

        # Should have entities from both contexts
        sources = {e.source_context for e in view.entities}
        assert "tcs" in sources
        assert "india_economy" in sources
        assert view.stats["contexts_queried"] == 2

    def test_sentiment_comparison(self):
        from contextcore.intelligence.runtime_context import RuntimeContextAssembler

        registry = self._setup_contexts()
        assembler = RuntimeContextAssembler(graph_registry=registry)

        view = assembler.assemble(contexts=["tcs", "india_economy"])

        # Should have sentiment comparison for entities
        assert isinstance(view.sentiment_comparison, dict)

    def test_facts_tagged_with_source(self):
        from contextcore.intelligence.runtime_context import RuntimeContextAssembler

        registry = self._setup_contexts()
        assembler = RuntimeContextAssembler(graph_registry=registry)

        view = assembler.assemble(contexts=["tcs", "india_economy"])

        for fact in view.facts:
            assert fact.source_context in ("tcs", "india_economy")

    def test_focus_entity(self):
        from contextcore.intelligence.runtime_context import RuntimeContextAssembler

        registry = self._setup_contexts()
        assembler = RuntimeContextAssembler(graph_registry=registry)

        view = assembler.assemble(contexts=["tcs"], focus_entity="TCS")
        assert view.focus_entity == "TCS"

        # TCS entities should have boosted relevance
        tcs_entities = [e for e in view.entities if "tcs" in e.name.lower()]
        for e in tcs_entities:
            assert e.relevance >= 0.9

    def test_cache(self):
        from contextcore.intelligence.runtime_context import RuntimeContextAssembler

        registry = self._setup_contexts()
        assembler = RuntimeContextAssembler(graph_registry=registry)

        view1 = assembler.assemble(contexts=["tcs"])
        view2 = assembler.assemble(contexts=["tcs"])

        # Should be the same cached object
        assert view1.assembled_at == view2.assembled_at

    def test_invalidate_cache(self):
        from contextcore.intelligence.runtime_context import RuntimeContextAssembler

        registry = self._setup_contexts()
        assembler = RuntimeContextAssembler(graph_registry=registry)

        assembler.assemble(contexts=["tcs"])
        assert len(assembler._cache) == 1

        assembler.invalidate_cache("tcs")
        assert len(assembler._cache) == 0

    def test_nonexistent_context(self):
        from contextcore.intelligence.runtime_context import RuntimeContextAssembler
        from contextcore.core.registry import GraphRegistry

        assembler = RuntimeContextAssembler(graph_registry=GraphRegistry())
        view = assembler.assemble(contexts=["nonexistent"])

        assert view.stats["total_entities"] == 0

    def test_cross_context_entities(self):
        from contextcore.intelligence.runtime_context import RuntimeContextAssembler

        registry = self._setup_contexts()
        assembler = RuntimeContextAssembler(graph_registry=registry)

        view = assembler.assemble(contexts=["tcs", "india_economy"])

        # cross_entity_pairs lists entities found in multiple contexts
        assert isinstance(view.cross_entity_pairs, list)

    def test_to_dict(self):
        from contextcore.intelligence.runtime_context import RuntimeContextAssembler

        registry = self._setup_contexts()
        assembler = RuntimeContextAssembler(graph_registry=registry)

        view = assembler.assemble(contexts=["tcs"])
        d = view.to_dict()

        assert "contexts" in d
        assert "entities" in d
        assert "facts" in d
        assert "stats" in d
        assert "assembled_at" in d
