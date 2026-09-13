"""Tests for Contextual Graph Projection."""

import pytest
from unittest.mock import MagicMock


class FakeNode:
    def __init__(self, id, label, properties):
        self.id = id
        self.label = label
        self.properties = properties


def make_runtime_db(nodes=None):
    """Mock runtime graph db."""
    db = MagicMock()
    db.get_all_nodes.return_value = nodes or []
    db.csr_adapter = MagicMock()
    db.csr_adapter.get_all_nodes.return_value = nodes or []
    return db


class TestIntentExtractor:

    def test_no_task_returns_zero_confidence(self):
        from contextcore.context.projection import IntentExtractor
        intent = IntentExtractor.extract(tasks=[], runtime_db=None, agent_name="claude")
        assert intent.confidence == 0.0
        assert len(intent.keywords) == 0

    def test_task_only_extracts_keywords(self):
        from contextcore.context.projection import IntentExtractor
        tasks = [{"title": "Audit auth module for vulnerabilities", "description": "Check token storage and session handling", "id": "t1", "priority": "high", "status": "in_progress"}]
        intent = IntentExtractor.extract(tasks=tasks, runtime_db=None, agent_name="claude")
        assert intent.confidence == 0.3
        assert "auth" in intent.keywords
        assert "vulnerabilities" in intent.keywords
        assert "token" in intent.keywords
        assert intent.task_title == "Audit auth module for vulnerabilities"

    def test_task_plus_findings_raises_confidence(self):
        from contextcore.context.projection import IntentExtractor
        tasks = [{"title": "Audit auth module", "id": "t1", "priority": "high", "status": "in_progress"}]
        findings = [
            FakeNode("f1", "Finding", {"name": "Token refresh has no rate limiting", "_agent_name": "claude"}),
            FakeNode("f2", "Finding", {"name": "Session tokens stored in plaintext", "_agent_name": "claude"}),
        ]
        rt_db = make_runtime_db(findings)
        intent = IntentExtractor.extract(tasks=tasks, runtime_db=rt_db, agent_name="claude")
        assert intent.confidence >= 0.6
        assert "token" in intent.keywords
        assert "rate" in intent.keywords or "plaintext" in intent.keywords

    def test_cross_agent_signals_included(self):
        from contextcore.context.projection import IntentExtractor
        tasks = [{"title": "Audit auth module", "id": "t1", "priority": "high", "status": "in_progress"}]
        nodes = [
            FakeNode("f1", "Finding", {"name": "SQL injection in login endpoint", "_agent_name": "gemini"}),
            FakeNode("f2", "Finding", {"name": "Auth bypass via token reuse", "_agent_name": "chatgpt"}),
            FakeNode("a1", "AgentAction", {"description": "Reviewed auth middleware", "agent": "claude"}),
        ]
        rt_db = make_runtime_db(nodes)
        intent = IntentExtractor.extract(tasks=tasks, runtime_db=rt_db, agent_name="claude")
        assert intent.confidence >= 0.8
        assert "injection" in intent.keywords or "login" in intent.keywords
        assert "bypass" in intent.keywords or "token" in intent.keywords

    def test_stopwords_excluded(self):
        from contextcore.context.projection import IntentExtractor
        tasks = [{"title": "The quick brown fox is very fast", "id": "t1", "priority": "high", "status": "open"}]
        intent = IntentExtractor.extract(tasks=tasks, runtime_db=None, agent_name="claude")
        assert "the" not in intent.keywords
        assert "is" not in intent.keywords
        assert "very" not in intent.keywords
        assert "quick" in intent.keywords or "brown" in intent.keywords or "fast" in intent.keywords

    def test_short_words_excluded(self):
        from contextcore.context.projection import IntentExtractor
        tasks = [{"title": "Go to DB on VM", "id": "t1", "priority": "high", "status": "open"}]
        intent = IntentExtractor.extract(tasks=tasks, runtime_db=None, agent_name="claude")
        assert "go" not in intent.keywords
        assert "to" not in intent.keywords
        assert "on" not in intent.keywords


class TestSubgraphProjector:

    def _make_atomic_db(self, nodes, edges=None):
        """Mock atomic graph with CSR adapter."""
        db = MagicMock()
        db.csr_adapter = MagicMock()
        db.csr_adapter.get_all_nodes.return_value = nodes
        db.csr_adapter.get_all_edges.return_value = edges or []

        def get_node(nid):
            for n in nodes:
                if n.id == nid:
                    return n
            return None
        db.csr_adapter.get_node = get_node

        def get_neighbors(nid):
            result = []
            for e in (edges or []):
                src = getattr(e, 'source', getattr(e, 'from_id', ''))
                tgt = getattr(e, 'target', getattr(e, 'to_id', ''))
                if src == nid:
                    n = get_node(tgt)
                    if n:
                        result.append(n)
                elif tgt == nid:
                    n = get_node(src)
                    if n:
                        result.append(n)
            return result
        db.csr_adapter.get_neighbors = get_neighbors
        return db

    def test_empty_intent_returns_empty_projection(self):
        from contextcore.context.projection import IntentVector, SubgraphProjector
        intent = IntentVector()
        result = SubgraphProjector.project(intent, atomic_db=None, namespace="test")
        assert result.seed_count == 0
        assert len(result.nodes) == 0

    def test_keyword_match_finds_seeds(self):
        from contextcore.context.projection import IntentVector, SubgraphProjector
        nodes = [
            FakeNode("n1", "Fact", {"name": "Auth module uses JWT tokens", "statement": "Auth module uses JWT tokens"}),
            FakeNode("n2", "Fact", {"name": "Database runs on PostgreSQL", "statement": "Database runs on PostgreSQL"}),
            FakeNode("n3", "Entity", {"name": "Auth token session handler", "description": "Handles auth session tokens"}),
        ]
        db = self._make_atomic_db(nodes)
        intent = IntentVector(keywords={"auth": 1.0, "token": 0.8, "jwt": 0.6}, confidence=0.5)
        result = SubgraphProjector.project(intent, atomic_db=db, namespace="test", max_tokens=2000)
        node_ids = {n["id"] for n in result.nodes}
        assert "n1" in node_ids   # auth + jwt + token = 3 hits
        assert "n3" in node_ids   # auth + token = 2 hits
        assert "n2" not in node_ids  # no keyword match
        assert result.seed_count >= 2

    def test_hop_expansion_adds_neighbors(self):
        from contextcore.context.projection import IntentVector, SubgraphProjector

        class FakeEdge:
            def __init__(self, source, target, label):
                self.source = source
                self.target = target
                self.label = label

        nodes = [
            FakeNode("n1", "Fact", {"name": "Auth uses JWT signing"}),
            FakeNode("n2", "Entity", {"name": "KeyManager", "description": "Manages RSA keys"}),
            FakeNode("n3", "Fact", {"name": "RSA keys stored in vault"}),
        ]
        edges = [
            FakeEdge("n1", "n2", "USES"),
            FakeEdge("n2", "n3", "MANAGES"),
        ]
        db = self._make_atomic_db(nodes, edges)
        intent = IntentVector(keywords={"auth": 1.0, "jwt": 0.8}, confidence=0.5)
        result = SubgraphProjector.project(intent, atomic_db=db, namespace="test", max_tokens=4000)
        node_ids = {n["id"] for n in result.nodes}
        assert "n1" in node_ids
        assert "n2" in node_ids
        assert result.hop_depth >= 1

    def test_budget_limits_projection_size(self):
        from contextcore.context.projection import IntentVector, SubgraphProjector
        # Use passage-sized content so budget actually constrains
        nodes = [FakeNode(f"n{i}", "Passage", {
            "name": f"Auth security passage {i}",
            "content": f"This is a detailed passage about authentication security topic {i}. " * 10,
        }) for i in range(50)]
        db = self._make_atomic_db(nodes)
        intent = IntentVector(keywords={"auth": 1.0, "security": 0.8}, confidence=0.5)
        result = SubgraphProjector.project(intent, atomic_db=db, namespace="test", max_tokens=500)
        # 500 tokens with ~80 tokens per passage = ~6 passages max
        assert len(result.nodes) <= 10
        assert result.token_estimate <= 600

    def test_empty_atomic_graph_returns_empty(self):
        from contextcore.context.projection import IntentVector, SubgraphProjector
        db = self._make_atomic_db([])
        intent = IntentVector(keywords={"auth": 1.0}, confidence=0.5)
        result = SubgraphProjector.project(intent, atomic_db=db, namespace="test")
        assert len(result.nodes) == 0


class TestProjectionAssembler:

    def test_empty_projection_returns_graceful_message(self):
        from contextcore.context.projection import ProjectedSubgraph, ProjectionAssembler
        result = ProjectionAssembler.assemble(
            ProjectedSubgraph(), task_title="Audit auth", total_nodes=100
        )
        assert "No relevant context" in result

    def test_formats_direct_matches(self):
        from contextcore.context.projection import ProjectedSubgraph, ProjectionAssembler
        subgraph = ProjectedSubgraph(
            nodes=[
                {"id": "n1", "label": "Fact", "name": "Auth uses JWT", "content": "Auth module uses JWT with RS256"},
                {"id": "n2", "label": "Entity", "name": "TokenStore", "content": "Handles session tokens"},
            ],
            seed_count=2,
            hop_depth=0,
            token_estimate=160,
            intent_keywords=["auth", "token"],
        )
        result = ProjectionAssembler.assemble(subgraph, task_title="Audit auth", total_nodes=500)
        assert "PROJECTED CONTEXT" in result
        assert "Auth uses JWT" in result
        assert "TokenStore" in result
        assert "auth" in result.lower()

    def test_groups_by_distance(self):
        from contextcore.context.projection import ProjectedSubgraph, ProjectionAssembler
        subgraph = ProjectedSubgraph(
            nodes=[
                {"id": "n1", "label": "Fact", "name": "Auth uses JWT", "content": ""},
                {"id": "n2", "label": "Entity", "name": "KeyManager", "content": "Manages RSA keys"},
                {"id": "n3", "label": "Fact", "name": "RSA stored in vault", "content": ""},
            ],
            edges=[{"source": "n1", "target": "n2", "label": "USES"}],
            seed_count=1,
            hop_depth=2,
            token_estimate=240,
            intent_keywords=["auth"],
        )
        result = ProjectionAssembler.assemble(subgraph, task_title="Audit auth", total_nodes=500)
        assert "DIRECT" in result or "direct" in result.lower()
        assert "CONNECTED" in result or "connected" in result.lower()

    def test_includes_cross_agent_signals(self):
        from contextcore.context.projection import ProjectedSubgraph, ProjectionAssembler
        signals = [
            {"agent": "gemini", "label": "Finding", "name": "SQL injection in login"},
            {"agent": "chatgpt", "label": "Insight", "name": "Auth pattern similar to payments"},
        ]
        subgraph = ProjectedSubgraph(
            nodes=[{"id": "n1", "label": "Fact", "name": "Auth uses JWT", "content": ""}],
            seed_count=1, hop_depth=0, token_estimate=80,
        )
        result = ProjectionAssembler.assemble(
            subgraph, task_title="Audit auth", total_nodes=500, cross_agent_signals=signals
        )
        assert "gemini" in result or "CROSS-AGENT" in result
        assert "SQL injection" in result

    def test_footer_shows_stats(self):
        from contextcore.context.projection import ProjectedSubgraph, ProjectionAssembler
        subgraph = ProjectedSubgraph(
            nodes=[{"id": "n1", "label": "Fact", "name": "Test", "content": ""}],
            seed_count=1, hop_depth=0, token_estimate=80,
        )
        result = ProjectionAssembler.assemble(subgraph, task_title="Test", total_nodes=1000)
        assert "1000" in result or "projected" in result.lower()


class TestOrientProjection:
    """Test that orient() uses CGP when agent has tasks."""

    def _make_ctx(self, atomic_nodes=None, runtime_nodes=None, tasks=None):
        from contextcore.tools.registry import ToolContext

        atomic_db = MagicMock()
        atomic_db.csr_adapter = MagicMock()
        atomic_db.csr_adapter.get_all_nodes.return_value = atomic_nodes or []
        atomic_db.csr_adapter.get_all_edges.return_value = []
        atomic_db.csr_adapter.get_node = lambda nid: next(
            (n for n in (atomic_nodes or []) if n.id == nid), None
        )
        atomic_db.csr_adapter.get_neighbors = lambda nid: []
        atomic_db.get_all_nodes.return_value = atomic_nodes or []
        atomic_db.get_all_edges.return_value = []

        conn = MagicMock()
        conn.db = atomic_db
        conn.namespace = "test_atomic"
        conn._namespace = "test_atomic"
        conn.contextcore = atomic_db

        rt_db = MagicMock()
        _rt_nodes = runtime_nodes or []
        def _rt_get_all(label=None):
            if label:
                return [n for n in _rt_nodes if n.label == label]
            return _rt_nodes
        rt_db.get_all_nodes = _rt_get_all
        rt_db.csr_adapter = MagicMock()
        rt_db.csr_adapter.get_all_nodes.return_value = _rt_nodes

        rt_conn = MagicMock()
        rt_conn.db = rt_db

        project = MagicMock()
        project.get_open_tasks.return_value = tasks or []

        ctx = ToolContext(conn=conn, runtime_conn=rt_conn, agent_id="agent-1", agent_name="claude")
        ctx.project = project
        return ctx

    def test_orient_with_task_returns_projection(self):
        from contextcore.context.projection import project_context
        atomic_nodes = [
            FakeNode("n1", "Fact", {"name": "Auth module uses JWT", "statement": "Auth module uses JWT"}),
            FakeNode("n2", "Entity", {"name": "Auth token session handler", "description": "Auth session token handler"}),
        ]
        tasks = [{"title": "Audit auth module", "id": "t1", "priority": "high", "status": "in_progress"}]
        ctx = self._make_ctx(atomic_nodes=atomic_nodes, tasks=tasks)
        result = project_context(ctx)
        assert result is not None
        assert "PROJECTED CONTEXT" in result
        assert "auth" in result.lower()

    def test_orient_without_task_returns_none(self):
        from contextcore.context.projection import project_context
        ctx = self._make_ctx(tasks=[])
        result = project_context(ctx)
        assert result is None

    def test_projection_includes_cross_agent_signals(self):
        from contextcore.context.projection import project_context, _projection_log
        # Clear caches from previous tests to avoid stale cache hits
        _projection_log.clear()
        try:
            from contextcore.context.projection import _get_redis
            r = _get_redis()
            if r:
                r.delete("cgp:cache:test_atomic:agent-1")
        except Exception:
            pass
        atomic_nodes = [
            FakeNode("n1", "Fact", {"name": "Auth module uses JWT tokens", "statement": "Auth module uses JWT tokens"}),
        ]
        runtime_nodes = [
            FakeNode("f1", "Finding", {"name": "SQL injection in auth", "_agent_name": "gemini"}),
        ]
        tasks = [{"title": "Audit auth module", "id": "t1", "priority": "high", "status": "in_progress"}]
        ctx = self._make_ctx(atomic_nodes=atomic_nodes, runtime_nodes=runtime_nodes, tasks=tasks)
        result = project_context(ctx)
        assert "gemini" in result.lower() or "CROSS-AGENT" in result


class TestEndToEnd:
    """Full pipeline: task + diverse graph → projection returns relevant slice only."""

    def test_full_pipeline(self):
        from contextcore.context.projection import project_context
        from contextcore.tools.registry import ToolContext

        atomic_nodes = [
            FakeNode("n1", "Fact", {"name": "Auth module uses JWT with RS256 signing", "statement": "Auth module uses JWT with RS256 signing"}),
            FakeNode("n2", "Fact", {"name": "Database uses PostgreSQL 15", "statement": "Database uses PostgreSQL 15"}),
            FakeNode("n3", "Entity", {"name": "TokenStore", "description": "Handles JWT refresh tokens"}),
            FakeNode("n4", "Fact", {"name": "API rate limit is 100 requests per minute", "statement": "API rate limit is 100 requests per minute"}),
            FakeNode("n5", "Entity", {"name": "AuthMiddleware", "description": "Express middleware for JWT verification"}),
            FakeNode("n6", "Fact", {"name": "Frontend uses React 18", "statement": "Frontend uses React 18"}),
            FakeNode("n7", "Fact", {"name": "CI pipeline runs on GitHub Actions", "statement": "CI pipeline runs on GitHub Actions"}),
        ]
        runtime_nodes = [
            FakeNode("f1", "Finding", {"name": "Token refresh endpoint has no rate limiting", "_agent_name": "agent-b"}),
        ]
        tasks = [{"title": "Audit auth module for security vulnerabilities", "description": "Review JWT handling, token storage, and session management", "id": "t1", "priority": "critical", "status": "in_progress"}]

        atomic_db = MagicMock()
        atomic_db.csr_adapter = MagicMock()
        atomic_db.csr_adapter.get_all_nodes.return_value = atomic_nodes
        atomic_db.csr_adapter.get_all_edges.return_value = []
        atomic_db.csr_adapter.get_node = lambda nid: next((n for n in atomic_nodes if n.id == nid), None)
        atomic_db.csr_adapter.get_neighbors = lambda nid: []

        rt_db = MagicMock()
        rt_db.get_all_nodes = MagicMock(return_value=runtime_nodes)

        conn = MagicMock()
        conn.db = atomic_db
        conn.namespace = "test_e2e"
        conn._namespace = "test_e2e"

        rt_conn = MagicMock()
        rt_conn.db = rt_db

        project = MagicMock()
        project.get_open_tasks.return_value = tasks

        ctx = ToolContext(conn=conn, runtime_conn=rt_conn, agent_id="agent-a", agent_name="agent-a")
        ctx.project = project

        result = project_context(ctx)

        assert "PROJECTED CONTEXT" in result
        assert "Auth" in result or "auth" in result.lower() or "JWT" in result
        assert "TokenStore" in result or "token" in result.lower()
        assert "React" not in result
        assert "GitHub Actions" not in result
        assert "agent-b" in result or "rate limiting" in result.lower()
        assert "YOUR TASKS" in result
        assert "Audit auth" in result
