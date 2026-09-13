"""Tests for table enricher — Indicator extraction from tabular data."""
import pytest


class TestNumericParsing:
    """Parse cell values into structured numbers."""

    def test_parse_money(self):
        from contextcore.intelligence.table_enricher import _parse_numeric
        val, unit = _parse_numeric("$25.5B")
        assert val == 25.5e9
        assert unit == "USD"

    def test_parse_money_million(self):
        from contextcore.intelligence.table_enricher import _parse_numeric
        val, unit = _parse_numeric("$500M")
        assert val == 500e6
        assert unit == "USD"

    def test_parse_percentage(self):
        from contextcore.intelligence.table_enricher import _parse_numeric
        val, unit = _parse_numeric("15%")
        assert val == 15.0
        assert unit == "%"

    def test_parse_negative_percentage(self):
        from contextcore.intelligence.table_enricher import _parse_numeric
        val, unit = _parse_numeric("-3.2%")
        assert val == -3.2
        assert unit == "%"

    def test_parse_plain_number(self):
        from contextcore.intelligence.table_enricher import _parse_numeric
        val, unit = _parse_numeric("1,234")
        assert val == 1234.0

    def test_parse_non_numeric_returns_none(self):
        from contextcore.intelligence.table_enricher import _parse_numeric
        assert _parse_numeric("Tesla") is None
        assert _parse_numeric("Q2 2026") is None
        assert _parse_numeric("") is None


class TestTableEnrichment:
    """Extract Indicator nodes from table structure."""

    def test_financial_table(self):
        from contextcore.intelligence.table_enricher import enrich_table

        indicators, edges = enrich_table(
            headers=["Quarter", "Revenue", "Growth"],
            rows=[
                ["Q1 2026", "$24.3B", "12%"],
                ["Q2 2026", "$25.5B", "15%"],
            ],
            source_entity="Tesla",
            table_title="Quarterly Revenue",
        )

        assert len(indicators) == 4  # 2 rows × 2 numeric columns
        # Check first indicator
        rev_indicators = [i for i in indicators if "Revenue" in i.column_header]
        assert len(rev_indicators) == 2
        assert rev_indicators[0].value == 24.3e9
        assert rev_indicators[0].unit == "USD"
        assert "Q1 2026" in rev_indicators[0].period

    def test_entity_detection_from_label_column(self):
        from contextcore.intelligence.table_enricher import enrich_table

        indicators, _ = enrich_table(
            headers=["Company", "Revenue", "Employees"],
            rows=[
                ["Apple", "$394B", "164,000"],
                ["Google", "$307B", "182,000"],
            ],
            table_title="Tech Companies",
        )

        apple_indicators = [i for i in indicators if i.entity == "Apple"]
        google_indicators = [i for i in indicators if i.entity == "Google"]
        assert len(apple_indicators) >= 1
        assert len(google_indicators) >= 1

    def test_empty_table(self):
        from contextcore.intelligence.table_enricher import enrich_table

        indicators, edges = enrich_table(headers=[], rows=[], table_title="Empty")
        assert indicators == []
        assert edges == []

    def test_no_numeric_columns(self):
        from contextcore.intelligence.table_enricher import enrich_table

        indicators, _ = enrich_table(
            headers=["Name", "Country", "Status"],
            rows=[["Tesla", "US", "Active"], ["BYD", "China", "Active"]],
        )
        assert len(indicators) == 0

    def test_indicator_to_node_dict(self):
        from contextcore.intelligence.table_enricher import IndicatorNode

        ind = IndicatorNode(
            name="Tesla Revenue (Q2 2026)",
            value=25.5e9,
            raw_value="$25.5B",
            unit="USD",
            period="Q2 2026",
            entity="Tesla",
        )
        d = ind.to_node_dict()
        assert d["label"] == "Indicator"
        assert d["properties"]["value"] == 25.5e9
        assert d["properties"]["unit"] == "USD"
        assert d["properties"]["entity"] == "Tesla"

    def test_mixed_table_with_periods(self):
        from contextcore.intelligence.table_enricher import enrich_table

        indicators, _ = enrich_table(
            headers=["Metric", "Q1 2026", "Q2 2026"],
            rows=[
                ["Revenue", "$24.3B", "$25.5B"],
                ["Net Income", "$2.1B", "$2.5B"],
            ],
            source_entity="Tesla",
        )

        # Should extract indicators with period from column headers
        assert len(indicators) >= 4
        for ind in indicators:
            assert ind.entity == "Tesla" or ind.entity in ("Revenue", "Net Income")
