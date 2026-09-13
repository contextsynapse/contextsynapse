"""Table Enricher — extract structured Indicator nodes from tabular data.

When tables are detected in documents (PDF, HTML, CSV), this module:
1. Preserves the full table structure as a TableNode
2. Extracts numeric cells as Indicator nodes (name, value, unit, period, entity)
3. Links Indicators to entities and geographic nodes via the intelligence tagger

Usage:
    from contextsynapse.intelligence.table_enricher import enrich_table

    indicators, edges = enrich_table(
        headers=["Quarter", "Revenue", "Growth"],
        rows=[["Q1 2026", "$24.3B", "12%"], ["Q2 2026", "$25.5B", "15%"]],
        source_entity="Tesla",
        table_title="Quarterly Revenue",
    )
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import uuid

logger = logging.getLogger(__name__)


@dataclass
class IndicatorNode:
    """A structured numeric data point extracted from a table cell."""
    id: str = ""
    name: str = ""            # e.g., "Revenue"
    value: float = 0.0        # numeric value
    raw_value: str = ""       # original cell text (e.g., "$25.5B")
    unit: str = ""            # USD, %, count, etc.
    period: str = ""          # Q1 2026, FY2025, etc.
    entity: str = ""          # which entity this indicator belongs to
    column_header: str = ""   # original column name
    row_label: str = ""       # original row label

    def __post_init__(self):
        if not self.id:
            self.id = f"ind_{uuid.uuid4().hex[:10]}"

    def to_node_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": "Indicator",
            "properties": {
                "name": self.name,
                "value": self.value,
                "raw_value": self.raw_value,
                "unit": self.unit,
                "period": self.period,
                "entity": self.entity,
                "column_header": self.column_header,
                "row_label": self.row_label,
                "source_type": "table_extraction",
                "confidence": 0.9,
            },
        }


# ── Value parsing patterns ──

_MONEY_PATTERN = re.compile(
    r'^\s*\$?\s*([\d,.]+)\s*(B|billion|M|million|K|thousand|T|trillion)?\s*$',
    re.IGNORECASE,
)

_PERCENT_PATTERN = re.compile(
    r'^\s*([+-]?\s*[\d,.]+)\s*%\s*$',
)

_NUMBER_PATTERN = re.compile(
    r'^\s*([+-]?\s*[\d,.]+)\s*$',
)

_PERIOD_PATTERN = re.compile(
    r'(?:Q[1-4]\s*(?:\'?\d{2,4}|20\d{2}|19\d{2}))|'
    r'(?:(?:FY|CY|H[12])\s*(?:\'?\d{2,4}|20\d{2}))|'
    r'(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+20\d{2})|'
    r'(?:20\d{2})',
    re.IGNORECASE,
)

# Column name patterns that indicate time periods
_PERIOD_COLUMN_HINTS = re.compile(
    r'(?:quarter|period|date|year|month|fiscal|FY|Q[1-4])',
    re.IGNORECASE,
)

# Column name patterns that indicate entity/label (not numeric)
_LABEL_COLUMN_HINTS = re.compile(
    r'(?:name|company|entity|ticker|symbol|country|region|category|sector|label|description|item)',
    re.IGNORECASE,
)

_MULTIPLIERS = {
    "b": 1e9, "billion": 1e9,
    "m": 1e6, "million": 1e6,
    "k": 1e3, "thousand": 1e3,
    "t": 1e12, "trillion": 1e12,
}


def _parse_numeric(cell: str) -> Optional[Tuple[float, str]]:
    """Parse a cell value into (number, unit). Returns None if not numeric."""
    if not cell or not cell.strip():
        return None

    cell = cell.strip()

    # Money: $25.5B, $1,234, $500M
    m = _MONEY_PATTERN.match(cell)
    if m:
        num_str = m.group(1).replace(",", "")
        multiplier_str = (m.group(2) or "").lower()
        try:
            value = float(num_str)
            if multiplier_str and multiplier_str in _MULTIPLIERS:
                value *= _MULTIPLIERS[multiplier_str]
            return (value, "USD")
        except ValueError:
            pass

    # Percentage: 15%, -3.2%, +12%
    m = _PERCENT_PATTERN.match(cell)
    if m:
        try:
            return (float(m.group(1).replace(",", "").replace(" ", "")), "%")
        except ValueError:
            pass

    # Plain number: 1,234 or 56.7
    m = _NUMBER_PATTERN.match(cell)
    if m:
        try:
            return (float(m.group(1).replace(",", "").replace(" ", "")), "")
        except ValueError:
            pass

    return None


def _detect_period(row: List[str], headers: List[str]) -> str:
    """Try to find a time period in the row or headers."""
    # Check row cells first
    for cell in row:
        m = _PERIOD_PATTERN.search(str(cell))
        if m:
            return m.group(0)

    # Check if any header looks like a period
    for h in headers:
        m = _PERIOD_PATTERN.search(str(h))
        if m:
            return m.group(0)

    return ""


def _detect_entity(row: List[str], headers: List[str]) -> str:
    """Try to find an entity name in the row (from label columns)."""
    for i, h in enumerate(headers):
        if _LABEL_COLUMN_HINTS.search(h) and i < len(row):
            val = str(row[i]).strip()
            if val and _parse_numeric(val) is None:
                return val
    # First non-numeric cell as fallback
    for cell in row:
        val = str(cell).strip()
        if val and _parse_numeric(val) is None and not _PERIOD_PATTERN.search(val):
            return val
    return ""


def enrich_table(
    headers: List[str],
    rows: List[List[str]],
    source_entity: str = "",
    table_title: str = "",
) -> Tuple[List[IndicatorNode], List[Dict[str, str]]]:
    """Extract Indicator nodes from a table.

    Parameters
    ----------
    headers : list of column names
    rows : list of rows (each row is a list of cell strings)
    source_entity : entity name this table belongs to (e.g., "Tesla")
    table_title : title of the table

    Returns
    -------
    (indicators, edges) where edges are dicts with source/target/label
    """
    if not headers or not rows:
        return [], []

    indicators: List[IndicatorNode] = []
    edges: List[Dict[str, str]] = []

    # Identify which columns are numeric vs label vs period
    numeric_cols = set()
    period_cols = set()
    label_cols = set()

    for i, h in enumerate(headers):
        if _PERIOD_COLUMN_HINTS.search(h):
            period_cols.add(i)
        elif _LABEL_COLUMN_HINTS.search(h):
            label_cols.add(i)

    # Sample first few rows to detect numeric columns
    for row in rows[:5]:
        for i, cell in enumerate(row):
            if i < len(headers) and _parse_numeric(str(cell)) is not None:
                numeric_cols.add(i)

    for row in rows:
        row_strs = [str(c) for c in row]
        period = _detect_period(row_strs, headers)
        entity = _detect_entity(row_strs, headers) or source_entity

        for col_idx in numeric_cols:
            if col_idx >= len(row_strs):
                continue

            cell = row_strs[col_idx]
            parsed = _parse_numeric(cell)
            if parsed is None:
                continue

            value, unit = parsed
            col_name = headers[col_idx] if col_idx < len(headers) else f"Column_{col_idx}"

            # Build indicator name
            name = f"{entity} {col_name}" if entity else col_name
            if period:
                name = f"{name} ({period})"

            indicator = IndicatorNode(
                name=name,
                value=value,
                raw_value=cell.strip(),
                unit=unit,
                period=period,
                entity=entity,
                column_header=col_name,
                row_label=row_strs[0] if row_strs else "",
            )
            indicators.append(indicator)

    logger.info("[TABLE] Extracted %d indicators from table '%s'", len(indicators), table_title)
    return indicators, edges
