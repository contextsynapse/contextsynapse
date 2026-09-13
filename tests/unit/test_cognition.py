"""Tests for CognitionEvent and CognitionEventStream."""

import pytest
from contextcore.cognition import CognitionEvent, CognitionEventStream


def test_create_event():
    """Fields are set correctly, event_id + timestamp auto-generated."""
    event = CognitionEvent(
        event_type="node_read",
        agent_id="agent-1",
        session_id="session-abc",
        node_ids=["n1", "n2"],
        derived_from=["n0"],
        metadata={"source": "test"},
    )
    assert event.event_type == "node_read"
    assert event.agent_id == "agent-1"
    assert event.session_id == "session-abc"
    assert event.node_ids == ["n1", "n2"]
    assert event.derived_from == ["n0"]
    assert event.metadata == {"source": "test"}
    assert len(event.event_id) == 16
    assert "T" in event.timestamp  # ISO 8601


def test_event_to_dict():
    """Serializes all fields."""
    event = CognitionEvent(
        event_type="node_write",
        agent_id="agent-2",
        session_id="session-xyz",
        node_ids=["n3"],
    )
    d = event.to_dict()
    assert d["event_type"] == "node_write"
    assert d["agent_id"] == "agent-2"
    assert d["session_id"] == "session-xyz"
    assert d["node_ids"] == ["n3"]
    assert d["derived_from"] == []
    assert d["metadata"] == {}
    assert "event_id" in d
    assert "timestamp" in d

    # Round-trip
    restored = CognitionEvent.from_dict(d)
    assert restored.event_id == event.event_id
    assert restored.timestamp == event.timestamp


def test_event_stream_append():
    """Add event, verify it's stored."""
    stream = CognitionEventStream(namespace="test-ns")
    event = CognitionEvent(
        event_type="node_read",
        agent_id="agent-1",
        session_id="s1",
        node_ids=["n1"],
    )
    stream.append(event)
    events = stream.get_events()
    assert len(events) == 1
    assert events[0].event_id == event.event_id


def test_event_stream_query_by_agent():
    """Filter by agent_id."""
    stream = CognitionEventStream(namespace="test-ns")
    for i, aid in enumerate(["agent-a", "agent-b", "agent-a"]):
        stream.append(CognitionEvent(
            event_type="node_read",
            agent_id=aid,
            session_id="s1",
            node_ids=[f"n{i}"],
        ))
    results = stream.get_events(agent_id="agent-a")
    assert len(results) == 2
    assert all(e.agent_id == "agent-a" for e in results)


def test_event_stream_query_by_type():
    """Filter by event_type, check derived_from."""
    stream = CognitionEventStream(namespace="test-ns")
    stream.append(CognitionEvent(
        event_type="node_read",
        agent_id="agent-1",
        session_id="s1",
        node_ids=["n1"],
    ))
    stream.append(CognitionEvent(
        event_type="node_derive",
        agent_id="agent-1",
        session_id="s1",
        node_ids=["n2"],
        derived_from=["n1"],
    ))
    stream.append(CognitionEvent(
        event_type="node_read",
        agent_id="agent-1",
        session_id="s1",
        node_ids=["n3"],
    ))

    results = stream.get_events(event_type="node_derive")
    assert len(results) == 1
    assert results[0].derived_from == ["n1"]


# ---------------------------------------------------------------------------
# ReadTracker tests
# ---------------------------------------------------------------------------
from contextcore.cognition.read_tracker import ReadTracker


class TestReadTracker:
    def setup_method(self):
        self.stream = CognitionEventStream(namespace="test")
        self.tracker = ReadTracker(self.stream)

    def test_record_read(self):
        """Record a read, verify event emitted with correct type + node_ids."""
        self.tracker.record_read(
            agent_id="agent-1",
            session_id="sess-1",
            node_ids=["node-a", "node-b"],
        )
        events = self.stream.get_events(event_type="read")
        assert len(events) == 1
        ev = events[0]
        assert ev.event_type == "read"
        assert ev.agent_id == "agent-1"
        assert ev.session_id == "sess-1"
        assert ev.node_ids == ["node-a", "node-b"]

    def test_get_consumers_of_node(self):
        """2 agents read same node, verify both returned."""
        self.tracker.record_read("agent-1", "sess-1", ["node-x"])
        self.tracker.record_read("agent-2", "sess-2", ["node-x"])
        consumers = self.tracker.get_consumers("node-x")
        assert set(consumers) == {"agent-1", "agent-2"}

    def test_get_consumed_by_agent(self):
        """Agent reads multiple times, verify all unique node_ids returned."""
        self.tracker.record_read("agent-1", "sess-1", ["node-a", "node-b"])
        self.tracker.record_read("agent-1", "sess-1", ["node-b", "node-c"])
        consumed = self.tracker.get_consumed_by("agent-1")
        assert set(consumed) == {"node-a", "node-b", "node-c"}


# ---------------------------------------------------------------------------
# DerivationTracker tests
# ---------------------------------------------------------------------------
from contextcore.cognition.derivation import DerivationTracker


class TestDerivationTracker:
    def setup_method(self):
        self.stream = CognitionEventStream(namespace="test")
        self.tracker = DerivationTracker(self.stream)

    def test_record_derivation(self):
        """Record finding_1 derived from fact_1 + fact_2, verify event emitted."""
        self.tracker.record_derivation(
            agent_id="agent-1",
            session_id="sess-1",
            output_node_id="finding_1",
            source_node_ids=["fact_1", "fact_2"],
        )
        events = self.stream.get_events(event_type="derive")
        assert len(events) == 1
        ev = events[0]
        assert ev.event_type == "derive"
        assert ev.agent_id == "agent-1"
        assert ev.session_id == "sess-1"
        assert ev.node_ids == ["finding_1"]
        assert ev.derived_from == ["fact_1", "fact_2"]

    def test_get_lineage(self):
        """finding_1 derived from fact_1 + fact_2, get_lineage returns both."""
        self.tracker.record_derivation(
            agent_id="agent-1",
            session_id="sess-1",
            output_node_id="finding_1",
            source_node_ids=["fact_1", "fact_2"],
        )
        lineage = self.tracker.get_lineage("finding_1")
        assert set(lineage) == {"fact_1", "fact_2"}

    def test_get_derived_from_node(self):
        """Two findings derived from same fact, get_derived_from returns both."""
        self.tracker.record_derivation(
            agent_id="agent-1",
            session_id="sess-1",
            output_node_id="finding_1",
            source_node_ids=["fact_1"],
        )
        self.tracker.record_derivation(
            agent_id="agent-2",
            session_id="sess-2",
            output_node_id="finding_2",
            source_node_ids=["fact_1", "fact_3"],
        )
        derived = self.tracker.get_derived_from("fact_1")
        assert set(derived) == {"finding_1", "finding_2"}


# ---------------------------------------------------------------------------
# FeedbackProcessor tests
# ---------------------------------------------------------------------------
from contextcore.cognition.feedback import FeedbackProcessor


class TestFeedback:
    def setup_method(self):
        self.stream = CognitionEventStream(namespace="test")
        self.fp = FeedbackProcessor(self.stream)

    def test_record_useful_feedback(self):
        """Record useful feedback, verify event emitted."""
        self.fp.record_feedback(agent_id="human", node_id="fact_1", signal="useful", reason="accurate data")

        events = self.stream.get_events(event_type="feedback")
        assert len(events) == 1
        assert events[0].metadata["signal"] == "useful"
        assert events[0].metadata["reason"] == "accurate data"

    def test_compute_score_adjustment(self):
        """Score adjustments match spec."""
        assert FeedbackProcessor.score_adjustment("useful") == 0.05
        assert FeedbackProcessor.score_adjustment("misleading") == -0.15
        assert FeedbackProcessor.score_adjustment("outdated") == -0.1
        assert FeedbackProcessor.score_adjustment("incorrect") == -0.3
        assert FeedbackProcessor.score_adjustment("unknown_signal") == 0.0

    def test_get_feedback_for_node(self):
        """Multiple feedback entries for same node returned correctly."""
        self.fp.record_feedback("human", "fact_1", "useful")
        self.fp.record_feedback("claude", "fact_1", "misleading")

        fb = self.fp.get_feedback("fact_1")
        assert len(fb) == 2
        signals = [f["signal"] for f in fb]
        assert "useful" in signals
        assert "misleading" in signals


# ---------------------------------------------------------------------------
# InvalidationEngine tests
# ---------------------------------------------------------------------------
from contextcore.cognition.invalidation import InvalidationEngine


class TestInvalidation:
    def setup_method(self):
        self.stream = CognitionEventStream(namespace="test")
        self.derivation = DerivationTracker(self.stream)
        self.engine = InvalidationEngine(self.stream, self.derivation)

    def test_invalidate_node(self):
        """Invalidate fact_1 → cascades to finding_1 → conclusion_1."""
        self.derivation.record_derivation("a1", "s1", "finding_1", ["fact_1"])
        self.derivation.record_derivation("a2", "s1", "conclusion_1", ["finding_1"])

        invalidated = self.engine.invalidate("fact_1", reason="source retracted")

        assert "fact_1" in invalidated
        assert "finding_1" in invalidated
        assert "conclusion_1" in invalidated

    def test_cascade_depth_limit(self):
        """Chain a→b→c→d with max_depth=2: d should NOT be invalidated."""
        engine = InvalidationEngine(self.stream, self.derivation, max_depth=2)

        self.derivation.record_derivation("a1", "s1", "b", ["a"])
        self.derivation.record_derivation("a1", "s1", "c", ["b"])
        self.derivation.record_derivation("a1", "s1", "d", ["c"])

        invalidated = engine.invalidate("a", reason="test")

        assert "a" in invalidated
        assert "b" in invalidated
        assert "c" in invalidated
        assert "d" not in invalidated

    def test_invalidation_emits_event(self):
        """Invalidation emits an 'invalidate' event with all affected nodes."""
        self.derivation.record_derivation("a1", "s1", "finding_1", ["fact_1"])

        self.engine.invalidate("fact_1", reason="incorrect")

        events = self.stream.get_events(event_type="invalidate")
        assert len(events) == 1
        assert "fact_1" in events[0].node_ids
        assert "finding_1" in events[0].node_ids
        assert events[0].metadata["reason"] == "incorrect"


# ---------------------------------------------------------------------------
# HallucinationTracer tests
# ---------------------------------------------------------------------------
from contextcore.cognition.hallucination import HallucinationTracer


class TestHallucinationTrace:
    def setup_method(self):
        self.stream = CognitionEventStream(namespace="test")
        self.derivation = DerivationTracker(self.stream)
        self.tracer = HallucinationTracer(self.stream, self.derivation)

    def test_trace_finds_root_cause(self):
        """Invalidated source identified as root cause with high confidence."""
        self.derivation.record_derivation("claude", "s1", "finding_1", ["fact_1", "fact_2"])

        node_status = {
            "fact_1": {"invalidated": False, "quality": 0.9},
            "fact_2": {"invalidated": True, "quality": 0.3},
        }

        result = self.tracer.trace("finding_1", node_status_fn=lambda nid: node_status.get(nid, {}))

        assert len(result["root_causes"]) >= 1
        assert result["root_causes"][0]["node_id"] == "fact_2"
        assert result["root_causes"][0]["confidence"] >= 0.8

    def test_trace_with_no_issues(self):
        """All sources healthy → no root causes."""
        self.derivation.record_derivation("claude", "s1", "finding_1", ["fact_1"])
        node_status = {"fact_1": {"invalidated": False, "quality": 0.9}}

        result = self.tracer.trace("finding_1", node_status_fn=lambda nid: node_status.get(nid, {}))
        assert len(result["root_causes"]) == 0

    def test_trace_no_lineage(self):
        """No derivation lineage → empty root causes with recommendation."""
        result = self.tracer.trace("orphan_node", node_status_fn=lambda nid: {})
        assert len(result["root_causes"]) == 0
        assert any("lineage" in r.lower() for r in result["recommendations"])


# ---------------------------------------------------------------------------
# Tool-level tests (simulate tool dispatch)
# ---------------------------------------------------------------------------

class TestCognitionTools:
    def test_trace_lineage_tool(self):
        """trace_lineage returns formatted lineage for a node."""
        from contextcore.cognition.events import CognitionEventStream
        from contextcore.cognition.derivation import DerivationTracker

        stream = CognitionEventStream(namespace="test")
        tracker = DerivationTracker(stream)
        tracker.record_derivation("claude", "s1", "finding_1", ["fact_1", "fact_2"])

        lineage = tracker.get_lineage("finding_1")
        assert set(lineage) == {"fact_1", "fact_2"}

        derived = tracker.get_derived_from("fact_1")
        assert "finding_1" in derived

    def test_invalidate_node_tool(self):
        """invalidate_node cascades and returns affected count."""
        from contextcore.cognition.events import CognitionEventStream
        from contextcore.cognition.derivation import DerivationTracker
        from contextcore.cognition.invalidation import InvalidationEngine

        stream = CognitionEventStream(namespace="test")
        derivation = DerivationTracker(stream)
        engine = InvalidationEngine(stream, derivation)

        derivation.record_derivation("a1", "s1", "finding_1", ["fact_1"])
        derivation.record_derivation("a2", "s1", "conclusion_1", ["finding_1"])

        invalidated = engine.invalidate("fact_1", reason="retracted")
        assert len(invalidated) == 3
