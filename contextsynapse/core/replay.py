"""
Agent Execution Replay
=======================
Git-like replay system for agent experiments. Every write step is checkpointed,
allowing step-by-step replay, graph diffs between steps, and forking from
any point to try alternative decisions.

Like ``git bisect`` for AI agent decisions.

Usage::

    engine = AgentReplayEngine(checkpoint_manager, graph_registry)

    # During experiment execution — record after each write step
    engine.record_step(run_id, step_num=3, agent_id="a1",
                       action="add_knowledge", params={...},
                       result="Created Finding...", namespace="sandbox_ns")

    # After experiment — replay
    steps = engine.get_replay(run_id)
    for step in steps:
        print(step.action, step.diff.nodes_added)

    # Fork from step 3 to try a different approach
    engine.fork_from_step(run_id, step_num=3, new_namespace="fork_ns")
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Only checkpoint on write operations (skip reads to reduce I/O)
WRITE_TOOLS = frozenset({
    "add_knowledge", "add_task", "add_decision", "add_relationship",
    "complete_task", "claim_task", "handoff_task", "log_action",
    "remember", "query_graph",  # query_graph can do CREATE/UPDATE
})


@dataclass
class GraphDiff:
    """Diff between two graph snapshots."""
    nodes_added: List[Dict[str, Any]] = field(default_factory=list)
    nodes_removed: List[str] = field(default_factory=list)
    edges_added: int = 0
    edges_removed: int = 0
    node_count_before: int = 0
    node_count_after: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nodes_added": [{"id": n.get("id", ""), "label": n.get("label", ""), "name": n.get("name", "")} for n in self.nodes_added[:20]],
            "nodes_removed": self.nodes_removed[:20],
            "edges_added": self.edges_added,
            "edges_removed": self.edges_removed,
            "node_count_before": self.node_count_before,
            "node_count_after": self.node_count_after,
        }


@dataclass
class ReplayStep:
    """A single step in an agent replay."""
    step_num: int
    agent_id: str
    agent_name: str
    action: str
    params: Dict[str, Any]
    result: str
    reasoning: str = ""
    checkpoint_id: Optional[str] = None
    diff: Optional[GraphDiff] = None
    timestamp: float = 0.0
    is_write: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step": self.step_num,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "action": self.action,
            "params": self.params,
            "result": self.result[:300],
            "reasoning": self.reasoning[:200],
            "checkpoint_id": self.checkpoint_id,
            "diff": self.diff.to_dict() if self.diff else None,
            "is_write": self.is_write,
            "timestamp": self.timestamp,
        }


class AgentReplayEngine:
    """Records and replays agent experiment steps with graph checkpoints."""

    def __init__(self, checkpoint_manager=None, graph_registry=None):
        self._cp_mgr = checkpoint_manager
        self._registry = graph_registry
        # run_id -> list of ReplayStep
        self._steps: Dict[str, List[ReplayStep]] = {}
        # run_id -> {step_num: node_ids_snapshot}
        self._snapshots: Dict[str, Dict[int, set]] = {}

    def record_step(
        self,
        run_id: str,
        step_num: int,
        agent_id: str,
        agent_name: str,
        action: str,
        params: Dict[str, Any],
        result: str,
        namespace: str,
        reasoning: str = "",
    ) -> Optional[str]:
        """Record a step and optionally checkpoint the graph.

        Only creates checkpoints for write operations to minimize I/O.
        Returns checkpoint_id if created, None otherwise.
        """
        is_write = action in WRITE_TOOLS
        checkpoint_id = None

        # Compute lightweight diff: snapshot node IDs before vs after
        diff = None
        if is_write and self._registry:
            try:
                db = self._registry.get_graph(namespace)
                if db:
                    current_ids = {n.id for n in db.get_all_nodes()}
                    prev_snapshot = self._snapshots.get(run_id, {})

                    # Find the most recent snapshot
                    prev_ids = set()
                    if prev_snapshot:
                        max_step = max(prev_snapshot.keys())
                        prev_ids = prev_snapshot[max_step]

                    added_ids = current_ids - prev_ids
                    removed_ids = prev_ids - current_ids

                    # Get details for added nodes
                    added_nodes = []
                    for nid in list(added_ids)[:20]:
                        node = db.get_node(nid)
                        if node:
                            props = getattr(node, "properties", {}) or {}
                            added_nodes.append({
                                "id": nid,
                                "label": getattr(node, "label", ""),
                                "name": props.get("name", props.get("title", "")),
                            })

                    diff = GraphDiff(
                        nodes_added=added_nodes,
                        nodes_removed=list(removed_ids)[:20],
                        node_count_before=len(prev_ids),
                        node_count_after=len(current_ids),
                    )

                    # Save snapshot for next diff
                    if run_id not in self._snapshots:
                        self._snapshots[run_id] = {}
                    self._snapshots[run_id][step_num] = current_ids

            except Exception as e:
                logger.debug("Replay diff failed: %s", e)

        # Create checkpoint for write steps
        if is_write and self._cp_mgr and namespace:
            try:
                ns_path = self._registry._get_namespace_path(namespace) if self._registry else None
                if ns_path:
                    checkpoint_id = self._cp_mgr.create_checkpoint(
                        namespace=namespace,
                        graph_file=str(ns_path),
                        message=f"replay:step_{step_num}:{agent_name}:{action}",
                    )
            except Exception as e:
                logger.debug("Replay checkpoint failed: %s", e)

        step = ReplayStep(
            step_num=step_num,
            agent_id=agent_id,
            agent_name=agent_name,
            action=action,
            params=params,
            result=result[:500],
            reasoning=reasoning[:300],
            checkpoint_id=checkpoint_id,
            diff=diff,
            timestamp=time.time(),
            is_write=is_write,
        )

        if run_id not in self._steps:
            self._steps[run_id] = []
        self._steps[run_id].append(step)

        if is_write:
            logger.info("[REPLAY] Step %d: %s.%s -> checkpoint=%s, +%d nodes",
                        step_num, agent_name, action, checkpoint_id,
                        len(diff.nodes_added) if diff else 0)

        return checkpoint_id

    def get_replay(
        self,
        run_id: str,
        from_step: int = 0,
        to_step: Optional[int] = None,
    ) -> List[ReplayStep]:
        """Get ordered replay steps, optionally filtered by range."""
        steps = self._steps.get(run_id, [])
        filtered = [s for s in steps if s.step_num >= from_step]
        if to_step is not None:
            filtered = [s for s in filtered if s.step_num <= to_step]
        return sorted(filtered, key=lambda s: (s.agent_name, s.step_num))

    def get_replay_dicts(self, run_id: str) -> List[Dict[str, Any]]:
        """Get replay as serializable dicts (for API response)."""
        return [s.to_dict() for s in self.get_replay(run_id)]

    def get_write_steps(self, run_id: str) -> List[ReplayStep]:
        """Get only write steps (the ones with checkpoints)."""
        return [s for s in self._steps.get(run_id, []) if s.is_write]

    def fork_from_step(
        self,
        run_id: str,
        step_num: int,
        new_namespace: str,
        source_namespace: str = "",
    ) -> Dict[str, Any]:
        """Fork the graph from a specific step's checkpoint.

        Restores the checkpoint at step_num into new_namespace,
        creating a branch point for trying alternative decisions.
        """
        steps = self._steps.get(run_id, [])
        target_step = None
        for s in steps:
            if s.step_num == step_num and s.checkpoint_id:
                target_step = s
                break

        if not target_step:
            # Find the nearest write step before this one
            write_steps = [s for s in steps if s.is_write and s.step_num <= step_num and s.checkpoint_id]
            if write_steps:
                target_step = write_steps[-1]

        if not target_step or not target_step.checkpoint_id:
            return {"error": f"No checkpoint found at or before step {step_num}"}

        if not self._cp_mgr or not self._registry:
            return {"error": "Checkpoint manager or registry not available"}

        # Determine source namespace from the step's context
        ns = source_namespace
        if not ns:
            # Try to find it from checkpoint metadata
            try:
                cp = self._cp_mgr.get_checkpoint(ns, target_step.checkpoint_id)
                if cp:
                    ns = cp.namespace
            except Exception:
                pass

        if not ns:
            return {"error": "Cannot determine source namespace for fork"}

        # Create the fork namespace and restore checkpoint into it
        try:
            self._registry.create_graph(new_namespace)
            ns_path = self._registry._get_namespace_path(new_namespace)
            if not ns_path:
                return {"error": f"Could not create fork namespace '{new_namespace}'"}

            result = self._cp_mgr.restore_checkpoint(
                ns, target_step.checkpoint_id, str(ns_path),
            )
            if not result.get("success"):
                return {"error": f"Checkpoint restore failed: {result.get('error')}"}

            self._registry.load_graph(new_namespace)

            return {
                "forked_from_step": target_step.step_num,
                "checkpoint_id": target_step.checkpoint_id,
                "new_namespace": new_namespace,
                "source_namespace": ns,
                "agent": target_step.agent_name,
                "action": target_step.action,
            }
        except Exception as e:
            return {"error": str(e)}

    def get_graph_at_step(
        self,
        run_id: str,
        step_num: int,
        namespace: str,
    ) -> Dict[str, Any]:
        """Get graph snapshot at a specific step (restores to temp, reads, restores back)."""
        steps = self._steps.get(run_id, [])
        write_steps = [s for s in steps if s.is_write and s.step_num <= step_num and s.checkpoint_id]

        if not write_steps:
            return {"error": f"No checkpoint at or before step {step_num}"}

        target = write_steps[-1]
        snapshot = self._snapshots.get(run_id, {}).get(target.step_num, set())

        return {
            "step": target.step_num,
            "checkpoint_id": target.checkpoint_id,
            "node_count": len(snapshot),
            "node_ids": list(snapshot)[:100],
            "agent": target.agent_name,
            "action": target.action,
        }

    def cleanup(self, run_id: str):
        """Remove replay data for a completed run."""
        self._steps.pop(run_id, None)
        self._snapshots.pop(run_id, None)


# Global singleton
_engine: Optional[AgentReplayEngine] = None


def get_replay_engine(checkpoint_manager=None, graph_registry=None) -> AgentReplayEngine:
    """Get or create the global replay engine.

    If called with new checkpoint_manager/graph_registry, updates the existing
    engine (so it gains checkpoint capability even if first created without it).
    """
    global _engine
    if _engine is None:
        _engine = AgentReplayEngine(checkpoint_manager, graph_registry)
    else:
        # Update if better deps are now available
        if checkpoint_manager and not _engine._cp_mgr:
            _engine._cp_mgr = checkpoint_manager
        if graph_registry and not _engine._registry:
            _engine._registry = graph_registry
    return _engine
