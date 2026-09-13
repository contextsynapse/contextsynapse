"""End-to-end test for the Cognitive Reliability Layer.

Flow: agent reads context -> writes a finding -> feedback signals it as incorrect ->
invalidation cascades -> hallucination tracer identifies root cause.
"""
from __future__ import annotations

import pytest
from contextcore.cognition import (
    CognitionEventStream,
    ReadTracker,
    DerivationTracker,
    FeedbackProcessor,
    InvalidationEngine,
    HallucinationTracer,
)


class TestCognitionE2E:
    """Full lifecycle: read -> derive -> feedback -> invalidate -> trace."""

    def setup_method(self):
        """Fresh stream per test."""
        self.stream = CognitionEventStream(namespace="e2e_test")
        self.read_tracker = ReadTracker(self.stream)
        self.derivation = DerivationTracker(self.stream)
        self.feedback = FeedbackProcessor(self.stream)
        self.invalidation = InvalidationEngine(self.stream, self.derivation)
        self.tracer = HallucinationTracer(self.stream, self.derivation)

    def test_full_cognition_cycle(self):
        """
        Scenario:
        1. Agent Claude reads fact_1, fact_2, fact_3
        2. Claude produces finding_1 (derived from fact_1, fact_2)
        3. Agent GPT reads finding_1
        4. GPT produces conclusion_1 (derived from finding_1)
        5. Human marks fact_2 as incorrect
        6. Invalidation cascades: fact_2 -> finding_1 -> conclusion_1
        7. Hallucination tracer identifies fact_2 as root cause of finding_1
        """
        # Step 1: Agent reads context
        self.read_tracker.record_read("claude", "session_1", ["fact_1", "fact_2", "fact_3"])

        # Verify read tracking
        consumers = self.read_tracker.get_consumers("fact_1")
        assert "claude" in consumers
        consumed = self.read_tracker.get_consumed_by("claude")
        assert set(consumed) == {"fact_1", "fact_2", "fact_3"}

        # Step 2: Claude produces a finding
        self.derivation.record_derivation("claude", "session_1", "finding_1", ["fact_1", "fact_2"])

        lineage = self.derivation.get_lineage("finding_1")
        assert set(lineage) == {"fact_1", "fact_2"}

        # Step 3: GPT reads finding_1
        self.read_tracker.record_read("gpt", "session_2", ["finding_1"])

        # Step 4: GPT produces conclusion
        self.derivation.record_derivation("gpt", "session_2", "conclusion_1", ["finding_1"])

        # Step 5: Human gives feedback - fact_2 is incorrect
        self.feedback.record_feedback("human", "fact_2", "incorrect", reason="data was fabricated")

        fb = self.feedback.get_feedback("fact_2")
        assert len(fb) == 1
        assert fb[0]["signal"] == "incorrect"

        # Step 6: Invalidation cascades
        invalidated = self.invalidation.invalidate("fact_2", reason="incorrect feedback", agent_id="system")

        assert "fact_2" in invalidated
        assert "finding_1" in invalidated      # derived from fact_2
        assert "conclusion_1" in invalidated   # derived from finding_1

        # Step 7: Trace hallucination in finding_1
        def node_status(nid):
            return {"invalidated": nid in invalidated, "quality": 0.3 if nid in invalidated else 0.9}

        trace = self.tracer.trace("finding_1", node_status_fn=node_status)

        assert trace["output_node_id"] == "finding_1"
        assert len(trace["root_causes"]) >= 1
        root_ids = [rc["node_id"] for rc in trace["root_causes"]]
        assert "fact_2" in root_ids
        assert trace["root_causes"][0]["confidence"] >= 0.8
        assert "conclusion_1" in trace["affected_downstream"]

    def test_recent_reads_drive_derivation(self):
        """Verify read tracking returns correct node_ids for derivation inference."""
        self.read_tracker.record_read("agent_x", "s1", ["a", "b"])
        self.read_tracker.record_read("agent_x", "s1", ["c", "d"])

        consumed = self.read_tracker.get_consumed_by("agent_x")
        assert set(consumed) == {"a", "b", "c", "d"}

    def test_multi_agent_isolation(self):
        """Events from one agent don't leak into another's read history."""
        self.read_tracker.record_read("agent_a", "s1", ["node_1", "node_2"])
        self.read_tracker.record_read("agent_b", "s2", ["node_3", "node_4"])

        a_consumed = self.read_tracker.get_consumed_by("agent_a")
        b_consumed = self.read_tracker.get_consumed_by("agent_b")

        assert set(a_consumed) == {"node_1", "node_2"}
        assert set(b_consumed) == {"node_3", "node_4"}

    def test_feedback_score_accumulation(self):
        """Multiple feedback signals accumulate correctly."""
        self.feedback.record_feedback("user_1", "node_x", "useful")
        self.feedback.record_feedback("user_2", "node_x", "useful")
        self.feedback.record_feedback("user_3", "node_x", "misleading")

        fb = self.feedback.get_feedback("node_x")
        assert len(fb) == 3

        total = sum(FeedbackProcessor.score_adjustment(f["signal"]) for f in fb)
        expected = 0.05 + 0.05 + (-0.15)
        assert abs(total - expected) < 0.001


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

import os
from httpx import AsyncClient, ASGITransport


@pytest.mark.asyncio
class TestCognitionAPI:
    """Test REST endpoints for the cognition layer."""

    async def _client(self):
        from contextcore.api.api import app
        transport = ASGITransport(app=app)
        admin_key = os.environ.get("AICONTEXTDB_ADMIN_KEY", "test-admin-key")
        return AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-Admin-Key": admin_key},
        )

    async def test_post_feedback(self):
        async with await self._client() as client:
            resp = await client.post("/cognition/test_ns/node/node_1/feedback", json={
                "signal": "useful",
                "agent_id": "test_agent",
                "reason": "very accurate",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["recorded"] is True
            assert data["signal"] == "useful"
            assert data["score_adjustment"] == 0.05

    async def test_post_feedback_invalid_signal(self):
        async with await self._client() as client:
            resp = await client.post("/cognition/test_ns/node/node_1/feedback", json={
                "signal": "invalid_signal",
            })
            assert resp.status_code == 400

    async def test_get_lineage_empty(self):
        async with await self._client() as client:
            resp = await client.get("/cognition/test_ns/node/unknown_node/lineage")
            assert resp.status_code == 200
            data = resp.json()
            assert data["sources"] == []
            assert data["derived_to"] == []

    async def test_get_events(self):
        async with await self._client() as client:
            await client.post("/cognition/api_test/node/n1/feedback", json={
                "signal": "useful", "agent_id": "tester",
            })
            resp = await client.get("/cognition/api_test/events", params={"event_type": "feedback"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] >= 1

    async def test_invalidate_and_trace(self):
        async with await self._client() as client:
            resp = await client.post("/cognition/trace_test/node/bad_fact/invalidate", json={
                "reason": "incorrect data",
                "agent_id": "admin",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert "bad_fact" in data["invalidated"]

            resp = await client.post("/cognition/trace_test/hallucination/trace", json={
                "output_node_id": "some_output",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert "root_causes" in data
