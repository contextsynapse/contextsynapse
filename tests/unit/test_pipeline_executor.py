"""Tests for pipeline executor (single Pipeline model, single store)."""
import pytest
from unittest.mock import MagicMock, patch


def _make_store():
    """Helper: create in-memory PipelineStore."""
    from contextcore.pipelines.models import PipelineStore
    return PipelineStore(redis_url=None)


def test_execute_pipeline_returns_run_result():
    from contextcore.pipelines.executor import execute_pipeline
    from contextcore.pipelines.models import Pipeline

    store = _make_store()
    p = Pipeline(
        name="TOI Pipeline",
        source_url="http://example.com",
        schema_id="news_article",
        schema_mode="schema_plus",
        target_context_id="ctx_test",
    )
    store.save(p)

    with patch("contextcore.pipelines.executor.ingest_crawl") as mock_crawl:
        mock_crawl.return_value = {
            "ingested": [{"title": "Test", "nodes": 5, "edges": 3}],
            "filtered": [],
            "failed": [],
        }
        with patch("contextcore.pipelines.executor._get_graph") as mock_graph:
            mock_graph.return_value = MagicMock()
            result = execute_pipeline(p.id, store)

    assert result["status"] == "success"
    assert result["pages_ingested"] == 1
    assert result["nodes_created"] == 5
    assert "pipeline_id" in result


def test_execute_pipeline_updates_stats():
    from contextcore.pipelines.executor import execute_pipeline
    from contextcore.pipelines.models import Pipeline

    store = _make_store()
    p = Pipeline(name="stats", source_url="http://example.com", target_context_id="ctx_1")
    store.save(p)

    with patch("contextcore.pipelines.executor.ingest_crawl") as mock_crawl:
        mock_crawl.return_value = {"ingested": [{"nodes": 3}], "filtered": [], "failed": []}
        with patch("contextcore.pipelines.executor._get_graph") as mock_graph:
            mock_graph.return_value = MagicMock()
            execute_pipeline(p.id, store)

    updated = store.get(p.id)
    assert updated.total_runs == 1
    assert updated.last_run_at != ""


def test_execute_pipeline_handles_error():
    from contextcore.pipelines.executor import execute_pipeline
    from contextcore.pipelines.models import Pipeline

    store = _make_store()
    p = Pipeline(name="err", source_url="http://bad.com", target_context_id="ctx_1")
    store.save(p)

    with patch("contextcore.pipelines.executor.ingest_crawl") as mock_crawl:
        mock_crawl.side_effect = ConnectionError("timeout")
        with patch("contextcore.pipelines.executor._get_graph") as mock_graph:
            mock_graph.return_value = MagicMock()
            result = execute_pipeline(p.id, store)

    assert result["status"] == "error"
    updated = store.get(p.id)
    assert "timeout" in updated.last_error


def test_execute_one_time_sets_completed():
    from contextcore.pipelines.executor import execute_pipeline
    from contextcore.pipelines.models import Pipeline

    store = _make_store()
    p = Pipeline(name="once", source_url="http://example.com",
                 trigger_type="one_time", target_context_id="ctx_1")
    store.save(p)

    with patch("contextcore.pipelines.executor.ingest_crawl") as mock_crawl:
        mock_crawl.return_value = {"ingested": [], "filtered": [], "failed": []}
        with patch("contextcore.pipelines.executor._get_graph") as mock_graph:
            mock_graph.return_value = MagicMock()
            execute_pipeline(p.id, store)

    updated = store.get(p.id)
    assert updated.status == "completed"


def test_execute_scheduled_sets_next_run():
    from contextcore.pipelines.executor import execute_pipeline
    from contextcore.pipelines.models import Pipeline

    store = _make_store()
    p = Pipeline(name="sched", source_url="http://example.com",
                 trigger_type="scheduled", interval_minutes=30,
                 target_context_id="ctx_1")
    store.save(p)

    with patch("contextcore.pipelines.executor.ingest_crawl") as mock_crawl:
        mock_crawl.return_value = {"ingested": [], "filtered": [], "failed": []}
        with patch("contextcore.pipelines.executor._get_graph") as mock_graph:
            mock_graph.return_value = MagicMock()
            execute_pipeline(p.id, store)

    updated = store.get(p.id)
    assert updated.next_run_at != ""
    assert updated.status == "active"


def test_execute_pipeline_not_found():
    """Calling execute_pipeline with unknown id returns error dict."""
    from contextcore.pipelines.executor import execute_pipeline

    store = _make_store()
    result = execute_pipeline("nonexistent-id", store)
    assert result["status"] == "error"
    assert "not found" in result["error"]


def test_execute_pipeline_uses_llm_model():
    """Pipeline llm_model is available on the object and survives the run."""
    from contextcore.pipelines.executor import execute_pipeline
    from contextcore.pipelines.models import Pipeline

    store = _make_store()
    p = Pipeline(name="model_test", source_url="http://example.com",
                 llm_model="openai:gpt-4o", target_context_id="ctx_model")
    store.save(p)

    with patch("contextcore.pipelines.executor.ingest_crawl") as mock_crawl:
        mock_crawl.return_value = {"ingested": [], "filtered": [], "failed": []}
        with patch("contextcore.pipelines.executor._get_graph") as mock_graph:
            mock_graph.return_value = MagicMock()
            execute_pipeline(p.id, store)

    updated = store.get(p.id)
    assert updated.llm_model == "openai:gpt-4o"
