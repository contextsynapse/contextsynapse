"""Tests for Watchdog Manager — multiple watchdogs per hierarchy level."""
import pytest


class TestWatchdogManager:

    def test_add_defaults(self):
        from contextcore.intelligence.watchdog_manager import WatchdogManager
        mgr = WatchdogManager()
        mgr.add_defaults()
        assert len(mgr._watchdogs) == 4
        assert "global" in mgr._watchdogs
        assert "india" in mgr._watchdogs
        assert "us" in mgr._watchdogs
        assert "it_sector" in mgr._watchdogs

    def test_each_watchdog_linked_to_context(self):
        from contextcore.intelligence.watchdog_manager import WatchdogManager
        mgr = WatchdogManager()
        mgr.add_defaults()
        assert mgr._configs["global"].context == "Global Macro"
        assert mgr._configs["india"].context == "India Economy"
        assert mgr._configs["us"].context == "US Economy"
        assert mgr._configs["it_sector"].context == "IT Services Sector"

    def test_custom_keywords_enhance_detection(self):
        from contextcore.intelligence.watchdog_manager import WatchdogManager, WatchdogConfig
        from contextcore.intelligence.watchdog import WatchdogSource

        mgr = WatchdogManager()
        mgr.add_watchdog(WatchdogConfig(
            name="india",
            context="India Economy",
            sources=[("https://example.com/rss", "Test")],
            keywords=["SEBI", "rupee crash", "repo rate"],
        ))

        wd = mgr._watchdogs["india"]
        source = WatchdogSource(url="https://example.com", name="Test")

        # "SEBI changes FII rules" — NOT a built-in high-impact keyword
        # Only the custom "SEBI" keyword should catch this
        alert = wd._classify_headline("SEBI changes FII rules affecting foreign investors", "", source)
        assert alert is not None
        assert "SEBI" in alert.matched_keywords

        # Normal headline — no match from either built-in or custom
        alert = wd._classify_headline("Cricket team wins world cup", "", source)
        assert alert is None

    def test_status(self):
        from contextcore.intelligence.watchdog_manager import WatchdogManager
        mgr = WatchdogManager()
        mgr.add_defaults()
        status = mgr.status()
        assert status["total"] == 4
        assert "global" in status["watchdogs"]
        assert status["watchdogs"]["global"]["context"] == "Global Macro"

    def test_serialization(self):
        from contextcore.intelligence.watchdog_manager import WatchdogManager
        mgr = WatchdogManager()
        mgr.add_defaults()
        d = mgr.to_dict()
        assert len(d["watchdogs"]) == 4

        mgr2 = WatchdogManager.from_dict(d)
        assert len(mgr2._watchdogs) == 4
        assert mgr2._configs["india"].context == "India Economy"

    def test_check_all(self):
        from contextcore.intelligence.watchdog_manager import WatchdogManager, WatchdogConfig
        mgr = WatchdogManager()
        # Empty watchdog (no sources) — should not crash
        mgr.add_watchdog(WatchdogConfig(name="test", context="Test", sources=[]))
        results = mgr.check_all()
        assert "test" in results
