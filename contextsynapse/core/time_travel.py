"""
Context Time Travel + Diffing + Replay
========================================
Query the graph at any past point in time, compare states, replay agent sessions.

1. Time Travel: get_graph_at(timestamp) → snapshot of nodes at that time
2. Diffing: diff_graph(t1, t2) → added/removed/modified nodes
3. Replay: replay_session(run_id) → re-execute with different params

Uses _created_at provenance on every node (no separate versioning needed).

Usage:
    from contextsynapse.core.time_travel import GraphTimeTraveler
    tt = GraphTimeTraveler(db)

    snapshot = tt.at("2026-03-24T10:00:00")
    diff = tt.diff("2026-03-24T10:00:00", "2026-03-25T10:00:00")
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class GraphTimeTraveler:
    """Query graph state at any point in time using _created_at provenance."""

    def __init__(self, db):
        self._db = db

    def at(self, timestamp: str) -> Dict[str, Any]:
        """Get the graph state at a specific timestamp.

        Returns nodes that existed at or before the given time.
        """
        cutoff = timestamp
        all_nodes = self._db.get_all_nodes()

        snapshot_nodes = []
        labels = {}
        for n in all_nodes:
            props = n.properties if hasattr(n, "properties") else {}
            created = props.get("_created_at", "")
            if created and created <= cutoff:
                snapshot_nodes.append(n)
                label = n.label if hasattr(n, "label") else "?"
                labels[label] = labels.get(label, 0) + 1

        return {
            "timestamp": timestamp,
            "node_count": len(snapshot_nodes),
            "labels": labels,
            "nodes": snapshot_nodes,
        }

    def diff(self, t1: str, t2: str) -> Dict[str, Any]:
        """Compare graph state between two timestamps.

        Returns added, removed, and modified nodes.
        """
        snap1 = self.at(t1)
        snap2 = self.at(t2)

        ids1 = {(n.id if hasattr(n, "id") else ""): n for n in snap1["nodes"]}
        ids2 = {(n.id if hasattr(n, "id") else ""): n for n in snap2["nodes"]}

        added = []
        removed = []
        modified = []

        # Nodes in t2 but not t1 = added
        for nid, node in ids2.items():
            if nid not in ids1:
                props = node.properties if hasattr(node, "properties") else {}
                added.append({
                    "id": nid,
                    "label": node.label if hasattr(node, "label") else "",
                    "name": props.get("name") or props.get("title") or "",
                    "created_at": props.get("_created_at", ""),
                    "agent": props.get("_agent_id", ""),
                })

        # Nodes in t1 but not t2 = removed (shouldn't happen with append-only, but possible with archival)
        for nid in ids1:
            if nid not in ids2:
                node = ids1[nid]
                props = node.properties if hasattr(node, "properties") else {}
                removed.append({
                    "id": nid,
                    "label": node.label if hasattr(node, "label") else "",
                    "name": props.get("name") or props.get("title") or "",
                })

        # Nodes in both but with _updated_at between t1 and t2 = modified
        for nid in ids1:
            if nid in ids2:
                props2 = ids2[nid].properties if hasattr(ids2[nid], "properties") else {}
                updated = props2.get("_updated_at", "")
                if updated and t1 < updated <= t2:
                    modified.append({
                        "id": nid,
                        "label": ids2[nid].label if hasattr(ids2[nid], "label") else "",
                        "name": props2.get("name") or props2.get("title") or "",
                        "updated_at": updated,
                        "updated_by": props2.get("_updated_by", ""),
                    })

        # Group added by label
        added_by_label = {}
        for a in added:
            added_by_label[a["label"]] = added_by_label.get(a["label"], 0) + 1

        return {
            "from": t1,
            "to": t2,
            "added": len(added),
            "removed": len(removed),
            "modified": len(modified),
            "added_by_label": added_by_label,
            "added_nodes": added[:20],
            "removed_nodes": removed[:20],
            "modified_nodes": modified[:20],
            "summary": (
                f"{len(added)} added, {len(removed)} removed, {len(modified)} modified "
                f"between {t1[:10]} and {t2[:10]}"
            ),
        }

    def timeline(self, hours: int = 24, bucket_minutes: int = 60) -> List[Dict[str, Any]]:
        """Get a timeline of graph activity bucketed by time.

        Returns hourly (or custom) buckets of node creation activity.
        """
        now = datetime.now(timezone.utc)
        all_nodes = self._db.get_all_nodes()

        # Bucket nodes by time
        buckets = {}
        for n in all_nodes:
            props = n.properties if hasattr(n, "properties") else {}
            created = props.get("_created_at", "")
            if not created:
                continue
            # Truncate to bucket
            try:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                minutes = dt.minute - (dt.minute % bucket_minutes)
                bucket_key = dt.replace(minute=minutes, second=0, microsecond=0).isoformat()
                if (now - dt).total_seconds() <= hours * 3600:
                    if bucket_key not in buckets:
                        buckets[bucket_key] = {"timestamp": bucket_key, "count": 0, "labels": {}, "agents": set()}
                    buckets[bucket_key]["count"] += 1
                    label = n.label if hasattr(n, "label") else "?"
                    buckets[bucket_key]["labels"][label] = buckets[bucket_key]["labels"].get(label, 0) + 1
                    agent = props.get("_agent_id", "")
                    if agent:
                        buckets[bucket_key]["agents"].add(agent)
            except Exception:
                continue

        # Convert sets to lists and sort
        result = sorted(buckets.values(), key=lambda b: b["timestamp"])
        for b in result:
            b["agents"] = list(b["agents"])
        return result

    def get_node_at_version(self, node_id: str, version: int) -> Optional[Dict[str, Any]]:
        """Get node content at a specific version. Returns snapshot dict or None."""
        ts = getattr(self._db, "temporal_storage", None)
        if not ts:
            return None
        return ts.get_version(node_id, version)

    def get_node_history(self, node_id: str) -> List[Dict[str, Any]]:
        """Get all version snapshots for a node, oldest first."""
        ts = getattr(self._db, "temporal_storage", None)
        if not ts:
            return []
        return ts.get_history(node_id)

    def diff_node_versions(self, node_id: str, v1: int, v2: int) -> Dict[str, Any]:
        """Return what changed between two versions of a node.

        Returns dict with keys: node_id, from_version, to_version,
        added (dict), removed (dict), changed (dict of {field: {from, to}}).
        Returns {"error": msg} if a version is not found.
        """
        snap1 = self.get_node_at_version(node_id, v1)
        snap2 = self.get_node_at_version(node_id, v2)
        if not snap1 or not snap2:
            missing = v1 if not snap1 else v2
            return {"error": f"Version {missing} not found for node {node_id}"}

        p1 = snap1.get("properties", {})
        p2 = snap2.get("properties", {})
        # Fields to skip in diff — they always change and carry no semantic meaning
        skip = {"version", "_updated_at", "_created_at", "_stale_since",
                "valid_from", "content_hash", "hash_algorithm"}
        all_keys = (set(p1) | set(p2)) - skip

        added = {k: p2[k] for k in all_keys if k not in p1}
        removed = {k: p1[k] for k in all_keys if k not in p2}
        changed = {
            k: {"from": p1[k], "to": p2[k]}
            for k in all_keys
            if k in p1 and k in p2 and p1[k] != p2[k]
        }
        return {
            "node_id": node_id,
            "from_version": v1,
            "to_version": v2,
            "added": added,
            "removed": removed,
            "changed": changed,
        }

    def restore_node_version(self, node_id: str, version: int,
                              author: str = "", reason: str = "") -> Optional[Any]:
        """Restore node to a previous version by creating a new version.

        Non-destructive — the intermediate versions are preserved.
        Returns the updated GraphNode or None if the version is not found.
        """
        snap = self.get_node_at_version(node_id, version)
        if not snap:
            return None
        old_props = dict(snap.get("properties", {}))
        # Strip version metadata — update_node will set new version
        for field in ("version", "_updated_at", "_updated_by", "_change_reason",
                      "_stale", "_stale_reason",
                      "_stale_since", "_stale_confirmed_by", "_stale_confirmed_at"):
            old_props.pop(field, None)
        restore_reason = reason or f"Restored to v{version}"
        return self._db.update_node(
            node_id,
            old_props,
            author=author,
            reason=restore_reason,
        )

    def agent_activity(self, agent_id: str) -> List[Dict[str, Any]]:
        """Get all nodes created by a specific agent, ordered by time."""
        all_nodes = self._db.get_all_nodes()
        activity = []
        for n in all_nodes:
            props = n.properties if hasattr(n, "properties") else {}
            if props.get("_agent_id") == agent_id:
                activity.append({
                    "id": n.id if hasattr(n, "id") else "",
                    "label": n.label if hasattr(n, "label") else "",
                    "name": props.get("name") or props.get("title") or props.get("tool_name") or "",
                    "created_at": props.get("_created_at", ""),
                    "origin": props.get("_origin", ""),
                })
        activity.sort(key=lambda a: a.get("created_at", ""))
        return activity
