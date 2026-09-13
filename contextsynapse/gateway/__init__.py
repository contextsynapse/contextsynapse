"""
Agent Gateway — Control Plane for AI Agent Tool Execution
==========================================================
Routes all agent actions through the boundary, enforces policies,
tracks costs, and emits events to the execution graph.

Components:
    - ToolGateway: Intercepts every tool call (internal + external)
    - ToolPolicy: Allowlist/blocklist per boundary
    - CostTracker: Per-agent, per-boundary cost and usage tracking
    - ActionEmitter: Auto-records every agent action as a graph node
"""

from .gateway import ToolGateway, get_gateway
from .policy import ToolPolicy, PolicyDecision
from .cost_tracker import CostTracker, get_cost_tracker
from .action_emitter import ActionEmitter, get_emitter

__all__ = [
    "ToolGateway", "get_gateway",
    "ToolPolicy", "PolicyDecision",
    "CostTracker", "get_cost_tracker",
    "ActionEmitter", "get_emitter",
]
