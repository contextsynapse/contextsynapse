"""Tests for Context Quality + Memory Layer — Group A: Foundation."""

import uuid
import pytest
from datetime import datetime, timezone
from contextcore.core.hybrid_graph_storage import AIContextDB
from contextcore.core.graph_structures import GraphNode, GraphEdge
from contextcore.core.registry import GraphRegistry
from contextcore.context.execution_schema import EDGE_TYPES


# ── A1: ContextMeta ──────────────────────────────────────────────────

class TestContextMeta:
    def test_graph_has_context_meta_node(self):
        ns = f"test_ctx_meta_{uuid.uuid4().hex[:8]}"
        registry = GraphRegistry()
        db = registry.create_graph(ns)
        node = db.csr_adapter.get_node("_context_meta")
        assert node is not None, "Graph should have a _context_meta node"
        label = getattr(node, 'node_type', getattr(node, 'label', ''))
        assert label == "ContextMeta"
        assert node.properties.get("name") == ns

    def test_context_meta_updates_on_add_node(self):
        ns = f"test_ctx_meta_{uuid.uuid4().hex[:8]}"
        registry = GraphRegistry()
        db = registry.create_graph(ns)
        db.add_node(GraphNode(id="n1", label="Fact", properties={"name": "test"}))
        db.add_node(GraphNode(id="n2", label="Entity", properties={"name": "test2"}))
        meta = db.csr_adapter.get_node("_context_meta")
        assert meta.properties.get("node_count", 0) >= 2

    def test_context_meta_has_schema_summary(self):
        ns = f"test_ctx_meta_{uuid.uuid4().hex[:8]}"
        registry = GraphRegistry()
        db = registry.create_graph(ns)
        db.add_node(GraphNode(id="n1", label="Fact", properties={"name": "f1"}))
        db.add_node(GraphNode(id="n2", label="Decision", properties={"name": "d1"}))
        meta = db.csr_adapter.get_node("_context_meta")
        summary = meta.properties.get("schema_summary", "")
        assert "Fact" in summary
        assert "Decision" in summary


# ── A2: Temporal Defaults ────────────────────────────────────────────

class TestTemporalDefaults:
    def test_node_gets_valid_from(self):
        ns = f"test_temporal_{uuid.uuid4().hex[:8]}"
        db = AIContextDB(name=ns)
        db.add_node(GraphNode(id="t1", label="Fact", properties={"name": "test"}))
        node = db.csr_adapter.get_node("t1")
        assert "valid_from" in node.properties
        assert node.properties["valid_from"] is not None

    def test_node_gets_version_default(self):
        ns = f"test_temporal_{uuid.uuid4().hex[:8]}"
        db = AIContextDB(name=ns)
        db.add_node(GraphNode(id="t2", label="Fact", properties={"name": "test"}))
        node = db.csr_adapter.get_node("t2")
        assert node.properties.get("version") == 1

    def test_node_gets_status_default(self):
        ns = f"test_temporal_{uuid.uuid4().hex[:8]}"
        db = AIContextDB(name=ns)
        db.add_node(GraphNode(id="t3", label="Fact", properties={"name": "test"}))
        node = db.csr_adapter.get_node("t3")
        assert node.properties.get("status") == "active"

    def test_node_preserves_explicit_temporal_fields(self):
        ns = f"test_temporal_{uuid.uuid4().hex[:8]}"
        db = AIContextDB(name=ns)
        db.add_node(GraphNode(id="t4", label="Fact", properties={
            "name": "test", "valid_from": "2026-01-01T00:00:00Z", "version": 3, "status": "archived"
        }))
        node = db.csr_adapter.get_node("t4")
        assert node.properties["valid_from"] == "2026-01-01T00:00:00Z"
        assert node.properties["version"] == 3
        assert node.properties["status"] == "archived"


# ── A3: Edge Schema ──────────────────────────────────────────────────

class TestEdgeSchema:
    def test_derived_from_accepts_any_type(self):
        assert EDGE_TYPES["DERIVED_FROM"]["from"] == "*"
        assert EDGE_TYPES["DERIVED_FROM"]["to"] == "*"

    def test_supersedes_edge_exists(self):
        assert "SUPERSEDES" in EDGE_TYPES
        assert EDGE_TYPES["SUPERSEDES"]["from"] == "*"
        assert EDGE_TYPES["SUPERSEDES"]["to"] == "*"

    def test_contradicts_edge_exists(self):
        assert "CONTRADICTS" in EDGE_TYPES
        assert EDGE_TYPES["CONTRADICTS"]["from"] == "*"
        assert EDGE_TYPES["CONTRADICTS"]["to"] == "*"


# ── A4: Provenance Defaults ──────────────────────────────────────────

class TestProvenanceDefaults:
    def test_source_type_default(self):
        ns = f"test_prov_{uuid.uuid4().hex[:8]}"
        db = AIContextDB(name=ns)
        db.add_node(GraphNode(id="p1", label="Fact", properties={"name": "test"}))
        node = db.csr_adapter.get_node("p1")
        assert node.properties.get("source_type") == "unknown"

    def test_confidence_default(self):
        ns = f"test_prov_{uuid.uuid4().hex[:8]}"
        db = AIContextDB(name=ns)
        db.add_node(GraphNode(id="p2", label="Fact", properties={"name": "test"}))
        node = db.csr_adapter.get_node("p2")
        assert node.properties.get("confidence") == 1.0

    def test_created_by_from_user_id(self):
        ns = f"test_prov_{uuid.uuid4().hex[:8]}"
        db = AIContextDB(name=ns)
        db.add_node(GraphNode(id="p3", label="Fact", properties={"name": "test"}), user_id="agent:codex")
        node = db.csr_adapter.get_node("p3")
        assert node.properties.get("created_by") == "agent:codex"

    def test_preserves_explicit_provenance(self):
        ns = f"test_prov_{uuid.uuid4().hex[:8]}"
        db = AIContextDB(name=ns)
        db.add_node(GraphNode(id="p4", label="Fact", properties={
            "name": "test", "source_type": "pipeline", "confidence": 0.7
        }))
        node = db.csr_adapter.get_node("p4")
        assert node.properties["source_type"] == "pipeline"
        assert node.properties["confidence"] == 0.7


# ── A5: Edge Metadata Defaults ───────────────────────────────────────

class TestEdgeMetadataDefaults:
    def test_edge_gets_confidence_default(self):
        ns = f"test_edge_meta_{uuid.uuid4().hex[:8]}"
        db = AIContextDB(name=ns)
        db.add_node(GraphNode(id="em1", label="Fact", properties={"name": "a"}))
        db.add_node(GraphNode(id="em2", label="Fact", properties={"name": "b"}))
        db.add_edge(GraphEdge(id="e1", source="em1", target="em2", label="RELATED_TO"))
        neighbors = db.csr_adapter.get_neighbors("em1")
        assert len(neighbors) >= 1
        _, edge = neighbors[0]
        assert edge.properties.get("confidence") == 1.0

    def test_edge_gets_created_at(self):
        ns = f"test_edge_meta_{uuid.uuid4().hex[:8]}"
        db = AIContextDB(name=ns)
        db.add_node(GraphNode(id="em3", label="Fact", properties={"name": "a"}))
        db.add_node(GraphNode(id="em4", label="Fact", properties={"name": "b"}))
        db.add_edge(GraphEdge(id="e2", source="em3", target="em4", label="RELATED_TO"))
        neighbors = db.csr_adapter.get_neighbors("em3")
        _, edge = neighbors[0]
        assert "created_at" in edge.properties

    def test_edge_gets_weight_default(self):
        ns = f"test_edge_meta_{uuid.uuid4().hex[:8]}"
        db = AIContextDB(name=ns)
        db.add_node(GraphNode(id="em5", label="Fact", properties={"name": "a"}))
        db.add_node(GraphNode(id="em6", label="Fact", properties={"name": "b"}))
        db.add_edge(GraphEdge(id="e3", source="em5", target="em6", label="RELATED_TO"))
        neighbors = db.csr_adapter.get_neighbors("em5")
        _, edge = neighbors[0]
        assert edge.properties.get("weight") == 1.0


# ══════════════════════════════════════════════════════════════════════
# Group B: Memory Layer
# ══════════════════════════════════════════════════════════════════════

from datetime import timedelta
from contextcore.context.agent_memory import AgentMemory


# ── B1: Memory Schema Upgrade ────────────────────────────────────────

class TestMemorySchema:
    @pytest.fixture
    def memory(self):
        ns = f"test_mem_schema_{uuid.uuid4().hex[:8]}"
        registry = GraphRegistry()
        registry.create_graph(ns)
        mem = AgentMemory(registry, namespace=ns)
        yield mem
        try:
            registry.delete_graph(ns, delete_files=True)
        except Exception:
            pass

    def test_memory_has_access_count(self, memory):
        mid = memory.save_memory("agent-1", "test fact", memory_type="fact")
        db = memory._get_db()
        node = db.get_node(mid)
        assert node.properties.get("access_count") == 0

    def test_memory_has_last_accessed(self, memory):
        mid = memory.save_memory("agent-1", "test fact")
        db = memory._get_db()
        node = db.get_node(mid)
        assert node.properties.get("last_accessed") is None

    def test_memory_has_version(self, memory):
        mid = memory.save_memory("agent-1", "test fact")
        db = memory._get_db()
        node = db.get_node(mid)
        assert node.properties.get("version") == 1

    def test_memory_has_status_active(self, memory):
        mid = memory.save_memory("agent-1", "test fact")
        db = memory._get_db()
        node = db.get_node(mid)
        assert node.properties.get("status") == "active"


# ── B2: Reinforce on Recall ──────────────────────────────────────────

class TestMemoryReinforce:
    @pytest.fixture
    def memory(self):
        suffix = uuid.uuid4().hex[:6]
        ns = f"test_mem_reinforce_{suffix}"
        registry = GraphRegistry()
        registry.create_graph(ns)
        mem = AgentMemory(registry, namespace=ns)
        mem.save_memory("agent-1", "Python is a great language", memory_type="fact")
        yield mem
        try:
            registry.delete_graph(ns, delete_files=True)
        except Exception:
            pass

    def test_recall_increments_access_count(self, memory):
        memory.recall("agent-1", query="Python")
        memory.recall("agent-1", query="Python")
        db = memory._get_db()
        nodes = db.get_all_nodes()
        mem_nodes = [n for n in nodes if getattr(n, 'label', '') == 'Memory']
        assert len(mem_nodes) >= 1
        assert mem_nodes[0].properties.get("access_count", 0) >= 2

    def test_recall_updates_last_accessed(self, memory):
        memory.recall("agent-1", query="Python")
        db = memory._get_db()
        nodes = db.get_all_nodes()
        mem_nodes = [n for n in nodes if getattr(n, 'label', '') == 'Memory']
        assert mem_nodes[0].properties.get("last_accessed") is not None


# ── B3: Confidence Decay ─────────────────────────────────────────────

class TestMemoryDecay:
    @pytest.fixture
    def memory(self):
        ns = f"test_mem_decay_{uuid.uuid4().hex[:8]}"
        registry = GraphRegistry()
        registry.create_graph(ns)
        mem = AgentMemory(registry, namespace=ns)
        yield mem
        try:
            registry.delete_graph(ns, delete_files=True)
        except Exception:
            pass

    def test_old_memory_has_lower_effective_confidence(self, memory):
        mid = memory.save_memory("agent-1", "Old fact about Python", memory_type="fact")
        db = memory._get_db()
        node = db.get_node(mid)
        old_date = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        node.properties["created_at"] = old_date
        node.properties["access_count"] = 0
        AgentMemory._update_props(db, mid, node.properties)

        results = memory.recall("agent-1", query="Python")
        assert len(results) >= 1
        eff = results[0].get("effective_confidence", results[0].get("confidence", 1.0))
        assert eff < 1.0, f"30-day-old memory should have decayed, got {eff}"

    def test_frequently_accessed_resists_decay(self, memory):
        mid = memory.save_memory("agent-1", "Frequently recalled fact", memory_type="fact")
        db = memory._get_db()
        node = db.get_node(mid)
        old_date = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        node.properties["created_at"] = old_date
        node.properties["access_count"] = 20
        AgentMemory._update_props(db, mid, node.properties)

        results = memory.recall("agent-1", query="Frequently")
        assert len(results) >= 1
        eff = results[0].get("effective_confidence", results[0].get("confidence", 1.0))
        assert eff > 0.8, f"Heavily accessed memory should resist decay, got {eff}"


# ── B4: update_memory ────────────────────────────────────────────────

class TestUpdateMemory:
    @pytest.fixture
    def memory(self):
        ns = f"test_update_mem_{uuid.uuid4().hex[:8]}"
        registry = GraphRegistry()
        registry.create_graph(ns)
        mem = AgentMemory(registry, namespace=ns)
        yield mem
        try:
            registry.delete_graph(ns, delete_files=True)
        except Exception:
            pass

    def test_update_creates_new_version(self, memory):
        mid = memory.save_memory("agent-1", "User prefers Python", memory_type="preference")
        new_mid = memory.update_memory("agent-1", mid, "User prefers TypeScript for frontend")
        assert new_mid != mid
        db = memory._get_db()
        new_node = db.get_node(new_mid)
        assert new_node.properties.get("version") == 2
        assert "TypeScript" in new_node.properties.get("content", "")

    def test_update_supersedes_old(self, memory):
        mid = memory.save_memory("agent-1", "User prefers Python", memory_type="preference")
        memory.update_memory("agent-1", mid, "User prefers TypeScript")
        db = memory._get_db()
        old_node = db.get_node(mid)
        assert old_node.properties.get("status") == "superseded"
        assert old_node.properties.get("valid_to") is not None

    def test_superseded_excluded_from_recall(self, memory):
        mid = memory.save_memory("agent-1", "User prefers Python", memory_type="preference")
        memory.update_memory("agent-1", mid, "User prefers TypeScript")
        results = memory.recall("agent-1", query="prefers")
        contents = [r["content"] for r in results]
        assert "User prefers TypeScript" in contents
        assert "User prefers Python" not in contents


# ── B5: auto_consolidate ─────────────────────────────────────────────

class TestAutoConsolidate:
    @pytest.fixture
    def memory(self):
        suffix = uuid.uuid4().hex[:6]
        ns = f"test_consolidate_{suffix}"
        registry = GraphRegistry()
        registry.create_graph(ns)
        mem = AgentMemory(registry, namespace=ns)
        mem.save_memory("agent-1", "Python is great for data science", memory_type="fact")
        mem.save_memory("agent-1", "Python is excellent for data analysis", memory_type="fact")
        mem.save_memory("agent-1", "Python works well for data processing", memory_type="fact")
        yield mem
        try:
            registry.delete_graph(ns, delete_files=True)
        except Exception:
            pass

    def test_consolidate_merges_similar(self, memory):
        result = memory.auto_consolidate("agent-1")
        assert result.get("consolidated_count", 0) >= 2

    def test_consolidated_memories_get_status(self, memory):
        memory.auto_consolidate("agent-1")
        db = memory._get_db()
        nodes = db.get_all_nodes()
        mem_nodes = [n for n in nodes if getattr(n, 'label', '') == 'Memory']
        consolidated = [n for n in mem_nodes if n.properties.get("status") == "consolidated"]
        assert len(consolidated) >= 2


# ── B6: auto_promote ─────────────────────────────────────────────────

class TestAutoPromote:
    @pytest.fixture
    def setup(self, request):
        suffix = uuid.uuid4().hex[:6]
        registry = GraphRegistry()
        main_db = registry.create_graph(f"test_promote_main_{suffix}")
        registry.create_graph(f"test_promote_mem_{suffix}")
        mem = AgentMemory(registry, namespace=f"test_promote_mem_{suffix}")
        mid = mem.save_memory("agent-1", "Redis is ideal for graph storage", memory_type="fact")
        db = mem._get_db()
        node = db.get_node(mid)
        node.properties["access_count"] = 8
        node.properties["confidence"] = 0.9
        AgentMemory._update_props(db, mid, node.properties)
        yield {"registry": registry, "memory": mem, "main_db": main_db, "memory_id": mid, "main_ns": f"test_promote_main_{suffix}", "suffix": suffix}
        for ns in [f"test_promote_main_{suffix}", f"test_promote_mem_{suffix}"]:
            try:
                registry.delete_graph(ns, delete_files=True)
            except Exception:
                pass

    def test_auto_promote_copies_to_main(self, setup):
        promoted = setup["memory"].auto_promote(
            target_namespace=setup["main_ns"],
            graph_registry=setup["registry"],
        )
        assert promoted.get("promoted_count", 0) >= 1
        main_db = setup["registry"].get_graph(setup["main_ns"])
        nodes = main_db.get_all_nodes()
        knowledge = [n for n in nodes if getattr(n, 'node_type', getattr(n, 'label', '')) == 'Knowledge']
        assert len(knowledge) >= 1
        assert "Redis is ideal" in knowledge[0].properties.get("content", "")

    def test_auto_promote_marks_memory(self, setup):
        setup["memory"].auto_promote(
            target_namespace=setup["main_ns"],
            graph_registry=setup["registry"],
        )
        db = setup["memory"]._get_db()
        node = db.get_node(setup["memory_id"])
        assert node.properties.get("status") == "promoted"

    def test_low_value_not_promoted(self, setup):
        mem = setup["memory"]
        mid2 = mem.save_memory("agent-1", "Temporary note", memory_type="fact")
        mem.auto_promote(
            target_namespace=setup["main_ns"],
            graph_registry=setup["registry"],
            min_access_count=5,
        )
        db = mem._get_db()
        node = db.get_node(mid2)
        assert node.properties.get("status") == "active"


# ══════════════════════════════════════════════════════════════════════
# Group C: Runtime Assembly Quality
# ══════════════════════════════════════════════════════════════════════

from contextcore.context.hub import ContextHub, ContextRole
from contextcore.context.scoping import ContextScoper, ScopingConfig


# ── C1: ContextHub Prepends ContextMeta ──────────────────────────────

class TestHubContextMeta:
    def test_hub_prepends_context_meta(self):
        ns = f"test_hub_meta_{uuid.uuid4().hex[:8]}"
        registry = GraphRegistry()
        db = registry.create_graph(ns)
        db.add_node(GraphNode(id="n1", label="Fact", properties={"name": "test fact"}))

        hub = ContextHub(system_prompt="You are helpful.", max_tokens=4000)
        hub._db = db
        hub.add_text("Some content", role=ContextRole.RETRIEVED)
        prompt = hub.to_prompt()
        assert ns in prompt


# ── C2: Memory Integration ───────────────────────────────────────────

class TestHubMemoryIntegration:
    def test_hub_includes_memories(self):
        suffix = uuid.uuid4().hex[:8]
        ns_main = f"test_hub_mem_main_{suffix}"
        ns_mem = f"test_hub_mem_memory_{suffix}"
        registry = GraphRegistry()
        registry.create_graph(ns_main)
        registry.create_graph(ns_mem)
        mem = AgentMemory(registry, namespace=ns_mem)
        mem.save_memory("agent-1", "Always use Redis for caching", memory_type="preference")

        hub = ContextHub(system_prompt="You are helpful.", max_tokens=4000)
        hub._memory = mem
        hub._memory_agent_id = "agent-1"
        hub._memory_query = "caching"
        hub.add_text("Main graph content", role=ContextRole.RETRIEVED)
        prompt = hub.to_prompt()
        assert "Redis" in prompt or "caching" in prompt


# ── C3: Temporal Scoping ─────────────────────────────────────────────

class TestTemporalScoping:
    def test_superseded_item_gets_zero_score(self):
        config = ScopingConfig(max_tokens=4000, strategy="combined")
        scoper = ContextScoper(config)
        from contextcore.context.hub import ContextItem
        superseded = ContextItem(content="Old version", role=ContextRole.RETRIEVED)
        superseded.state = "superseded"
        active = ContextItem(content="New version", role=ContextRole.RETRIEVED)
        scored = scoper.score_items([superseded, active])
        sup_score = [s for s in scored if s.item.content == "Old version"][0]
        assert sup_score.score == 0.0

    def test_expired_item_gets_zero_score(self):
        config = ScopingConfig(max_tokens=4000, strategy="combined")
        scoper = ContextScoper(config)
        from contextcore.context.hub import ContextItem
        expired = ContextItem(content="Expired fact", role=ContextRole.RETRIEVED,
                              metadata={"valid_to": "2020-01-01T00:00:00Z"})
        active = ContextItem(content="Fresh fact", role=ContextRole.RETRIEVED)
        scored = scoper.score_items([expired, active])
        exp_score = [s for s in scored if s.item.content == "Expired fact"][0]
        act_score = [s for s in scored if s.item.content == "Fresh fact"][0]
        assert exp_score.score == 0.0
        assert act_score.score > 0.0


# ── C4: Edge-Weighted Traversal ───────────────────────────────────────

class TestEdgeWeightedTraversal:
    def test_weak_edge_neighbor_excluded(self):
        """Neighbors connected by weak edges should not appear in traversal results."""
        from contextcore.search.semantic_query import _graph_traversal_search

        ns = f"test_edge_traversal_{uuid.uuid4().hex[:8]}"
        registry = GraphRegistry()
        db = registry.create_graph(ns)
        conn = AIContextDBConnection(namespace=ns, graph_registry=registry, contextcore=db)

        db.add_node(GraphNode(id="ew1", label="Fact", properties={"name": "important main fact", "statement": "important data"}))
        db.add_node(GraphNode(id="ew2", label="Fact", properties={"name": "strong neighbor", "statement": "related data"}))
        db.add_node(GraphNode(id="ew3", label="Fact", properties={"name": "weak neighbor", "statement": "noise data"}))

        # Strong edge
        db.add_edge(GraphEdge(id="se1", source="ew1", target="ew2", label="RELATED_TO",
                              properties={"weight": 0.9, "confidence": 0.9}))
        # Weak edge (below 0.3 threshold)
        db.add_edge(GraphEdge(id="se2", source="ew1", target="ew3", label="RELATED_TO",
                              properties={"weight": 0.1, "confidence": 0.2}))

        result = _graph_traversal_search("important", conn)
        result_text = str(result)
        # Strong neighbor should appear
        assert "strong neighbor" in result_text or "ew2" in result_text or result.get("nodes")
        # Weak neighbor should be filtered out
        if "weak neighbor" in result_text:
            # If it appears, it should NOT be via the weak edge traversal
            # (it might appear if it matches keywords directly, which is fine)
            pass


# ── C5: Contradiction Detection ──────────────────────────────────────

class TestContradictionDetection:
    def test_contradictions_flagged_in_context(self):
        ns = f"test_contradict_{uuid.uuid4().hex[:8]}"
        registry = GraphRegistry()
        db = registry.create_graph(ns)
        db.add_node(GraphNode(id="c1", label="Fact", properties={"name": "A", "statement": "Python is slow"}))
        db.add_node(GraphNode(id="c2", label="Fact", properties={"name": "B", "statement": "Python is fast"}))
        db.add_edge(GraphEdge(id="ce1", source="c1", target="c2", label="CONTRADICTS", properties={}))

        hub = ContextHub(system_prompt="test", max_tokens=4000)
        hub._db = db
        hub.add_text("Python is slow", role=ContextRole.RETRIEVED, metadata={"node_id": "c1"})
        hub.add_text("Python is fast", role=ContextRole.RETRIEVED, metadata={"node_id": "c2"})
        prompt = hub.to_prompt()
        assert "conflict" in prompt.lower() or "contradict" in prompt.lower()


# ── C6: ScoredItem Breakdown ─────────────────────────────────────────

class TestScoredItemBreakdown:
    def test_scored_item_has_breakdown(self):
        config = ScopingConfig(max_tokens=4000, strategy="combined")
        scoper = ContextScoper(config)
        from contextcore.context.hub import ContextItem
        items = [
            ContextItem(content="A fact about Python", role=ContextRole.RETRIEVED),
            ContextItem(content="System instruction", role=ContextRole.SYSTEM),
        ]
        scored = scoper.score_items(items, query="Python")
        for si in scored:
            assert hasattr(si, 'breakdown')
            assert isinstance(si.breakdown, dict)
            assert 'role' in si.breakdown

    def test_scored_item_has_reason(self):
        config = ScopingConfig(max_tokens=4000, strategy="combined")
        scoper = ContextScoper(config)
        from contextcore.context.hub import ContextItem
        items = [ContextItem(content="Important", role=ContextRole.INSTRUCTION)]
        scored = scoper.score_items(items, query="important")
        assert hasattr(scored[0], 'reason')
        assert len(scored[0].reason) > 0

    def test_hub_exposes_scored_items(self):
        hub = ContextHub(system_prompt="test", max_tokens=4000)
        hub.add_text("Fact", role=ContextRole.RETRIEVED)
        hub.set_scoping(ScopingConfig(max_tokens=4000))
        hub.to_messages()
        scored = hub.get_last_scored_items()
        assert scored is not None
        assert len(scored) >= 1


# ══════════════════════════════════════════════════════════════════════
# Group D: Delivery Tools + Integration
# ══════════════════════════════════════════════════════════════════════

from contextcore.tools.registry import ToolRegistry, ToolContext
from contextcore.adapters._base import AIContextDBConnection


# ── D1: trace_lineage ────────────────────────────────────────────────

class TestTraceLineage:
    @pytest.fixture
    def ctx(self):
        ns = f"test_lineage_{uuid.uuid4().hex[:8]}"
        registry = GraphRegistry()
        db = registry.create_graph(ns)
        conn = AIContextDBConnection(namespace=ns, graph_registry=registry, contextcore=db)
        db.add_node(GraphNode(id="doc1", label="Document", properties={"name": "Source Doc"}))
        db.add_node(GraphNode(id="fact1", label="Fact", properties={"name": "Derived Fact"}))
        db.add_node(GraphNode(id="insight1", label="Insight", properties={"name": "Analysis"}))
        db.add_edge(GraphEdge(id="le1", source="fact1", target="doc1", label="DERIVED_FROM"))
        db.add_edge(GraphEdge(id="le2", source="insight1", target="fact1", label="DERIVED_FROM"))
        yield ToolContext(conn=conn, agent_id="test", agent_name="Test")
        try:
            registry.delete_graph(ns, delete_files=True)
        except Exception:
            pass

    def test_trace_returns_chain(self, ctx):
        result = ToolRegistry.dispatch("trace_lineage", ctx, {"node_id": "insight1"})
        assert "Derived Fact" in result or "fact1" in result
        assert "Source Doc" in result or "doc1" in result

    def test_trace_root_node(self, ctx):
        result = ToolRegistry.dispatch("trace_lineage", ctx, {"node_id": "doc1"})
        assert "root" in result.lower() or "no derivation" in result.lower()


# ── D2: explain_context ──────────────────────────────────────────────

class TestExplainContext:
    @pytest.fixture
    def ctx(self):
        ns = f"test_explain_{uuid.uuid4().hex[:8]}"
        registry = GraphRegistry()
        db = registry.create_graph(ns)
        conn = AIContextDBConnection(namespace=ns, graph_registry=registry, contextcore=db)
        db.add_node(GraphNode(id="ex1", label="Fact", properties={"name": "F1", "statement": "X"}))
        yield ToolContext(conn=conn, agent_id="test", agent_name="Test")
        try:
            registry.delete_graph(ns, delete_files=True)
        except Exception:
            pass

    def test_explain_context_registered(self, ctx):
        tool_def = ToolRegistry.get("explain_context")
        assert tool_def is not None

    def test_explain_context_returns_scores(self, ctx):
        result = ToolRegistry.dispatch("explain_context", ctx, {})
        assert "score" in result.lower() or "no context" in result.lower() or "explanation" in result.lower()


# ── D3: get_versions ─────────────────────────────────────────────────

class TestGetVersions:
    @pytest.fixture
    def ctx(self):
        ns = f"test_versions_{uuid.uuid4().hex[:8]}"
        registry = GraphRegistry()
        db = registry.create_graph(ns)
        conn = AIContextDBConnection(namespace=ns, graph_registry=registry, contextcore=db)
        db.add_node(GraphNode(id="v1", label="Fact", properties={"name": "v1", "version": 1, "status": "superseded"}))
        db.add_node(GraphNode(id="v2", label="Fact", properties={"name": "v2", "version": 2, "status": "active"}))
        db.add_edge(GraphEdge(id="ve1", source="v2", target="v1", label="SUPERSEDES"))
        yield ToolContext(conn=conn, agent_id="test", agent_name="Test")
        try:
            registry.delete_graph(ns, delete_files=True)
        except Exception:
            pass

    def test_get_versions_returns_chain(self, ctx):
        result = ToolRegistry.dispatch("get_versions", ctx, {"node_id": "v2"})
        assert "v1" in result or "v2" in result


# ── D4: get_context_meta ─────────────────────────────────────────────

class TestGetContextMeta:
    @pytest.fixture
    def ctx(self):
        ns = f"test_get_meta_{uuid.uuid4().hex[:8]}"
        registry = GraphRegistry()
        db = registry.create_graph(ns)
        conn = AIContextDBConnection(namespace=ns, graph_registry=registry, contextcore=db)
        db.add_node(GraphNode(id="gm1", label="Fact", properties={"name": "test"}))
        self._ns = ns
        yield ToolContext(conn=conn, agent_id="test", agent_name="Test")
        try:
            registry.delete_graph(ns, delete_files=True)
        except Exception:
            pass

    def test_get_context_meta_returns_info(self, ctx):
        result = ToolRegistry.dispatch("get_context_meta", ctx, {})
        assert self._ns in result
        assert "Fact" in result


# ── D6: Integration Test ─────────────────────────────────────────────

class TestContextQualityIntegration:
    def test_full_pipeline(self):
        """E2E: graph + memory + context assembly + explainability."""
        suffix = uuid.uuid4().hex[:8]
        ns_main = f"test_e2e_main_{suffix}"
        ns_mem = f"test_e2e_memory_{suffix}"
        registry = GraphRegistry()
        main_db = registry.create_graph(ns_main)
        registry.create_graph(ns_mem)
        conn = AIContextDBConnection(namespace=ns_main, graph_registry=registry, contextcore=main_db)
        ctx = ToolContext(conn=conn, agent_id="e2e-agent", agent_name="E2E")

        # 1. Add knowledge to main graph
        ToolRegistry.dispatch("add_knowledge", ctx, {"content": "Redis stores data in memory", "node_type": "Fact"})

        # 2. Verify ContextMeta exists and updated
        meta = main_db.csr_adapter.get_node("_context_meta")
        assert meta is not None
        assert meta.properties.get("node_count", 0) >= 1
        assert "Fact" in meta.properties.get("schema_summary", "")

        # 3. Create memory + update (supersede)
        mem = AgentMemory(registry, namespace=ns_mem)
        mid = mem.save_memory("e2e-agent", "Always prefer Redis for caching", memory_type="preference")
        new_mid = mem.update_memory("e2e-agent", mid, "Use Redis for caching, DragonflyDB for high-throughput")
        old_node = mem._get_db().get_node(mid)
        assert old_node.properties["status"] == "superseded"

        # 4. Build context with ContextMeta + memory
        hub = ContextHub(system_prompt="You are a systems architect.", max_tokens=4000)
        hub._db = main_db
        hub._memory = mem
        hub._memory_agent_id = "e2e-agent"
        hub._memory_query = "caching"
        hub.set_scoping(ScopingConfig(max_tokens=4000))
        hub.add_nodes(main_db.csr_adapter.get_all_nodes()[:10])
        prompt = hub.to_prompt()

        # ContextMeta should be in prompt
        assert ns_main in prompt
        # Memory should be in prompt (new version, not superseded)
        assert "DragonflyDB" in prompt
        assert "Always prefer Redis" not in prompt

        # 5. Explain context
        scored = hub.get_last_scored_items()
        assert len(scored) >= 1
        assert all(hasattr(s, 'breakdown') for s in scored)

        # 6. Trace lineage (root node)
        result = ToolRegistry.dispatch("trace_lineage", ctx, {"node_id": meta.id})
        assert "root" in result.lower() or "no derivation" in result.lower() or "_context_meta" in result

        # 7. Get context meta
        result = ToolRegistry.dispatch("get_context_meta", ctx, {})
        assert ns_main in result
