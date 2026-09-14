"""Workflow Type Registry — verticals register their own workflow types.

Platform provides the state machine engine.
Verticals define: what workflows exist, who approves, what auto-checks run.

Usage:
    from contextsynapse.workflow.registry import get_workflow_registry

    reg = get_workflow_registry()

    # PMS vertical registers its workflows
    reg.register("trade", {
        "label": "Trade Proposal",
        "auto_checks": ["compliance_check", "risk_check"],
        "approvers": ["cio", "compliance_officer"],
        "auto_approve_below": 1000000,
        "timeout_hours": 24,
        "vertical": "pms",
    })

    # Engine uses registry to find config
    config = reg.get("trade")
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class WorkflowTypeRegistry:
    """Central registry for workflow types across verticals."""

    def __init__(self):
        self._types: dict[str, dict[str, Any]] = {}

    def register(self, workflow_type: str, config: dict[str, Any]):
        """Register a workflow type.

        config: {
            label, auto_checks, approvers, auto_approve_below,
            timeout_hours, vertical,
        }
        """
        self._types[workflow_type] = config
        logger.info("[WORKFLOW] Registered type: %s (%s)", workflow_type, config.get("label", ""))

    def register_many(self, types: dict[str, dict]):
        """Register multiple workflow types at once."""
        for wtype, config in types.items():
            self.register(wtype, config)

    def get(self, workflow_type: str) -> dict | None:
        return self._types.get(workflow_type)

    def list_all(self, vertical: str = "") -> dict[str, dict]:
        if vertical:
            return {k: v for k, v in self._types.items() if v.get("vertical") == vertical}
        return dict(self._types)

    def exists(self, workflow_type: str) -> bool:
        return workflow_type in self._types


_instance: WorkflowTypeRegistry | None = None

def get_workflow_registry() -> WorkflowTypeRegistry:
    global _instance
    if _instance is None:
        _instance = WorkflowTypeRegistry()
    return _instance
