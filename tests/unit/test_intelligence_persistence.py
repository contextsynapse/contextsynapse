"""Tests for intelligence persistence layer."""
import pytest
import os
import tempfile


@pytest.fixture
def context_manager():
    """Create a temporary context manager for testing."""
    from contextcore.context.context_manager import ContextManager
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "test_context.db")
    cm = ContextManager(db_path=db_path)
    yield cm
    try:
        os.remove(db_path)
        os.rmdir(tmp)
    except Exception:
        pass


class TestHierarchyPersistence:

    def test_save_and_load_hierarchy(self, context_manager):
        from contextcore.intelligence.persistence import IntelligencePersistence
        from contextcore.intelligence.signal_hierarchy import SignalHierarchy, ContextLink

        store = IntelligencePersistence(context_manager)

        h = SignalHierarchy()
        h.set_level("Global Macro", "world")
        h.set_level("TCS", "company")
        h.add_link(ContextLink(upstream="Global Macro", downstream="TCS", weight=0.7))

        assert store.save_hierarchy(h)

        loaded = store.load_hierarchy()
        assert loaded is not None
        assert "TCS" in loaded.get_downstream("Global Macro")

    def test_load_empty_returns_none(self, context_manager):
        from contextcore.intelligence.persistence import IntelligencePersistence

        store = IntelligencePersistence(context_manager)
        assert store.load_hierarchy() is None


class TestPlanPersistence:

    def test_save_and_load_plan(self, context_manager):
        from contextcore.intelligence.persistence import IntelligencePersistence
        from contextcore.intelligence.collector import CollectionPlan, PipelineConfig, TopicFilter

        store = IntelligencePersistence(context_manager)

        plan = CollectionPlan(
            context_name="TCS",
            context_description="Track TCS",
            tags=["tcs", "it"],
            template="market_analysis",
            pipelines=[
                PipelineConfig(
                    name="news", source_type="rss",
                    sources=["https://example.com/rss"],
                    interval="30m",
                    filter=TopicFilter(topics=["TCS"]),
                ),
            ],
        )

        assert store.save_collection_plan(plan)

        loaded = store.load_collection_plan("TCS")
        assert loaded is not None
        assert loaded.context_name == "TCS"
        assert len(loaded.pipelines) == 1
        assert loaded.pipelines[0].name == "news"
        assert loaded.pipelines[0].filter.topics == ["TCS"]

    def test_load_all_plans(self, context_manager):
        from contextcore.intelligence.persistence import IntelligencePersistence
        from contextcore.intelligence.collector import CollectionPlan, PipelineConfig

        store = IntelligencePersistence(context_manager)

        for name in ["TCS", "Infosys"]:
            store.save_collection_plan(CollectionPlan(
                context_name=name,
                pipelines=[PipelineConfig(name="news", source_type="manual")],
            ))

        plans = store.load_all_plans()
        names = [p.context_name for p in plans]
        assert "TCS" in names
        assert "Infosys" in names

    def test_plan_creates_context_if_missing(self, context_manager):
        from contextcore.intelligence.persistence import IntelligencePersistence
        from contextcore.intelligence.collector import CollectionPlan, PipelineConfig

        store = IntelligencePersistence(context_manager)

        plan = CollectionPlan(
            context_name="New Company",
            context_description="Brand new",
            pipelines=[PipelineConfig(name="p1", source_type="manual")],
        )

        store.save_collection_plan(plan)

        # Find by name via list_contexts
        ctx = None
        for c in context_manager.list_contexts(status="active"):
            if c.name == "New Company":
                ctx = c
                break
        assert ctx is not None
        assert ctx.description == "Brand new"


class TestReactivePersistence:

    def test_save_and_load_rules(self, context_manager):
        from contextcore.intelligence.persistence import IntelligencePersistence
        from contextcore.intelligence.reactive import ReactiveController, ReactionRule

        store = IntelligencePersistence(context_manager)

        rc = ReactiveController()
        rc.add_rule(ReactionRule(
            signal_type="sentiment_reversal",
            actions=["accelerate", "alert"],
        ))

        assert store.save_reactive_rules(rc)

        rc2 = ReactiveController()
        assert store.load_reactive_rules(rc2)
        assert "sentiment_reversal" in rc2._rules
        assert len(rc2._rules["sentiment_reversal"]) == 1


class TestWatchdogPersistence:

    def test_save_and_load_watchdog(self, context_manager):
        from contextcore.intelligence.persistence import IntelligencePersistence
        from contextcore.intelligence.watchdog import BreakingNewsWatchdog

        store = IntelligencePersistence(context_manager)

        wd = BreakingNewsWatchdog(watchdog_context="Global Macro")
        wd.add_source("https://reuters.com/rss", name="Reuters")
        wd.add_source("https://bbc.com/rss", name="BBC")

        assert store.save_watchdog_config(wd)

        wd2 = BreakingNewsWatchdog()
        assert store.load_watchdog_config(wd2)
        assert len(wd2._sources) == 2
        assert wd2._watchdog_context == "Global Macro"
