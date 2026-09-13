"""
Graph Replication — Snapshot shipping for multi-region read replicas.

Exports graph snapshots as portable HDF5/JSON files that can be shipped
to other regions and imported as read replicas.

Usage:
    from contextsynapse.core.replication import GraphReplicator
    replicator = GraphReplicator(graph_registry)

    # Export a snapshot
    snapshot = replicator.export_snapshot("my_graph")
    # → contextcore_data/snapshots/my_graph_20260324_120000.json

    # Import on another region
    replicator.import_snapshot("path/to/snapshot.json", read_only=True)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SNAPSHOT_DIR = "contextcore_data/snapshots"


class GraphReplicator:
    """Export/import graph snapshots for cross-region replication."""

    def __init__(self, graph_registry=None):
        self._registry = graph_registry
        Path(SNAPSHOT_DIR).mkdir(parents=True, exist_ok=True)

    def export_snapshot(self, graph_name: str) -> Dict[str, Any]:
        """Export a graph as a portable snapshot file.

        Returns:
            {snapshot_path, node_count, edge_count, size_bytes, region, exported_at}
        """
        if not self._registry:
            return {"error": "No graph registry"}

        db = self._registry.get_graph(graph_name)
        if not db:
            return {"error": f"Graph '{graph_name}' not found"}

        nodes = db.get_all_nodes()
        edges = db.get_all_edges()

        # Serialize nodes
        node_data = []
        for n in nodes:
            node_data.append({
                "id": n.id if hasattr(n, "id") else str(n),
                "label": n.label if hasattr(n, "label") else "",
                "properties": n.properties if hasattr(n, "properties") else {},
            })

        # Serialize edges
        edge_data = []
        for e in edges:
            edge_data.append({
                "id": e.id if hasattr(e, "id") else "",
                "source": e.source if hasattr(e, "source") else "",
                "target": e.target if hasattr(e, "target") else "",
                "label": e.label if hasattr(e, "label") else "",
                "properties": e.properties if hasattr(e, "properties") else {},
            })

        from .write_context import REGION
        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y%m%d_%H%M%S")
        safe_name = graph_name.replace(":", "_").replace("/", "_")
        filename = f"{safe_name}_{timestamp}.json"
        snapshot_path = os.path.join(SNAPSHOT_DIR, filename)

        snapshot = {
            "format": "contextcore_snapshot_v1",
            "graph_name": graph_name,
            "region": REGION,
            "exported_at": now.isoformat(),
            "node_count": len(node_data),
            "edge_count": len(edge_data),
            "nodes": node_data,
            "edges": edge_data,
        }

        with open(snapshot_path, "w") as f:
            json.dump(snapshot, f, default=str)

        size = os.path.getsize(snapshot_path)
        logger.info("[REPLICATION] Exported snapshot: %s (%d nodes, %d edges, %d bytes)",
                     filename, len(node_data), len(edge_data), size)

        return {
            "snapshot_path": snapshot_path,
            "filename": filename,
            "node_count": len(node_data),
            "edge_count": len(edge_data),
            "size_bytes": size,
            "region": REGION,
            "exported_at": now.isoformat(),
        }

    def import_snapshot(
        self,
        snapshot_path: str,
        target_graph: Optional[str] = None,
        read_only: bool = False,
    ) -> Dict[str, Any]:
        """Import a graph snapshot from a file.

        Args:
            snapshot_path: Path to the snapshot JSON file
            target_graph: Override graph name (default: use name from snapshot)
            read_only: If True, mark imported nodes as read-only replicas
        """
        if not os.path.exists(snapshot_path):
            return {"error": f"Snapshot file not found: {snapshot_path}"}

        with open(snapshot_path) as f:
            snapshot = json.load(f)

        if snapshot.get("format") != "contextcore_snapshot_v1":
            return {"error": "Unknown snapshot format"}

        graph_name = target_graph or snapshot.get("graph_name", "")
        if not graph_name or not self._registry:
            return {"error": "No target graph name or registry"}

        # Create or get graph
        db = self._registry.get_graph(graph_name)
        if not db:
            self._registry.create_graph(graph_name)
            db = self._registry.get_graph(graph_name)
        if not db:
            return {"error": f"Could not create graph '{graph_name}'"}

        from .graph_structures import GraphNode, GraphEdge

        source_region = snapshot.get("region", "unknown")
        imported_nodes = 0
        imported_edges = 0

        for nd in snapshot.get("nodes", []):
            try:
                props = nd.get("properties", {})
                if read_only:
                    props["_replica"] = True
                    props["_source_region"] = source_region
                node = GraphNode(id=nd["id"], label=nd["label"], properties=props)
                db.add_node(node)
                imported_nodes += 1
            except Exception:
                pass  # optional

        for ed in snapshot.get("edges", []):
            try:
                props = ed.get("properties", {})
                if read_only:
                    props["_replica"] = True
                    props["_source_region"] = source_region
                edge = GraphEdge(
                    id=ed.get("id", ""), source=ed["source"],
                    target=ed["target"], label=ed["label"], properties=props,
                )
                db.add_edge(edge)
                imported_edges += 1
            except Exception:
                pass  # optional

        try:
            self._registry.save_graph(graph_name)
        except Exception:
            pass  # optional

        logger.info("[REPLICATION] Imported snapshot into %s: %d nodes, %d edges (from %s)",
                     graph_name, imported_nodes, imported_edges, source_region)

        return {
            "graph": graph_name,
            "imported_nodes": imported_nodes,
            "imported_edges": imported_edges,
            "source_region": source_region,
            "read_only": read_only,
        }

    # Node labels that represent accumulated team intelligence — ALWAYS cloned
    INTELLIGENCE_LABELS = frozenset({
        "Decision", "Insight", "Finding", "Task", "Memory",
    })

    # Node labels that are ephemeral agent noise — NEVER cloned
    NOISE_LABELS = frozenset({
        "AgentThought", "AgentAction", "AgentMessage",
        "AgentPresence", "ExperimentRun", "PipelineRun",
    })

    def clone_graph(self, source_name: str, target_name: str,
                    smart_clone: bool = True) -> Dict[str, Any]:
        """Clone a graph into a new namespace for sandbox experiments.

        Smart clone (default): copies all original data + accumulated team
        intelligence (Decisions, Insights, verified Findings, completed Tasks)
        while filtering out ephemeral noise (AgentThought, AgentAction, etc.).

        This ensures each experiment builds on prior team knowledge instead
        of starting from a blank slate.

        Args:
            smart_clone: If True, filter noise nodes. If False, clone everything.
        Returns: {target_graph, cloned_nodes, cloned_edges, skipped_noise, carried_intelligence}
        """
        if not self._registry:
            return {"error": "No graph registry"}

        db = self._registry.get_graph(source_name, load_if_missing=True)
        if not db:
            # Fallback: source may be a Redis-backed graph not in registry metadata
            db = self._registry.get_graph_for_request(source_name)
            if not db or not db.get_all_nodes():
                return {"error": f"Source graph '{source_name}' not found"}

        # Create target — use get_graph_for_request so it picks up Redis backend
        target_db = self._registry.get_graph(target_name)
        if not target_db:
            self._registry.create_graph(target_name)
            target_db = self._registry.get_graph(target_name)
        if not target_db:
            target_db = self._registry.get_graph_for_request(target_name)

        from .graph_structures import GraphNode, GraphEdge

        nodes = db.get_all_nodes()
        edges = db.get_all_edges()

        cloned_nodes = 0
        skipped_noise = 0
        carried_intelligence = 0
        cloned_node_ids = set()

        for n in nodes:
            try:
                label = n.label if hasattr(n, "label") else ""
                props = (n.properties if hasattr(n, "properties") else {}).copy()
                node_id = n.id if hasattr(n, "id") else str(n)

                if smart_clone:
                    # Skip ephemeral noise
                    if label in self.NOISE_LABELS:
                        skipped_noise += 1
                        continue

                    # For intelligence nodes, apply quality filters
                    if label in self.INTELLIGENCE_LABELS:
                        # Skip archived/stale tasks
                        if label == "Task" and props.get("status") in ("archived", "stale"):
                            skipped_noise += 1
                            continue

                        # Skip Findings that contain raw tool output (auto-write garbage)
                        if label == "Finding":
                            content = props.get("content", "")
                            if "Found 20 node(s) via aiql" in content or "Analysis: Found" in content:
                                skipped_noise += 1
                                continue

                        carried_intelligence += 1

                node = GraphNode(id=node_id, label=label, properties=props)
                target_db.add_node(node)
                cloned_nodes += 1
                cloned_node_ids.add(node_id)
            except Exception:
                pass

        # Clone edges — only if both endpoints were cloned
        cloned_edges = 0
        for e in edges:
            try:
                src = e.source if hasattr(e, "source") else ""
                tgt = e.target if hasattr(e, "target") else ""

                if smart_clone and (src not in cloned_node_ids or tgt not in cloned_node_ids):
                    continue  # skip edges pointing to filtered nodes

                props = (e.properties if hasattr(e, "properties") else {}).copy()
                edge = GraphEdge(
                    id=e.id if hasattr(e, "id") else "",
                    source=src, target=tgt,
                    label=e.label if hasattr(e, "label") else "",
                    properties=props,
                )
                target_db.add_edge(edge)
                cloned_edges += 1
            except Exception:
                pass

        logger.info(
            "[REPLICATION] Smart clone '%s' → '%s': %d nodes (%d intelligence carried, %d noise skipped), %d edges",
            source_name, target_name, cloned_nodes, carried_intelligence, skipped_noise, cloned_edges,
        )

        return {
            "target_graph": target_name,
            "cloned_nodes": cloned_nodes,
            "cloned_edges": cloned_edges,
            "carried_intelligence": carried_intelligence,
            "skipped_noise": skipped_noise,
        }

    def list_snapshots(self) -> List[Dict[str, Any]]:
        """List available snapshot files."""
        snapshots = []
        for fname in sorted(os.listdir(SNAPSHOT_DIR), reverse=True):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(SNAPSHOT_DIR, fname)
            try:
                with open(fpath) as f:
                    header = json.load(f)
                snapshots.append({
                    "filename": fname,
                    "graph_name": header.get("graph_name", ""),
                    "region": header.get("region", ""),
                    "exported_at": header.get("exported_at", ""),
                    "node_count": header.get("node_count", 0),
                    "edge_count": header.get("edge_count", 0),
                    "size_bytes": os.path.getsize(fpath),
                })
            except Exception:
                pass  # optional
        return snapshots
