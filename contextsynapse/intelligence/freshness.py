"""Freshness Detection — extract event dates, detect stale content.

Every piece of ingested content gets:
  _ingested_at:  when we fetched it (system timestamp)
  _event_date:   when the event actually happened (extracted from content)
  _freshness:    fresh | recent | aging | stale | historical
  _staleness_days: how old the event is relative to ingestion

This prevents treating old news as current just because we ingested it today.

Usage:
    from contextsynapse.intelligence.freshness import detect_event_date, score_freshness

    event_date = detect_event_date("TCS reported Q4 2025 results on January 15, 2026")
    # → "2026-01-15"

    freshness = score_freshness(event_date, ingested_at="2026-08-31")
    # → {"freshness": "stale", "staleness_days": 228, "event_date": "2026-01-15"}
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class FreshnessResult:
    """Freshness assessment of a piece of content."""
    event_date: str = ""              # ISO date of the event (extracted)
    ingested_at: str = ""             # when we ingested it
    staleness_days: int = 0           # how old the event is
    freshness: str = "unknown"        # fresh | recent | aging | stale | historical
    confidence: float = 0.0           # confidence in event_date extraction
    evidence: str = ""                # the text that gave us the date

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_date": self.event_date,
            "ingested_at": self.ingested_at,
            "staleness_days": self.staleness_days,
            "freshness": self.freshness,
            "confidence": self.confidence,
            "evidence": self.evidence,
        }


# ── Date extraction patterns ──

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
    "oct": 10, "nov": 11, "dec": 12,
}

# "January 15, 2026" or "15 January 2026" or "Jan 15, 2026"
_FULL_DATE = re.compile(
    r'\b(\d{1,2})\s+(' + '|'.join(_MONTHS.keys()) + r')\s+(\d{4})\b'
    r'|'
    r'\b(' + '|'.join(_MONTHS.keys()) + r')\s+(\d{1,2}),?\s+(\d{4})\b',
    re.IGNORECASE,
)

# "Q1 2026", "Q4 FY25", "Q2 FY27"
_QUARTER = re.compile(
    r'\b(Q[1-4])\s*(?:FY)?[\'"]?(\d{2,4})\b',
    re.IGNORECASE,
)

# "FY27", "FY2027", "FY25-26"
_FISCAL_YEAR = re.compile(
    r'\bFY[\'"]?(\d{2,4})(?:\s*-\s*\d{2})?\b',
    re.IGNORECASE,
)

# "2026-08-15" ISO format
_ISO_DATE = re.compile(
    r'\b(20\d{2})-(\d{2})-(\d{2})\b',
)

# "August 2026" or "Aug 2026"
_MONTH_YEAR = re.compile(
    r'\b(' + '|'.join(_MONTHS.keys()) + r')\s+(\d{4})\b',
    re.IGNORECASE,
)

# Relative time markers
_RECENCY_MARKERS = re.compile(
    r'\b(today|yesterday|this\s+week|last\s+week|this\s+month|last\s+month|'
    r'this\s+quarter|last\s+quarter|earlier\s+today|this\s+morning)\b',
    re.IGNORECASE,
)

# Historical markers — content about past events
_HISTORICAL_MARKERS = re.compile(
    r'\b(last\s+year|previous\s+year|year\s+ago|years?\s+earlier|'
    r'in\s+20[12]\d|back\s+in|historically|decade)\b',
    re.IGNORECASE,
)


def _quarter_to_date(quarter: str, year_str: str) -> str:
    """Convert Q1 2026 to approximate ISO date."""
    q = int(quarter[1])
    year = int(year_str)
    if year < 100:
        year += 2000

    # FY convention: FY27 Q1 = Apr-Jun 2026
    # Calendar convention: Q1 = Jan-Mar
    # Default to calendar quarters
    month_map = {1: 2, 2: 5, 3: 8, 4: 11}  # mid-quarter
    month = month_map.get(q, 1)

    return f"{year}-{month:02d}-15"


def _fy_to_date(fy_str: str) -> str:
    """Convert FY27 to approximate ISO date (mid fiscal year)."""
    fy = int(fy_str)
    if fy < 100:
        fy += 2000
    # Indian FY: FY27 = April 2026 - March 2027
    # Use October as midpoint
    return f"{fy - 1}-10-01"


def detect_event_date(text: str) -> FreshnessResult:
    """Extract the most likely event date from content.

    Checks multiple patterns in priority order:
    1. Full dates (January 15, 2026)
    2. ISO dates (2026-08-15)
    3. Quarter references (Q1 FY27)
    4. Month-year (August 2026)
    5. Fiscal year (FY27)
    6. Relative markers (today, this week)

    Returns FreshnessResult with event_date and confidence.
    """
    if not text:
        return FreshnessResult()

    text_sample = text[:2000]
    now = datetime.now(timezone.utc)

    # 1. ISO date
    m = _ISO_DATE.search(text_sample)
    if m:
        return FreshnessResult(
            event_date=f"{m.group(1)}-{m.group(2)}-{m.group(3)}",
            confidence=0.95,
            evidence=m.group(0),
        )

    # 2. Full date (January 15, 2026 or 15 January 2026)
    m = _FULL_DATE.search(text_sample)
    if m:
        if m.group(1):  # day month year
            day = int(m.group(1))
            month = _MONTHS.get(m.group(2).lower(), 1)
            year = int(m.group(3))
        else:  # month day year
            month = _MONTHS.get(m.group(4).lower(), 1)
            day = int(m.group(5))
            year = int(m.group(6))
        try:
            dt = datetime(year, month, min(day, 28))
            return FreshnessResult(
                event_date=dt.strftime("%Y-%m-%d"),
                confidence=0.9,
                evidence=m.group(0),
            )
        except ValueError:
            pass

    # 3. Quarter (Q1 FY27, Q2 2026)
    m = _QUARTER.search(text_sample)
    if m:
        return FreshnessResult(
            event_date=_quarter_to_date(m.group(1), m.group(2)),
            confidence=0.7,
            evidence=m.group(0),
        )

    # 4. Month-year (August 2026)
    m = _MONTH_YEAR.search(text_sample)
    if m:
        month = _MONTHS.get(m.group(1).lower(), 1)
        year = int(m.group(2))
        return FreshnessResult(
            event_date=f"{year}-{month:02d}-15",
            confidence=0.6,
            evidence=m.group(0),
        )

    # 5. Fiscal year (FY27)
    m = _FISCAL_YEAR.search(text_sample)
    if m:
        return FreshnessResult(
            event_date=_fy_to_date(m.group(1)),
            confidence=0.4,
            evidence=m.group(0),
        )

    # 6. Relative markers
    m = _RECENCY_MARKERS.search(text_sample)
    if m:
        marker = m.group(1).lower()
        if "today" in marker or "morning" in marker:
            event_date = now.strftime("%Y-%m-%d")
        elif "yesterday" in marker:
            event_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        elif "this week" in marker:
            event_date = now.strftime("%Y-%m-%d")
        elif "last week" in marker:
            event_date = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        elif "this month" in marker:
            event_date = now.strftime("%Y-%m-15")
        elif "last month" in marker:
            event_date = (now - timedelta(days=30)).strftime("%Y-%m-15")
        elif "this quarter" in marker:
            event_date = now.strftime("%Y-%m-15")
        elif "last quarter" in marker:
            event_date = (now - timedelta(days=90)).strftime("%Y-%m-15")
        else:
            event_date = now.strftime("%Y-%m-%d")
        return FreshnessResult(
            event_date=event_date,
            confidence=0.5,
            evidence=m.group(0),
        )

    # 7. Historical markers
    m = _HISTORICAL_MARKERS.search(text_sample)
    if m:
        return FreshnessResult(
            event_date="",
            confidence=0.3,
            evidence=m.group(0),
            freshness="historical",
        )

    return FreshnessResult(confidence=0.0)


def score_freshness(
    event_date: str = "",
    ingested_at: str = "",
) -> FreshnessResult:
    """Score content freshness based on event date vs ingestion date.

    Freshness levels:
      fresh:       0-2 days old
      recent:      3-7 days old
      aging:       8-30 days old
      stale:       31-90 days old
      historical:  90+ days old
    """
    if not ingested_at:
        ingested_at = datetime.now(timezone.utc).isoformat()

    result = FreshnessResult(
        event_date=event_date,
        ingested_at=ingested_at,
    )

    if not event_date:
        result.freshness = "unknown"
        return result

    try:
        event_dt = datetime.fromisoformat(event_date.replace("Z", "+00:00"))
        ingest_dt = datetime.fromisoformat(ingested_at.replace("Z", "+00:00"))

        # Handle dates without timezone
        if event_dt.tzinfo is None:
            event_dt = event_dt.replace(tzinfo=timezone.utc)
        if ingest_dt.tzinfo is None:
            ingest_dt = ingest_dt.replace(tzinfo=timezone.utc)

        delta = ingest_dt - event_dt
        days = max(0, delta.days)
        result.staleness_days = days

        if days <= 2:
            result.freshness = "fresh"
        elif days <= 7:
            result.freshness = "recent"
        elif days <= 30:
            result.freshness = "aging"
        elif days <= 90:
            result.freshness = "stale"
        else:
            result.freshness = "historical"

    except (ValueError, TypeError):
        result.freshness = "unknown"

    return result
