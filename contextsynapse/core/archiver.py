"""
Graph Archiver — TTL-based node archival to cold storage.

Moves old nodes from active graph to archive HDF5 files based on _created_at.
Archived nodes are saved separately and removed from the active graph.

Usage:
    from contextsynapse.core.archiver import GraphArchiver
    archiver = GraphArchiver(graph_registry)
    result = archiver.archive_old_nodes("my_graph", ttl_days=30)
    # {"archived": 150, "remaining": 850, "archive_path": "..."}
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ARCHIVE_DIR = "contextcore_data/archive"


class GraphArchiver:
    """TTL-based graph archival — moves old nodes to cold HDF5 storage."""

    def __init__(self, graph_registry=None):
        self._registry = graph_registry
        Path(ARCHIVE_DIR).mkdir(parents=True, exist_ok=True)

    def archive_old_nodes(
        self,
        graph_name: str,
        ttl_days: int = 30,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Archive nodes older than ttl_days to a separate HDF5 file.

        Args:
            graph_name: Name of the graph to archive from
            ttl_days: Nodes older than this many days get archived
            dry_run: If True, report what would be archived without doing it

        Returns:
            {archived, remaining, archive_path, cutoff_date}
        """
        if not self._registry:
            return {"error": "No graph registry"}

        db = self._registry.get_graph(graph_name)
        if not db:
            return {"error": f"Graph '{graph_name}' not found"}

        cutoff = (datetime.now(timezone.utc) - timedelta(days=ttl_days)).isoformat()
        all_nodes = db.get_all_nodes()

        # Partition nodes by age
        old_nodes = []
        keep_nodes = []
        for node in all_nodes:
            props = node.properties if hasattr(node, "properties") else {}
            created = props.get("_created_at", "")
            if created and created < cutoff:
                old_nodes.append(node)
            else:
                keep_nodes.append(node)

        result = {
            "graph": graph_name,
            "total": len(all_nodes),
            "to_archive": len(old_nodes),
            "remaining": len(keep_nodes),
            "cutoff_date": cutoff[:10],
            "ttl_days": ttl_days,
            "dry_run": dry_run,
        }

        if not old_nodes:
            result["archived"] = 0
            result["message"] = "No nodes older than cutoff"
            return result

        if dry_run:
            result["archived"] = 0
            result["message"] = f"Would archive {len(old_nodes)} nodes"
            return result

        # Save old nodes to archive HDF5
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_name = graph_name.replace(":", "_").replace("/", "_")
        archive_path = os.path.join(ARCHIVE_DIR, f"{safe_name}_archive_{timestamp}.json")

        archived_data = []
        for node in old_nodes:
            archived_data.append({
                "id": node.id if hasattr(node, "id") else str(node),
                "label": node.label if hasattr(node, "label") else "",
                "properties": node.properties if hasattr(node, "properties") else {},
            })

        # Also archive edges connected to archived nodes
        archived_node_ids = {n["id"] for n in archived_data}
        all_edges = db.get_all_edges()
        archived_edges = []
        for edge in all_edges:
            src = edge.source if hasattr(edge, "source") else ""
            tgt = edge.target if hasattr(edge, "target") else ""
            if src in archived_node_ids or tgt in archived_node_ids:
                archived_edges.append({
                    "id": edge.id if hasattr(edge, "id") else "",
                    "source": src,
                    "target": tgt,
                    "label": edge.label if hasattr(edge, "label") else "",
                    "properties": edge.properties if hasattr(edge, "properties") else {},
                })

        # Write archive file
        archive_record = {
            "graph": graph_name,
            "archived_at": datetime.now(timezone.utc).isoformat(),
            "cutoff_date": cutoff,
            "ttl_days": ttl_days,
            "node_count": len(archived_data),
            "edge_count": len(archived_edges),
            "nodes": archived_data,
            "edges": archived_edges,
        }
        with open(archive_path, "w") as f:
            json.dump(archive_record, f, default=str)

        # Remove archived nodes from active graph
        removed = 0
        for node_data in archived_data:
            try:
                db.remove_node(node_data["id"])
                removed += 1
            except Exception as e:
                logger.debug("Failed to remove node %s: %s", node_data["id"][:12], e)

        # Save the active graph
        try:
            self._registry.save_graph(graph_name, create_checkpoint=True)
        except Exception as e:
            logger.warning("Failed to save graph after archival: %s", e)

        result["archived"] = removed
        result["archive_path"] = archive_path
        result["edges_archived"] = len(archived_edges)
        result["message"] = f"Archived {removed} nodes + {len(archived_edges)} edges"

        logger.info("Archived %d nodes from %s (cutoff: %s) → %s",
                     removed, graph_name, cutoff[:10], archive_path)
        return result

    def list_archives(self, graph_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """List archive files, optionally filtered by graph name."""
        archives = []
        for fname in sorted(os.listdir(ARCHIVE_DIR), reverse=True):
            if not fname.endswith(".json"):
                continue
            if graph_name:
                safe = graph_name.replace(":", "_").replace("/", "_")
                if not fname.startswith(safe):
                    continue
            fpath = os.path.join(ARCHIVE_DIR, fname)
            try:
                with open(fpath) as f:
                    header = json.load(f)
                archives.append({
                    "filename": fname,
                    "graph": header.get("graph", ""),
                    "archived_at": header.get("archived_at", ""),
                    "node_count": header.get("node_count", 0),
                    "edge_count": header.get("edge_count", 0),
                    "ttl_days": header.get("ttl_days", 0),
                    "size_bytes": os.path.getsize(fpath),
                })
            except Exception:
                pass
        return archives

    def restore_archive(self, filename: str, graph_name: Optional[str] = None) -> Dict[str, Any]:
        """Restore archived nodes back into a graph."""
        fpath = os.path.join(ARCHIVE_DIR, filename)
        if not os.path.exists(fpath):
            return {"error": f"Archive file not found: {filename}"}

        with open(fpath) as f:
            archive = json.load(f)

        target_graph = graph_name or archive.get("graph", "")
        if not target_graph or not self._registry:
            return {"error": "No target graph specified"}

        db = self._registry.get_graph(target_graph)
        if not db:
            return {"error": f"Graph '{target_graph}' not found"}

        from .graph_structures import GraphNode, GraphEdge

        restored_nodes = 0
        for nd in archive.get("nodes", []):
            try:
                node = GraphNode(id=nd["id"], label=nd["label"], properties=nd.get("properties", {}))
                db.add_node(node)
                restored_nodes += 1
            except Exception:
                pass

        restored_edges = 0
        for ed in archive.get("edges", []):
            try:
                edge = GraphEdge(
                    id=ed.get("id", ""),
                    source=ed["source"], target=ed["target"],
                    label=ed["label"], properties=ed.get("properties", {}),
                )
                db.add_edge(edge)
                restored_edges += 1
            except Exception:
                pass

        try:
            self._registry.save_graph(target_graph)
        except Exception:
            pass

        return {
            "restored_nodes": restored_nodes,
            "restored_edges": restored_edges,
            "from_archive": filename,
            "to_graph": target_graph,
        }
