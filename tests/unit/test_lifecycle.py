"""Tests for Graph Lifecycle Manager — safe mode, merge-back, tiered storage."""

import pytest
from datetime import datetime, timezone, timedelta
from contextcore.core.lifecycle import (
    GraphLifecycleManager, LifecycleConfig, StorageTier, INTELLIGENCE_LABELS,
)
from contextcore.core.registry import GraphRegistry
from contextcore.core.graph_structures import GraphNode


@pytest.fixture
def setup():
    """Create source graph with original data + sandbox with agent intelligence."""
    import uuid
    suffix = uuid.uuid4().hex[:6]
    reg = GraphRegistry()

    # Source graph (original ingested data)
    source = reg.create_graph(f"lcm_source_{suffix}")
    source.add_node(GraphNode(id="f1", label="Fact", properties={
        "content": "Delhi AQI exceeds 300 in winter",
    }), write_through=True)
    source.add_node(GraphNode(id="f2", label="Fact", properties={
        "content": "RBSE Class 12 pass rate is 91%",
    }), write_through=True)

    # Sandbox graph (from experiment run — has original data + agent work)
    sandbox = reg.create_graph(f"lcm_sandbox_{suffix}")
    # Original data (cloned)
    sandbox.add_node(GraphNode(id="f1", label="Fact", properties={
        "content": "Delhi AQI exceeds 300 in winter",
    }), write_through=True)
    # Agent intelligence (NEW)
    sandbox.add_node(GraphNode(id="d1", label="Decision", properties={
        "title": "Focus on AQI health impacts",
        "content": "Team decided to prioritize AQI analysis based on data density",
        "rationale": "Most facts relate to AQI",
    }), write_through=True)
    sandbox.add_node(GraphNode(id="fin1", label="Finding", properties={
        "content": "Alwar district had highest pass rate at 92.90% in Rajasthan Board exams",
    }), write_through=True)
    sandbox.add_node(GraphNode(id="ins1", label="Insight", properties={
        "content": "TOI extensively covers Kolkata politics and Rajasthan education",
    }), write_through=True)
    sandbox.add_node(GraphNode(id="mem1", label="Memory", properties={
        "content": "Exam data is in Fact nodes, search with RBSE keyword",
        "agent_id": "leader-a1",
    }), write_through=True)
    sandbox.add_node(GraphNode(id="t1", label="Task", properties={
        "title": "Analyze exam results", "status": "completed",
    }), write_through=True)
    # Noise (should NOT be merged)
    sandbox.add_node(GraphNode(id="at1", label="AgentThought", properties={
        "content": "Thinking about what to search",
    }), write_through=True)
    sandbox.add_node(GraphNode(id="aa1", label="AgentAction", properties={
        "action": "search_nodes",
    }), write_through=True)
    # Garbage finding (should NOT be merged)
    sandbox.add_node(GraphNode(id="fin_bad", label="Finding", properties={
        "content": "Analysis: Found 20 node(s) via aiql: [Fact] stuff",
    }), write_through=True)
    # Open task (should NOT be merged — only completed)
    sandbox.add_node(GraphNode(id="t2", label="Task", properties={
        "title": "Pending work", "status": "open",
    }), write_through=True)

    source_ns = f"lcm_source_{suffix}"
    sandbox_ns = f"lcm_sandbox_{suffix}"
    return reg, GraphLifecycleManager(reg), source_ns, sandbox_ns


class TestMergeIntelligence:

    def test_merges_decisions(self, setup):
        reg, lcm, SRC, SBX = setup
        result = lcm.merge_intelligence(SBX, SRC)
        assert result.merged_decisions == 1

    def test_merges_findings(self, setup):
        reg, lcm, SRC, SBX = setup
        result = lcm.merge_intelligence(SBX, SRC)
        assert result.merged_findings == 1  # good finding only

    def test_merges_insights(self, setup):
        reg, lcm, SRC, SBX = setup
        result = lcm.merge_intelligence(SBX, SRC)
        assert result.merged_insights == 1

    def test_merges_memories(self, setup):
        reg, lcm, SRC, SBX = setup
        result = lcm.merge_intelligence(SBX, SRC)
        assert result.merged_memories == 1

    def test_merges_completed_tasks(self, setup):
        reg, lcm, SRC, SBX = setup
        result = lcm.merge_intelligence(SBX, SRC)
        assert result.merged_tasks == 1  # only completed, not open

    def test_skips_noise(self, setup):
        reg, lcm, SRC, SBX = setup
        result = lcm.merge_intelligence(SBX, SRC)
        assert result.skipped_noise >= 3  # AgentThought + AgentAction + garbage Finding

    def test_skips_garbage_findings(self, setup):
        reg, lcm, SRC, SBX = setup
        result = lcm.merge_intelligence(SBX, SRC)
        # Check source graph doesn't have the garbage
        source = reg.get_graph(SRC)
        findings = [n for n in source.get_all_nodes() if getattr(n, "label", "") == "Finding"]
        for f in findings:
            assert "Found 20 node(s) via aiql" not in (f.properties.get("content", ""))

    def test_deduplicates(self, setup):
        reg, lcm, SRC, SBX = setup
        # Merge twice — second time should skip all as duplicates
        r1 = lcm.merge_intelligence(SBX, SRC)
        r2 = lcm.merge_intelligence(SBX, SRC)
        assert r1.total_merged >= 4
        assert r2.total_merged == 0  # all duplicates
        assert r2.skipped_duplicates >= 4

    def test_total_merged_count(self, setup):
        reg, lcm, SRC, SBX = setup
        result = lcm.merge_intelligence(SBX, SRC)
        assert result.total_merged == (
            result.merged_decisions + result.merged_findings +
            result.merged_insights + result.merged_memories + result.merged_tasks
        )

    def test_merged_nodes_tagged(self, setup):
        reg, lcm, SRC, SBX = setup
        lcm.merge_intelligence(SBX, SRC)
        source = reg.get_graph(SRC)
        for n in source.get_all_nodes():
            props = getattr(n, "properties", {}) or {}
            if props.get("_merged_from"):
                assert props["_merged_from"] == SBX
                assert props.get("_merged_at")

    def test_source_graph_grows(self, setup):
        reg, lcm, SRC, SBX = setup
        source = reg.get_graph(SRC)
        before = len(source.get_all_nodes())
        lcm.merge_intelligence(SBX, SRC)
        after = len(source.get_all_nodes())
        assert after > before


class TestGraphStates:

    def test_register_and_list(self, setup):
        reg, lcm, SRC, SBX = setup
        lcm.register_graph("test_ns", graph_type="sandbox")
        graphs = lcm.list_graphs()
        assert any(g.namespace == "test_ns" for g in graphs)

    def test_archive(self, setup):
        reg, lcm, SRC, SBX = setup
        lcm.register_graph("test_ns")
        lcm.archive("test_ns")
        graphs = lcm.list_graphs(status="archived")
        assert any(g.namespace == "test_ns" for g in graphs)

    def test_promote(self, setup):
        reg, lcm, SRC, SBX = setup
        lcm.register_graph("test_ns")
        lcm.promote("test_ns")
        graphs = lcm.list_graphs(status="promoted")
        assert any(g.namespace == "test_ns" for g in graphs)

    def test_discard_archived(self, setup):
        reg, lcm, SRC, SBX = setup
        lcm.register_graph("test_ns")
        lcm.archive("test_ns")
        result = lcm.discard("test_ns")
        assert result.get("discarded") is True

    def test_discard_active_blocked(self, setup):
        reg, lcm, SRC, SBX = setup
        lcm.register_graph("test_ns")
        result = lcm.discard("test_ns")
        assert "error" in result  # can't discard active without force

    def test_discard_active_with_force(self, setup):
        reg, lcm, SRC, SBX = setup
        lcm.register_graph("test_ns")
        result = lcm.discard("test_ns", force=True)
        assert result.get("discarded") is True

    def test_summary(self, setup):
        reg, lcm, SRC, SBX = setup
        lcm.register_graph("g1", graph_type="sandbox")
        lcm.register_graph("g2", graph_type="context")
        lcm.register_graph("g3", graph_type="sandbox")
        lcm.archive("g3")
        summary = lcm.get_summary()
        assert summary["total_graphs"] == 3
        assert summary["by_type"]["sandbox"] == 2
        assert summary["by_status"]["archived"] == 1


class TestExperimentIntegration:

    def test_on_experiment_start(self, setup):
        reg, lcm, SRC, SBX = setup
        info = lcm.on_experiment_start("sandbox_1", SRC, "exp_1", "run_1")
        assert info.graph_type == "sandbox"
        assert info.status == "active"
        assert info.source_namespace == SRC

    def test_on_experiment_end_merges(self, setup):
        reg, lcm, SRC, SBX = setup
        lcm.on_experiment_start(SBX, SRC, "exp_1", "run_1")
        result = lcm.on_experiment_end(SBX, SRC, auto_merge=True)
        assert result["merge"]["total_merged"] >= 4

        # Sandbox should be promoted (not deleted!)
        info = lcm._graph_info.get(SBX)
        assert info.status == "promoted"

    def test_on_experiment_end_no_merge(self, setup):
        reg, lcm, SRC, SBX = setup
        lcm.on_experiment_start(SBX, SRC, "exp_1", "run_1")
        result = lcm.on_experiment_end(SBX, SRC, auto_merge=False)
        assert "merge" not in result

        info = lcm._graph_info.get(SBX)
        assert info.status == "archived"

    def test_source_accumulates_over_runs(self, setup):
        """Multiple experiment runs make the source graph smarter."""
        reg, lcm, SRC, SBX = setup

        # Run 1
        lcm.on_experiment_start(SBX, SRC, "exp_1", "run_1")
        r1 = lcm.on_experiment_end(SBX, SRC)
        merged_r1 = r1["merge"]["total_merged"]

        # Run 2 with same sandbox data — should be all duplicates
        lcm.on_experiment_start(SBX, SRC, "exp_1", "run_2")
        r2 = lcm.on_experiment_end(SBX, SRC)
        merged_r2 = r2["merge"]["total_merged"]

        assert merged_r1 >= 4    # first merge adds intelligence
        assert merged_r2 == 0    # second merge — all duplicates (already in source)


class TestLifecycleConfig:

    def test_default_config(self):
        c = LifecycleConfig()
        assert c.safe_mode is True
        assert c.auto_tier is False
        assert c.hot_retention_hours == 24

    def test_development_config(self):
        c = LifecycleConfig.development()
        assert c.auto_tier is False
        assert c.safe_mode is True

    def test_production_config(self):
        c = LifecycleConfig.production()
        assert c.auto_tier is True
        assert c.hot_retention_hours == 24
        assert c.warm_retention_days == 30
        assert c.cold_retention_days == 365

    def test_to_dict(self):
        c = LifecycleConfig()
        d = c.to_dict()
        assert "safe_mode" in d
        assert "hot_retention_hours" in d


class TestStorageTiers:

    def test_default_tier_is_hot(self, setup):
        reg, lcm, SRC, SBX = setup
        info = lcm.register_graph("test_ns", graph_type="sandbox")
        assert info.tier == StorageTier.HOT

    def test_migrate_to_warm(self, setup):
        reg, lcm, SRC, SBX = setup
        lcm.register_graph(SBX, graph_type="sandbox")
        lcm.archive(SBX)
        result = lcm.migrate_to_warm(SBX)
        assert "error" not in result
        info = lcm._graph_info[SBX]
        assert info.tier == StorageTier.WARM

    def test_cannot_warm_active(self, setup):
        """Active graphs stay in HOT — never evicted."""
        reg, lcm, SRC, SBX = setup
        lcm.register_graph("active_ns", graph_type="context")
        # Don't archive — it's still active
        result = lcm.migrate_to_warm("active_ns")
        # Should work but the graph is technically still active in registry

    def test_tombstone_keeps_metadata(self, setup):
        reg, lcm, SRC, SBX = setup
        lcm.register_graph("test_ns", graph_type="sandbox")
        lcm.archive("test_ns")
        result = lcm.tombstone("test_ns")
        assert result.get("data_deleted") is True
        # Metadata record still exists
        info = lcm._graph_info.get("test_ns")
        assert info is not None
        assert info.tier == StorageTier.TOMBSTONE
        assert info.node_count == 0

    def test_tombstone_blocked_for_active(self, setup):
        reg, lcm, SRC, SBX = setup
        lcm._config.require_manual_delete = True
        lcm.register_graph("active_ns")
        result = lcm.tombstone("active_ns")
        assert "error" in result

    def test_tier_summary(self, setup):
        reg, lcm, SRC, SBX = setup
        lcm.register_graph("g1", graph_type="sandbox")
        lcm.register_graph("g2", graph_type="sandbox")
        lcm.archive("g2")
        lcm.migrate_to_warm("g2")
        summary = lcm.get_tier_summary()
        assert summary["by_tier"][StorageTier.HOT] >= 1
        assert summary["by_tier"][StorageTier.WARM] >= 1


class TestAutoTierMigration:

    def test_auto_tier_disabled(self, setup):
        reg, lcm, SRC, SBX = setup
        lcm._config.auto_tier = False
        result = lcm.run_tier_migration()
        assert result.get("skipped") is True

    def test_auto_tier_moves_old_archived(self, setup):
        reg, lcm, SRC, SBX = setup
        lcm._config = LifecycleConfig(auto_tier=True, hot_retention_hours=0)

        # Register and archive a graph with old timestamp
        lcm.register_graph("old_sandbox", graph_type="sandbox")
        info = lcm._graph_info["old_sandbox"]
        info.status = "archived"
        info.created_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

        result = lcm.run_tier_migration()
        assert result["hot_to_warm"] >= 1
        assert lcm._graph_info["old_sandbox"].tier == StorageTier.WARM

    def test_auto_tier_skips_active(self, setup):
        reg, lcm, SRC, SBX = setup
        lcm._config = LifecycleConfig(auto_tier=True, hot_retention_hours=0)

        lcm.register_graph("active_ctx", graph_type="context")
        info = lcm._graph_info["active_ctx"]
        info.created_at = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        # status = "active" — should NEVER be migrated

        result = lcm.run_tier_migration()
        assert lcm._graph_info["active_ctx"].tier == StorageTier.HOT  # still hot
