"""
Code Context
==============
A spec-driven code context that agents use to build applications.

Give it a spec (stack, requirements, rules) and connected agents
(Claude, Codex) collaboratively design, implement, and test the app.

Usage:
    from contextsynapse.project.code_context import CodeContext

    cc = CodeContext.create(
        name="my-saas-app",
        stack={"backend": "Python/FastAPI", "frontend": "React", "database": "PostgreSQL"},
        spec="Build a task management app with auth, CRUD tasks, team sharing",
        rules=["TypeScript strict", "pytest for tests", "no ORM"],
    )

    # Auto-generate tasks from spec
    cc.generate_tasks()

    # Agents work on tasks
    cc.claim_task(task_id, agent_id)
    cc.complete_task(task_id, agent_id, summary="Implemented auth", files=["auth.py"])

    # Check status
    cc.status()
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..adapters._base import AIContextDBConnection
from ..core.graph_structures import GraphNode, GraphEdge
from ..storage.namespace_store import NamespaceStore

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _esc(s: str) -> str:
    return s.replace('"', '\\"').replace("\n", " ")


class CodeContext:
    """A spec-driven code context for agentic application building.

    The context IS the graph. The graph namespace = context name.
    Everything lives in the graph: spec, architecture, tasks, code, tests.
    """

    def __init__(
        self,
        name: str,
        conn: Optional[AIContextDBConnection] = None,
        namespace_store: Optional[NamespaceStore] = None,
        thread_id: Optional[str] = None,
    ):
        self.name = name
        self.conn = conn or AIContextDBConnection(namespace=name)
        self.ns = namespace_store or NamespaceStore(name)
        self._spec_node_id: Optional[str] = None
        self._thread_id = thread_id

    @classmethod
    def create(
        cls,
        name: str,
        stack: Dict[str, str],
        spec: str,
        rules: Optional[List[str]] = None,
        repo: Optional[str] = None,
    ) -> "CodeContext":
        """Create a new code context with spec.

        Args:
            name: Project name (becomes the graph namespace).
            stack: Tech stack dict (backend, frontend, database, testing).
            spec: Natural language specification of what to build.
            rules: Coding rules and constraints.
            repo: Optional GitHub repo URL.
        """
        cc = cls(name)

        # Create ProjectSpec root node
        spec_id = str(uuid.uuid4())
        spec_node = GraphNode(
            id=spec_id,
            label="ProjectSpec",
            properties={
                "name": name,
                "spec": spec,
                "stack": json.dumps(stack),
                "rules": json.dumps(rules or []),
                "repo": repo or "",
                "status": "active",
                "created_at": _now(),
            },
        )
        cc.conn.contextsynapse.add_node(spec_node, write_through=True)
        cc._spec_node_id = spec_id

        # Create Technology nodes from stack
        for role, tech in stack.items():
            tech_id = str(uuid.uuid4())
            tech_node = GraphNode(
                id=tech_id,
                label="Technology",
                properties={"name": tech, "role": role},
            )
            cc.conn.contextsynapse.add_node(tech_node, write_through=True)
            cc.conn.contextsynapse.add_edge(GraphEdge(
                id=str(uuid.uuid4()), source=spec_id, target=tech_id,
                label="USES", properties={"role": role},
            ))

        # Store config in namespace SQL
        cc.ns.set_metadata("stack", stack)
        cc.ns.set_metadata("spec", spec)
        cc.ns.set_metadata("rules", rules or [])
        if repo:
            cc.ns.set_metadata("repo", repo)

        logger.info("Created code context: %s", name)
        return cc

    @classmethod
    def load(cls, name: str) -> "CodeContext":
        """Load an existing code context."""
        cc = cls(name)
        # Find the ProjectSpec node
        nodes = cc.conn.get_nodes(label="ProjectSpec")
        if nodes:
            cc._spec_node_id = nodes[0].id if hasattr(nodes[0], 'id') else None
        return cc

    # ==================================================================
    # Task Generation (LLM-powered)
    # ==================================================================

    def generate_tasks(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> List[str]:
        """Use LLM to generate tasks from the project spec.

        Returns list of created task node IDs.
        """
        from ..llm import get_llm_client

        spec = self.ns.get_metadata("spec") or ""
        stack = self.ns.get_metadata("stack") or {}
        rules = self.ns.get_metadata("rules") or []

        # If spec is thin, pull in requirement docs from the graph
        requirements_text = self._gather_requirements_text()
        if requirements_text:
            spec = f"{spec}\n\n--- Requirements Document ---\n{requirements_text}"

        prompt = f"""Given this project specification, generate a list of implementation tasks.

Project: {self.name}
Stack: {json.dumps(stack)}
Spec: {spec}
Rules: {json.dumps(rules)}

Return JSON with this structure:
{{
  "tasks": [
    {{
      "title": "Task title",
      "description": "Detailed description",
      "priority": "high|medium|low",
      "tags": ["auth", "backend"],
      "depends_on": []
    }}
  ]
}}

Generate 5-15 concrete, actionable tasks ordered by dependency (foundational first).
"""
        llm = get_llm_client(provider=provider, model=model)
        result = llm.generate_json(
            prompt=prompt,
            system="You are a senior software architect. Generate precise, actionable development tasks.",
        )

        task_ids = []
        title_to_id: Dict[str, str] = {}

        for task in result.get("tasks", []):
            task_id = str(uuid.uuid4())
            title = task.get("title", "")
            tags = task.get("tags", [])

            node = GraphNode(
                id=task_id,
                label="Task",
                properties={
                    "title": title,
                    "description": task.get("description", ""),
                    "priority": task.get("priority", "medium"),
                    "status": "open",
                    "tags": ",".join(tags) if tags else "",
                    "created_at": _now(),
                },
            )
            self.conn.contextsynapse.add_node(node, write_through=True)

            # Link to spec
            if self._spec_node_id:
                self.conn.contextsynapse.add_edge(GraphEdge(
                    id=str(uuid.uuid4()), source=self._spec_node_id,
                    target=task_id, label="HAS_TASK", properties={},
                ))

            title_to_id[title] = task_id
            task_ids.append(task_id)

        # Create dependency edges
        for task in result.get("tasks", []):
            title = task.get("title", "")
            task_id = title_to_id.get(title)
            if not task_id:
                continue
            for dep_title in task.get("depends_on", []):
                dep_id = title_to_id.get(dep_title)
                if dep_id:
                    self.conn.contextsynapse.add_edge(GraphEdge(
                        id=str(uuid.uuid4()), source=task_id,
                        target=dep_id, label="DEPENDS_ON", properties={},
                    ))

        logger.info("Generated %d tasks for %s", len(task_ids), self.name)
        return task_ids

    # ==================================================================
    # Thread logging
    # ==================================================================

    def _thread_log(self, content: str, agent_id: str = "", action: str = ""):
        """Log an agent action to the session thread (if thread_id is set)."""
        if not self._thread_id:
            return
        try:
            from ..context.conversation import ConversationStore
            store = ConversationStore()
            store.append_message(
                conversation_id=self._thread_id,
                role="assistant",
                content=content,
                agent_id=agent_id,
                metadata={"type": "agent_action", "action": action},
            )
        except Exception as e:
            logger.debug("Thread log failed (non-critical): %s", e)

    def get_thread_messages(self, limit: int = 50) -> List[Dict]:
        """Get recent thread messages for context building."""
        if not self._thread_id:
            return []
        try:
            from ..context.conversation import ConversationStore
            store = ConversationStore()
            conv = store.get(self._thread_id, include_messages=True)
            if not conv or not conv.messages:
                return []
            return [m.to_dict() for m in conv.messages[-limit:]]
        except Exception:
            return []

    # ==================================================================
    # Requirements → Tasks
    # ==================================================================

    def _gather_requirements_text(self, max_chars: int = 15000) -> str:
        """Collect text from Document and TextChunk nodes in the graph."""
        text_parts = []
        total = 0

        # Collect from Document nodes
        for node in self.conn.get_nodes(label="Document"):
            p = node.properties if hasattr(node, "properties") else {}
            content = p.get("content") or p.get("text") or p.get("title") or ""
            if content and total + len(content) < max_chars:
                text_parts.append(content)
                total += len(content)

        # Collect from TextChunk / Chunk nodes
        for label in ("TextChunk", "Chunk", "Passage"):
            for node in self.conn.get_nodes(label=label):
                p = node.properties if hasattr(node, "properties") else {}
                content = p.get("text") or p.get("content") or ""
                if content and total + len(content) < max_chars:
                    text_parts.append(content)
                    total += len(content)
                if total >= max_chars:
                    break

        # Collect from Requirement nodes (manually added)
        for node in self.conn.get_nodes(label="Requirement"):
            p = node.properties if hasattr(node, "properties") else {}
            title = p.get("title", "")
            if title:
                text_parts.append(f"Requirement: {title}")

        return "\n\n".join(text_parts)

    def generate_tasks_from_documents(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> List[str]:
        """Generate tasks specifically from uploaded requirement documents."""
        requirements_text = self._gather_requirements_text()
        if not requirements_text:
            logger.warning("No document content found in graph for %s", self.name)
            return []

        original_spec = self.ns.get_metadata("spec") or ""
        combined_spec = f"{original_spec}\n\n--- Uploaded Requirements ---\n{requirements_text}"
        self.ns.set_metadata("spec", combined_spec)

        try:
            task_ids = self.generate_tasks(provider=provider, model=model)
        finally:
            self.ns.set_metadata("spec", original_spec)

        return task_ids

    # ==================================================================
    # Task Management
    # ==================================================================

    def claim_task(self, task_id: str, agent_id: str) -> str:
        node = self.conn.get_node(task_id)
        if not node:
            return f"Task {task_id[:8]} not found"
        props = node.properties if hasattr(node, 'properties') else {}
        title = props.get("title", "?")

        self.conn.query(
            f'UPDATE NODE Task SET {{status: "in_progress", assigned_to: "{agent_id}", claimed_at: "{_now()}"}} '
            f'WHERE uuid = "{task_id}"'
        )
        self._thread_log(f"Claimed task: {title}", agent_id=agent_id, action="claimed_task")
        return f"Claimed: {title}"

    def complete_task(
        self, task_id: str, agent_id: str,
        summary: str = "", files: Optional[List[str]] = None,
    ) -> str:
        node = self.conn.get_node(task_id)
        if not node:
            return f"Task {task_id[:8]} not found"
        props = node.properties if hasattr(node, 'properties') else {}
        title = props.get("title", "?")

        self.conn.query(
            f'UPDATE NODE Task SET {{status: "completed", completed_by: "{agent_id}", '
            f'completed_at: "{_now()}", summary: "{_esc(summary)}"}} WHERE uuid = "{task_id}"'
        )

        if files:
            for fp in files:
                file_id = str(uuid.uuid4())
                file_node = GraphNode(
                    id=file_id, label="CodeFile",
                    properties={"path": fp, "created_by": agent_id, "created_at": _now()},
                )
                self.conn.contextsynapse.add_node(file_node, write_through=True)
                self.conn.contextsynapse.add_edge(GraphEdge(
                    id=str(uuid.uuid4()), source=file_id, target=task_id,
                    label="IMPLEMENTS", properties={},
                ))

        files_str = ", ".join(files) if files else ""
        log_msg = f"Completed task: {title}"
        if files_str:
            log_msg += f" (files: {files_str})"
        if summary:
            log_msg += f" — {summary[:100]}"
        self._thread_log(log_msg, agent_id=agent_id, action="completed_task")
        return f"Completed: {title}"

    def add_decision(self, title: str, rationale: str, agent_id: str) -> str:
        dec_id = str(uuid.uuid4())
        node = GraphNode(
            id=dec_id, label="Decision",
            properties={
                "title": title, "rationale": rationale,
                "decided_by": agent_id, "decided_at": _now(),
            },
        )
        self.conn.contextsynapse.add_node(node, write_through=True)
        if self._spec_node_id:
            self.conn.contextsynapse.add_edge(GraphEdge(
                id=str(uuid.uuid4()), source=self._spec_node_id,
                target=dec_id, label="HAS_DECISION", properties={},
            ))
        self._thread_log(f"Decision: {title} — {rationale[:100]}", agent_id=agent_id, action="decision")
        return dec_id

    def add_requirement(self, title: str, priority: str = "medium") -> str:
        req_id = str(uuid.uuid4())
        node = GraphNode(
            id=req_id, label="Requirement",
            properties={"title": title, "priority": priority, "status": "open"},
        )
        self.conn.contextsynapse.add_node(node, write_through=True)
        if self._spec_node_id:
            self.conn.contextsynapse.add_edge(GraphEdge(
                id=str(uuid.uuid4()), source=self._spec_node_id,
                target=req_id, label="REQUIRES", properties={},
            ))
        return req_id

    # ==================================================================
    # Status & Context
    # ==================================================================

    def status(self) -> Dict[str, Any]:
        """Get project status."""
        tasks = self.conn.get_nodes(label="Task")
        decisions = self.conn.get_nodes(label="Decision")
        code_files = self.conn.get_nodes(label="CodeFile")
        requirements = self.conn.get_nodes(label="Requirement")

        status_counts: Dict[str, int] = {}
        for t in tasks:
            p = t.properties if hasattr(t, 'properties') else {}
            s = p.get("status", "unknown")
            status_counts[s] = status_counts.get(s, 0) + 1

        return {
            "project": self.name,
            "spec": self.ns.get_metadata("spec") or "",
            "stack": self.ns.get_metadata("stack") or {},
            "tasks": {"total": len(tasks), "by_status": status_counts},
            "decisions": len(decisions),
            "code_files": len(code_files),
            "requirements": len(requirements),
        }

    def get_open_tasks(self, unblocked_only: bool = False) -> List[Dict]:
        """Get open tasks. If unblocked_only, only return tasks whose dependencies are all completed."""
        tasks = self.conn.get_nodes(label="Task")
        edges = self.conn.get_edges(label="DEPENDS_ON")

        deps: Dict[str, List[str]] = {}
        for e in edges:
            src = e.source if hasattr(e, 'source') else e.get('source', '')
            tgt = e.target if hasattr(e, 'target') else e.get('target', '')
            if src not in deps:
                deps[src] = []
            deps[src].append(tgt)

        task_status: Dict[str, str] = {}
        for t in tasks:
            p = t.properties if hasattr(t, 'properties') else {}
            tid = t.id if hasattr(t, 'id') else '?'
            task_status[tid] = p.get("status", "unknown")

        results = []
        for t in tasks:
            p = t.properties if hasattr(t, 'properties') else {}
            tid = t.id if hasattr(t, 'id') else '?'
            if p.get("status") not in ("open", "in_progress"):
                continue

            task_deps = deps.get(tid, [])
            blocked = any(task_status.get(d) != "completed" for d in task_deps)

            if unblocked_only and blocked:
                continue

            results.append({
                "id": tid,
                "title": p.get("title", "?"),
                "status": p.get("status", "?"),
                "priority": p.get("priority", "medium"),
                "assigned_to": p.get("assigned_to", "unassigned"),
                "blocked": blocked,
                "depends_on": [d[:8] for d in task_deps],
            })
        prio = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        results.sort(key=lambda x: (x["blocked"], prio.get(x["priority"], 9)))
        return results

    # ==================================================================
    # Reviews (Human in the Loop)
    # ==================================================================

    def request_review(self, task_id: str, notes: str = "", reviewer: str = "human") -> str:
        """Request human review for a task or decision."""
        review_id = str(uuid.uuid4())
        node = GraphNode(
            id=review_id, label="Review",
            properties={
                "status": "pending",
                "reviewer": reviewer,
                "notes": notes,
                "requested_at": _now(),
            },
        )
        self.conn.contextsynapse.add_node(node, write_through=True)
        self.conn.contextsynapse.add_edge(GraphEdge(
            id=str(uuid.uuid4()), source=task_id, target=review_id,
            label="NEEDS_REVIEW", properties={},
        ))
        return review_id

    def approve_review(self, review_id: str, approved_by: str = "human", feedback: str = "") -> str:
        """Approve a pending review."""
        self.conn.query(
            f'UPDATE NODE Review SET {{status: "approved", approved_by: "{approved_by}", '
            f'feedback: "{_esc(feedback)}", approved_at: "{_now()}"}} WHERE uuid = "{review_id}"'
        )
        return f"Review {review_id[:8]} approved"

    def reject_review(self, review_id: str, rejected_by: str = "human", feedback: str = "") -> str:
        """Reject a review with feedback."""
        self.conn.query(
            f'UPDATE NODE Review SET {{status: "rejected", rejected_by: "{rejected_by}", '
            f'feedback: "{_esc(feedback)}", rejected_at: "{_now()}"}} WHERE uuid = "{review_id}"'
        )
        return f"Review {review_id[:8]} rejected: {feedback}"

    def get_pending_reviews(self) -> List[Dict]:
        """Get all pending reviews."""
        reviews = self.conn.get_nodes(label="Review")
        return [
            {
                "id": r.id if hasattr(r, 'id') else '?',
                "status": (r.properties if hasattr(r, 'properties') else {}).get("status", "?"),
                "notes": (r.properties if hasattr(r, 'properties') else {}).get("notes", ""),
                "reviewer": (r.properties if hasattr(r, 'properties') else {}).get("reviewer", "?"),
            }
            for r in reviews
            if (r.properties if hasattr(r, 'properties') else {}).get("status") == "pending"
        ]

    # ==================================================================
    # Connectors
    # ==================================================================

    def add_connector(self, connector_type: str, config: Dict[str, Any]) -> str:
        """Attach a connector (GitHub, Jira, etc.) to this project."""
        conn_id = str(uuid.uuid4())
        node = GraphNode(
            id=conn_id, label="Connector",
            properties={
                "type": connector_type,
                "config": json.dumps(config),
                "status": "active",
                "created_at": _now(),
            },
        )
        self.conn.contextsynapse.add_node(node, write_through=True)
        if self._spec_node_id:
            self.conn.contextsynapse.add_edge(GraphEdge(
                id=str(uuid.uuid4()), source=self._spec_node_id,
                target=conn_id, label="HAS_CONNECTOR", properties={},
            ))
        self.ns.set_metadata(f"connector_{connector_type}", config)
        return conn_id

    def get_connectors(self) -> List[Dict]:
        """List attached connectors."""
        connectors = self.conn.get_nodes(label="Connector")
        return [
            {
                "id": c.id if hasattr(c, 'id') else '?',
                "type": (c.properties if hasattr(c, 'properties') else {}).get("type", "?"),
                "config": json.loads((c.properties if hasattr(c, 'properties') else {}).get("config", "{}")),
                "status": (c.properties if hasattr(c, 'properties') else {}).get("status", "?"),
            }
            for c in connectors
        ]

    def log_connector_action(self, connector_id: str, action_type: str, result: str) -> str:
        """Log a connector action (push, create_ticket, etc.)."""
        action_id = str(uuid.uuid4())
        node = GraphNode(
            id=action_id, label="ConnectorAction",
            properties={
                "type": action_type,
                "result": result,
                "timestamp": _now(),
            },
        )
        self.conn.contextsynapse.add_node(node, write_through=True)
        self.conn.contextsynapse.add_edge(GraphEdge(
            id=str(uuid.uuid4()), source=connector_id, target=action_id,
            label="ACTION", properties={},
        ))
        return action_id

    def build_context(self, agent_id: str = "", max_tokens: int = 6000) -> str:
        """Build LLM-ready context for an agent working on this project."""
        from ..context.hub import ContextHub, ContextRole

        spec = self.ns.get_metadata("spec") or ""
        stack = self.ns.get_metadata("stack") or {}
        rules = self.ns.get_metadata("rules") or []

        hub = ContextHub(
            system_prompt=(
                f"You are working on project '{self.name}'. "
                f"Stack: {json.dumps(stack)}. "
                f"Follow these rules: {json.dumps(rules)}."
            ),
            max_tokens=max_tokens,
        )

        hub.add_text(f"Project Spec:\n{spec}", role=ContextRole.INSTRUCTION, label="Specification")

        unblocked = self.get_open_tasks(unblocked_only=True)
        if unblocked:
            task_text = "\n".join(
                f"- [{t['priority'].upper()}] {t['title']} (id: {t['id'][:8]}, assigned: {t['assigned_to']})"
                for t in unblocked
            )
            hub.add_text(task_text, role=ContextRole.INSTRUCTION, label="Unblocked Tasks (ready to claim)")

        blocked = [t for t in self.get_open_tasks() if t.get("blocked")]
        if blocked:
            blocked_text = "\n".join(
                f"- {t['title']} (blocked by: {', '.join(t['depends_on'])})"
                for t in blocked
            )
            hub.add_text(blocked_text, role=ContextRole.BACKGROUND, label="Blocked Tasks")

        decisions = self.conn.get_nodes(label="Decision")
        if decisions:
            dec_text = "\n".join(
                f"- {p.get('title', '?')}: {p.get('rationale', '')}"
                for d in decisions
                for p in [d.properties if hasattr(d, 'properties') else {}]
            )
            hub.add_text(dec_text, role=ContextRole.DECISION, label="Decisions Made")

        reviews = self.get_pending_reviews()
        if reviews:
            review_text = "\n".join(
                f"- Review pending: {r['notes']} (id: {r['id'][:8]})"
                for r in reviews
            )
            hub.add_text(review_text, role=ContextRole.BACKGROUND, label="Pending Reviews (waiting for human)")

        connectors = self.get_connectors()
        if connectors:
            conn_text = "\n".join(
                f"- {c['type']}: {json.dumps(c['config'])}"
                for c in connectors
            )
            hub.add_text(conn_text, role=ContextRole.BACKGROUND, label="Connected Services")

        thread_msgs = self.get_thread_messages(limit=20)
        if thread_msgs:
            thread_lines = []
            for m in thread_msgs:
                role = m.get("role", "?")
                content = m.get("content", "")[:200]
                meta = m.get("metadata", {})
                msg_type = meta.get("type", "")
                prefix = f"[{role}]"
                if msg_type == "approval":
                    prefix = f"[{role} — {meta.get('action', 'approval')}]"
                elif msg_type == "agent_action":
                    prefix = f"[agent:{m.get('agent_id', '?')}]"
                thread_lines.append(f"{prefix} {content}")
            hub.add_text(
                "## Session Thread\n" + "\n".join(thread_lines),
                role=ContextRole.INTERACTION,
                label="Session Thread (recent messages)",
            )

        return hub.to_prompt()

    # ── Spec-driven traceability ──────────────────────────────────────

    def ingest_spec(self, markdown_text: str) -> List[str]:
        """Parse markdown spec and write Requirement/Feature/Constraint nodes to the graph.

        Each node is tagged with the project name for scoped queries.

        Returns list of node IDs created.
        """
        from .spec_parser import parse_spec

        nodes = parse_spec(markdown_text)
        if not nodes:
            return []

        ids = []
        for n in nodes:
            props = {
                "name": n["name"],
                "description": n["description"],
                "priority": n["priority"],
                "tags": json.dumps(n["tags"]),
                "project": self.name,
                "created_at": _now(),
            }
            graph_node = GraphNode(
                id=n["node_id"],
                label=n["label"],
                properties=props,
            )
            self.conn.contextsynapse.add_node(graph_node)
            ids.append(n["node_id"])

        return ids

    def link_artifact(self, req_id: str, artifact_id: str, edge_label: str) -> None:
        """Create a traceability edge between a requirement/feature node and an artifact.

        Common edge labels:
            IMPLEMENTED_BY  — Requirement → Task or CodeFile
            VERIFIED_BY     — Requirement → TestCase
            COVERS          — TestCase → Requirement
        """
        self.conn.contextsynapse.add_edge(GraphEdge(
            id=str(uuid.uuid4()),
            source=req_id,
            target=artifact_id,
            label=edge_label,
            properties={},
        ))

    def get_requirements(self) -> List[Dict[str, Any]]:
        """Return all Requirement nodes for this project.

        Returns dicts with keys: node_id, label, properties.
        """
        nodes = self.conn.get_nodes(label="Requirement")
        results = []
        for node in nodes:
            props = node.properties if hasattr(node, "properties") else {}
            # Filter to this project
            if props.get("project") == self.name:
                results.append({
                    "node_id": node.id if hasattr(node, "id") else "",
                    "label": node.label if hasattr(node, "label") else "Requirement",
                    "properties": props,
                })
        return results

    def check_coverage(self) -> Dict[str, Any]:
        """Analyse which requirements are implemented and verified.

        Returns:
            {
                "covered":      [req_id, ...],   # both IMPLEMENTED_BY and VERIFIED_BY
                "partial":      [req_id, ...],   # one but not the other
                "uncovered":    [req_id, ...],   # neither
                "coverage_pct": float,           # weighted 0-100
                "total":        int,
            }
        """
        reqs = self.get_requirements()
        total = len(reqs)
        if total == 0:
            return {"covered": [], "partial": [], "uncovered": [],
                    "coverage_pct": 100.0, "total": 0}

        covered, partial, uncovered = [], [], []
        for req in reqs:
            req_id = req.get("node_id", "")

            # Check IMPLEMENTED_BY edges (outgoing from req_id)
            impl_neighbors = self.conn.contextsynapse.get_neighbors(
                req_id, edge_label="IMPLEMENTED_BY", direction="OUTGOING"
            )
            # Check VERIFIED_BY edges (outgoing from req_id)
            test_neighbors = self.conn.contextsynapse.get_neighbors(
                req_id, edge_label="VERIFIED_BY", direction="OUTGOING"
            )

            has_impl = bool(impl_neighbors)
            has_test = bool(test_neighbors)

            if has_impl and has_test:
                covered.append(req_id)
            elif has_impl or has_test:
                partial.append(req_id)
            else:
                uncovered.append(req_id)

        coverage_pct = (len(covered) + 0.5 * len(partial)) / total * 100
        return {
            "covered": covered,
            "partial": partial,
            "uncovered": uncovered,
            "coverage_pct": round(coverage_pct, 2),
            "total": total,
        }
