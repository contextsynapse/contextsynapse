"""Tests for the promotion path — runtime to atomic context."""

import pytest
from unittest.mock import MagicMock


class FakeNode:
    def __init__(self, id, label, properties):
        self.id = id
        self.label = label
        self.node_type = label
        self.properties = properties


DEFAULTS = {
    "auto_promote_threshold": 0.8,
    "review_threshold": 0.5,
    "weights": {
        "agent_references": 0.3,
        "schema_valid": 0.2,
        "atomic_overlap": 0.2,
        "agent_trust": 0.15,
        "content_quality": 0.15,
    },
    "require_human_review": False,
}


class TestPromotionScorer:

    def test_high_quality_node_auto_promotes(self):
        from contextcore.context.promotion import PromotionScorer
        scorer = PromotionScorer(DEFAULTS)

        node = FakeNode("f1", "Finding", {
            "name": "JWT tokens lack expiry validation",
            "content": "The authentication middleware does not validate JWT token expiry timestamps. "
                       "This allows expired tokens to be reused indefinitely, creating a replay attack "
                       "vector. Recommend adding exp claim validation in the auth middleware.",
            "_agent_name": "claude",
            "_agent_id": "agent_1",
        })

        rt_nodes = [
            FakeNode("f2", "Finding", {"name": "Token replay vulnerability found", "_agent_name": "gpt"}),
            FakeNode("f3", "Insight", {"name": "Auth tokens need expiry check", "_agent_name": "gemini"}),
            FakeNode("f4", "Finding", {"name": "JWT token security analysis", "_agent_name": "researcher"}),
        ]
        runtime_db = MagicMock()
        runtime_db.get_all_nodes.return_value = rt_nodes

        atomic_nodes = [
            FakeNode("a1", "Fact", {"name": "System uses JWT for auth", "content": "JWT RS256 tokens"}),
        ]
        atomic_db = MagicMock()
        atomic_db.get_all_nodes.return_value = atomic_nodes

        result = scorer.score(node, runtime_db, atomic_db)
        assert result.score >= 0.8
        assert result.action == "auto_promote"

    def test_medium_quality_goes_to_review(self):
        from contextcore.context.promotion import PromotionScorer
        scorer = PromotionScorer(DEFAULTS)

        node = FakeNode("f1", "Finding", {
            "name": "Auth module needs security review for token handling",
            "content": "The authentication module has potential issues that warrant further investigation. "
                       "Token handling and session management need careful security analysis. "
                       "Multiple attack vectors including replay attacks and session fixation.",
            "_agent_name": "claude",
            "_agent_id": "agent_1",
        })

        # Two other agents reference overlapping keywords → agent_references = 0.6
        rt_nodes = [
            FakeNode("f2", "Finding", {"name": "Auth token handling concerns flagged for review", "_agent_name": "gpt"}),
            FakeNode("f3", "Insight", {"name": "Security review needed for auth module tokens", "_agent_name": "gemini"}),
        ]
        runtime_db = MagicMock()
        runtime_db.get_all_nodes.return_value = rt_nodes

        atomic_db = MagicMock()
        atomic_db.get_all_nodes.return_value = []

        result = scorer.score(node, runtime_db, atomic_db)
        assert 0.5 <= result.score < 0.8
        assert result.action == "review"

    def test_low_quality_ignored(self):
        from contextcore.context.promotion import PromotionScorer
        scorer = PromotionScorer(DEFAULTS)

        node = FakeNode("f1", "Finding", {
            "name": "Not sure",
            "content": "I don't have enough context",
            "_agent_name": "claude",
        })

        runtime_db = MagicMock()
        runtime_db.get_all_nodes.return_value = []

        atomic_db = MagicMock()
        atomic_db.get_all_nodes.return_value = []

        result = scorer.score(node, runtime_db, atomic_db)
        assert result.score < 0.5
        assert result.action == "ignore"

    def test_require_human_review_overrides_auto(self):
        from contextcore.context.promotion import PromotionScorer
        config = {**DEFAULTS, "require_human_review": True}
        scorer = PromotionScorer(config)

        node = FakeNode("f1", "Finding", {
            "name": "Critical security vulnerability in auth",
            "content": "SQL injection found in the login endpoint via unsanitized email parameter. "
                       "The query constructs raw SQL without parameterized queries, allowing "
                       "attackers to extract the full user database including password hashes.",
            "_agent_name": "claude",
        })

        rt_nodes = [
            FakeNode("f2", "Finding", {"name": "SQL injection in login", "_agent_name": "gpt"}),
            FakeNode("f3", "Finding", {"name": "Login SQL injection confirmed", "_agent_name": "gemini"}),
            FakeNode("f4", "Insight", {"name": "Auth SQL vulnerability critical", "_agent_name": "researcher"}),
        ]
        runtime_db = MagicMock()
        runtime_db.get_all_nodes.return_value = rt_nodes

        atomic_db = MagicMock()
        atomic_db.get_all_nodes.return_value = []

        result = scorer.score(node, runtime_db, atomic_db)
        assert result.score >= 0.7  # 3 agent refs + good content
        assert result.action == "review"  # require_human_review overrides auto

    def test_duplicate_detection(self):
        from contextcore.context.promotion import PromotionScorer
        scorer = PromotionScorer(DEFAULTS)

        node = FakeNode("f1", "Fact", {
            "name": "System uses JWT for authentication",
            "content": "The system uses JWT RS256 tokens for authentication",
            "_agent_name": "claude",
        })

        runtime_db = MagicMock()
        runtime_db.get_all_nodes.return_value = []

        atomic_nodes = [
            FakeNode("a1", "Fact", {"name": "System uses JWT for auth", "content": "JWT RS256 tokens for authentication"}),
        ]
        atomic_db = MagicMock()
        atomic_db.get_all_nodes.return_value = atomic_nodes

        result = scorer.score(node, runtime_db, atomic_db)
        assert result.signals["atomic_overlap"] == 0.0

    def test_custom_weights(self):
        from contextcore.context.promotion import PromotionScorer
        config = {
            **DEFAULTS,
            "weights": {
                "agent_references": 0.5,
                "schema_valid": 0.1,
                "atomic_overlap": 0.1,
                "agent_trust": 0.1,
                "content_quality": 0.2,
            },
        }
        scorer = PromotionScorer(config)
        node = FakeNode("f1", "Finding", {
            "name": "Test finding",
            "content": "This is a test finding with enough content to be considered substantive.",
            "_agent_name": "claude",
        })
        runtime_db = MagicMock()
        runtime_db.get_all_nodes.return_value = []
        atomic_db = MagicMock()
        atomic_db.get_all_nodes.return_value = []
        result = scorer.score(node, runtime_db, atomic_db)
        assert 0.0 <= result.score <= 1.0
        assert result.action in ("auto_promote", "review", "ignore")


class TestPromotionQueue:

    def test_submit_and_pending(self):
        from contextcore.context.promotion import PromotionQueue
        queue = PromotionQueue("test_ns")
        queue._clear()

        added = queue.submit("node_1", 0.75, "Good quality finding", submitted_by="claude")
        assert added is True

        items = queue.pending()
        assert len(items) == 1
        assert items[0]["node_id"] == "node_1"
        assert items[0]["score"] == 0.75

        queue._clear()

    def test_submit_duplicate_rejected(self):
        from contextcore.context.promotion import PromotionQueue
        queue = PromotionQueue("test_ns")
        queue._clear()

        queue.submit("node_1", 0.75, "First submission")
        added = queue.submit("node_1", 0.85, "Second submission")
        assert added is False

        items = queue.pending()
        assert len(items) == 1

        queue._clear()

    def test_approve_removes_from_queue(self):
        from contextcore.context.promotion import PromotionQueue
        queue = PromotionQueue("test_ns")
        queue._clear()

        queue.submit("node_1", 0.75, "Review this")
        queue.approve("node_1", "admin_1")

        items = queue.pending()
        assert len(items) == 0

        queue._clear()

    def test_reject_removes_from_queue(self):
        from contextcore.context.promotion import PromotionQueue
        queue = PromotionQueue("test_ns")
        queue._clear()

        queue.submit("node_1", 0.75, "Review this")
        queue.reject("node_1", "admin_1", reason="Not relevant")

        items = queue.pending()
        assert len(items) == 0

        queue._clear()

    def test_pending_sorted_by_score(self):
        from contextcore.context.promotion import PromotionQueue
        queue = PromotionQueue("test_ns")
        queue._clear()

        queue.submit("low", 0.55, "Low score")
        queue.submit("high", 0.79, "High score")
        queue.submit("mid", 0.65, "Mid score")

        items = queue.pending()
        assert items[0]["node_id"] == "high"
        assert items[1]["node_id"] == "mid"
        assert items[2]["node_id"] == "low"

        queue._clear()

    def test_count(self):
        from contextcore.context.promotion import PromotionQueue
        queue = PromotionQueue("test_ns")
        queue._clear()

        assert queue.count() == 0
        queue.submit("n1", 0.6, "test")
        queue.submit("n2", 0.7, "test")
        assert queue.count() == 2

        queue._clear()


class TestPromoteNode:

    def _make_conns(self, runtime_nodes=None, atomic_nodes=None):
        rt_db = MagicMock()
        _rt_nodes = list(runtime_nodes or [])

        def rt_get_node(nid):
            return next((n for n in _rt_nodes if n.id == nid), None)

        rt_db.get_all_nodes.return_value = _rt_nodes
        rt_db.get_node = rt_get_node

        rt_conn = MagicMock()
        rt_conn.db = rt_db
        rt_conn.namespace = "test_ns_rt"

        at_db = MagicMock()
        at_db.get_all_nodes.return_value = atomic_nodes or []

        at_conn = MagicMock()
        at_conn.db = at_db
        at_conn.namespace = "test_ns"
        at_conn.query.return_value = {"success": True, "data": {"uuid": "promoted_123"}}

        return rt_conn, at_conn

    def test_promote_copies_to_atomic(self):
        from contextcore.context.promotion import promote_node

        node = FakeNode("f1", "Finding", {
            "name": "JWT tokens lack expiry",
            "content": "Detailed analysis of JWT expiry issue...",
            "_agent_name": "claude",
            "_agent_id": "agent_1",
        })
        rt_conn, at_conn = self._make_conns(runtime_nodes=[node])

        result = promote_node("f1", rt_conn, at_conn, promoted_by="admin_1")
        assert result == "promoted_123"

        at_conn.query.assert_called_once()
        call_args = at_conn.query.call_args[0][0]
        assert "CREATE NODE Finding" in call_args
        assert "_promoted_from" in call_args

    def test_promote_marks_runtime_node(self):
        from contextcore.context.promotion import promote_node

        node = FakeNode("f1", "Finding", {
            "name": "Test finding",
            "content": "Test content",
            "_agent_name": "claude",
        })
        rt_conn, at_conn = self._make_conns(runtime_nodes=[node])

        promote_node("f1", rt_conn, at_conn)

        rt_calls = [str(c) for c in rt_conn.query.call_args_list]
        assert any("promoted" in c for c in rt_calls)

    def test_promote_nonexistent_node_returns_none(self):
        from contextcore.context.promotion import promote_node

        rt_conn, at_conn = self._make_conns(runtime_nodes=[])

        result = promote_node("nonexistent", rt_conn, at_conn)
        assert result is None
        at_conn.query.assert_not_called()


class TestRequestPromotionTool:

    def test_request_promotion_scores_and_returns(self):
        from contextcore.tools.graph import _request_promotion
        from contextcore.tools.registry import ToolContext

        rt_db = MagicMock()
        node = FakeNode("f1", "Finding", {
            "name": "Critical security issue found in auth module",
            "content": "The authentication module has a critical vulnerability allowing token replay attacks. "
                       "This was confirmed by analyzing the JWT validation middleware.",
            "_agent_name": "claude",
            "_agent_id": "agent_1",
        })
        rt_db.get_node.return_value = node
        rt_db.get_all_nodes.return_value = []

        at_db = MagicMock()
        at_db.get_all_nodes.return_value = []

        conn = MagicMock()
        conn.db = at_db
        conn.namespace = "test_ns"
        conn._namespace = "test_ns"

        rt_conn = MagicMock()
        rt_conn.db = rt_db
        rt_conn.namespace = "test_ns_rt"

        ctx = ToolContext(conn=conn, runtime_conn=rt_conn,
                          agent_id="agent_1", agent_name="claude")

        result = _request_promotion(ctx, node_id="f1", reason="Critical finding")
        assert "score" in result.lower() or "promotion" in result.lower()
