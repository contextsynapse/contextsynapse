"""Tests for remaining features: health monitor, rate limiter, time-series, OCR."""
import pytest
import time


class TestPipelineHealthMonitor:

    def test_healthy_after_success(self):
        from contextcore.intelligence.pipeline_health import PipelineHealthMonitor
        monitor = PipelineHealthMonitor()
        health = monitor.record_run("TCS", "news", success=True)
        assert health.status == "healthy"
        assert health.consecutive_failures == 0

    def test_degraded_after_2_failures(self):
        from contextcore.intelligence.pipeline_health import PipelineHealthMonitor
        monitor = PipelineHealthMonitor()
        monitor.record_run("TCS", "news", success=False, error="timeout")
        health = monitor.record_run("TCS", "news", success=False, error="timeout")
        assert health.status == "degraded"

    def test_failing_after_3_failures(self):
        from contextcore.intelligence.pipeline_health import PipelineHealthMonitor
        monitor = PipelineHealthMonitor(failure_threshold=3)
        for i in range(3):
            monitor.record_run("TCS", "news", success=False, error="timeout")
        health = monitor.get_health("TCS", "news")
        assert len(health) == 1
        assert health[0]["status"] == "failing"

    def test_recovery_after_success(self):
        from contextcore.intelligence.pipeline_health import PipelineHealthMonitor
        monitor = PipelineHealthMonitor()
        monitor.record_run("TCS", "news", success=False)
        monitor.record_run("TCS", "news", success=False)
        health = monitor.record_run("TCS", "news", success=True)
        assert health.status == "healthy"
        assert health.consecutive_failures == 0

    def test_get_failing(self):
        from contextcore.intelligence.pipeline_health import PipelineHealthMonitor
        monitor = PipelineHealthMonitor(failure_threshold=2)
        monitor.record_run("TCS", "news", success=False)
        monitor.record_run("TCS", "news", success=False)
        monitor.record_run("TCS", "filings", success=True)
        failing = monitor.get_failing()
        assert len(failing) == 1
        assert failing[0]["pipeline"] == "news"


class TestPipelineRateLimiter:

    def test_allows_under_limit(self):
        from contextcore.intelligence.rate_limiter import PipelineRateLimiter
        limiter = PipelineRateLimiter(default_rpm=100, default_cooldown=0)
        assert limiter.allow("https://reuters.com/feed")

    def test_blocks_over_rpm(self):
        from contextcore.intelligence.rate_limiter import PipelineRateLimiter
        limiter = PipelineRateLimiter(default_rpm=3, default_cooldown=0)
        assert limiter.allow("https://example.com/1")
        assert limiter.allow("https://example.com/2")
        assert limiter.allow("https://example.com/3")
        assert not limiter.allow("https://example.com/4")  # blocked

    def test_cooldown(self):
        from contextcore.intelligence.rate_limiter import PipelineRateLimiter
        limiter = PipelineRateLimiter(default_rpm=100, default_cooldown=0.5)
        assert limiter.allow("https://example.com")
        assert not limiter.allow("https://example.com")  # too soon
        time.sleep(0.6)
        assert limiter.allow("https://example.com")  # ok now

    def test_custom_limit_per_domain(self):
        from contextcore.intelligence.rate_limiter import PipelineRateLimiter
        limiter = PipelineRateLimiter(default_rpm=100, default_cooldown=0)
        limiter.set_limit("example.com", max_per_minute=1, cooldown=0)
        assert limiter.allow("https://example.com/a")
        assert not limiter.allow("https://example.com/b")  # custom limit: 1/min

    def test_stats(self):
        from contextcore.intelligence.rate_limiter import PipelineRateLimiter
        limiter = PipelineRateLimiter(default_rpm=100, default_cooldown=0)
        limiter.allow("https://reuters.com/1")
        limiter.allow("https://reuters.com/2")
        stats = limiter.get_stats()
        assert "reuters.com" in stats
        assert stats["reuters.com"]["requests_last_minute"] == 2


class TestTimeSeriesQuery:

    def test_sentiment_over_time(self):
        from contextcore.intelligence.timeseries import TimeSeriesQuery
        from contextcore import AIContextDB
        from contextcore.ingestion.smart_ingest import ingest_text

        db = AIContextDB()
        ingest_text("Apple revenue surged 20% beating all expectations", db,
                     title="Apple Q3", context_purpose="Apple analysis")

        ts = TimeSeriesQuery(db)
        trajectory = ts.sentiment_over_time("Apple", days=1)
        # May or may not have entries depending on timestamp format
        assert isinstance(trajectory, list)

    def test_compare_sentiment(self):
        from contextcore.intelligence.timeseries import TimeSeriesQuery
        ts = TimeSeriesQuery(db=None)
        result = ts.compare_sentiment(["TCS", "Infosys"], days=7)
        assert "TCS" in result
        assert "Infosys" in result

    def test_entity_timeline(self):
        from contextcore.intelligence.timeseries import TimeSeriesQuery
        ts = TimeSeriesQuery(db=None)
        timeline = ts.entity_timeline("TCS", days=30)
        assert isinstance(timeline, list)


class TestOCR:

    def test_availability_check(self):
        from contextcore.intelligence.ocr import is_available
        # Should not crash regardless of whether pytesseract is installed
        result = is_available()
        assert isinstance(result, bool)

    def test_extract_without_pytesseract(self):
        from contextcore.intelligence.ocr import extract_text_from_image
        # Should return empty string gracefully if not available
        result = extract_text_from_image("/nonexistent/image.png")
        assert isinstance(result, str)
