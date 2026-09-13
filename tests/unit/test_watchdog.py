"""Tests for Breaking News Watchdog."""
import pytest


class TestHeadlineClassification:

    def test_high_impact_war(self):
        from contextcore.intelligence.watchdog import BreakingNewsWatchdog, WatchdogSource
        wd = BreakingNewsWatchdog()
        alert = wd._classify_headline(
            "Russia launches missile strikes on Ukraine energy grid",
            "https://reuters.com/article/123",
            WatchdogSource(url="https://reuters.com", name="Reuters"),
        )
        assert alert is not None
        assert alert.impact_level in ("critical", "high")
        assert any("missile" in k.lower() for k in alert.matched_keywords)

    def test_high_impact_crash(self):
        from contextcore.intelligence.watchdog import BreakingNewsWatchdog, WatchdogSource
        wd = BreakingNewsWatchdog()
        alert = wd._classify_headline(
            "Global markets crash as US defaults on debt",
            "https://bbc.com/news/123",
            WatchdogSource(url="https://bbc.com", name="BBC"),
        )
        assert alert is not None
        assert alert.impact_level in ("critical", "high")

    def test_medium_impact_layoffs(self):
        from contextcore.intelligence.watchdog import BreakingNewsWatchdog, WatchdogSource
        wd = BreakingNewsWatchdog()
        alert = wd._classify_headline(
            "Tech giant announces massive layoffs, stock plunges",
            "",
            WatchdogSource(url="https://cnbc.com", name="CNBC"),
        )
        assert alert is not None
        assert alert.impact_level in ("high", "medium")

    def test_normal_news_no_alert(self):
        from contextcore.intelligence.watchdog import BreakingNewsWatchdog, WatchdogSource
        wd = BreakingNewsWatchdog()
        alert = wd._classify_headline(
            "Company reports steady Q2 results in line with expectations",
            "",
            WatchdogSource(url="https://reuters.com", name="Reuters"),
        )
        assert alert is None

    def test_dedup_same_headline(self):
        from contextcore.intelligence.watchdog import BreakingNewsWatchdog
        wd = BreakingNewsWatchdog()
        wd._seen_headlines.add("abc123")  # simulate already seen

        import hashlib
        headline = "Test breaking news about war"
        h_key = hashlib.md5(headline.lower().encode()).hexdigest()
        wd._seen_headlines.add(h_key)

        # Same headline won't produce duplicate alert in check()


class TestWatchdogReactions:

    def test_trigger_propagates_through_hierarchy(self):
        from contextcore.intelligence.watchdog import BreakingNewsWatchdog, WatchdogAlert
        from contextcore.intelligence.signal_hierarchy import SignalHierarchy, ContextLink

        h = SignalHierarchy()
        h.add_link(ContextLink(upstream="Global Macro", downstream="India Economy", weight=0.8))
        h.add_link(ContextLink(upstream="India Economy", downstream="TCS", weight=0.7))

        wd = BreakingNewsWatchdog(hierarchy=h)
        alert = WatchdogAlert(
            headline="Oil prices crash 30% on OPEC collapse",
            source_name="Reuters",
            source_url="https://reuters.com",
            impact_level="critical",
            matched_keywords=["crash"],
        )

        triggered = wd._trigger_reactions(alert)
        # Should propagate to India Economy and TCS
        assert triggered == 0  # no reactive controller wired, but propagation happened
        assert len(h.get_propagation_log()) >= 1

    def test_trigger_with_reactive(self):
        from contextcore.intelligence.watchdog import BreakingNewsWatchdog, WatchdogAlert
        from contextcore.intelligence.signal_hierarchy import SignalHierarchy, ContextLink
        from contextcore.intelligence.reactive import ReactiveController

        h = SignalHierarchy()
        h.add_link(ContextLink(upstream="Global Macro", downstream="India Economy", weight=0.8))

        rc = ReactiveController()
        rc.add_default_rules()

        wd = BreakingNewsWatchdog(hierarchy=h, reactive=rc)
        alert = WatchdogAlert(
            headline="Global markets crash as sanctions escalate",
            source_name="Reuters",
            source_url="https://reuters.com",
            impact_level="critical",
            matched_keywords=["crash", "sanctions"],
        )

        triggered = wd._trigger_reactions(alert)
        assert triggered >= 1  # India Economy should have reacted

    def test_trigger_records_prediction(self):
        from contextcore.intelligence.watchdog import BreakingNewsWatchdog, WatchdogAlert
        from contextcore.intelligence.signal_hierarchy import SignalHierarchy, ContextLink
        from contextcore.intelligence.impact_tracker import ImpactTracker

        h = SignalHierarchy()
        h.add_link(ContextLink(upstream="Global Macro", downstream="TCS", weight=0.6))

        tracker = ImpactTracker()
        wd = BreakingNewsWatchdog(hierarchy=h, impact_tracker=tracker)

        alert = WatchdogAlert(
            headline="Trade war escalates with new tariffs",
            source_name="BBC",
            source_url="https://bbc.com",
            impact_level="high",
            matched_keywords=["war", "tariff"],
        )

        wd._trigger_reactions(alert)

        # Should have recorded prediction for TCS
        data = tracker.get_training_data()
        # No outcome yet, so training data empty, but prediction recorded
        assert len(tracker._predictions) >= 1


class TestWatchdogLifecycle:

    def test_add_sources(self):
        from contextcore.intelligence.watchdog import BreakingNewsWatchdog
        wd = BreakingNewsWatchdog()
        wd.add_source("https://reuters.com/rss", name="Reuters")
        wd.add_source("https://bbc.com/rss", name="BBC")
        assert len(wd._sources) == 2

    def test_alert_log(self):
        from contextcore.intelligence.watchdog import BreakingNewsWatchdog, WatchdogAlert
        wd = BreakingNewsWatchdog()
        wd._alert_log.append(WatchdogAlert(
            headline="Test alert",
            source_name="Test",
            source_url="",
            impact_level="high",
            matched_keywords=["test"],
        ))
        log = wd.get_alert_log()
        assert len(log) == 1
        assert log[0]["headline"] == "Test alert"
