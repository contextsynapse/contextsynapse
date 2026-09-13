"""Tests for YAML-driven ConnectorRegistry."""
import os
import tempfile
import pytest
import yaml

from contextcore.connectors.registry import ConnectorRegistry
from contextcore.connectors.base import BaseConnector, ConnectorConfig, ConnectorDocument


class FakeConnector(BaseConnector):
    """Test connector that returns canned documents."""
    def poll(self):
        return [ConnectorDocument(title="fake", content="fake content")]

    def health_check(self):
        return True


class TestConnectorRegistry:

    def test_register_connector_class(self):
        reg = ConnectorRegistry()
        reg.register_connector_class("fake", FakeConnector)
        cls = reg.get_connector_class("fake")
        assert cls is FakeConnector

    def test_get_unknown_class_raises(self):
        reg = ConnectorRegistry()
        with pytest.raises(KeyError, match="Unknown connector type"):
            reg.get_connector_class("nonexistent")

    def test_load_from_yaml(self):
        config_data = {
            "name": "test_connector",
            "connector_type": "fake",
            "target_context_id": "ctx_test",
            "poll_interval_minutes": 10,
            "active": True,
            "config": {"url": "https://example.com"},
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            yaml.dump(config_data, f)
            tmp_path = f.name
        # File closed before reading — required on Windows
        try:
            reg = ConnectorRegistry()
            loaded = reg.load_from_yaml(tmp_path)
            assert loaded.name == "test_connector"
            assert loaded.connector_type == "fake"
            assert loaded.poll_interval_minutes == 10
            assert loaded.config["url"] == "https://example.com"
        finally:
            os.unlink(tmp_path)

    def test_load_from_yaml_missing_name_raises(self):
        config_data = {"connector_type": "fake"}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            yaml.dump(config_data, f)
            tmp_path = f.name
        # File closed before reading — required on Windows
        try:
            reg = ConnectorRegistry()
            with pytest.raises(ValueError, match="name"):
                reg.load_from_yaml(tmp_path)
        finally:
            os.unlink(tmp_path)

    def test_load_from_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            for i in range(3):
                cfg = {
                    "name": f"conn_{i}",
                    "connector_type": "fake",
                    "target_context_id": f"ctx_{i}",
                    "active": True,
                }
                with open(os.path.join(tmpdir, f"conn_{i}.yaml"), "w") as f:
                    yaml.dump(cfg, f)

            reg = ConnectorRegistry()
            count = reg.load_from_directory(tmpdir)
            assert count == 3
            assert len(reg.configs) == 3

    def test_load_from_directory_skips_invalid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Valid config
            with open(os.path.join(tmpdir, "good.yaml"), "w") as f:
                yaml.dump({"name": "good", "connector_type": "fake",
                           "target_context_id": "ctx"}, f)
            # Invalid config (missing name)
            with open(os.path.join(tmpdir, "bad.yaml"), "w") as f:
                yaml.dump({"connector_type": "fake"}, f)

            reg = ConnectorRegistry()
            count = reg.load_from_directory(tmpdir)
            assert count == 1

    def test_create_connector(self):
        reg = ConnectorRegistry()
        reg.register_connector_class("fake", FakeConnector)

        config = ConnectorConfig(
            connector_id="test_1",
            connector_type="fake",
            name="test",
            target_context_id="ctx_test",
        )
        connector = reg.create_connector(config)
        assert isinstance(connector, FakeConnector)
        docs = connector.poll()
        assert len(docs) == 1
        assert docs[0].title == "fake"

    def test_create_all(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            for i in range(2):
                cfg = {
                    "name": f"conn_{i}",
                    "connector_type": "fake",
                    "target_context_id": f"ctx_{i}",
                    "active": True,
                }
                with open(os.path.join(tmpdir, f"conn_{i}.yaml"), "w") as f:
                    yaml.dump(cfg, f)

            reg = ConnectorRegistry()
            reg.register_connector_class("fake", FakeConnector)
            reg.load_from_directory(tmpdir)
            connectors = reg.create_all()
            assert len(connectors) == 2
            assert all(isinstance(c, FakeConnector) for c in connectors)

    def test_non_yaml_files_ignored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "readme.txt"), "w") as f:
                f.write("not a config")
            with open(os.path.join(tmpdir, "good.yaml"), "w") as f:
                yaml.dump({"name": "g", "connector_type": "fake",
                           "target_context_id": "ctx"}, f)

            reg = ConnectorRegistry()
            count = reg.load_from_directory(tmpdir)
            assert count == 1
