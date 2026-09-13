"""Tests for pipeline scheduler (single PipelineStore, _get_due_pipelines)."""
import pytest
from datetime import datetime, timezone, timedelta


def _make_store():
    from contextcore.pipelines.models import PipelineStore
    return PipelineStore(redis_url=None)


def test_scheduler_finds_due_pipelines():
    from contextcore.pipelines.scheduler import PipelineScheduler
    from contextcore.pipelines.models import Pipeline

    store = _make_store()
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(minutes=60)).isoformat()

    p_due = Pipeline(name="due", source_url="http://a.com", trigger_type="scheduled",
                     status="active", next_run_at=past)
    p_not_due = Pipeline(name="not_due", source_url="http://b.com", trigger_type="scheduled",
                         status="active", next_run_at=future)
    p_paused = Pipeline(name="paused", source_url="http://c.com", trigger_type="scheduled",
                        status="paused", next_run_at=past)

    store.save(p_due)
    store.save(p_not_due)
    store.save(p_paused)

    scheduler = PipelineScheduler(store=store)
    due = scheduler._get_due_pipelines()

    due_ids = {p.id for p in due}
    assert p_due.id in due_ids
    assert p_not_due.id not in due_ids
    assert p_paused.id not in due_ids


def test_scheduler_skips_one_time_and_webhook():
    from contextcore.pipelines.scheduler import PipelineScheduler
    from contextcore.pipelines.models import Pipeline

    store = _make_store()
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()

    p_once = Pipeline(name="once", source_url="http://a.com", trigger_type="one_time",
                      status="active", next_run_at=past)
    p_hook = Pipeline(name="hook", source_url="http://b.com", trigger_type="webhook",
                      status="active", next_run_at=past)
    store.save(p_once)
    store.save(p_hook)

    scheduler = PipelineScheduler(store=store)
    due = scheduler._get_due_pipelines()
    assert len(due) == 0


def test_scheduler_includes_first_run():
    """Pipeline with empty next_run_at should be considered due (first run)."""
    from contextcore.pipelines.scheduler import PipelineScheduler
    from contextcore.pipelines.models import Pipeline

    store = _make_store()
    p = Pipeline(name="first", source_url="http://a.com", trigger_type="scheduled",
                 status="active", next_run_at="")
    store.save(p)

    scheduler = PipelineScheduler(store=store)
    due = scheduler._get_due_pipelines()
    assert len(due) == 1
    assert due[0].id == p.id


def test_scheduler_skips_draft_pipelines():
    """Draft pipelines (no context attached) must not be picked up by the scheduler."""
    from contextcore.pipelines.scheduler import PipelineScheduler
    from contextcore.pipelines.models import Pipeline

    store = _make_store()
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()

    p_draft = Pipeline(name="draft_pipeline", source_url="http://a.com", trigger_type="scheduled",
                       status="draft", next_run_at=past)
    p_active = Pipeline(name="active_pipeline", source_url="http://b.com", trigger_type="scheduled",
                        status="active", next_run_at=past)
    store.save(p_draft)
    store.save(p_active)

    scheduler = PipelineScheduler(store=store)
    due = scheduler._get_due_pipelines()

    due_ids = {p.id for p in due}
    assert p_draft.id not in due_ids, "Draft pipeline must not be scheduled"
    assert p_active.id in due_ids, "Active pipeline must be scheduled"


def test_scheduler_store_property():
    """PipelineScheduler.store returns the PipelineStore."""
    from contextcore.pipelines.scheduler import PipelineScheduler

    store = _make_store()
    scheduler = PipelineScheduler(store=store)
    assert scheduler.store is store
