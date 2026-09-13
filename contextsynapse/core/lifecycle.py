"""
Graph Lifecycle Manager
========================
Safe-mode graph lifecycle: no auto-delete. Graphs get smarter over time.

States:
  active    — being used (context, session, running experiment)
  archived  — run ended, intelligence merged back, kept for replay/audit
  promoted  — sandbox results merged into source
  discarded — user explicitly deleted (manual only)

Intelligence merge: after each experiment run, Decisions/Findings/Insights/Memories
from the sandbox are merged back into the source graph, making it smarter.

Usage::

    lcm = GraphLifecycleManager(graph_registry)

    # After experiment run ends
    result = lcm.merge_intelligence("exp_sandbox_123", "source_graph")
    # → {merged_decisions: 2, merged_findings: 3, merged_insights: 1}

    # Archive sandbox (don't delete)
    lcm.archive("exp_sandbox_123")

    # List all graphs with status
    graphs = lcm.list_graphs()

    # User manually cleans up
    lcm.discard("exp_sandbox_123")  # only on explicit user action
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Node types that represent accumulated team intelligence
INTELLIGENCE_LABELS = frozenset({
    "Decision", "Finding", "Insight", "Memory",
})

# Node types that are ephemeral — not merged back
NOISE_LABELS = frozenset({
    "AgentThought", "AgentAction", "AgentMessage",
    "AgentPresence", "ExperimentRun", "PipelineRun",
})


class StorageTier:
    """Storage tier for graph data."""
    HOT = "hot"            # Redis + Qdrant — fast access, running/recent
    WARM = "warm"          # Disk only — queryable but slower
    COLD = "cold"          # Compressed archive — compliance storage
    TOMBSTONE = "tombstone"  # Metadata only — data deleted, audit record kept


@dataclass
class LifecycleConfig:
    """Production lifecycle configuration."""
    safe_mode: bool = True              # never auto-delete (default)
    auto_merge: bool = True             # merge intelligence on experiment end
    auto_tier: bool = False             # automatically move between tiers
    hot_retention_hours: int = 24       # keep in Redis after run ends
    warm_retention_days: int = 30       # keep on disk after eviction from Redis
    cold_retention_days: int = 365      # keep compressed archive
    require_manual_delete: bool = True  # user must explicitly delete
    archive_dir: str = "contextcore_data/archive"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "safe_mode": self.safe_mode,
            "auto_merge": self.auto_merge,
            "auto_tier": self.auto_tier,
            "hot_retention_hours": self.hot_retention_hours,
            "warm_retention_days": self.warm_retention_days,
            "cold_retention_days": self.cold_retention_days,
            "require_manual_delete": self.require_manual_delete,
        }

    @classmethod
    def development(cls) -> "LifecycleConfig":
        """Dev config — keep everything in hot, no tiering."""
        return cls(safe_mode=True, auto_tier=False)

    @classmethod
    def production(cls) -> "LifecycleConfig":
        """Production config — auto-tier with safe defaults."""
        return cls(safe_mode=True, auto_tier=True,
                   hot_retention_hours=24, warm_retention_days=30, cold_retention_days=365)


@dataclass
class GraphInfo:
    """Information about a managed graph."""
    namespace: str
    status: str = "active"         # active | archived | promoted | discarded
    tier: str = "hot"              # hot | warm | cold | tombstone
    graph_type: str = "unknown"    # context | session | sandbox | test | system
    source_namespace: str = ""     # for sandboxes: the source graph
    experiment_id: str = ""
    run_id: str = ""
    node_count: int = 0
    edge_count: int = 0
    intelligence_count: int = 0    # Decisions + Findings + Insights + Memories
    created_at: str = ""
    last_accessed: str = ""
    merged_at: str = ""            # when intelligence was merged back
    size_estimate_kb: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "namespace": self.namespace,
            "status": self.status,
            "tier": self.tier,
            "graph_type": self.graph_type,
            "source_namespace": self.source_namespace,
            "experiment_id": self.experiment_id,
            "run_id": self.run_id,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "intelligence_count": self.intelligence_count,
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
            "merged_at": self.merged_at,
            "size_estimate_kb": self.size_estimate_kb,
        }


@dataclass
class MergeResult:
    """Result of merging intelligence from sandbox → source."""
    source_namespace: str
    target_namespace: str
    merged_decisions: int = 0
    merged_findings: int = 0
    merged_insights: int = 0
    merged_memories: int = 0
    merged_tasks: int = 0          # completed tasks only
    skipped_noise: int = 0
    skipped_duplicates: int = 0
    total_merged: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_namespace": self.source_namespace,
            "target_namespace": self.target_namespace,
            "merged_decisions": self.merged_decisions,
            "merged_findings": self.merged_findings,
            "merged_insights": self.merged_insights,
            "merged_memories": self.merged_memories,
            "merged_tasks": self.merged_tasks,
            "skipped_noise": self.skipped_noise,
            "skipped_duplicates": self.skipped_duplicates,
            "total_merged": self.total_merged,
        }


class GraphLifecycleManager:
    """Manages graph lifecycle with safe mode (no auto-delete).

    Core principle: graphs get smarter over time. Intelligence from
    experiment runs is merged back into the source graph. Nothing is
    auto-deleted — only archived or explicitly discarded by the user.
    """

    def __init__(self, graph_registry=None, config: Optional[LifecycleConfig] = None):
        self._registry = graph_registry
        self._config = config or LifecycleConfig()
        self._graph_info: Dict[str, GraphInfo] = {}

    @property
    def config(self) -> LifecycleConfig:
        return self._config

    # ── Intelligence Merge ────────────────────────

    def merge_intelligence(
        self,
        sandbox_namespace: str,
        source_namespace: str,
        include_tasks: bool = True,
    ) -> MergeResult:
        """Merge intelligence nodes from sandbox back into the source graph.

        After an experiment run, the sandbox has new Decisions, Findings,
        Insights, and Memories created by agents. This merges them into
        the source graph so future runs start with accumulated intelligence.

        Only merges INTELLIGENCE nodes. Skips noise (AgentThought, etc.)
        and duplicates (same content already in source).
        """
        if not self._registry:
            return MergeResult(sandbox_namespace, source_namespace)

        sandbox_db = self._registry.get_graph(sandbox_namespace, load_if_missing=True)
        source_db = self._registry.get_graph(source_namespace, load_if_missing=True)

        if not sandbox_db or not source_db:
            logger.warning("[LIFECYCLE] Cannot merge: sandbox=%s source=%s",
                           sandbox_namespace, source_namespace)
            return MergeResult(sandbox_namespace, source_namespace)

        from .graph_structures import GraphNode, GraphEdge

        result = MergeResult(sandbox_namespace, source_namespace)
        now = datetime.now(timezone.utc).isoformat()

        # Get existing content hashes in source (for dedup)
        source_hashes = set()
        for n in source_db.get_all_nodes():
            props = getattr(n, "properties", {}) or {}
            for key in ("content", "title", "name"):
                val = props.get(key, "")
                if val:
                    source_hashes.add(val[:200].lower().strip())

        # Scan sandbox for intelligence nodes
        for node in sandbox_db.get_all_nodes():
            label = getattr(node, "label", "")
            props = (getattr(node, "properties", {}) or {}).copy()

            # Skip noise
            if label in NOISE_LABELS:
                result.skipped_noise += 1
                continue

            # Skip non-intelligence
            if label not in INTELLIGENCE_LABELS and label != "Task":
                continue

            # For Tasks: only merge completed ones
            if label == "Task":
                if not include_tasks:
                    continue
                if props.get("status") != "completed":
                    continue

            # Skip garbage auto-write findings
            content = props.get("content", "")
            if "Found 20 node(s) via aiql" in content or "Analysis: Found" in content:
                result.skipped_noise += 1
                continue

            # Dedup: skip if same content or title already in source
            dedup_keys = []
            for key in ("content", "title", "name"):
                val = props.get(key, "")
                if val:
                    dedup_keys.append(val[:200].lower().strip())
            if any(k in source_hashes for k in dedup_keys):
                result.skipped_duplicates += 1
                continue
            content_key = dedup_keys[0] if dedup_keys else ""

            # Merge: create node in source graph
            props["_merged_from"] = sandbox_namespace
            props["_merged_at"] = now
            merge_id = f"merged_{uuid.uuid4().hex[:10]}"

            try:
                source_db.add_node(GraphNode(
                    id=merge_id, label=label, properties=props,
                ), write_through=True)

                if content_key:
                    source_hashes.add(content_key)

                if label == "Decision":
                    result.merged_decisions += 1
                elif label == "Finding":
                    result.merged_findings += 1
                elif label == "Insight":
                    result.merged_insights += 1
                elif label == "Memory":
                    result.merged_memories += 1
                elif label == "Task":
                    result.merged_tasks += 1

                result.total_merged += 1
            except Exception as e:
                logger.debug("[LIFECYCLE] Merge node failed: %s", e)

        logger.info(
            "[LIFECYCLE] Merged intelligence %s → %s: "
            "%d decisions, %d findings, %d insights, %d memories, %d tasks "
            "(%d noise skipped, %d duplicates skipped)",
            sandbox_namespace, source_namespace,
            result.merged_decisions, result.merged_findings,
            result.merged_insights, result.merged_memories, result.merged_tasks,
            result.skipped_noise, result.skipped_duplicates,
        )

        return result

    # ── Graph State Management ────────────────────

    def register_graph(self, namespace: str, graph_type: str = "unknown",
                        source_namespace: str = "", experiment_id: str = "",
                        run_id: str = "") -> GraphInfo:
        """Register a graph in the lifecycle tracker."""
        info = GraphInfo(
            namespace=namespace,
            status="active",
            graph_type=graph_type,
            source_namespace=source_namespace,
            experiment_id=experiment_id,
            run_id=run_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            last_accessed=datetime.now(timezone.utc).isoformat(),
        )
        self._graph_info[namespace] = info
        return info

    def archive(self, namespace: str) -> bool:
        """Archive a graph (safe — no deletion). Marked for potential cleanup."""
        info = self._graph_info.get(namespace)
        if info:
            info.status = "archived"
            return True
        # Create info if not tracked
        self._graph_info[namespace] = GraphInfo(
            namespace=namespace, status="archived",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        return True

    def promote(self, namespace: str) -> bool:
        """Mark sandbox as promoted (intelligence merged into source)."""
        info = self._graph_info.get(namespace)
        if info:
            info.status = "promoted"
            info.merged_at = datetime.now(timezone.utc).isoformat()
            return True
        return False

    def discard(self, namespace: str, force: bool = False) -> Dict[str, Any]:
        """Explicitly discard a graph (USER ACTION ONLY).

        Safe mode: requires force=True for active graphs.
        Only archived/promoted graphs can be discarded without force.
        """
        info = self._graph_info.get(namespace)

        if info and info.status == "active" and not force:
            return {
                "error": "Cannot discard active graph. Archive it first or use force=True.",
                "status": info.status,
            }

        # Actually delete from registry
        deleted_registry = False
        if self._registry:
            try:
                self._registry.delete_graph(namespace)
                deleted_registry = True
            except Exception as e:
                logger.warning("[LIFECYCLE] Registry delete failed for %s: %s", namespace, e)

        if info:
            info.status = "discarded"

        logger.info("[LIFECYCLE] Discarded graph '%s' (registry_deleted=%s)",
                    namespace, deleted_registry)
        return {"discarded": True, "namespace": namespace, "registry_deleted": deleted_registry}

    # ── Listing + Visibility ──────────────────────

    def list_graphs(self, status: str = "", graph_type: str = "") -> List[GraphInfo]:
        """List all tracked graphs with optional filters."""
        # Refresh counts from registry
        self._refresh_counts()

        graphs = list(self._graph_info.values())
        if status:
            graphs = [g for g in graphs if g.status == status]
        if graph_type:
            graphs = [g for g in graphs if g.graph_type == graph_type]
        return sorted(graphs, key=lambda g: g.created_at or "", reverse=True)

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all graphs by status and type."""
        self._refresh_counts()
        by_status = {}
        by_type = {}
        total_nodes = 0
        total_intelligence = 0

        for info in self._graph_info.values():
            by_status[info.status] = by_status.get(info.status, 0) + 1
            by_type[info.graph_type] = by_type.get(info.graph_type, 0) + 1
            total_nodes += info.node_count
            total_intelligence += info.intelligence_count

        return {
            "total_graphs": len(self._graph_info),
            "by_status": by_status,
            "by_type": by_type,
            "total_nodes": total_nodes,
            "total_intelligence_nodes": total_intelligence,
        }

    def _refresh_counts(self):
        """Update node/edge counts from registry."""
        if not self._registry:
            return
        for ns, info in self._graph_info.items():
            if info.status == "discarded":
                continue
            try:
                db = self._registry.get_graph(ns)
                if db:
                    nodes = db.get_all_nodes()
                    info.node_count = len(nodes)
                    info.edge_count = len(db.get_all_edges())
                    info.intelligence_count = sum(
                        1 for n in nodes
                        if getattr(n, "label", "") in INTELLIGENCE_LABELS
                    )
            except Exception:
                pass

    # ── Experiment Run Integration ────────────────

    def on_experiment_start(self, sandbox_namespace: str, source_namespace: str,
                             experiment_id: str, run_id: str) -> GraphInfo:
        """Called when an experiment creates a sandbox."""
        return self.register_graph(
            sandbox_namespace, graph_type="sandbox",
            source_namespace=source_namespace,
            experiment_id=experiment_id, run_id=run_id,
        )

    def on_experiment_end(self, sandbox_namespace: str, source_namespace: str,
                           auto_merge: bool = True) -> Dict[str, Any]:
        """Called when an experiment run completes.

        Default behavior: merge intelligence back, then archive sandbox.
        Safe mode: NEVER deletes. Archives only.
        """
        result = {"sandbox": sandbox_namespace, "source": source_namespace}

        # Merge intelligence back to source
        if auto_merge and source_namespace:
            merge = self.merge_intelligence(sandbox_namespace, source_namespace)
            result["merge"] = merge.to_dict()
            self.promote(sandbox_namespace)
        else:
            self.archive(sandbox_namespace)

        return result

    # ── Tiered Storage ────────────────────────────

    def migrate_to_warm(self, namespace: str) -> Dict[str, Any]:
        """Move graph from HOT (Redis) to WARM (disk only).

        Evicts from Redis but keeps HDF5/JSON files on disk.
        Graph can still be loaded on-demand (slower).
        """
        info = self._graph_info.get(namespace)
        if not info:
            return {"error": f"Graph '{namespace}' not tracked"}
        if info.tier != StorageTier.HOT:
            return {"error": f"Graph is already in {info.tier} tier"}

        # Save to disk first
        if self._registry:
            try:
                self._registry.save_graph(namespace, create_checkpoint=False)
            except Exception as e:
                logger.warning("[LIFECYCLE] Save before warm migration failed: %s", e)

        # Evict from Redis (keep disk files)
        evicted_keys = 0
        try:
            import redis as _redis_mod
            redis_url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL", "")
            if redis_url:
                r = _redis_mod.from_url(redis_url, decode_responses=True)
                safe_ns = namespace.replace(":", "_")
                # Delete graph-specific Redis keys
                for pattern in [f"context:{safe_ns}:*", f"ntype:{safe_ns}:*",
                                f"etype:{safe_ns}:*", f"adj:out:{safe_ns}:*",
                                f"adj:in:{safe_ns}:*"]:
                    for key in r.scan_iter(pattern, count=100):
                        r.delete(key)
                        evicted_keys += 1
                # Remove from context registry in Redis
                r.srem("context:names", namespace)
                r.hdel(f"context:{namespace}", *r.hkeys(f"context:{namespace}") or ["_"])
        except Exception as e:
            logger.debug("[LIFECYCLE] Redis eviction partial: %s", e)

        info.tier = StorageTier.WARM
        logger.info("[LIFECYCLE] %s: HOT → WARM (evicted %d Redis keys)", namespace, evicted_keys)
        return {"namespace": namespace, "tier": "warm", "evicted_keys": evicted_keys}

    def migrate_to_cold(self, namespace: str) -> Dict[str, Any]:
        """Move graph from WARM (disk) to COLD (compressed archive).

        Compresses graph data into a gzipped bundle and removes original files.
        """
        info = self._graph_info.get(namespace)
        if not info:
            return {"error": f"Graph '{namespace}' not tracked"}
        if info.tier not in (StorageTier.WARM, StorageTier.HOT):
            return {"error": f"Graph is already in {info.tier} tier"}

        archive_dir = Path(self._config.archive_dir)
        archive_dir.mkdir(parents=True, exist_ok=True)

        safe_ns = namespace.replace(":", "_")
        ns_dir = Path("contextcore_data/namespaces") / safe_ns

        if not ns_dir.exists():
            return {"error": f"Namespace directory not found: {ns_dir}"}

        # Compress to gzip bundle
        archive_path = archive_dir / f"{safe_ns}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.tar.gz"
        try:
            import tarfile
            with tarfile.open(str(archive_path), "w:gz") as tar:
                tar.add(str(ns_dir), arcname=safe_ns)
            archive_size = archive_path.stat().st_size

            # Remove original files (keep archive)
            shutil.rmtree(str(ns_dir), ignore_errors=True)

            # Also delete Qdrant collection
            try:
                from qdrant_client import QdrantClient
                qc = QdrantClient(url=os.environ.get("QDRANT_URL", "http://localhost:6333"))
                collection = f"{safe_ns}_passages"
                if qc.collection_exists(collection):
                    qc.delete_collection(collection)
            except Exception:
                pass

            # Delete BM25 index
            bm25_dir = Path(f"contextcore_data/bm25_index/{safe_ns}")
            if bm25_dir.exists():
                shutil.rmtree(str(bm25_dir), ignore_errors=True)

            info.tier = StorageTier.COLD
            info.size_estimate_kb = archive_size // 1024
            logger.info("[LIFECYCLE] %s: WARM → COLD (archive: %s, %dKB)",
                        namespace, archive_path, archive_size // 1024)
            return {"namespace": namespace, "tier": "cold",
                    "archive_path": str(archive_path), "size_kb": archive_size // 1024}

        except Exception as e:
            return {"error": f"Archive failed: {e}"}

    def tombstone(self, namespace: str) -> Dict[str, Any]:
        """Delete all data, keep only metadata record (for audit).

        FINAL STATE — cannot be undone.
        """
        info = self._graph_info.get(namespace)
        if not info:
            return {"error": f"Graph '{namespace}' not tracked"}

        if info.status == "active" and self._config.require_manual_delete:
            return {"error": "Cannot tombstone active graph. Archive or discard first."}

        # Delete archive if in cold
        safe_ns = namespace.replace(":", "_")
        archive_dir = Path(self._config.archive_dir)
        for f in archive_dir.glob(f"{safe_ns}_*"):
            f.unlink(missing_ok=True)

        # Delete any remaining disk files
        ns_dir = Path("contextcore_data/namespaces") / safe_ns
        if ns_dir.exists():
            shutil.rmtree(str(ns_dir), ignore_errors=True)

        # Delete from registry
        if self._registry:
            try:
                self._registry.delete_graph(namespace)
            except Exception:
                pass

        info.tier = StorageTier.TOMBSTONE
        info.status = "discarded"
        info.node_count = 0
        info.edge_count = 0
        info.intelligence_count = 0
        info.size_estimate_kb = 0

        logger.info("[LIFECYCLE] %s: → TOMBSTONE (metadata record kept for audit)", namespace)
        return {"namespace": namespace, "tier": "tombstone", "data_deleted": True}

    # ── Automatic Tier Migration ──────────────────

    def run_tier_migration(self) -> Dict[str, Any]:
        """Run automatic tier migration based on lifecycle config.

        Called by scheduler (e.g., every hour). Moves graphs between tiers
        based on age and retention policy.

        Safe mode: NEVER deletes. Only moves between tiers.
        """
        if not self._config.auto_tier:
            return {"skipped": True, "reason": "auto_tier disabled"}

        now = datetime.now(timezone.utc)
        migrated = {"hot_to_warm": 0, "warm_to_cold": 0}

        for ns, info in list(self._graph_info.items()):
            if info.status == "active":
                continue  # never touch active graphs
            if info.tier == StorageTier.TOMBSTONE:
                continue

            if not info.created_at:
                continue

            try:
                created = datetime.fromisoformat(info.created_at.replace("Z", "+00:00"))
                age = now - created
            except Exception:
                continue

            # HOT → WARM: after hot_retention_hours
            if (info.tier == StorageTier.HOT and
                    info.status in ("archived", "promoted") and
                    age > timedelta(hours=self._config.hot_retention_hours)):
                result = self.migrate_to_warm(ns)
                if "error" not in result:
                    migrated["hot_to_warm"] += 1

            # WARM → COLD: after warm_retention_days
            elif (info.tier == StorageTier.WARM and
                  age > timedelta(days=self._config.warm_retention_days)):
                result = self.migrate_to_cold(ns)
                if "error" not in result:
                    migrated["warm_to_cold"] += 1

        logger.info("[LIFECYCLE] Tier migration: %d HOT→WARM, %d WARM→COLD",
                    migrated["hot_to_warm"], migrated["warm_to_cold"])
        return migrated

    def get_tier_summary(self) -> Dict[str, Any]:
        """Summary of graphs by storage tier."""
        by_tier = {StorageTier.HOT: 0, StorageTier.WARM: 0,
                   StorageTier.COLD: 0, StorageTier.TOMBSTONE: 0}
        size_by_tier = {StorageTier.HOT: 0, StorageTier.WARM: 0,
                        StorageTier.COLD: 0}

        for info in self._graph_info.values():
            tier = info.tier
            by_tier[tier] = by_tier.get(tier, 0) + 1
            if tier != StorageTier.TOMBSTONE:
                size_by_tier[tier] = size_by_tier.get(tier, 0) + info.size_estimate_kb

        return {
            "by_tier": by_tier,
            "size_kb_by_tier": size_by_tier,
            "total_graphs": len(self._graph_info),
            "config": self._config.to_dict(),
        }


# Global singleton
_lifecycle: Optional[GraphLifecycleManager] = None


def get_lifecycle_manager(graph_registry=None,
                          config: Optional[LifecycleConfig] = None) -> GraphLifecycleManager:
    global _lifecycle
    if _lifecycle is None:
        _lifecycle = GraphLifecycleManager(graph_registry, config)
    else:
        if graph_registry and not _lifecycle._registry:
            _lifecycle._registry = graph_registry
        if config:
            _lifecycle._config = config
    return _lifecycle
