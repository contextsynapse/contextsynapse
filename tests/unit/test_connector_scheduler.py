"""Tests for ConnectorScheduler — runs connectors on their configured intervals."""
import time
import pytest
from unittest.mock import MagicMock

from contextcore.connectors.base import BaseConnector, ConnectorConfig, ConnectorDocument, ConnectorManager
from contextcore.connectors.registry import ConnectorRegistry
from contextcore.connectors.scheduler import ConnectorScheduler


class FakeConnector(BaseConnector):
    def __init__(self, config):
        super().__init__(config)
        self.poll_count = 0

    def poll(self):
        self.poll_count += 1
        return [ConnectorDocument(title=f"doc_{self.poll_count}", content="test")]

    def health_check(self):
        return True


@pytest.fixture
def setup():
    registry = ConnectorRegistry()
    registry.register_connector_class("fake", FakeConnector)
    manager = ConnectorManager()

    # Register two connectors with different intervals
    c1_config = ConnectorConfig(
        connector_id="c1", connector_type="fake", name="fast",
        poll_interval_minutes=0,  # always due
    )
    c2_config = ConnectorConfig(
        connector_id="c2", connector_type="fake", name="slow",
        poll_interval_minutes=9999,  # never due
    )
    c1 = registry.create_connector(c1_config)
    c2 = registry.create_connector(c2_config)
    manager.register(c1)
    manager.register(c2)

    scheduler = ConnectorScheduler(registry, manager)
    return scheduler, manager


class TestRunDue:

    def test_runs_due_connectors(self, setup):
        scheduler, manager = setup
        results = scheduler.run_due()
        assert "c1" in results
        assert results["c1"] >= 1  # fast connector ran
        # slow connector should not have run
        assert results.get("c2", 0) == 0

    def test_skips_not_due(self, setup):
        scheduler, manager = setup
        # Run once
        scheduler.run_due()
        # Mark c1 as just run with a long interval
        scheduler._last_run["c1"] = time.time()
        scheduler._intervals["c1"] = 9999 * 60  # 9999 minutes
        results = scheduler.run_due()
        assert results.get("c1", 0) == 0


class TestRunAll:

    def test_runs_all_regardless(self, setup):
        scheduler, manager = setup
        results = scheduler.run_all()
        assert "c1" in results
        assert "c2" in results
        assert results["c1"] >= 1
        assert results["c2"] >= 1


class TestStatus:

    def test_get_status(self, setup):
        scheduler, manager = setup
        scheduler.run_all()
        status = scheduler.get_status()
        assert len(status) >= 2
        names = [s["name"] for s in status]
        assert "fast" in names or "c1" in [s["connector_id"] for s in status]
