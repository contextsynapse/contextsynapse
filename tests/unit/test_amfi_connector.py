"""Tests for AMFI NAV connector."""
from unittest.mock import MagicMock, patch
import pytest

from contextcore.connectors.base import ConnectorConfig
from contextcore.connectors.financial.amfi_nav import AmfiNavConnector


@pytest.fixture
def amfi_config():
    return ConnectorConfig(
        connector_id="amfi_1",
        connector_type="amfi_nav",
        name="AMFI Daily NAV",
        target_context_id="fund_nav",
        poll_interval_minutes=1440,
        config={
            "scheme_codes": ["119551", "120503", "118989"],
        },
    )


@pytest.fixture
def mock_mftool():
    """Mock mftool.Mftool to avoid network calls."""
    with patch("contextcore.connectors.financial.amfi_nav.Mftool") as MockMf:
        instance = MockMf.return_value
        instance.get_scheme_details.side_effect = lambda code: {
            "119551": {
                "scheme_code": "119551",
                "scheme_name": "HDFC Top 100 Fund - Growth",
                "net_asset_value": "856.432",
                "scheme_type": "Open Ended Schemes",
                "scheme_category": "Equity Scheme - Large Cap Fund",
                "fund_house": "HDFC Mutual Fund",
            },
            "120503": {
                "scheme_code": "120503",
                "scheme_name": "ICICI Prudential Bluechip Fund - Growth",
                "net_asset_value": "78.21",
                "scheme_type": "Open Ended Schemes",
                "scheme_category": "Equity Scheme - Large Cap Fund",
                "fund_house": "ICICI Prudential Mutual Fund",
            },
            "118989": {
                "scheme_code": "118989",
                "scheme_name": "SBI Blue Chip Fund - Growth",
                "net_asset_value": "65.89",
                "scheme_type": "Open Ended Schemes",
                "scheme_category": "Equity Scheme - Large Cap Fund",
                "fund_house": "SBI Mutual Fund",
            },
        }.get(str(code))
        yield instance


class TestAmfiNavConnector:

    def test_poll_returns_documents(self, amfi_config, mock_mftool):
        connector = AmfiNavConnector(amfi_config)
        docs = connector.poll()
        assert len(docs) == 3
        assert docs[0].title == "HDFC Top 100 Fund - Growth"
        assert docs[0].metadata["nav_value"] == "856.432"
        assert docs[0].metadata["scheme_code"] == "119551"
        assert docs[0].metadata["fund_house"] == "HDFC Mutual Fund"
        assert docs[0].doc_type == "nav_update"
        assert docs[0].source == "amfi"

    def test_poll_skips_failed_schemes(self, amfi_config, mock_mftool):
        mock_mftool.get_scheme_details.side_effect = [
            {"scheme_code": "119551", "scheme_name": "HDFC",
             "net_asset_value": "856.432", "fund_house": "HDFC MF"},
            None,  # scheme 120503 fails
            {"scheme_code": "118989", "scheme_name": "SBI",
             "net_asset_value": "65.89", "fund_house": "SBI MF"},
        ]
        connector = AmfiNavConnector(amfi_config)
        docs = connector.poll()
        assert len(docs) == 2

    def test_poll_deduplicates(self, amfi_config, mock_mftool):
        connector = AmfiNavConnector(amfi_config)
        docs1 = connector.poll()
        assert len(docs1) == 3
        # Second poll returns same data — should dedup to 0
        docs2 = connector.poll()
        assert len(docs2) == 0

    def test_fetch_nav_single(self, amfi_config, mock_mftool):
        connector = AmfiNavConnector(amfi_config)
        nav = connector.fetch_nav("119551")
        assert nav is not None
        assert nav["net_asset_value"] == "856.432"

    def test_fetch_nav_unknown_scheme(self, amfi_config, mock_mftool):
        mock_mftool.get_scheme_details.return_value = None
        connector = AmfiNavConnector(amfi_config)
        nav = connector.fetch_nav("999999")
        assert nav is None

    def test_health_check(self, amfi_config, mock_mftool):
        connector = AmfiNavConnector(amfi_config)
        assert connector.health_check() is True

    def test_health_check_failure(self, amfi_config, mock_mftool):
        mock_mftool.get_scheme_details.side_effect = Exception("Network error")
        connector = AmfiNavConnector(amfi_config)
        assert connector.health_check() is False

    def test_document_content_format(self, amfi_config, mock_mftool):
        connector = AmfiNavConnector(amfi_config)
        docs = connector.poll()
        doc = docs[0]
        assert "HDFC Top 100 Fund" in doc.content
        assert "856.432" in doc.content
        assert "amfi" in doc.tags
        assert "nav" in doc.tags

    def test_empty_scheme_codes_polls_nothing(self, mock_mftool):
        config = ConnectorConfig(
            connector_id="amfi_empty",
            connector_type="amfi_nav",
            name="AMFI Empty",
            config={"scheme_codes": []},
        )
        connector = AmfiNavConnector(config)
        docs = connector.poll()
        assert len(docs) == 0
