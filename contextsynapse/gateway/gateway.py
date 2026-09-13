"""
Tool Gateway — The Control Plane
==================================
Intercepts every tool call (internal + external) and:
    1. Evaluates policy (allow/deny)
    2. Records the call in the cost tracker
    3. Emits an action event to the execution graph
    4. Returns the result (or a policy denial message)

This is the SINGLE POINT OF CONTROL for all agent activity.
Instead of agents calling tools directly, they go through the gateway.

Architecture:
    Agent → Gateway.execute() → Policy Check → Tool Handler → Action Emitter → Cost Tracker → Result

For external tools (outside our registry):
    Agent → Gateway.observe_external() → Cost Tracker → Action Emitter → Graph Node

Patent-worthy: Unified control plane that makes every AI agent action
observable, controllable, and cost-tracked through a shared execution graph.

Usage:
    gateway = ToolGateway()

    # Internal tool call (routed through our registry)
    result = gateway.execute("search_nodes", ctx, {"label": "Task"})

    # External tool observation (agent used a tool we don't own)
    gateway.observe_external(ctx, "web_search", input="auth best practices", output="...")
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from .policy import ToolPolicy, PolicyDecision, get_policy
from .cost_tracker import CostTracker, get_cost_tracker
from .action_emitter import ActionEmitter, get_emitter

logger = logging.getLogger(__name__)


class ToolGateway:
    """Unified control plane for all agent tool execution."""

    def __init__(
        self,
        policy: Optional[ToolPolicy] = None,
        cost_tracker: Optional[CostTracker] = None,
        emitter: Optional[ActionEmitter] = None,
    ):
        self._policy = policy
        self._cost_tracker = cost_tracker
        self._emitter = emitter

    @property
    def policy(self) -> ToolPolicy:
        if self._policy is None:
            self._policy = get_policy()
        return self._policy

    @property
    def cost_tracker(self) -> CostTracker:
        if self._cost_tracker is None:
            self._cost_tracker = get_cost_tracker()
        return self._cost_tracker

    @property
    def emitter(self) -> ActionEmitter:
        if self._emitter is None:
            self._emitter = get_emitter()
        return self._emitter

    def execute(
        self,
        tool_name: str,
        ctx,  # ToolContext
        kwargs=None,
    ) -> str:
        """Execute a tool call through the gateway.


        This replaces direct ToolRegistry.dispatch() calls.
        Adds: policy check → cost tracking → action emission.
        """
        from ..tools.registry import ToolRegistry

        # Normalize kwargs — LLMs sometimes send strings instead of dicts
        if isinstance(kwargs, str):
            kwargs = {"query": kwargs}
        elif not isinstance(kwargs, dict):
            kwargs = kwargs or {}

        start_ms = time.monotonic_ns() // 1_000_000
        namespace = getattr(ctx.conn, "namespace", "") if ctx.conn else ""
        agent_id = ctx.agent_id or "unknown"
        agent_name = ctx.agent_name or agent_id

        # 1. Get tool definition
        tool_def = ToolRegistry.get(tool_name)
        tool_category = tool_def.category if tool_def else "unknown"

        # 2. Policy check
        decision = self.policy.evaluate(namespace, agent_id, tool_name, tool_category)
        if not decision.allowed:
            logger.info(
                "DENIED: %s/%s tried %s — %s",
                agent_name, namespace, tool_name, decision.reason,
            )
            # Record the denied attempt
            self.cost_tracker.record_tool_call(
                namespace=namespace,
                agent_id=agent_id,
                tool_name=tool_name,
                tool_category=tool_category,
                success=False,
                agent_name=agent_name,
                args_summary=f"DENIED: {decision.reason}",
            )
            return f"Policy denied: {decision.reason}"

        # 2b. AgentShield trust-based permission check
        try:
            from ..shield import get_agent_shield
            shield = get_agent_shield()
            shield_decision = shield.check_permission(agent_id, tool_name)
            if not shield_decision.allowed:
                logger.info("SHIELD DENIED: %s tried %s — %s", agent_name, tool_name, shield_decision.reason)
                self.cost_tracker.record_tool_call(
                    namespace=namespace, agent_id=agent_id, tool_name=tool_name,
                    tool_category=tool_category, success=False, agent_name=agent_name,
                    args_summary=f"SHIELD DENIED: {shield_decision.reason}",
                )
                return f"Trust denied: {shield_decision.reason}"
        except Exception:
            pass  # shield must never break tool execution

        # 3. Execute the tool (direct, bypassing gateway to avoid recursion)
        from ..core.write_context import set_write_context, clear_write_context
        set_write_context(agent_id=agent_id, origin="tool", verified=True)
        try:
            result = ToolRegistry._dispatch_direct(tool_name, ctx, kwargs)
        finally:
            clear_write_context()
        elapsed_ms = (time.monotonic_ns() // 1_000_000) - start_ms
        success = not result.startswith("Error")

        # 4a. Audit trail logging
        try:
            from ..security.audit_trail import get_audit_trail
            audit = get_audit_trail()
            op = "write" if tool_name.startswith(("add_", "create_", "update_", "delete_", "ws_write", "ws_commit", "complete_", "claim_")) else "read"
            audit.log(
                actor=f"agent:{agent_id}",
                action=tool_name,
                operation_type=op,
                resource_type="tool",
                resource_id=tool_name,
                namespace=namespace or "",
                details={"success": success, "duration_ms": elapsed_ms},
                success=success,
                actor_type="agent",
            )
        except Exception:
            pass  # audit logging must never break tool execution

        # 4. Record in cost tracker
        args_summary = ""
        if kwargs:
            args_summary = json.dumps(kwargs, default=str)[:200]
        self.cost_tracker.record_tool_call(
            namespace=namespace,
            agent_id=agent_id,
            tool_name=tool_name,
            tool_category=tool_category,
            duration_ms=elapsed_ms,
            success=success,
            is_external=False,
            agent_name=agent_name,
            args_summary=args_summary,
            result_length=len(result),
        )

        # 5. Track action in trust system
        try:
            from ..context.store_factory import create_agent_registry
            trust_registry = create_agent_registry()
            trust_registry.record_action(agent_id)
        except Exception:
            pass

        # 5b. Feed Context Relevance Gravity — track what agent is interested in
        try:
            from ..context.gravity import get_gravity
            get_gravity().observe(agent_id, tool_name, kwargs or {})
        except Exception:
            pass

        # 5c. AgentShield — update profile
        try:
            from ..shield import get_agent_shield
            shield = get_agent_shield()
            shield.on_tool_call(agent_id, tool_name, tool_category, success)
        except Exception:
            pass

        # 5d. Cross-Agent Propagation — notify other agents of significant actions
        if success and tool_category in ("graph", "task"):
            try:
                from ..context.propagation import get_propagator
                # Build a human-readable summary of what happened
                if tool_name == "add_decision":
                    title = (kwargs or {}).get("title", "")
                    get_propagator().propagate(
                        source_agent=agent_name, event_type="decision",
                        content=f"Decided: {title}", namespace=namespace, priority="critical",
                    )
                elif tool_name == "complete_task":
                    summary = (kwargs or {}).get("summary", "")
                    get_propagator().propagate(
                        source_agent=agent_name, event_type="task_completed",
                        content=f"Completed task: {summary[:100]}", namespace=namespace,
                    )
                elif tool_name == "add_knowledge":
                    label = (kwargs or {}).get("node_type", "") or (kwargs or {}).get("label", "") or "Finding"
                    content_preview = ((kwargs or {}).get("content", "") or "")[:100]
                    get_propagator().propagate(
                        source_agent=agent_name, event_type="knowledge_added",
                        content=f"Added [{label}]: {content_preview}", namespace=namespace, priority="info",
                    )
                elif tool_name == "add_task":
                    title = (kwargs or {}).get("title", "")
                    assigned = (kwargs or {}).get("assigned_to", "")
                    get_propagator().propagate(
                        source_agent=agent_name, event_type="task_created",
                        content=f"Created task '{title}' → {assigned}", namespace=namespace,
                    )
                elif tool_name in ("ws_write_file", "ws_commit"):
                    filepath = (kwargs or {}).get("filepath", (kwargs or {}).get("message", ""))
                    get_propagator().propagate(
                        source_agent=agent_name, event_type="code_changed",
                        content=f"Wrote: {filepath[:80]}", namespace=namespace, priority="info",
                    )
            except Exception:
                pass

        # 6. Emit action to graph (lightweight — only significant actions)
        if tool_category in ("graph", "task", "workspace") or not success:
            self.emitter.emit_action(
                conn=ctx.conn,
                agent_id=agent_id,
                agent_name=agent_name,
                action_type="tool_call",
                tool_name=tool_name,
                details=kwargs,
                result_summary=result[:200] if len(result) > 200 else result,
                duration_ms=elapsed_ms,
                success=success,
            )

        # 6. Observe for Context Relevance Gravity
        try:
            from ..context.gravity import get_gravity
            get_gravity().observe(agent_id, tool_name, kwargs or {})
        except Exception:
            pass

        return result

    def observe_external(
        self,
        ctx,  # ToolContext
        tool_name: str,
        input_data: str = "",
        output_data: str = "",
        duration_ms: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Record an external tool call that happened outside our control.

        Agents should call this to report what they did externally.
        This gives the boundary visibility into ALL agent activity.

        Returns confirmation message.
        """
        namespace = getattr(ctx.conn, "namespace", "") if ctx.conn else ""
        agent_id = ctx.agent_id or "unknown"
        agent_name = ctx.agent_name or agent_id

        # Check if external tools are allowed
        policy = self.policy._load_policy(namespace)
        if not policy.allow_external:
            return f"External tools are not allowed in this boundary. Tool '{tool_name}' was blocked."

        # Record in cost tracker
        self.cost_tracker.record_tool_call(
            namespace=namespace,
            agent_id=agent_id,
            tool_name=tool_name,
            tool_category="external",
            duration_ms=duration_ms,
            success=True,
            is_external=True,
            agent_name=agent_name,
            args_summary=input_data[:200],
            result_length=len(output_data),
        )

        # Emit to graph
        self.emitter.emit_external_observation(
            conn=ctx.conn,
            agent_id=agent_id,
            agent_name=agent_name,
            external_tool=tool_name,
            input_summary=input_data,
            output_summary=output_data,
            duration_ms=duration_ms,
        )

        return f"Recorded external tool call: {tool_name}"

    def record_tokens(
        self,
        ctx,
        input_tokens: int,
        output_tokens: int,
        model: str,
        operation: str = "",
    ) -> float:
        """Record token usage for cost tracking. Returns estimated cost."""
        namespace = getattr(ctx.conn, "namespace", "") if ctx.conn else ""
        agent_id = ctx.agent_id or "unknown"
        agent_name = ctx.agent_name or agent_id

        return self.cost_tracker.record_tokens(
            namespace=namespace,
            agent_id=agent_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model,
            operation=operation,
            agent_name=agent_name,
        )

    def get_agent_report(self, namespace: str, agent_id: str) -> Dict[str, Any]:
        """Get cost/usage report for a specific agent."""
        return self.cost_tracker.get_agent_report(namespace, agent_id)

    def get_boundary_report(self, namespace: str) -> Dict[str, Any]:
        """Get aggregate cost report for the boundary."""
        return self.cost_tracker.get_boundary_report(namespace)

    def get_external_log(self, namespace: str, agent_id: str = "") -> List[Dict[str, Any]]:
        """Get log of external tool calls."""
        return self.cost_tracker.get_external_tool_log(namespace, agent_id)

    def get_policy(self, namespace: str) -> Dict[str, Any]:
        """Get current policy for a boundary."""
        return self.policy.get_boundary_policy(namespace)

    def set_policy(self, namespace: str, **kwargs) -> None:
        """Update policy for a boundary."""
        self.policy.set_boundary_policy(namespace, **kwargs)


# Global singleton
_gateway = None


def get_gateway() -> ToolGateway:
    global _gateway
    if _gateway is None:
        _gateway = ToolGateway()
    return _gateway
