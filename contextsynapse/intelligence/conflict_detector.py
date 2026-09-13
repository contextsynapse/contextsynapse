"""Conflict Detector — find contradictions between related nodes."""
from __future__ import annotations

import difflib
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .config import IntelligenceConfig

logger = logging.getLogger(__name__)


@dataclass
class ConflictRecord:
    """A detected conflict between two nodes."""
    node_a_id: str
    node_b_id: str
    conflict_type: str  # version_drift | sibling | semantic
    summary: str
    severity: str       # high | medium | low
    conflict_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = "open"  # open | resolved | dismissed
    detected_at: float = field(default_factory=time.time)
    resolution: Optional[str] = None


def is_conflict_candidate(similarity: float, config: IntelligenceConfig) -> bool:
    """Check if a similarity score falls in the conflict candidate band."""
    return config.conflict_similarity_low <= similarity <= config.conflict_similarity_high


def classify_severity(similarity: float) -> str:
    """Classify conflict severity based on similarity. Lower = more severe."""
    if similarity < 0.65:
        return "high"
    elif similarity < 0.8:
        return "medium"
    return "low"


def detect_version_conflict(old_content: str, new_content: str) -> Dict[str, Any]:
    """Compare old vs new content to detect substantive changes."""
    # Normalize whitespace for fair comparison
    old_norm = " ".join(old_content.split())
    new_norm = " ".join(new_content.split())

    if old_norm == new_norm:
        return {"is_conflict": False, "change_ratio": 0.0}

    # Compute change ratio using SequenceMatcher
    ratio = difflib.SequenceMatcher(None, old_norm, new_norm).ratio()
    change_ratio = 1.0 - ratio

    # Threshold: less than 5% change is trivial (formatting, whitespace)
    if change_ratio < 0.05:
        return {"is_conflict": False, "change_ratio": change_ratio}

    # Generate readable diff summary
    old_lines = old_norm.split(". ")
    new_lines = new_norm.split(". ")
    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=0))
    diff_summary = "\n".join(diff[:10])  # cap summary length

    return {
        "is_conflict": True,
        "change_ratio": change_ratio,
        "similarity": ratio,
        "diff_summary": diff_summary,
    }


class ConflictDetector:
    """Manages conflict detection across the graph."""

    def __init__(self, config: IntelligenceConfig, event_bus):
        self._config = config
        self._bus = event_bus
        self._conflicts: Dict[str, ConflictRecord] = {}
        # LLM rate limiting
        self._llm_calls_this_hour: int = 0
        self._llm_hour_start: float = time.time()

    async def on_source_changed(self, event: Dict[str, Any]):
        """Handle source_changed events — Strategy 1 (version conflicts)."""
        node_id = event.get("node_id")
        old_hash = event.get("old_hash", "")
        new_hash = event.get("new_hash", "")
        if old_hash and new_hash and old_hash != new_hash:
            conflict = ConflictRecord(
                node_a_id=node_id,
                node_b_id=f"{node_id}:snapshot",
                conflict_type="version_drift",
                summary=f"Source content changed (hash mismatch)",
                severity="medium",
            )
            self._conflicts[conflict.conflict_id] = conflict
            await self._bus.publish("conflict_found", {
                "conflict_id": conflict.conflict_id,
                "node_a_id": conflict.node_a_id,
                "node_b_id": conflict.node_b_id,
                "conflict_type": conflict.conflict_type,
                "severity": conflict.severity,
            })
            return conflict
        return None

    def resolve_conflict(self, conflict_id: str, resolution: str) -> Optional[ConflictRecord]:
        """Mark a conflict as resolved."""
        conflict = self._conflicts.get(conflict_id)
        if conflict:
            conflict.status = "resolved"
            conflict.resolution = resolution
        return conflict

    def dismiss_conflict(self, conflict_id: str) -> Optional[ConflictRecord]:
        """Dismiss a conflict as not relevant."""
        conflict = self._conflicts.get(conflict_id)
        if conflict:
            conflict.status = "dismissed"
        return conflict

    def list_conflicts(self, status: str = None) -> List[ConflictRecord]:
        """List conflicts, optionally filtered by status."""
        conflicts = list(self._conflicts.values())
        if status:
            conflicts = [c for c in conflicts if c.status == status]
        return sorted(conflicts, key=lambda c: c.detected_at, reverse=True)

    def scan_graph(self, db, graph_name: str = "") -> int:
        """Scan a graph for conflicting facts. Returns count of new conflicts found."""
        found = 0
        try:
            nodes = db.get_all_nodes()
            # Collect Fact nodes
            facts = []
            for n in (nodes or []):
                label = getattr(n, 'label', getattr(n, 'node_type', ''))
                if label != 'Fact':
                    continue
                props = getattr(n, 'properties', {}) or {}
                content = props.get('statement', props.get('content', ''))
                if content:
                    facts.append({"id": getattr(n, 'id', ''), "content": content, "name": props.get('name', '')})

            # Compare facts pairwise (cap at 100 to avoid O(n^2) explosion)
            for i, a in enumerate(facts[:100]):
                for b in facts[i+1:100]:
                    ratio = difflib.SequenceMatcher(None, a["content"].lower(), b["content"].lower()).ratio()
                    if is_conflict_candidate(ratio, self._config):
                        cid = f"{a['id']}:{b['id']}"
                        if cid not in self._conflicts:
                            conflict = ConflictRecord(
                                node_a_id=a["id"],
                                node_b_id=b["id"],
                                conflict_type="sibling",
                                summary=f"Similar facts ({ratio:.0%}): '{a['name'][:40]}' vs '{b['name'][:40]}'",
                                severity=classify_severity(ratio),
                                conflict_id=cid,
                            )
                            self._conflicts[cid] = conflict
                            found += 1
        except Exception as e:
            logger.debug("[CONFLICT] scan_graph error for %s: %s", graph_name, e)
        return found

    def get_stats(self) -> Dict[str, Any]:
        """Return conflict stats for dashboard."""
        all_c = list(self._conflicts.values())
        return {
            "total": len(all_c),
            "open": sum(1 for c in all_c if c.status == "open"),
            "resolved": sum(1 for c in all_c if c.status == "resolved"),
            "dismissed": sum(1 for c in all_c if c.status == "dismissed"),
            "by_severity": {
                "high": sum(1 for c in all_c if c.severity == "high" and c.status == "open"),
                "medium": sum(1 for c in all_c if c.severity == "medium" and c.status == "open"),
                "low": sum(1 for c in all_c if c.severity == "low" and c.status == "open"),
            },
        }
