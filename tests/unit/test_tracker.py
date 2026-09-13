"""Tests for EntityTracker — one-call entity tracking."""
import pytest


class TestTrackingTemplates:

    def test_indian_listed_company(self):
        from contextcore.intelligence.tracker import IndianListedCompanyTemplate

        tmpl = IndianListedCompanyTemplate()
        plan = tmpl.build_plan("TCS", extra={
            "full_name": "Tata Consultancy Services",
            "bse_code": "532540",
            "topics": ["TCS", "Tata Consultancy"],
        })

        assert plan.context_name == "TCS"
        assert len(plan.pipelines) == 3  # news + earnings + filings
        assert plan.pipelines[0].name == "company_news"
        assert "TCS" in plan.pipelines[0].filter.topics

    def test_us_listed_company(self):
        from contextcore.intelligence.tracker import USListedCompanyTemplate

        tmpl = USListedCompanyTemplate()
        plan = tmpl.build_plan("Apple", extra={
            "full_name": "Apple Inc.",
            "ticker": "AAPL",
        })

        assert plan.context_name == "Apple"
        assert len(plan.pipelines) == 3  # news + sec + earnings

    def test_generic_topic(self):
        from contextcore.intelligence.tracker import GenericTopicTemplate

        tmpl = GenericTopicTemplate()
        plan = tmpl.build_plan("Climate Change", extra={
            "topics": ["climate", "carbon", "emissions", "net zero"],
            "description": "Track climate policy and corporate commitments",
        })

        assert plan.context_name == "Climate Change"
        assert "climate" in plan.pipelines[0].filter.topics


class TestEntityTracker:

    def test_track_creates_plan(self):
        from contextcore.intelligence.tracker import EntityTracker
        from contextcore.intelligence.collector import ContextCollector

        collector = ContextCollector()
        tracker = EntityTracker(collector=collector)

        result = tracker.track("TCS", template="indian_listed_company", extra={
            "bse_code": "532540", "full_name": "Tata Consultancy Services",
        })

        assert result["status"] == "tracking"
        assert result["entity"] == "TCS"
        assert result["pipelines"] == 3
        assert "TCS" in collector.list_plans()

    def test_track_links_hierarchy(self):
        from contextcore.intelligence.tracker import EntityTracker
        from contextcore.intelligence.collector import ContextCollector
        from contextcore.intelligence.signal_hierarchy import SignalHierarchy

        collector = ContextCollector()
        hierarchy = SignalHierarchy()
        hierarchy.set_level("IT Services Sector", "sector")

        tracker = EntityTracker(collector=collector, hierarchy=hierarchy)

        tracker.track("TCS", template="indian_listed_company")
        tracker.track("Infosys", template="indian_listed_company")

        # TCS should be downstream of IT Services Sector
        downstream = hierarchy.get_downstream("IT Services Sector")
        assert "TCS" in downstream
        assert "Infosys" in downstream

        # TCS and Infosys should be lateral peers
        peers = hierarchy.get_lateral("TCS")
        assert "Infosys" in peers

    def test_track_unknown_template(self):
        from contextcore.intelligence.tracker import EntityTracker

        tracker = EntityTracker()
        result = tracker.track("Test", template="nonexistent")
        assert "error" in result

    def test_list_tracked(self):
        from contextcore.intelligence.tracker import EntityTracker
        from contextcore.intelligence.collector import ContextCollector

        collector = ContextCollector()
        tracker = EntityTracker(collector=collector)

        tracker.track("TCS", template="indian_listed_company")
        tracker.track("Apple", template="us_listed_company")

        tracked = tracker.list_tracked()
        names = [t["entity"] for t in tracked]
        assert "TCS" in names
        assert "Apple" in names

    def test_untrack(self):
        from contextcore.intelligence.tracker import EntityTracker
        from contextcore.intelligence.collector import ContextCollector

        collector = ContextCollector()
        tracker = EntityTracker(collector=collector)

        tracker.track("TCS", template="indian_listed_company")
        assert "TCS" in collector.list_plans()

        tracker.untrack("TCS")
        assert "TCS" not in collector.list_plans()
