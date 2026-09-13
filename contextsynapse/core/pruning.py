"""
Auto-pruning system and confidence decay for AIContextDB.

Tasks 6 & 7 of the Distributed Context Engine plan.

Components:
- PruningPolicy  — configurable thresholds
- PruneResult    — counts of actions taken
- ContextPruner  — runs pruning passes on a db
- compute_confidence — exponential decay + usage anchoring
- PruningScheduler   — background daemon that runs prune periodically
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from math import exp, log
from typing import List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PruningPolicy:
    stale_days: int = 30
    noise_labels: List[str] = field(default_factory=lambda: [
        "AgentThought",
        "AgentAction",
        "AgentMessage",
        "AgentPresence",
        "ExperimentRun",
    ])
    noise_max_age_hours: int = 72
    min_quality: float = 0.2
    quarantine_days: int = 14
    max_nodes_per_graph: int = 50_000
    evergreen_labels: List[str] = field(default_factory=lambda: [
        "Document",
        "Passage",
        "Schema",
        "ContextMeta",
        "PipelineRun",
    ])


@dataclass
class PruneResult:
    noise_deleted: int = 0
    stale_archived: int = 0
    quarantined: int = 0
    quarantine_deleted: int = 0
    orphans_removed: int = 0


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-format datetime string to a timezone-aware datetime."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _age_hours(created_at: Optional[datetime]) -> float:
    """Hours since *created_at*. Returns 0 if None (treat as just created)."""
    if created_at is None:
        return 0.0
    return (_now() - created_at).total_seconds() / 3600.0


def _age_days(created_at: Optional[datetime]) -> float:
    """Days since *created_at*. Returns 0 if None."""
    return _age_hours(created_at) / 24.0


# ---------------------------------------------------------------------------
# ContextPruner
# ---------------------------------------------------------------------------

class ContextPruner:
    """Runs multi-pass pruning on a single db instance."""

    def prune(self, db, policy: Optional[PruningPolicy] = None) -> PruneResult:
        """Execute all pruning passes and return counts of actions taken."""
        if policy is None:
            policy = PruningPolicy()

        result = PruneResult()
        adapter = db.csr_adapter
        now = _now()

        # Compat: use db-level remove_node (handles both CSR and LMDB)
        _db_ns = (getattr(db, 'name', '') or getattr(db, 'namespace', '')
                  or getattr(db, '_namespace', ''))

        def _remove(adp, nid):
            try:
                if hasattr(db, 'remove_node'):
                    db.remove_node(nid)
                elif hasattr(adp, 'delete_node'):
                    adp.delete_node(nid)
                else:
                    # Direct CSR: remove from nodes dict
                    if hasattr(adp, 'nodes') and nid in adp.nodes:
                        del adp.nodes[nid]
            except Exception:
                pass  # best-effort pruning
            # Clean LMDB search index
            try:
                from ..search.lmdb_index import lmdb_delete_node
                lmdb_delete_node(_db_ns, nid)
            except Exception:
                pass

        noise_set = set(policy.noise_labels)
        evergreen_set = set(policy.evergreen_labels)
        noise_cutoff = timedelta(hours=policy.noise_max_age_hours)
        quarantine_cutoff = timedelta(days=policy.quarantine_days)
        stale_cutoff = timedelta(days=policy.stale_days)

        for node in adapter.get_all_nodes():
            props = node.properties
            label = getattr(node, 'label', None) or getattr(node, 'node_type', '')
            node_id = node.id

            created_at = _parse_dt(props.get("_created_at"))

            # ------------------------------------------------------------------
            # Step 1: Delete old noise
            # ------------------------------------------------------------------
            if label in noise_set:
                age = (now - created_at) if created_at else timedelta(0)
                if age > noise_cutoff:
                    _remove(adapter, node_id)
                    result.noise_deleted += 1
                continue  # noise nodes skip all other checks

            # ------------------------------------------------------------------
            # Step 2: Skip evergreen labels — nothing else applies
            # ------------------------------------------------------------------
            if label in evergreen_set:
                continue

            # ------------------------------------------------------------------
            # Step 3 & 4: Quarantine handling
            # ------------------------------------------------------------------
            quarantined_at = _parse_dt(props.get("_quarantined_at"))

            if quarantined_at is not None:
                # Node is already quarantined — check if it's past expiry
                if (now - quarantined_at) > quarantine_cutoff:
                    _remove(adapter, node_id)
                    result.quarantine_deleted += 1
                    continue
                # Still within quarantine window — leave it alone
                continue

            # Step 3: Quarantine low-quality nodes (not yet quarantined)
            quality = float(props.get("_quality_score", 1.0))
            if quality < policy.min_quality:
                adapter.update_node_properties(node_id, {
                    "_quarantined_at": now.isoformat(),
                })
                result.quarantined += 1
                continue

            # ------------------------------------------------------------------
            # Step 5: Archive (delete) stale nodes — 0 accesses AND old enough
            # ------------------------------------------------------------------
            access_count = int(props.get("_access_count", 0))
            if access_count == 0 and created_at is not None:
                age = now - created_at
                if age > stale_cutoff:
                    _remove(adapter, node_id)
                    result.stale_archived += 1
                    continue

        return result


# ---------------------------------------------------------------------------
# compute_confidence
# ---------------------------------------------------------------------------

def compute_confidence(props: dict, evergreen: bool = False) -> float:
    """
    Compute a confidence score in [0.0, 1.0] combining quality, age decay,
    usage anchoring, and source anchoring.

    Parameters
    ----------
    props:
        Node property dict (may contain _quality_score, _created_at,
        _access_count, source, source_url).
    evergreen:
        If True, skip age decay entirely.
    """
    halflife = float(os.environ.get("CONTEXTSYNAPSE_CONFIDENCE_HALFLIFE_DAYS") or os.environ.get("AICONTEXTDB_CONFIDENCE_HALFLIFE_DAYS", "28"))

    base = float(props.get("_quality_score", 0.5))
    base = max(0.0, min(1.0, base))

    # Age decay
    created_at = _parse_dt(props.get("_created_at"))
    age_days_val = _age_days(created_at)  # 0 if missing → no decay

    if evergreen:
        age_factor = 1.0
    else:
        # Half-life decay: exp(-ln2 * age / halflife)
        age_factor = exp(-0.693 * age_days_val / halflife)

    # Usage anchor — log-scaled, capped at 0.3
    access_count = int(props.get("_access_count", 0))
    usage_anchor = min(0.3, log(1 + access_count) * 0.05)

    # Source anchor — flat bonus for attributed content
    has_source = ("source" in props) or ("source_url" in props)
    source_anchor = 0.1 if has_source else 0.0

    confidence = base * age_factor + usage_anchor + source_anchor
    confidence = max(0.0, min(1.0, confidence))
    return round(confidence, 3)


# ---------------------------------------------------------------------------
# PruningScheduler
# ---------------------------------------------------------------------------

class PruningScheduler:
    """
    Background daemon that runs ContextPruner on all graphs in *registry*
    every *interval* seconds.
    """

    def __init__(self, registry, interval: Optional[int] = None):
        self._registry = registry
        if interval is None:
            interval = int(os.environ.get("CONTEXTSYNAPSE_PRUNE_INTERVAL") or os.environ.get("AICONTEXTDB_PRUNE_INTERVAL", "3600"))
        self._interval = interval
        self._pruner = ContextPruner()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Start the background daemon thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("PruningScheduler already running")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="pruning-scheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info("PruningScheduler started (interval=%ds)", self._interval)

    def stop(self):
        """Signal the daemon thread to stop and wait for it."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("PruningScheduler stopped")

    def _run(self):
        while not self._stop_event.wait(timeout=self._interval):
            self._run_once()

    def _run_once(self):
        policy = PruningPolicy()
        try:
            graph_names = self._registry.list_graphs()
        except Exception as exc:
            logger.error("PruningScheduler: failed to list graphs: %s", exc)
            return

        for name in graph_names:
            try:
                db = self._registry.get(name)
                result = self._pruner.prune(db, policy)
                logger.info(
                    "Pruned graph=%s noise_deleted=%d stale_archived=%d "
                    "quarantined=%d quarantine_deleted=%d",
                    name,
                    result.noise_deleted,
                    result.stale_archived,
                    result.quarantined,
                    result.quarantine_deleted,
                )
            except Exception as exc:
                logger.error("PruningScheduler: error pruning graph %s: %s", name, exc)
