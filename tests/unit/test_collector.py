"""Tests for ContextCollector — multi-pipeline orchestration."""
import pytest


class TestTopicFilter:
    """Topic/keyword/semantic filter configuration."""

    def test_filter_with_topics(self):
        from contextcore.intelligence.collector import TopicFilter
        f = TopicFilter(
            topics=["TCS", "Tata Consultancy"],
            exclude=["cricket"],
            semantic_query="TCS business performance and earnings",
            semantic_threshold=0.7,
        )
        assert f.topics == ["TCS", "Tata Consultancy"]
        assert f.exclude == ["cricket"]
        fc = f.to_filter_config()
        assert fc is not None
        assert fc.keywords_include == ["TCS", "Tata Consultancy"]
        assert fc.keywords_exclude == ["cricket"]
        assert fc.semantic_query == "TCS business performance and earnings"

    def test_empty_filter_returns_none(self):
        from contextcore.intelligence.collector import TopicFilter
        f = TopicFilter()
        assert f.to_filter_config() is None

    def test_filter_roundtrip(self):
        from contextcore.intelligence.collector import TopicFilter
        f = TopicFilter(topics=["AI", "ML"], semantic_query="artificial intelligence")
        d = f.to_dict()
        restored = TopicFilter.from_dict(d)
        assert restored.topics == ["AI", "ML"]
        assert restored.semantic_query == "artificial intelligence"


class TestCollectionPlan:
    """Collection plan model."""

    def test_create_plan_with_filters(self):
        from contextcore.intelligence.collector import CollectionPlan, PipelineConfig, TopicFilter

        plan = CollectionPlan(
            context_name="TCS Intelligence",
            context_description="Track TCS across all signals",
            tags=["tcs", "it-services", "india"],
            template="market_analysis",
            pipelines=[
                PipelineConfig(
                    name="news", source_type="rss",
                    sources=["https://example.com/rss"],
                    interval="30m",
                    filter=TopicFilter(
                        topics=["TCS", "Tata Consultancy"],
                        exclude=["cricket", "weather"],
                        semantic_query="TCS business, earnings, deals",
                    ),
                ),
                PipelineConfig(name="filings", source_type="crawl", sources=["https://bse.com/tcs"], interval="daily"),
                PipelineConfig(name="earnings", source_type="manual", interval="quarterly"),
            ],
        )

        assert plan.context_name == "TCS Intelligence"
        assert len(plan.pipelines) == 3
        assert plan.pipelines[0].filter.topics == ["TCS", "Tata Consultancy"]
        assert plan.pipelines[0].filter.exclude == ["cricket", "weather"]

    def test_plan_roundtrip(self):
        from contextcore.intelligence.collector import CollectionPlan, PipelineConfig, TopicFilter

        plan = CollectionPlan(
            context_name="Test",
            tags=["a", "b"],
            pipelines=[PipelineConfig(
                name="p1", source_type="rss", sources=["url1"],
                filter=TopicFilter(topics=["keyword1"]),
            )],
        )

        d = plan.to_dict()
        restored = CollectionPlan.from_dict(d)
        assert restored.context_name == "Test"
        assert len(restored.pipelines) == 1
        assert restored.pipelines[0].sources == ["url1"]
        assert restored.pipelines[0].filter.topics == ["keyword1"]


class TestContextCollector:
    """Collector orchestration."""

    def test_register_and_list(self):
        from contextcore.intelligence.collector import ContextCollector, CollectionPlan, PipelineConfig

        collector = ContextCollector()
        plan = CollectionPlan(
            context_name="TCS",
            pipelines=[PipelineConfig(name="news", source_type="manual")],
        )
        collector.register(plan)

        assert "TCS" in collector.list_plans()
        assert collector.get_plan("TCS") is not None

    def test_status(self):
        from contextcore.intelligence.collector import ContextCollector, CollectionPlan, PipelineConfig

        collector = ContextCollector()
        plan = CollectionPlan(
            context_name="TCS",
            template="market_analysis",
            pipelines=[
                PipelineConfig(name="news", source_type="rss"),
                PipelineConfig(name="filings", source_type="crawl"),
            ],
        )
        collector.register(plan)

        status = collector.status("TCS")
        assert status["context_name"] == "TCS"
        assert status["pipeline_count"] == 2
        assert "news" in status["pipelines"]
        assert "filings" in status["pipelines"]

    def test_run_all_with_manual_skips(self):
        from contextcore.intelligence.collector import ContextCollector, CollectionPlan, PipelineConfig

        collector = ContextCollector()
        plan = CollectionPlan(
            context_name="TCS",
            pipelines=[
                PipelineConfig(name="earnings", source_type="manual"),
                PipelineConfig(name="disabled", source_type="rss", enabled=False),
            ],
        )
        collector.register(plan)

        result = collector.run_all("TCS")
        assert result.pipelines_run == 1  # manual runs but returns "skipped"
        assert result.pipelines_skipped == 1  # disabled pipeline

    def test_run_all_with_custom_ingest_fn(self):
        from contextcore.intelligence.collector import ContextCollector, CollectionPlan, PipelineConfig

        ingested = []

        def mock_ingest(source, strategy, context_purpose):
            ingested.append(source)

            class FakeResult:
                errors = []
                entity_ids = {}
                fact_ids = []
            return FakeResult()

        collector = ContextCollector()
        plan = CollectionPlan(
            context_name="TCS",
            context_description="Track TCS",
            tags=["tcs"],
            pipelines=[
                PipelineConfig(name="news", source_type="rss", sources=["https://example.com/feed1", "https://example.com/feed2"]),
            ],
        )
        collector.register(plan)

        result = collector.run_all("TCS", ingest_fn=mock_ingest)
        assert result.total_ingested == 2
        assert len(ingested) == 2
        assert "https://example.com/feed1" in ingested

    def test_run_single_pipeline(self):
        from contextcore.intelligence.collector import ContextCollector, CollectionPlan, PipelineConfig

        ingested = []

        def mock_ingest(source, strategy, context_purpose):
            ingested.append(source)

            class FakeResult:
                errors = []
            return FakeResult()

        collector = ContextCollector()
        plan = CollectionPlan(
            context_name="TCS",
            pipelines=[
                PipelineConfig(name="news", source_type="rss", sources=["url1"]),
                PipelineConfig(name="filings", source_type="crawl", sources=["url2"]),
            ],
        )
        collector.register(plan)

        result = collector.run_pipeline("TCS", "news", ingest_fn=mock_ingest)
        assert result["ingested"] == 1
        assert len(ingested) == 1  # only news ran, not filings

    def test_dedup_counted(self):
        from contextcore.intelligence.collector import ContextCollector, CollectionPlan, PipelineConfig

        call_count = [0]

        def dedup_ingest(source, strategy, context_purpose):
            call_count[0] += 1

            class FakeResult:
                errors = ["Duplicate content (already ingested)"] if call_count[0] > 1 else []
            return FakeResult()

        collector = ContextCollector()
        plan = CollectionPlan(
            context_name="TCS",
            pipelines=[
                PipelineConfig(name="news", source_type="rss", sources=["url1", "url1"]),
            ],
        )
        collector.register(plan)

        result = collector.run_all("TCS", ingest_fn=dedup_ingest)
        assert result.total_ingested == 1
        assert result.total_deduped == 1

    def test_nonexistent_plan(self):
        from contextcore.intelligence.collector import ContextCollector

        collector = ContextCollector()
        result = collector.run_all("nonexistent")
        assert result.total_errors >= 1


class TestAdaptiveScheduling:
    """Pipeline self-tunes interval based on run results."""

    def test_all_deduped_slows_down(self):
        from contextcore.intelligence.collector import ContextCollector, CollectionPlan, PipelineConfig

        call_count = [0]

        def always_dup(source, strategy, context_purpose):
            call_count[0] += 1
            class R:
                errors = ["Duplicate content (already ingested)"]
            return R()

        collector = ContextCollector()
        plan = CollectionPlan(
            context_name="TCS",
            pipelines=[PipelineConfig(name="news", source_type="rss", sources=["url1"], interval="30m")],
        )
        collector.register(plan)

        result = collector.run_all("TCS", ingest_fn=always_dup)

        # Should have slowed down: 30m -> 1h
        assert plan.pipelines[0].interval == "1h"
        assert result.pipeline_results[0].get("_schedule_reason") == "all_deduped_slowing_down"

    def test_errors_retry_sooner(self):
        from contextcore.intelligence.collector import ContextCollector, CollectionPlan, PipelineConfig

        def always_error(source, strategy, context_purpose):
            class R:
                errors = ["Connection timeout"]
            return R()

        collector = ContextCollector()
        plan = CollectionPlan(
            context_name="TCS",
            pipelines=[PipelineConfig(name="news", source_type="rss", sources=["url1"], interval="1h")],
        )
        collector.register(plan)

        result = collector.run_all("TCS", ingest_fn=always_error)

        # Should retry sooner: 1h -> 30m
        assert plan.pipelines[0].interval == "30m"
        assert result.pipeline_results[0].get("_schedule_reason") == "errors_retry_sooner"

    def test_high_volume_speeds_up(self):
        from contextcore.intelligence.collector import ContextCollector, CollectionPlan, PipelineConfig

        def many_results(source, strategy, context_purpose):
            class R:
                errors = []
            return R()

        collector = ContextCollector()
        plan = CollectionPlan(
            context_name="TCS",
            pipelines=[PipelineConfig(
                name="news", source_type="rss",
                sources=["u1", "u2", "u3", "u4", "u5", "u6"],  # 6 sources → 6 ingested
                interval="1h",
            )],
        )
        collector.register(plan)

        result = collector.run_all("TCS", ingest_fn=many_results)

        # 6 items ingested → speed up: 1h -> 30m
        assert plan.pipelines[0].interval == "30m"
        assert result.pipeline_results[0].get("_schedule_reason") == "high_volume_speeding_up"

    def test_normal_keeps_interval(self):
        from contextcore.intelligence.collector import ContextCollector, CollectionPlan, PipelineConfig

        def normal_result(source, strategy, context_purpose):
            class R:
                errors = []
            return R()

        collector = ContextCollector()
        plan = CollectionPlan(
            context_name="TCS",
            pipelines=[PipelineConfig(name="news", source_type="rss", sources=["url1", "url2"], interval="30m")],
        )
        collector.register(plan)

        result = collector.run_all("TCS", ingest_fn=normal_result)

        # 2 items, normal → keep 30m
        assert plan.pipelines[0].interval == "30m"
        assert result.pipeline_results[0].get("_schedule_reason") == "normal"
