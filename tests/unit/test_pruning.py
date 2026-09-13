"""
Tests for auto-pruning system and confidence decay.
Tasks 6 & 7 of the Distributed Context Engine plan.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


# ---------------------------------------------------------------------------
# Mock fixtures
# ---------------------------------------------------------------------------

class MockNode:
    def __init__(self, id, node_type, properties):
        self.id = id
        self.node_type = node_type
        self.label = node_type
        self.properties = properties


class MockAdapter:
    def __init__(self):
        self.nodes = {}  # id -> MockNode

    def get_all_nodes(self):
        return list(self.nodes.values())

    def delete_node(self, node_id):
        if node_id in self.nodes:
            del self.nodes[node_id]
            return True
        return False

    def update_node_properties(self, node_id, updates):
        if node_id in self.nodes:
            self.nodes[node_id].properties.update(updates)


class MockDB:
    def __init__(self):
        self.csr_adapter = MockAdapter()

    def add_mock_node(self, id, label, props):
        self.csr_adapter.nodes[id] = MockNode(id, label, props)


def _iso(delta_hours: float = 0.0, delta_days: float = 0.0) -> str:
    """Return ISO timestamp offset from now."""
    now = datetime.now(timezone.utc)
    offset = timedelta(hours=delta_hours, days=delta_days)
    return (now - offset).isoformat()


# ---------------------------------------------------------------------------
# Pruner tests
# ---------------------------------------------------------------------------

from contextcore.core.pruning import ContextPruner, PruningPolicy  # noqa: E402


def _make_pruner():
    return ContextPruner()


def test_prune_old_noise():
    """AgentThought older than noise_max_age_hours gets deleted."""
    db = MockDB()
    db.add_mock_node("n1", "AgentThought", {
        "_created_at": _iso(delta_hours=100),
        "_access_count": 0,
    })
    policy = PruningPolicy()
    result = _make_pruner().prune(db, policy)
    assert result.noise_deleted == 1
    assert "n1" not in db.csr_adapter.nodes


def test_prune_preserves_recent_noise():
    """AgentThought created 1 hour ago should survive."""
    db = MockDB()
    db.add_mock_node("n1", "AgentThought", {
        "_created_at": _iso(delta_hours=1),
        "_access_count": 0,
    })
    policy = PruningPolicy()
    result = _make_pruner().prune(db, policy)
    assert result.noise_deleted == 0
    assert "n1" in db.csr_adapter.nodes


def test_prune_preserves_evergreen():
    """Document survives even if it is old with zero accesses."""
    db = MockDB()
    db.add_mock_node("doc1", "Document", {
        "_created_at": _iso(delta_days=90),
        "_access_count": 0,
        "_quality_score": 0.1,  # would normally trigger quarantine
    })
    policy = PruningPolicy()
    result = _make_pruner().prune(db, policy)
    assert result.noise_deleted == 0
    assert result.stale_archived == 0
    assert result.quarantined == 0
    assert "doc1" in db.csr_adapter.nodes


def test_prune_archives_stale():
    """Entity with 0 accesses and 40-day age gets deleted (stale)."""
    db = MockDB()
    db.add_mock_node("e1", "Entity", {
        "_created_at": _iso(delta_days=40),
        "_access_count": 0,
        "_quality_score": 0.8,
    })
    policy = PruningPolicy(stale_days=30)
    result = _make_pruner().prune(db, policy)
    assert result.stale_archived == 1
    assert "e1" not in db.csr_adapter.nodes


def test_prune_keeps_used_nodes():
    """Entity with 50 accesses and 40-day age should NOT be stale-deleted."""
    db = MockDB()
    db.add_mock_node("e1", "Entity", {
        "_created_at": _iso(delta_days=40),
        "_access_count": 50,
        "_quality_score": 0.8,
    })
    policy = PruningPolicy(stale_days=30)
    result = _make_pruner().prune(db, policy)
    assert result.stale_archived == 0
    assert "e1" in db.csr_adapter.nodes


def test_prune_quarantines_low_quality():
    """Fact with _quality_score=0.1 gets _quarantined_at set (not deleted)."""
    db = MockDB()
    db.add_mock_node("f1", "Fact", {
        "_created_at": _iso(delta_days=2),
        "_access_count": 5,
        "_quality_score": 0.1,
    })
    policy = PruningPolicy(min_quality=0.2)
    result = _make_pruner().prune(db, policy)
    assert result.quarantined == 1
    assert "f1" in db.csr_adapter.nodes  # still exists
    assert "_quarantined_at" in db.csr_adapter.nodes["f1"].properties


def test_prune_deletes_old_quarantine():
    """Node already quarantined 20 days ago (beyond quarantine_days=14) is deleted."""
    db = MockDB()
    db.add_mock_node("f1", "Fact", {
        "_created_at": _iso(delta_days=30),
        "_access_count": 0,
        "_quality_score": 0.1,
        "_quarantined_at": _iso(delta_days=20),
    })
    policy = PruningPolicy(min_quality=0.2, quarantine_days=14)
    result = _make_pruner().prune(db, policy)
    assert result.quarantine_deleted == 1
    assert "f1" not in db.csr_adapter.nodes


# ---------------------------------------------------------------------------
# Confidence decay tests
# ---------------------------------------------------------------------------

from contextcore.core.pruning import compute_confidence  # noqa: E402


def test_confidence_fresh_high_quality():
    """1-day-old, quality 0.85 → confidence > 0.8."""
    props = {
        "_created_at": _iso(delta_days=1),
        "_quality_score": 0.85,
        "_access_count": 0,
    }
    conf = compute_confidence(props)
    assert conf > 0.8, f"Expected > 0.8, got {conf}"


def test_confidence_old_unused():
    """60-day-old, 0 accesses, no source → confidence < 0.3."""
    props = {
        "_created_at": _iso(delta_days=60),
        "_quality_score": 0.5,
        "_access_count": 0,
    }
    conf = compute_confidence(props)
    assert conf < 0.3, f"Expected < 0.3, got {conf}"


def test_confidence_old_but_used():
    """60-day-old, 100 accesses + source → confidence significantly boosted by anchors.

    Formula: base * age_factor + usage_anchor + source_anchor
    At 60 days with halflife=28: age_factor ≈ 0.226
    usage_anchor = min(0.3, log(101)*0.05) ≈ 0.231
    source_anchor = 0.1
    Result ≈ 0.113 + 0.231 + 0.1 = 0.444  (well above the age-only value of ~0.113)
    """
    props = {
        "_created_at": _iso(delta_days=60),
        "_quality_score": 0.5,
        "_access_count": 100,
        "source": "https://example.com/article",
    }
    conf = compute_confidence(props)
    # Anchors rescue the node significantly above its bare decayed value (~0.113)
    assert conf > 0.35, f"Expected > 0.35 (anchors active), got {conf}"


def test_confidence_evergreen_skips_decay():
    """evergreen=True, 180 days old → confidence >= 0.8."""
    props = {
        "_created_at": _iso(delta_days=180),
        "_quality_score": 0.85,
        "_access_count": 0,
    }
    conf = compute_confidence(props, evergreen=True)
    assert conf >= 0.8, f"Expected >= 0.8, got {conf}"


def test_confidence_clamped_to_one():
    """Confidence never exceeds 1.0."""
    props = {
        "_created_at": _iso(delta_days=0),
        "_quality_score": 1.0,
        "_access_count": 1000,
        "source_url": "https://example.com",
    }
    conf = compute_confidence(props)
    assert conf <= 1.0


def test_confidence_missing_created_at():
    """Missing _created_at defaults gracefully — treated as now (no decay)."""
    props = {
        "_quality_score": 0.7,
        "_access_count": 0,
    }
    conf = compute_confidence(props)
    # Should be close to base quality (minimal decay since age≈0)
    assert conf >= 0.6
