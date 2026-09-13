"""Tests for freshness detection — event date extraction and staleness scoring."""
import pytest


class TestEventDateExtraction:

    def test_full_date(self):
        from contextcore.intelligence.freshness import detect_event_date
        r = detect_event_date("TCS announced results on January 15, 2026")
        assert r.event_date == "2026-01-15"
        assert r.confidence >= 0.8

    def test_iso_date(self):
        from contextcore.intelligence.freshness import detect_event_date
        r = detect_event_date("Published 2026-08-30 on Reuters")
        assert r.event_date == "2026-08-30"
        assert r.confidence >= 0.9

    def test_quarter(self):
        from contextcore.intelligence.freshness import detect_event_date
        r = detect_event_date("TCS reported Q1 FY27 revenue of $7.5 billion")
        assert "2027" in r.event_date or "2026" in r.event_date
        assert r.confidence >= 0.5

    def test_month_year(self):
        from contextcore.intelligence.freshness import detect_event_date
        r = detect_event_date("India GDP data for August 2026 shows 7.2% growth")
        assert r.event_date.startswith("2026-08")

    def test_fiscal_year(self):
        from contextcore.intelligence.freshness import detect_event_date
        r = detect_event_date("NASSCOM projects FY27 IT exports at $210 billion")
        assert "2026" in r.event_date  # FY27 = 2026-2027

    def test_relative_today(self):
        from contextcore.intelligence.freshness import detect_event_date
        from datetime import datetime, timezone
        r = detect_event_date("RBI announced earlier today a rate hold decision")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert r.event_date == today

    def test_no_date(self):
        from contextcore.intelligence.freshness import detect_event_date
        r = detect_event_date("IT services are growing rapidly")
        assert r.confidence < 0.3


class TestFreshnessScoring:

    def test_fresh(self):
        from contextcore.intelligence.freshness import score_freshness
        r = score_freshness("2026-08-30", "2026-08-31")
        assert r.freshness == "fresh"
        assert r.staleness_days <= 2

    def test_recent(self):
        from contextcore.intelligence.freshness import score_freshness
        r = score_freshness("2026-08-25", "2026-08-31")
        assert r.freshness == "recent"

    def test_stale(self):
        from contextcore.intelligence.freshness import score_freshness
        r = score_freshness("2026-07-15", "2026-08-31")
        assert r.freshness == "stale"
        assert r.staleness_days > 30

    def test_historical(self):
        from contextcore.intelligence.freshness import score_freshness
        r = score_freshness("2025-01-01", "2026-08-31")
        assert r.freshness == "historical"
        assert r.staleness_days > 90

    def test_unknown_no_event_date(self):
        from contextcore.intelligence.freshness import score_freshness
        r = score_freshness("", "2026-08-31")
        assert r.freshness == "unknown"


class TestFreshnessInPipeline:

    def test_ingested_content_gets_freshness(self):
        from contextcore import AIContextDB
        from contextcore.ingestion.smart_ingest import ingest_text

        db = AIContextDB()
        result = ingest_text(
            "TCS reported Q1 FY27 revenue of $7.5 billion on July 10, 2026, "
            "beating analyst estimates by a wide margin.",
            db, title="TCS Q1", context_purpose="TCS analysis",
        )

        all_nodes = list(db.csr_adapter.get_all_nodes())
        entities = [n for n in all_nodes
                     if getattr(n, 'label', '') not in ('Document', 'Passage', 'ContextIntelligence')]

        tagged = [n for n in entities if getattr(n, 'properties', {}).get('_event_date')]
        assert len(tagged) >= 1

        for n in tagged:
            props = getattr(n, 'properties', {})
            assert props["_event_date"].startswith("2026")
            assert props["_freshness"] in ("fresh", "recent", "aging", "stale", "historical")
            assert isinstance(props["_staleness_days"], int)
