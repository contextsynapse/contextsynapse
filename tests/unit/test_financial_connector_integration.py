"""Integration test: YAML config → Registry → ConnectorManager → poll."""
import os
import tempfile
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest
import yaml
from datetime import datetime

from contextcore.connectors.registry import ConnectorRegistry
from contextcore.connectors.base import ConnectorManager
from contextcore.connectors.financial.amfi_nav import AmfiNavConnector
from contextcore.connectors.financial.stock_price import StockPriceConnector


@pytest.fixture
def mock_mftool():
    with patch("contextcore.connectors.financial.amfi_nav.Mftool") as MockMf:
        instance = MockMf.return_value
        instance.get_scheme_details.return_value = {
            "scheme_code": "119551",
            "scheme_name": "HDFC Top 100",
            "net_asset_value": "856.43",
            "fund_house": "HDFC MF",
            "scheme_category": "Large Cap",
            "scheme_type": "Open Ended",
        }
        yield instance


@pytest.fixture
def mock_yfinance():
    with patch("contextcore.connectors.financial.stock_price.yf") as mock_yf:
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame({
            "Open": [3450.0], "High": [3495.0],
            "Low": [3440.0], "Close": [3480.0], "Volume": [1250000],
        }, index=pd.DatetimeIndex([datetime(2026, 9, 1)]))
        mock_yf.Ticker.return_value = mock_ticker
        yield mock_yf


class TestFinancialConnectorIntegration:

    def test_yaml_to_registry_to_manager(self, mock_mftool, mock_yfinance):
        """Full flow: YAML configs → Registry → ConnectorManager → poll."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write AMFI config
            amfi_cfg = {
                "name": "amfi_test",
                "connector_type": "amfi_nav",
                "target_context_id": "fund_nav",
                "config": {"scheme_codes": ["119551"]},
            }
            with open(os.path.join(tmpdir, "amfi.yaml"), "w") as f:
                yaml.dump(amfi_cfg, f)

            # Write stock config
            stock_cfg = {
                "name": "nse_test",
                "connector_type": "stock_price",
                "target_context_id": "stock_prices",
                "config": {"symbols": ["TCS.NS"], "exchange": "NSE"},
            }
            with open(os.path.join(tmpdir, "nse.yaml"), "w") as f:
                yaml.dump(stock_cfg, f)

            # Load registry
            registry = ConnectorRegistry()
            registry.register_connector_class("amfi_nav", AmfiNavConnector)
            registry.register_connector_class("stock_price", StockPriceConnector)
            count = registry.load_from_directory(tmpdir)
            assert count == 2

            # Create connectors
            connectors = registry.create_all()
            assert len(connectors) == 2

            # Register with manager
            manager = ConnectorManager()
            for c in connectors:
                manager.register(c)

            # Poll all
            results = manager.poll_all()
            assert len(results) == 2
            # Each connector should have returned documents
            total_docs = sum(results.values())
            assert total_docs >= 2  # at least 1 NAV + 1 price

    def test_registry_discovers_financial_connectors(self):
        """Verify both financial connector classes can be registered."""
        registry = ConnectorRegistry()
        registry.register_connector_class("amfi_nav", AmfiNavConnector)
        registry.register_connector_class("stock_price", StockPriceConnector)

        assert registry.get_connector_class("amfi_nav") is AmfiNavConnector
        assert registry.get_connector_class("stock_price") is StockPriceConnector

    def test_real_config_files_load(self, mock_mftool, mock_yfinance):
        """Test that the actual config/connectors/ YAML files load correctly."""
        config_dir = os.path.join(
            os.path.dirname(__file__), "..", "..", "contextcore", "config", "connectors"
        )
        if not os.path.isdir(config_dir):
            pytest.skip("config/connectors/ directory not found")

        registry = ConnectorRegistry()
        registry.register_connector_class("amfi_nav", AmfiNavConnector)
        registry.register_connector_class("stock_price", StockPriceConnector)
        count = registry.load_from_directory(config_dir)
        assert count >= 2  # at least amfi_nav.yaml and nse_price.yaml
