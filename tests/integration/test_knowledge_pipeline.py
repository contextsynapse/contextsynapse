"""End-to-end test: schema-driven pipeline with knowledge schema.

Proves that a non-SDLC schema can be loaded, bound to a namespace,
and used for type validation — the foundation for domain-agnostic
Context as a Service.
"""
from pathlib import Path


def _real_schema_dir():
    return Path(__file__).resolve().parents[2] / "contextcore" / "project" / "schemas"


class TestKnowledgePipelineE2E:
    def test_schema_binds_to_namespace(self):
        """SchemaManager loads knowledge.yaml and binds to a namespace."""
        from contextcore.project.schema_manager import SchemaManager

        mgr = SchemaManager(schema_dir=_real_schema_dir())
        mgr.load_all()
        mgr.bind("test-kb", "knowledge")

        schema = mgr.for_namespace("test-kb")
        assert schema is not None
        assert schema.meta.name == "knowledge"
        assert schema.is_valid_node_type("Document") is True
        assert schema.is_valid_node_type("CodeModule") is False

    def test_schema_validates_correct_types(self):
        """Knowledge schema accepts Document/Fact/Entity, rejects SDLC types."""
        from contextcore.project.schema_manager import SchemaManager

        mgr = SchemaManager(schema_dir=_real_schema_dir())
        mgr.load_all()
        mgr.bind("test-kb", "knowledge")

        # Valid knowledge types
        assert mgr.is_valid_node_type("test-kb", "Document") is True
        assert mgr.is_valid_node_type("test-kb", "Fact") is True
        assert mgr.is_valid_node_type("test-kb", "Entity") is True
        assert mgr.is_valid_node_type("test-kb", "Concept") is True

        # Invalid (SDLC types)
        assert mgr.is_valid_node_type("test-kb", "CodeModule") is False
        assert mgr.is_valid_node_type("test-kb", "Requirement") is False
        assert mgr.is_valid_node_type("test-kb", "TestCase") is False

        # Valid knowledge edges
        assert mgr.is_valid_edge_type("test-kb", "STATES") is True
        assert mgr.is_valid_edge_type("test-kb", "MENTIONS") is True

        # Invalid (SDLC edges)
        assert mgr.is_valid_edge_type("test-kb", "IMPLEMENTS") is False
        assert mgr.is_valid_edge_type("test-kb", "TESTS") is False

    def test_consumer_hints_available(self):
        """Knowledge schema provides consumer hints."""
        from contextcore.project.schema_manager import SchemaManager

        mgr = SchemaManager(schema_dir=_real_schema_dir())
        mgr.load_all()
        mgr.bind("test-kb", "knowledge")

        labels = mgr.get_consumer_hint("test-kb", "context_filter_labels", None)
        assert labels is not None
        assert "Document" in labels
        assert "Fact" in labels

        promo = mgr.get_consumer_hint("test-kb", "promotion_labels", None)
        assert promo is not None

    def test_sdlc_still_works_alongside_knowledge(self):
        """Both schemas coexist — SDLC and knowledge bound to different namespaces."""
        from contextcore.project.schema_manager import SchemaManager

        mgr = SchemaManager(schema_dir=_real_schema_dir())
        mgr.load_all()
        mgr.bind("my-project", "sdlc")
        mgr.bind("my-kb", "knowledge")

        # SDLC namespace accepts SDLC types
        assert mgr.is_valid_node_type("my-project", "CodeModule") is True
        assert mgr.is_valid_node_type("my-project", "Document") is False

        # Knowledge namespace accepts knowledge types
        assert mgr.is_valid_node_type("my-kb", "Document") is True
        assert mgr.is_valid_node_type("my-kb", "CodeModule") is False

        # Unbound namespace accepts anything
        assert mgr.is_valid_node_type("random", "Anything") is True

    def test_unbound_namespace_is_fully_open(self):
        """No schema = accept everything."""
        from contextcore.project.schema_manager import SchemaManager

        mgr = SchemaManager(schema_dir=_real_schema_dir())
        mgr.load_all()

        assert mgr.is_valid_node_type("no-schema", "LiterallyAnything") is True
        assert mgr.is_valid_edge_type("no-schema", "MADE_UP_EDGE") is True
        assert mgr.get_consumer_hint("no-schema", "key", "default") == "default"
        assert mgr.get_layers("no-schema") is None
