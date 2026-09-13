"""Tests for Stock Price connector (yfinance)."""
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest
from datetime import datetime

from contextcore.connectors.base import ConnectorConfig
from contextcore.connectors.financial.stock_price import StockPriceConnector


@pytest.fixture
def price_config():
    return ConnectorConfig(
        connector_id="nse_1",
        connector_type="stock_price",
        name="NSE Price Feed",
        target_context_id="stock_prices",
        poll_interval_minutes=1,
        config={
            "symbols": ["TCS.NS", "INFY.NS", "RELIANCE.NS"],
            "period": "1d",
            "interval": "1d",
            "exchange": "NSE",
        },
    )


@pytest.fixture
def mock_yfinance():
    """Mock yfinance.download to avoid network calls."""
    with patch("contextcore.connectors.financial.stock_price.yf") as mock_yf:
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame({
            "Open": [3450.0],
            "High": [3495.0],
            "Low": [3440.0],
            "Close": [3480.0],
            "Volume": [1250000],
        }, index=pd.DatetimeIndex([datetime(2026, 9, 1)]))
        mock_yf.Ticker.return_value = mock_ticker
        yield mock_yf


class TestStockPriceConnector:

    def test_poll_returns_documents(self, price_config, mock_yfinance):
        connector = StockPriceConnector(price_config)
        docs = connector.poll()
        assert len(docs) == 3
        assert docs[0].doc_type == "price_update"
        assert docs[0].source == "yfinance"
        assert docs[0].metadata["symbol"] == "TCS.NS"
        assert docs[0].metadata["close"] == 3480.0
        assert docs[0].metadata["volume"] == 1250000
        assert "price" in docs[0].tags

    def test_poll_skips_empty_data(self, price_config, mock_yfinance):
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame()
        mock_yfinance.Ticker.return_value = mock_ticker

        connector = StockPriceConnector(price_config)
        docs = connector.poll()
        assert len(docs) == 0

    def test_poll_handles_api_error(self, price_config, mock_yfinance):
        mock_yfinance.Ticker.side_effect = Exception("API error")
        connector = StockPriceConnector(price_config)
        docs = connector.poll()
        assert len(docs) == 0

    def test_fetch_price_single(self, price_config, mock_yfinance):
        connector = StockPriceConnector(price_config)
        result = connector.fetch_price("TCS.NS")
        assert result is not None
        assert result["close"] == 3480.0
        assert result["open"] == 3450.0

    def test_health_check_success(self, price_config, mock_yfinance):
        connector = StockPriceConnector(price_config)
        assert connector.health_check() is True

    def test_health_check_failure(self, price_config, mock_yfinance):
        mock_yfinance.Ticker.side_effect = Exception("Network error")
        connector = StockPriceConnector(price_config)
        assert connector.health_check() is False

    def test_document_content_readable(self, price_config, mock_yfinance):
        connector = StockPriceConnector(price_config)
        docs = connector.poll()
        doc = docs[0]
        assert "TCS.NS" in doc.content
        assert "3480" in doc.content
        assert doc.metadata["exchange"] == "NSE"
