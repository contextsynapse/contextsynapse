"""AdaptivePermissions — map trust levels to tool access tiers."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Set

TIER_READ = {
    "search_nodes", "get_context", "orient", "briefing", "ask", "recall",
    "graph_summary", "query_graph", "rag_query", "rag_graph", "graph_timeline",
    "graph_diff", "check_freshness", "get_suggestions", "project_status",
    "list_tasks", "my_tasks", "get_agent_context", "get_unblocked_tasks",
    "task_context", "ws_read_file", "ws_list_files", "ws_git_status",
    "a2a_discover", "a2a_get_task", "my_usage", "boundary_costs", "get_tool_policy",
    "explain_context", "topic_scan", "check_reviews", "trace_lineage",
    "search", "code_project_status", "list_pipelines", "list_schemas",
    "get_versions", "get_context_meta",
}

TIER_WRITE = TIER_READ | {
    "add_knowledge", "add_relationship", "add_decision", "add_task",
    "claim_task", "complete_task", "handoff_task", "log_action",
    "remember", "rate_context", "detect_conflicts", "resolve_conflict",
    "report_external", "report_tokens", "a2a_send_task", "a2a_cancel_task",
    "invalidate_node", "request_review", "request_promotion",
}

TIER_FULL = TIER_WRITE | {
    "dispatch_goal", "generate_tasks",
    "ws_write_file", "ws_commit", "ws_push", "ws_create_branch", "ws_run_command",
    "use_graph", "save_schema", "run_pipeline",
}

@dataclass
class PermissionDecision:
    allowed: bool
    reason: str = ""

class AdaptivePermissions:
    def __init__(self):
        self._frozen: Set[str] = set()
        self._active: Dict[str, str] = {}  # agent_id -> task_id (mid-workflow)
        self._pending_restrictions: Dict[str, str] = {}  # agent_id -> reason (apply after task)

    def check(self, agent_id: str, tool_name: str, trust_score: float = 0.5) -> PermissionDecision:
        # Grace period: if agent is mid-task, allow but warn
        if agent_id in self._active:
            is_restricted = (agent_id in self._frozen) or (trust_score < 0.2 and tool_name not in TIER_READ)
            if is_restricted:
                reason = "frozen" if agent_id in self._frozen else f"low trust ({trust_score:.2f})"
                self._pending_restrictions[agent_id] = reason
                return PermissionDecision(True, f"WARNING: {reason} — restrictions apply after current task completes")
            # Normal permission check for active agents
            return self._check_tier(agent_id, tool_name, trust_score)

        # Not active — enforce restrictions immediately
        # But first check if there's a pending restriction from a completed task
        if agent_id in self._pending_restrictions:
            if tool_name == "claim_task":
                # Block new task claims — restrictions are now active
                reason = self._pending_restrictions.pop(agent_id)
                return PermissionDecision(False, f"Restricted: {reason}")

        return self._check_tier(agent_id, tool_name, trust_score)

    def _check_tier(self, agent_id: str, tool_name: str, trust_score: float) -> PermissionDecision:
        if agent_id in self._frozen:
            if tool_name in TIER_READ:
                return PermissionDecision(True, "read-only (frozen)")
            return PermissionDecision(False, "Agent frozen — anomaly detected")
        if trust_score >= 0.8:
            return PermissionDecision(True, "trusted")
        if trust_score >= 0.5:
            if tool_name in TIER_FULL:
                return PermissionDecision(True, "verified")
            return PermissionDecision(False, f"Requires trust >= 0.8 (current: {trust_score:.2f})")
        if trust_score >= 0.2:
            if tool_name in TIER_WRITE:
                return PermissionDecision(True, "provisional")
            return PermissionDecision(False, f"Requires trust >= 0.5 (current: {trust_score:.2f})")
        if tool_name in TIER_READ:
            return PermissionDecision(True, "untrusted: read-only")
        return PermissionDecision(False, "Untrusted agent: read-only access")

    def mark_active(self, agent_id: str, task_id: str):
        """Mark agent as mid-workflow (won't be blocked until task completes)."""
        self._active[agent_id] = task_id

    def mark_idle(self, agent_id: str):
        """Mark agent as idle (restrictions now enforced)."""
        self._active.pop(agent_id, None)

    def freeze(self, agent_id: str):
        self._frozen.add(agent_id)

    def unfreeze(self, agent_id: str):
        self._frozen.discard(agent_id)
        self._pending_restrictions.pop(agent_id, None)

    def is_frozen(self, agent_id: str) -> bool:
        return agent_id in self._frozen
