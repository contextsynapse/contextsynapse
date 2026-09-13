"""Tests for ScannerRegistry — domain-specific scanner plugin mechanism."""
import pytest
import logging


class TestScannerRegistry:
    def test_register_and_resolve(self):
        from contextcore.project.scanner_registry import ScannerRegistry
        from contextcore.project.schema_manager import Schema

        class FakeScanner:
            name = "fake_scanner"

        registry = ScannerRegistry()
        registry.register("fake_scanner", FakeScanner)

        schema_data = {
            "meta": {"name": "test", "version": "1.0", "description": "Test"},
            "node_types": {"Doc": {"description": "doc", "properties": {}}},
            "relationships": {"R": {"source": "Doc", "target": "Doc", "description": "r"}},
            "scanners": {"fake_scanner": {"description": "A test scanner"}},
        }
        schema = Schema.from_dict(schema_data)
        scanners = registry.resolve(schema)
        assert len(scanners) == 1
        assert isinstance(scanners[0], FakeScanner)

    def test_resolve_warns_for_missing_scanner(self, caplog):
        from contextcore.project.scanner_registry import ScannerRegistry
        from contextcore.project.schema_manager import Schema

        schema_data = {
            "meta": {"name": "test", "version": "1.0", "description": "Test"},
            "node_types": {"Doc": {"description": "doc", "properties": {}}},
            "relationships": {"R": {"source": "Doc", "target": "Doc", "description": "r"}},
            "scanners": {"nonexistent_scanner": {"description": "Missing"}},
        }
        schema = Schema.from_dict(schema_data)
        registry = ScannerRegistry()
        with caplog.at_level(logging.WARNING):
            scanners = registry.resolve(schema)
        assert len(scanners) == 0
        assert "nonexistent_scanner" in caplog.text

    def test_resolve_returns_empty_when_no_scanners_section(self):
        from contextcore.project.scanner_registry import ScannerRegistry
        from contextcore.project.schema_manager import Schema

        schema_data = {
            "meta": {"name": "test", "version": "1.0", "description": "Test"},
            "node_types": {"Doc": {"description": "doc", "properties": {}}},
            "relationships": {"R": {"source": "Doc", "target": "Doc", "description": "r"}},
        }
        schema = Schema.from_dict(schema_data)
        registry = ScannerRegistry()
        assert registry.resolve(schema) == []

    def test_resolve_returns_empty_for_none_schema(self):
        from contextcore.project.scanner_registry import ScannerRegistry

        registry = ScannerRegistry()
        assert registry.resolve(None) == []
