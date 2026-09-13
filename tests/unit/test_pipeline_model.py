"""Tests for the single Pipeline model and PipelineStore persistence."""
import pytest
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Pipeline tests
# ---------------------------------------------------------------------------

def test_pipeline_create_with_defaults():
    from contextcore.pipelines.models import Pipeline
    p = Pipeline(name="News Pipeline", source_type="web_crawl")
    assert p.name == "News Pipeline"
    assert p.source_type == "web_crawl"
    assert p.schema_mode == "schema_plus"
    assert p.steps["fetch"] is True
    assert p.steps["extract"] is True
    assert len(p.id) > 0
    assert p.created_at != ""


def test_pipeline_default_fields():
    from contextcore.pipelines.models import Pipeline
    p = Pipeline(name="test")
    assert p.source_url == ""
    assert p.max_pages == 5
    assert p.trigger_type == "one_time"
    assert p.interval_minutes == 60
    assert p.include_keywords == []
    assert p.exclude_keywords == []
    assert p.semantic_filter == ""
    assert p.semantic_threshold == 0.6
    assert p.llm_model == ""
    assert p.embedding_model == ""
    assert p.target_context_id == ""
    assert p.status == "draft"
    assert p.last_run_at == ""
    assert p.next_run_at == ""
    assert p.total_runs == 0
    assert p.total_documents == 0
    assert p.last_error == ""


def test_pipeline_default_steps():
    from contextcore.pipelines.models import Pipeline
    p = Pipeline(name="test")
    expected_steps = ["fetch", "clean", "chunk", "filter", "extract", "graph_build", "embed", "index", "notify"]
    for step in expected_steps:
        assert p.steps[step] is True


def test_pipeline_custom_steps():
    from contextcore.pipelines.models import Pipeline
    p = Pipeline(
        name="fast",
        steps={"fetch": True, "clean": True, "chunk": True, "filter": True,
               "extract": False, "graph_build": True, "embed": False,
               "index": True, "notify": False},
    )
    assert p.steps["extract"] is False
    assert p.steps["embed"] is False
    assert p.steps["fetch"] is True


def test_pipeline_to_dict_and_from_dict():
    from contextcore.pipelines.models import Pipeline
    p = Pipeline(
        name="TOI Politics & Economy",
        description="Scheduled news ingestion",
        source_type="web_crawl",
        source_url="https://timesofindia.indiatimes.com/",
        max_pages=10,
        trigger_type="scheduled",
        interval_minutes=60,
        include_keywords=["politics", "economy"],
        exclude_keywords=["cricket"],
        semantic_filter="Indian political news",
        semantic_threshold=0.7,
        schema_id="news_article",
        schema_mode="strict",
        llm_model="groq:llama-3.3-70b",
        embedding_model="ollama:nomic-embed-text",
        target_context_id="ctx_123",
        status="active",
    )
    d = p.to_dict()
    assert d["name"] == "TOI Politics & Economy"
    assert d["source_url"] == "https://timesofindia.indiatimes.com/"
    assert d["trigger_type"] == "scheduled"
    assert d["interval_minutes"] == 60
    assert d["include_keywords"] == ["politics", "economy"]
    assert d["schema_mode"] == "strict"
    assert d["llm_model"] == "groq:llama-3.3-70b"
    assert d["embedding_model"] == "ollama:nomic-embed-text"
    assert d["target_context_id"] == "ctx_123"
    assert "steps" in d

    p2 = Pipeline.from_dict(d)
    assert p2.name == p.name
    assert p2.source_url == p.source_url
    assert p2.include_keywords == p.include_keywords
    assert p2.schema_mode == "strict"
    assert p2.id == p.id
    assert p2.llm_model == "groq:llama-3.3-70b"


def test_pipeline_draft_when_no_context():
    """Pipeline created without target_context_id should default to draft."""
    from contextcore.pipelines.models import Pipeline
    p = Pipeline(name="No Context", source_url="http://example.com")
    assert p.status == "draft"
    assert p.target_context_id == ""


def test_pipeline_active_when_status_set():
    """Pipeline created with explicit status=active should honor it."""
    from contextcore.pipelines.models import Pipeline
    p = Pipeline(name="With Context", source_url="http://example.com",
                 target_context_id="ctx_123", status="active")
    assert p.status == "active"
    assert p.target_context_id == "ctx_123"


# ---------------------------------------------------------------------------
# PipelineStore tests
# ---------------------------------------------------------------------------

def test_pipeline_store_save_and_load():
    from contextcore.pipelines.models import Pipeline, PipelineStore
    store = PipelineStore(redis_url=None)
    p = Pipeline(name="test pipeline", source_url="http://example.com",
                 schema_id="news_article", target_context_id="ctx_1")
    store.save(p)
    loaded = store.get(p.id)
    assert loaded is not None
    assert loaded.name == "test pipeline"
    assert loaded.schema_id == "news_article"
    assert loaded.target_context_id == "ctx_1"


def test_pipeline_store_list():
    from contextcore.pipelines.models import Pipeline, PipelineStore
    store = PipelineStore(redis_url=None)
    store.save(Pipeline(name="p1", source_url="http://a.com"))
    store.save(Pipeline(name="p2", source_url="http://b.com"))
    pipelines = store.list_all()
    assert len(pipelines) >= 2
    names = {p.name for p in pipelines}
    assert "p1" in names
    assert "p2" in names


def test_pipeline_store_delete():
    from contextcore.pipelines.models import Pipeline, PipelineStore
    store = PipelineStore(redis_url=None)
    p = Pipeline(name="del_pipeline")
    store.save(p)
    assert store.get(p.id) is not None
    store.delete(p.id)
    assert store.get(p.id) is None


def test_pipeline_store_save_run_in_memory():
    """save_run is a no-op for in-memory store (no Redis), get_runs returns []."""
    from contextcore.pipelines.models import Pipeline, PipelineStore
    store = PipelineStore(redis_url=None)
    p = Pipeline(name="run_pipeline", source_url="http://example.com")
    store.save(p)
    store.save_run(p.id, {"run_at": "2026-04-14T00:00:00", "pages_ingested": 3})
    runs = store.get_runs(p.id)
    assert isinstance(runs, list)


def test_pipeline_store_redis_key_format():
    """PipelineStore uses pipeline:{id} as the Redis key prefix."""
    from contextcore.pipelines.models import Pipeline, PipelineStore
    store = PipelineStore(redis_url=None)
    p = Pipeline(name="key_test")
    assert store._key(p.id) == f"pipeline:{p.id}"


# ---------------------------------------------------------------------------
# PipelineStore (SQLite) — extended fields tests
# ---------------------------------------------------------------------------

@pytest.fixture
def sqlite_store(tmp_path):
    """Create a PipelineStore backed by a temp SQLite database."""
    from contextcore.ingestion.pipeline_store import PipelineStore as SQLitePipelineStore
    db_path = str(tmp_path / "test_pipelines.db")
    return SQLitePipelineStore(db_path=db_path)


def test_create_pipeline_with_schema(sqlite_store):
    """Create a pipeline with all 4 new fields and verify they're stored."""
    filters = {"keywords": ["ai", "ml"], "semantic_threshold": 0.7}
    graph_config = {"entity_resolution": True, "co_occurrence_threshold": 3}

    result = sqlite_store.create_pipeline(
        tenant_id="t1",
        name="Schema Test",
        description="Test pipeline",
        stages=[{"name": "chunk", "type": "CHUNK", "config": {}}],
        aiql_template="USE GRAPH {{graph}}",
        schema_name="healthcare",
        parser="turn",
        filters=filters,
        graph_config=graph_config,
    )

    assert result["schema_name"] == "healthcare"
    assert result["parser"] == "turn"
    assert result["filters"] == filters
    assert result["graph_config"] == graph_config


def test_get_pipeline_returns_extended_fields(sqlite_store):
    """Fetch a pipeline and verify extended fields are returned and parsed."""
    filters = {"keywords": ["security"]}
    graph_config = {"entity_resolution": False}

    created = sqlite_store.create_pipeline(
        tenant_id="t1",
        name="Fetch Test",
        description="desc",
        stages=[{"name": "persist", "type": "PERSIST", "config": {}}],
        aiql_template="",
        schema_name="sdlc",
        parser="record",
        filters=filters,
        graph_config=graph_config,
    )

    fetched = sqlite_store.get_pipeline(created["id"])
    assert fetched is not None
    assert fetched["schema_name"] == "sdlc"
    assert fetched["parser"] == "record"
    assert fetched["filters"] == filters
    assert fetched["graph_config"] == graph_config


def test_update_pipeline_schema(sqlite_store):
    """Update schema_name on an existing pipeline."""
    created = sqlite_store.create_pipeline(
        tenant_id="t1",
        name="Update Test",
        description="desc",
        stages=[{"name": "chunk", "type": "CHUNK", "config": {}}],
        aiql_template="",
        schema_name="generic",
        parser="paragraph",
    )

    updated = sqlite_store.update_pipeline(created["id"], schema_name="healthcare")
    assert updated is not None
    assert updated["schema_name"] == "healthcare"
    # parser should remain unchanged
    assert updated["parser"] == "paragraph"


def test_default_values_for_new_fields(sqlite_store):
    """Create a pipeline without new fields and verify defaults."""
    result = sqlite_store.create_pipeline(
        tenant_id="t1",
        name="Defaults Test",
        description="desc",
        stages=[{"name": "persist", "type": "PERSIST", "config": {}}],
        aiql_template="",
    )

    assert result["schema_name"] == ""
    assert result["parser"] == ""
    assert result["filters"] == {}
    assert result["graph_config"] == {}
