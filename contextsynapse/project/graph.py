"""
Project Knowledge Graph
========================
A structured graph representing a software project: tasks, documents,
decisions, code files, and agents. Provides team coordination primitives
(claim, complete, handoff) so multiple AI agents collaborate like a team.

Usage:
    from contextsynapse.project import ProjectGraph

    pg = ProjectGraph("my-project")
    pg.seed_from_directory(".")                    # auto-scan codebase
    pg.add_task("Refactor auth module", priority="high", tags=["auth", "security"])
    pg.claim_task(task_id, agent_id="claude")
    pg.complete_task(task_id, agent_id="claude", summary="Extracted token logic")
    pg.handoff_task(task_id, from_agent="claude", to_agent="codex", notes="Need tests")
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..adapters._base import AIContextDBConnection
from ..context.agents import AgentRegistry
from .task_lock import TaskLock, TaskEventBus

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectGraph:
    """A project-aware knowledge graph with team coordination.

    Wraps AIContextDBConnection with project-specific node types and
    team workflow primitives (task assignment, handoff, completion).
    """

    def __init__(
        self,
        name: str,
        connection: Optional[AIContextDBConnection] = None,
        agent_registry: Optional[AgentRegistry] = None,
    ):
        self.name = name
        self.conn = connection or AIContextDBConnection(namespace=name)
        self.registry = agent_registry or AgentRegistry()
        self._task_lock = TaskLock(namespace=name)
        self._event_bus = TaskEventBus(namespace=name)

        # Ensure the project node exists and is linked to Context root
        existing = self.conn.get_nodes(label="Project", where={"name": name})
        if not existing:
            result = self.conn.query(
                f'CREATE NODE Project {{name: "{name}", status: "active", created_at: "{_now()}"}}'
            )
            project_id = result.get("data", {}).get("uuid", "")
            # Link Project to Context root if it exists
            if project_id:
                ctx_nodes = self.conn.get_nodes(label="Context")
                if ctx_nodes:
                    ctx_id = ctx_nodes[0].id if hasattr(ctx_nodes[0], "id") else None
                    if ctx_id:
                        self.conn.add_edge(ctx_id, project_id, "HAS_PROJECT")

    def _resolve_task_id(self, task_id: str):
        """Resolve a full or partial task ID to a graph node."""
        # Try adapter directly (works with Redis/LMDB/CSR)
        try:
            db = self.conn.db if hasattr(self.conn, "db") else None
            if db:
                adapter = getattr(db, 'csr_adapter', None)
                if adapter:
                    node = adapter.get_node(task_id)
                    if node:
                        return node
        except Exception:
            pass

        # Try via connection get_node
        try:
            node = self.conn.get_node(task_id)
            if node:
                return node
        except Exception:
            pass

        # Try via AIQL query
        try:
            result = self.conn.query(f'SELECT * FROM Task WHERE id = "{task_id}"')
            nodes = result.get("nodes", [])
            if not nodes:
                result = self.conn.query(f'SELECT * FROM Task WHERE uuid = "{task_id}"')
                nodes = result.get("nodes", [])
            if nodes:
                n = nodes[0]
                if isinstance(n, dict):
                    props = n.get("properties", {}) if "properties" in n else {k: v for k, v in n.items() if k not in ("id", "uuid", "label")}
                    return type("N", (), {
                        "id": n.get("id", n.get("uuid", task_id)),
                        "label": "Task",
                        "properties": props,
                    })()
        except Exception:
            pass

        # Prefix match: search all Task nodes
        try:
            for t in self.conn.get_nodes(label="Task"):
                tid = t.id if hasattr(t, "id") else (t.get("id") or t.get("uuid", ""))
                if tid.startswith(task_id):
                    return t
        except Exception:
            pass

        return None

    def _find_category_node(self, category: str) -> Optional[str]:
        """Find a category node (CodeBase, KnowledgeBase, etc.) in the context graph.

        The context schema creates category nodes like:
            Context --HAS_CODE_BASE--> CodeBase
            Context --HAS_KNOWLEDGE_BASE--> KnowledgeBase

        Returns the category node ID, or None.
        """
        # Map node labels to their parent category label
        _LABEL_TO_CATEGORY = {
            "Task": "CodeBase", "ProjectSpec": "CodeBase", "CodeFile": "CodeBase",
            "Action": "CodeBase", "Module": "CodeBase",
            "Decision": "UserStore", "AgentPresence": "UserStore",
            "Document": "KnowledgeBase", "Finding": "KnowledgeBase",
            "Entity": "KnowledgeBase", "Fact": "KnowledgeBase",
            "Summary": "GeneratedStore", "Response": "GeneratedStore",
        }
        cat_label = _LABEL_TO_CATEGORY.get(category)
        if not cat_label:
            return None
        nodes = self.conn.get_nodes(label=cat_label)
        if nodes:
            return nodes[0].id if hasattr(nodes[0], "id") else None
        return None

    def _query_nodes(self, label: str) -> List[Dict]:
        """Query nodes by label using AIQL (namespace-safe)."""
        result = self.conn.query(f"SELECT * FROM {label}")
        nodes = result.get("nodes", [])
        # Normalize to objects with .id, .label, .properties
        normalized = []
        for n in nodes:
            if isinstance(n, dict):
                # Properties may be nested in a "properties" key or flat on the dict
                if "properties" in n and isinstance(n["properties"], dict):
                    props = n["properties"]
                else:
                    props = {k: v for k, v in n.items()
                             if k not in ("id", "uuid", "label", "domain", "name")}
                normalized.append(type("N", (), {
                    "id": n.get("id", n.get("uuid", "")),
                    "label": n.get("label", label),
                    "properties": props,
                })())
            else:
                normalized.append(n)
        return normalized

    def _link_to_context(self, node_id: str, node_label: str):
        """Link a node to its category in the context graph (if category exists)."""
        cat_id = self._find_category_node(node_label)
        if cat_id:
            self.conn.add_edge(cat_id, node_id, "CONTAINS")

    # ==================================================================
    # Task Management
    # ==================================================================

    def add_task(
        self,
        title: str,
        description: str = "",
        priority: str = "medium",
        tags: Optional[List[str]] = None,
        depends_on: Optional[List[str]] = None,
        assigned_to: Optional[str] = None,
        parent_task: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> str:
        """Create a task (or subtask). Returns the task node UUID.

        Args:
            parent_task: If set, links this as a subtask of the parent via HAS_SUBTASK edge.
            created_by: Agent ID or name that created this task (for lineage).
        """
        tag_str = ",".join(tags) if tags else ""
        assign_str = f', assigned_to: "{_esc(assigned_to)}"' if assigned_to else ''
        parent_str = f', parent_task: "{_esc(parent_task)}"' if parent_task else ''
        creator_str = f', created_by: "{_esc(created_by)}"' if created_by else ''
        # If task depends on uncompleted tasks, start as blocked
        initial_status = "open"
        if depends_on:
            for dep_id in depends_on:
                dep_node = self._resolve_task_id(dep_id)
                if dep_node:
                    dp = dep_node.properties if hasattr(dep_node, "properties") else dep_node
                    if dp.get("status") != "completed":
                        initial_status = "blocked"
                        break
        result = self.conn.query(
            f'CREATE NODE Task {{'
            f'title: "{_esc(title)}", '
            f'description: "{_esc(description)}", '
            f'priority: "{priority}", '
            f'status: "{initial_status}"{assign_str}{parent_str}{creator_str}, '
            f'tags: "{tag_str}", '
            f'_version: 1, '
            f'created_at: "{_now()}"'
            f'}}'
        )
        task_id = result.get("data", {}).get("uuid", "")

        # Link to parent task
        if parent_task:
            parent_node = self._resolve_task_id(parent_task)
            if parent_node:
                parent_id = parent_node.id if hasattr(parent_node, "id") else parent_node.get("id", parent_task)
                self.conn.add_edge(parent_id, task_id, "HAS_SUBTASK")

        # Add dependency edges
        if depends_on:
            for dep_id in depends_on:
                self.conn.add_edge(task_id, dep_id, "DEPENDS_ON")

        # Link to context graph category (CodeBase --CONTAINS--> Task)
        self._link_to_context(task_id, "Task")

        # Publish to stream for push-based delivery
        if initial_status == "open":
            try:
                from .task_stream import get_task_publisher
                pub = get_task_publisher()
                pub.publish(
                    namespace=self.name,
                    task_id=task_id,
                    priority={"critical": 3, "high": 2, "medium": 1, "low": 0}.get(priority, 1),
                    metadata={"title": title, "assigned_to": assigned_to or "", "created_by": created_by or ""},
                )
            except Exception:
                pass  # stream publishing must never break task creation

        return task_id

    def check_file_conflicts(self, task_id: str, agent_id: str) -> List[Dict]:
        """Check if any files in this task are being worked on by another agent.

        Returns list of conflicts: [{files, other_agent, other_task}]
        """
        conflicts = []
        # Get files for this task
        task_node = self.conn.get_node(task_id) if hasattr(self.conn, 'get_node') else None
        if not task_node:
            return conflicts

        task_props = getattr(task_node, 'properties', {}) or {}
        try:
            task_files = json.loads(task_props.get('files', '[]'))
        except Exception:
            task_files = []

        if not task_files:
            return conflicts

        # Find all in-progress tasks by other agents
        all_tasks = self.conn.get_nodes(label="Task") if hasattr(self.conn, 'get_nodes') else []
        for other_task in all_tasks:
            other_props = getattr(other_task, 'properties', {}) or {}
            if other_props.get('status') != 'in_progress':
                continue
            if other_props.get('assigned_to') == agent_id:
                continue

            try:
                other_files = json.loads(other_props.get('files', '[]'))
            except Exception:
                other_files = []

            overlap = set(task_files) & set(other_files)
            if overlap:
                conflicts.append({
                    "files": list(overlap),
                    "other_agent": other_props.get('assigned_to', 'unknown'),
                    "other_task": other_props.get('title', other_task.id if hasattr(other_task, 'id') else 'unknown'),
                })

        return conflicts

    def claim_task(self, task_id: str, agent_id: str) -> str:
        """Agent claims an open task. Atomic via TaskLock."""
        node = self._resolve_task_id(task_id)
        if not node:
            return f"Error: Task {task_id} not found"
        task_id = node.id if hasattr(node, "id") else node.get("id", task_id)
        props = node.properties if hasattr(node, "properties") else node
        title = props.get("title", "?")

        if props.get("status") not in ("open", "blocked", "in_progress"):
            return f"Error: Task is {props.get('status')}, not claimable"

        # Atomic lock
        if not self._task_lock.acquire_claim(task_id, agent_id):
            holder = self._task_lock.get_holder(task_id) or "another agent"
            # Suggest available tasks
            all_open = self.get_open_tasks()
            available = [t for t in all_open if t.get("assigned_to") in ("", "unassigned", "queue", None)
                         and t.get("id") != task_id]
            hint = ""
            if available:
                hint = f". Available: {', '.join(t['title'][:30] for t in available[:3])}"
            return f"Error: Task '{title}' is claimed by {holder}{hint}"

        # If already mine and in_progress, just confirm
        current_assignee = props.get("assigned_to", "")
        agent_name_resolved = ""
        if self.registry:
            agent_obj = self.registry.get(agent_id)
            agent_name_resolved = agent_obj.name if agent_obj else ""
        is_mine = current_assignee in (agent_id, agent_name_resolved) if current_assignee else True
        if is_mine and props.get("status") == "in_progress":
            return f"Task already claimed: {title} — continue working on it"

        # Optimistic version check
        version = props.get("_version", 0)
        new_version = version + 1

        self.conn.query(
            f'UPDATE NODE Task SET {{'
            f'status: "in_progress", assigned_to: "{agent_id}", '
            f'claimed_at: "{_now()}", _version: {new_version}'
            f'}} WHERE uuid = "{task_id}"'
        )
        # Direct update via adapter (Redis/LMDB/CSR compatible)
        try:
            db = self.conn.db if hasattr(self.conn, "db") else None
            adapter = getattr(db, 'csr_adapter', None) if db else None
            if adapter and hasattr(adapter, 'update_node_properties'):
                adapter.update_node_properties(task_id, {
                    "status": "in_progress",
                    "assigned_to": agent_id,
                    "claimed_at": _now(),
                    "_version": new_version,
                })
        except Exception:
            pass

        # Create edge
        self.conn.add_edge(task_id, agent_id, "ASSIGNED_TO", {"claimed_at": _now()})

        # Emit event
        self._event_bus.emit("task_claimed", task_id, agent_id, task_title=title)

        # Invalidate agent's working memory — task context changed
        try:
            from ..context.working_memory import get_working_memory
            wm = get_working_memory()
            ns = self.name.replace("_rt", "")
            wm.invalidate(agent_id, ns)
        except Exception:
            pass

        # Check for file conflicts (warn but don't block)
        conflicts = self.check_file_conflicts(task_id, agent_id)
        conflict_warning = ""
        if conflicts:
            conflict_msg = "; ".join(f"{c['files']} (by {c['other_agent']} on '{c['other_task']}')" for c in conflicts)
            logger.warning("[CONFLICT] Task %s: overlapping files: %s", task_id[:12], conflict_msg)
            conflict_warning = f" [WARNING: file conflicts with {len(conflicts)} task(s)]"

        agent = self.registry.get(agent_id)
        agent_name = agent.name if agent else agent_id[:8]
        return f"{agent_name} claimed task: {title}{conflict_warning}"

    def complete_task(
        self,
        task_id: str,
        agent_id: str,
        summary: str = "",
        files_changed: Optional[List[str]] = None,
    ) -> str:
        """Mark a task as completed. Version-checked + event emission."""
        node = self._resolve_task_id(task_id)
        if not node:
            return f"Error: Task {task_id} not found"
        task_id = node.id if hasattr(node, "id") else node.get("id", task_id)
        props = node.properties if hasattr(node, "properties") else node
        title = props.get("title", "?")

        # Version check
        version = props.get("_version", 0)
        new_version = version + 1

        # Update status via AIQL
        self.conn.query(
            f'UPDATE NODE Task SET {{'
            f'status: "completed", completed_at: "{_now()}", '
            f'completed_by: "{agent_id}", _version: {new_version}'
            f'}} WHERE uuid = "{task_id}"'
        )
        # Direct update via adapter (Redis/LMDB/CSR compatible)
        try:
            db = self.conn.db if hasattr(self.conn, "db") else None
            adapter = getattr(db, 'csr_adapter', None) if db else None
            if adapter and hasattr(adapter, 'update_node_properties'):
                adapter.update_node_properties(task_id, {
                    "status": "completed",
                    "completed_at": _now(),
                    "completed_by": agent_id,
                    "_version": new_version,
                })
        except Exception:
            pass

        # Create CodeFile nodes for changed files
        if files_changed:
            for fp in files_changed:
                file_node = self.conn.add_node(label="CodeFile", properties={
                    "path": fp, "created_by": agent_id, "task_id": task_id, "created_at": _now(),
                })
                self.conn.add_edge(file_node.id, task_id, "IMPLEMENTS")
                self._link_to_context(file_node.id, "CodeFile")

        # Log action
        files_str = ",".join(files_changed) if files_changed else ""
        action_result = self.conn.query(
            f'CREATE NODE Action {{'
            f'type: "task_completed", '
            f'task_id: "{task_id}", '
            f'agent_id: "{agent_id}", '
            f'summary: "{_esc(summary)}", '
            f'files_changed: "{files_str}", '
            f'timestamp: "{_now()}"'
            f'}}'
        )
        action_id = action_result.get("data", {}).get("uuid", "")
        if action_id:
            self.conn.add_edge(action_id, task_id, "COMPLETES")
            self._link_to_context(action_id, "Action")

        # Auto-link agent artifacts
        for label in ("Knowledge", "Finding", "GitPush", "ArtifactUpload"):
            for n in self.conn.get_nodes(label=label):
                p = n.properties if hasattr(n, "properties") else {}
                if p.get("_agent_id") == agent_id and p.get("task_id") == task_id:
                    self.conn.add_edge(n.id, task_id, "PRODUCED_FOR")

        # Unblock dependents + emit events for each
        self._unblock_dependents(task_id)

        # Release the task lock
        self._task_lock.release_claim(task_id, agent_id)

        # Emit completion event
        self._event_bus.emit(
            "task_completed", task_id, agent_id,
            task_title=title, summary=summary,
            files_changed=files_changed or [],
        )

        # Invalidate agent's working memory — task context changed
        try:
            from ..context.working_memory import get_working_memory
            wm = get_working_memory()
            ns = self.name.replace("_rt", "")
            wm.invalidate(agent_id, ns)
        except Exception:
            pass

        agent = self.registry.get(agent_id)
        agent_name = agent.name if agent else agent_id[:8]

        # Promote agent findings from runtime → atomic (3-tier: auto/review/discard)
        # >= 0.3: auto-promote (most findings — novel content scores ~0.5)
        # 0.1-0.3: manual review queue
        # < 0.1: discard (empty/garbage)
        try:
            from ..context.promotion import PromotionScorer, PromotionQueue, promote_node, PROMOTION_DEFAULTS
            scorer = PromotionScorer(PROMOTION_DEFAULTS)
            rt_db = self.conn.db if hasattr(self.conn, "db") else None
            ns = self.name.replace("_rt", "")
            promoted = 0
            queued = 0
            if rt_db:
                for label in ("Finding", "Insight", "Observation", "Note"):
                    try:
                        for n in rt_db.get_all_nodes(label=label):
                            p = n.properties if hasattr(n, "properties") else {}
                            if p.get("_agent_id") == agent_id or p.get("created_by") == agent_name:
                                score = scorer.score(n, rt_db)
                                if score.score >= 0.3:
                                    # Auto-promote — good enough for atomic context
                                    try:
                                        from ..adapters._base import AIContextDBConnection
                                        at_conn = AIContextDBConnection(namespace=ns)
                                        promote_node(n.id, self.conn, at_conn, promoted_by=agent_name)
                                        promoted += 1
                                    except Exception:
                                        pass
                                elif score.score >= 0.1:
                                    # Manual review — low quality, needs human check
                                    queue = PromotionQueue(ns)
                                    queue.submit(n.id, score.score, score.reason,
                                                 submitted_by=agent_name)
                                    queued += 1
                                # < 0.1: discard (empty/garbage content)
                    except Exception:
                        pass
            if promoted or queued:
                logger.info("[PROMOTE] Task %s: %d findings promoted, %d queued for review", task_id[:12], promoted, queued)
        except Exception:
            pass  # Promotion must never block task completion

        return f"{agent_name} completed: {title}"

    def handoff_task(
        self,
        task_id: str,
        from_agent: str,
        to_agent: str,
        notes: str = "",
    ) -> str:
        """Hand off a task from one agent to another. Lock transferred atomically."""
        node = self._resolve_task_id(task_id)
        if not node:
            return f"Error: Task {task_id} not found"
        task_id = node.id if hasattr(node, "id") else node.get("id", task_id)
        props = node.properties if hasattr(node, "properties") else node
        title = props.get("title", "?")

        # Version check
        version = props.get("_version", 0)
        new_version = version + 1

        # Transfer lock: release from old, acquire for new
        self._task_lock.release_claim(task_id, from_agent)
        if not self._task_lock.acquire_claim(task_id, to_agent):
            holder = self._task_lock.get_holder(task_id) or "unknown"
            return f"Error: Handoff failed — task grabbed by {holder} during transfer"

        self.conn.query(
            f'UPDATE NODE Task SET {{'
            f'assigned_to: "{to_agent}", status: "in_progress", _version: {new_version}'
            f'}} WHERE uuid = "{task_id}"'
        )
        # Direct fallback
        try:
            db = self.conn.db if hasattr(self.conn, "db") else None
            if db and hasattr(db, "get_node"):
                live = db.get_node(task_id)
                if live and hasattr(live, "properties"):
                    live.properties["assigned_to"] = to_agent
                    live.properties["status"] = "in_progress"
                    live.properties["_version"] = new_version
                    if hasattr(db, "add_node"):
                        db.add_node(live, write_through=True)
        except Exception:
            pass

        self.conn.add_edge(task_id, to_agent, "HANDED_TO", {
            "from_agent": from_agent,
            "notes": notes,
            "timestamp": _now(),
        })

        # Emit event
        self._event_bus.emit(
            "task_handed_off", task_id, from_agent,
            to_agent=to_agent, task_title=title, notes=notes,
        )

        from_name = (self.registry.get(from_agent) or type("", (), {"name": from_agent[:8]})).name
        to_name = (self.registry.get(to_agent) or type("", (), {"name": to_agent[:8]})).name
        return f"Handoff: {title} ({from_name} -> {to_name}). Notes: {notes}"

    def get_open_tasks(self, agent_id: Optional[str] = None, agent_name: Optional[str] = None,
                       max_age_hours: int = 2) -> List[Dict]:
        """Get open/in-progress tasks, optionally filtered by agent id or name.

        Args:
            max_age_hours: Only return tasks created within this many hours (0=no limit).
                Prevents stale tasks from prior experiment runs from confusing agents.
        """
        from datetime import datetime, timezone, timedelta
        tasks = self._query_nodes("Task")
        cutoff = None
        if max_age_hours > 0:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()

        results = []
        for t in tasks:
            p = t.properties if hasattr(t, "properties") else t
            if p.get("status") in ("open", "in_progress", "blocked"):
                # Skip stale tasks from prior runs
                if cutoff:
                    created = p.get("created_at", "")
                    if created and created < cutoff:
                        continue

                assigned = p.get("assigned_to", "")
                if agent_id and assigned != agent_id:
                    if not agent_name or assigned != agent_name:
                        continue
                results.append({
                    "id": t.id if hasattr(t, "id") else t.get("id", "?"),
                    "title": p.get("title", "?"),
                    "status": p.get("status", "?"),
                    "priority": p.get("priority", "medium"),
                    "assigned_to": p.get("assigned_to", "unassigned"),
                    "tags": p.get("tags", ""),
                    "created_at": p.get("created_at", ""),
                })
        # Deduplicate by title — keep only the most recent task per title
        seen_titles = {}
        deduped = []
        for t in results:
            title = t["title"]
            if title in seen_titles:
                # Keep the one with the newer created_at
                existing = seen_titles[title]
                if t.get("created_at", "") > existing.get("created_at", ""):
                    deduped = [x for x in deduped if x["title"] != title]
                    deduped.append(t)
                    seen_titles[title] = t
            else:
                seen_titles[title] = t
                deduped.append(t)

        # Sort: high > medium > low, then by status
        prio_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        deduped.sort(key=lambda x: (prio_order.get(x["priority"], 9), x["status"]))
        return deduped

    def get_project_status(self) -> Dict[str, Any]:
        """Get a summary of project state."""
        tasks = self._query_nodes("Task")
        decisions = self._query_nodes("Decision")
        docs = self._query_nodes("Document")
        code_files = self._query_nodes("CodeFile")
        actions = self._query_nodes("Action")

        status_counts = {}
        for t in tasks:
            p = t.properties if hasattr(t, "properties") else t
            s = p.get("status", "unknown")
            status_counts[s] = status_counts.get(s, 0) + 1

        agent_tasks = {}
        for t in tasks:
            p = t.properties if hasattr(t, "properties") else t
            agent = p.get("assigned_to")
            if agent and p.get("status") == "in_progress":
                if agent not in agent_tasks:
                    agent_tasks[agent] = []
                agent_tasks[agent].append(p.get("title", "?"))

        return {
            "project": self.name,
            "tasks": {
                "total": len(tasks),
                "by_status": status_counts,
            },
            "decisions": len(decisions),
            "documents": len(docs),
            "code_files": len(code_files),
            "actions": len(actions),
            "agents_working": agent_tasks,
        }

    # ==================================================================
    # Document Management
    # ==================================================================

    def add_document(
        self,
        title: str,
        content: str,
        doc_type: str = "spec",
        author: str = "",
    ) -> str:
        """Add a project document (spec, design doc, README, etc.)."""
        result = self.conn.query(
            f'CREATE NODE Document {{'
            f'title: "{_esc(title)}", '
            f'content: "{_esc(content)}", '
            f'type: "{doc_type}", '
            f'author: "{author}", '
            f'created_at: "{_now()}"'
            f'}}'
        )
        doc_id = result.get("data", {}).get("uuid", "")
        if doc_id:
            self._link_to_context(doc_id, "Document")
        return doc_id

    # ==================================================================
    # Decision Tracking
    # ==================================================================

    def add_decision(
        self,
        title: str,
        rationale: str,
        agent_id: str,
        impacts: Optional[List[str]] = None,
    ) -> str:
        """Record an architectural/design decision."""
        result = self.conn.query(
            f'CREATE NODE Decision {{'
            f'title: "{_esc(title)}", '
            f'rationale: "{_esc(rationale)}", '
            f'decided_by: "{agent_id}", '
            f'status: "approved", '
            f'decided_at: "{_now()}"'
            f'}}'
        )
        decision_id = result.get("data", {}).get("uuid", "")

        if impacts and decision_id:
            for target_id in impacts:
                self.conn.add_edge(decision_id, target_id, "IMPACTS")

        if decision_id:
            self._link_to_context(decision_id, "Decision")

        return decision_id

    # ==================================================================
    # Code File Tracking
    # ==================================================================

    def add_code_file(self, file_path: str, module: str = "", description: str = "") -> str:
        """Track a code file in the project graph."""
        result = self.conn.query(
            f'CREATE NODE CodeFile {{'
            f'path: "{_esc(file_path)}", '
            f'module: "{_esc(module)}", '
            f'description: "{_esc(description)}", '
            f'tracked_at: "{_now()}"'
            f'}}'
        )
        return result.get("data", {}).get("uuid", "")

    def seed_from_directory(
        self,
        root: str = ".",
        extensions: Optional[List[str]] = None,
        ignore_dirs: Optional[List[str]] = None,
    ) -> int:
        """Scan a directory and create CodeFile nodes for source files.

        Returns the number of files added.
        """
        exts = extensions or [".py", ".ts", ".js", ".tsx", ".jsx", ".go", ".rs", ".java"]
        skip = set(ignore_dirs or [
            "__pycache__", "node_modules", ".git", ".venv", "venv",
            "dist", "build", ".next", ".mypy_cache", "egg-info",
        ])
        root_path = Path(root).resolve()
        count = 0
        for path in root_path.rglob("*"):
            if path.is_file() and path.suffix in exts:
                # Skip ignored directories
                if any(part in skip for part in path.parts):
                    continue
                rel = str(path.relative_to(root_path)).replace("\\", "/")
                module = str(path.parent.relative_to(root_path)).replace("\\", "/").replace("/", ".")
                self.add_code_file(file_path=rel, module=module)
                count += 1
                if count >= 100:  # safety limit
                    break
        return count

    # ==================================================================
    # Context Building
    # ==================================================================

    def build_agent_context(
        self,
        agent_id: str,
        agent_name: str = "",
        system_prompt: str = "",
        max_tokens: int = 6000,
    ) -> str:
        """Build LLM-ready context for a specific agent.

        Includes: project status, agent's tasks, recent decisions,
        recent actions by other agents (what happened while you were away).
        """
        from ..context.hub import ContextHub, ContextRole

        status = self.get_project_status()
        my_tasks = self.get_open_tasks(agent_id=agent_id, agent_name=agent_name)
        all_open = self.get_open_tasks()
        decisions = self._query_nodes("Decision")
        actions = self._query_nodes("Action")

        if not agent_name:
            agent_obj = self.registry.get(agent_id)
            agent_name = agent_obj.name if agent_obj else agent_id[:8]

        default_prompt = (
            f"You are {agent_name}, an AI agent working on project '{self.name}'. "
            f"You collaborate with other agents through a shared knowledge graph. "
            f"Check your assigned tasks, complete them, and hand off work when needed. "
            f"Log decisions and actions so other agents know what happened."
        )

        hub = ContextHub(system_prompt=system_prompt or default_prompt, max_tokens=max_tokens)

        # Enable scoping — ranks items by role importance + recency
        try:
            from ..context.scoping import ScopingConfig
            hub.set_scoping(ScopingConfig(strategy="combined"))
        except Exception:
            pass

        # Project spec & requirements (high priority — INSTRUCTION role)
        specs = self._query_nodes("ProjectSpec")
        if specs:
            spec_props = specs[0].properties if hasattr(specs[0], "properties") else {}
            spec_text = f"Project: {spec_props.get('name', self.name)}\n"
            if spec_props.get("spec"):
                spec_text += f"Spec: {spec_props['spec']}\n"
            if spec_props.get("stack"):
                spec_text += f"Stack: {spec_props['stack']}\n"
            if spec_props.get("rules"):
                spec_text += f"Rules: {spec_props['rules']}\n"
            if spec_props.get("repo"):
                spec_text += f"Repo: {spec_props['repo']}\n"
            hub.add_text(spec_text, role=ContextRole.INSTRUCTION, label="Project Spec")

        # Requirements (DOCUMENT role — kept if budget allows)
        requirements = self._query_nodes("Requirement")
        if requirements:
            req_text = "\n".join(
                f"- {_prop(r, 'title')}: {_prop(r, 'description') or _prop(r, 'content')}"
                for r in requirements[:20]
            )
            hub.add_text(req_text, role=ContextRole.DOCUMENT, label="Requirements")

        # Key documents
        docs = self._query_nodes("Document")
        if docs:
            doc_text = "\n".join(
                f"- [{_prop(d, 'type', 'doc')}] {_prop(d, 'title')}"
                for d in docs[:10]
            )
            hub.add_text(doc_text, role=ContextRole.BACKGROUND, label="Project Documents")

        # Project status
        status_text = (
            f"Project: {self.name}\n"
            f"Tasks: {status['tasks']['total']} total "
            f"({json.dumps(status['tasks']['by_status'])})\n"
            f"Decisions: {status['decisions']}, Documents: {len(docs)}\n"
        )
        if status["agents_working"]:
            for aid, titles in status["agents_working"].items():
                a = self.registry.get(aid)
                aname = a.name if a else aid[:8]
                status_text += f"  {aname} working on: {', '.join(titles)}\n"
        hub.add_text(status_text, role=ContextRole.BACKGROUND, label="Project Status")

        # My tasks
        if my_tasks:
            my_text = "\n".join(
                f"- [{t['priority'].upper()}] {t['title']} (status: {t['status']}, id: {t['id']})"
                for t in my_tasks
            )
            hub.add_text(my_text, role=ContextRole.INSTRUCTION, label=f"Your Tasks ({agent_name})")

        # Unassigned tasks (available to claim)
        unassigned = [t for t in all_open if t["assigned_to"] == "unassigned"]
        if unassigned:
            ua_text = "\n".join(
                f"- [{t['priority'].upper()}] {t['title']} (id: {t['id']})"
                for t in unassigned[:10]
            )
            hub.add_text(ua_text, role=ContextRole.BACKGROUND, label="Unassigned Tasks (available to claim)")

        # Recent decisions
        if decisions:
            dec_text = "\n".join(
                f"- {_prop(d, 'title')}: {_prop(d, 'rationale')} (by {_prop(d, 'decided_by')[:8]})"
                for d in decisions[-5:]
            )
            hub.add_text(dec_text, role=ContextRole.DECISION, label="Recent Decisions")

        # Recent actions by OTHER agents
        other_actions = [
            a for a in actions
            if _prop(a, "agent_id") != agent_id
        ]
        if other_actions:
            act_text = "\n".join(
                f"- [{_prop(a, 'type')}] {_prop(a, 'summary')} (by {_prop(a, 'agent_id')[:8]})"
                for a in other_actions[-5:]
            )
            hub.add_text(act_text, role=ContextRole.BACKGROUND, label="Recent Actions by Other Agents")

        # Code files produced so far
        code_files = self._query_nodes("CodeFile")
        if code_files:
            cf_text = "\n".join(
                f"- {_prop(f, 'path')} (by {_prop(f, 'created_by', '?')[:8]})"
                for f in code_files[:20]
            )
            hub.add_text(cf_text, role=ContextRole.BACKGROUND, label="Code Files Produced")

        # Knowledge nodes — includes all knowledge-like types from the graph
        knowledge = self._query_nodes("Knowledge")
        insights = self._query_nodes("Insight")
        findings = self._query_nodes("Finding")
        facts = self._query_nodes("Fact")
        features = self._query_nodes("Feature")
        assumptions = self._query_nodes("Assumption")
        all_knowledge = knowledge + insights + findings + facts + features + assumptions
        if all_knowledge:
            kn_text = "\n".join(
                f"- [{getattr(k, 'label', 'Knowledge')}] {_prop(k, 'name', _prop(k, 'title', ''))}: {_prop(k, 'content', _prop(k, 'description', ''))[:150]}"
                for k in all_knowledge[:15]
            )
            hub.add_text(kn_text, role=ContextRole.BACKGROUND, label="Shared Knowledge (from agents)")

        # Workspace files (from filesystem, if available)
        try:
            ws_nodes = self._query_nodes("WorkspaceFile")
            if not ws_nodes:
                # Try to get from workspace directly
                from ..workspace.base import Workspace
                session_config = {}
                try:
                    # Discover workspace path from session
                    import glob as _glob
                    ws_dirs = _glob.glob(f"generated/*{self.name[:12].lower().replace(' ', '-')}*")
                    if ws_dirs:
                        ws = Workspace.from_config({"type": "local", "path": ws_dirs[0]})
                        files = ws.list_files()
                        if files:
                            ws_text = "\n".join(f"- {f}" for f in files[:30])
                            hub.add_text(ws_text, role=ContextRole.BACKGROUND, label="Workspace Files")
                except Exception:
                    pass
        except Exception:
            pass

        # Entities extracted from documents
        entities = self._query_nodes("Entity")
        if entities:
            ent_text = "\n".join(
                f"- {_prop(e, 'type', 'Entity')}: {_prop(e, 'name')} ({_prop(e, 'description', '')[:80]})"
                for e in entities[:20]
            )
            hub.add_text(ent_text, role=ContextRole.BACKGROUND, label="Extracted Entities")

        return hub.to_prompt(budget=max_tokens)

    # ==================================================================
    # Internals
    # ==================================================================

    def _unblock_dependents(self, completed_task_id: str):
        """Check if completing this task unblocks others. Emits task_unblocked events."""
        edges = self.conn.get_edges(label="DEPENDS_ON")
        for e in edges:
            src = e.source if hasattr(e, "source") else e.get("source", "")
            tgt = e.target if hasattr(e, "target") else e.get("target", "")
            if tgt == completed_task_id:
                dep_node = self.conn.get_node(src)
                if dep_node:
                    props = dep_node.properties if hasattr(dep_node, "properties") else dep_node
                    if props.get("status") == "blocked":
                        self.conn.query(
                            f'UPDATE NODE Task SET {{status: "open"}} WHERE uuid = "{src}"'
                        )
                        self._event_bus.emit(
                            "task_unblocked", src, "",
                            unblocked_by=completed_task_id,
                        )
                        # Publish unblocked task to stream
                        try:
                            from .task_stream import get_task_publisher
                            pub = get_task_publisher()
                            pub.publish(
                                namespace=self.name,
                                task_id=src,
                                metadata={"title": props.get("title", ""), "unblocked_by": completed_task_id},
                            )
                        except Exception:
                            pass

    def save(self) -> bool:
        """Persist the project graph to disk."""
        return self.conn.save()


def _esc(s: str) -> str:
    """Escape a string for AIQL."""
    return s.replace('"', '\\"').replace("\n", " ")


def _prop(node, key: str, default: str = "") -> str:
    """Get a property from a node (handles both objects and dicts)."""
    if hasattr(node, "properties"):
        return str(node.properties.get(key, default))
    if isinstance(node, dict):
        return str(node.get(key, node.get("properties", {}).get(key, default)))
    return default
