"""
Action Emitter
===============
After every agent action, emit an event node to the execution graph.

This ensures the boundary's graph always reflects what agents are doing —
including calls to external tools, decisions, file changes, and API calls.

Every emitted action becomes a node in the graph with edges to the agent
and the boundary, making the full execution history queryable via AIQL.

Usage:
    emitter = ActionEmitter()
    emitter.emit_action(
        conn=connection,
        agent_id="codex",
        agent_name="codex",
        action_type="tool_call",
        tool_name="search_nodes",
        details={"query": "auth"},
        result_summary="Found 5 nodes",
    )
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ActionEmitter:
    """Records agent actions as nodes in the execution graph."""

    # Action types and their graph labels
    ACTION_LABELS = {
        "tool_call": "ToolCall",
        "external_tool_call": "ExternalToolCall",
        "decision": "Decision",
        "knowledge_added": "KnowledgeAction",
        "task_claimed": "TaskAction",
        "task_completed": "TaskAction",
        "file_changed": "FileAction",
        "api_call": "APICall",
        "error": "ErrorEvent",
        "context_request": "ContextRequest",
    }

    def __init__(self, auto_emit: bool = True, emit_to_event_bus: bool = True):
        """
        Args:
            auto_emit: If True, also emit to the event_bus for real-time UI updates.
            emit_to_event_bus: If True, fire events for WebSocket subscribers.
        """
        self._auto_emit = auto_emit
        self._emit_to_event_bus = emit_to_event_bus

    def emit_action(
        self,
        conn,  # AIContextDBConnection
        agent_id: str,
        agent_name: str,
        action_type: str,
        tool_name: str = "",
        details: Optional[Dict[str, Any]] = None,
        result_summary: str = "",
        duration_ms: int = 0,
        success: bool = True,
        is_external: bool = False,
    ) -> Optional[str]:
        """Record an action as a node in the execution graph.

        Returns the node ID if successful, None otherwise.
        """
        if not conn:
            return None

        label = self.ACTION_LABELS.get(action_type, "AgentAction")
        node_id = str(uuid.uuid4())
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        properties = {
            "action_type": action_type,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "tool_name": tool_name,
            "timestamp": timestamp,
            "duration_ms": duration_ms,
            "success": success,
            "is_external": is_external,
        }
        if result_summary:
            properties["result_summary"] = result_summary[:500]
        if details and isinstance(details, dict):
            # Flatten simple details into properties
            for k, v in details.items():
                if isinstance(v, (str, int, float, bool)):
                    properties[f"detail_{k}"] = v

        try:
            from ..core.graph_structures import GraphNode
            node = GraphNode(id=node_id, label=label, properties=properties)
            conn.contextsynapse.add_node(node)

            # Create edge from agent to action (if agent node exists)
            self._link_agent_to_action(conn, agent_id, agent_name, node_id, action_type)

            # Auto-persist
            try:
                conn.graph_registry.save_graph(conn.namespace, create_checkpoint=False)
            except Exception:
                pass

            # Emit to event bus for real-time UI
            if self._emit_to_event_bus:
                try:
                    from ..api.events import event_bus
                    event_bus.emit("agent_action", {
                        "namespace": conn.namespace,
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                        "action_type": action_type,
                        "tool_name": tool_name,
                        "success": success,
                        "is_external": is_external,
                        "node_id": node_id,
                        "timestamp": timestamp,
                    })
                except Exception:
                    pass

            logger.debug(
                "Emitted %s action: %s/%s tool=%s",
                action_type, agent_name, agent_id, tool_name,
            )
            return node_id

        except Exception as e:
            logger.debug("Failed to emit action: %s", e)
            return None

    def emit_external_observation(
        self,
        conn,
        agent_id: str,
        agent_name: str,
        external_tool: str,
        input_summary: str = "",
        output_summary: str = "",
        duration_ms: int = 0,
    ) -> Optional[str]:
        """Record an observation of an agent using an external tool.

        This is for tools OUTSIDE our registry — web search, file system,
        custom APIs, etc. We can't control these, but we CAN observe them.
        """
        return self.emit_action(
            conn=conn,
            agent_id=agent_id,
            agent_name=agent_name,
            action_type="external_tool_call",
            tool_name=external_tool,
            details={
                "input": input_summary[:200] if input_summary else "",
                "output": output_summary[:200] if output_summary else "",
            },
            result_summary=output_summary[:500] if output_summary else "",
            duration_ms=duration_ms,
            is_external=True,
        )

    def _link_agent_to_action(self, conn, agent_id: str, agent_name: str, action_node_id: str, action_type: str):
        """Create PERFORMED edge from AgentRef to action node."""
        try:
            # Find existing agent node
            all_nodes = conn.contextsynapse.get_all_nodes()
            agent_node_id = None
            for n in all_nodes:
                label = getattr(n, "label", "")
                props = getattr(n, "properties", {})
                if label == "AgentRef" and (
                    props.get("agent_id") == agent_id or props.get("name") == agent_name
                ):
                    agent_node_id = getattr(n, "id", None)
                    break

            if agent_node_id:
                from ..core.graph_structures import GraphEdge
                edge = GraphEdge(
                    id=str(uuid.uuid4()),
                    source=agent_node_id,
                    target=action_node_id,
                    label="PERFORMED",
                    properties={"action_type": action_type},
                )
                conn.contextsynapse.add_edge(edge)
        except Exception as e:
            logger.debug("Failed to link agent to action: %s", e)


# Global singleton
_emitter = None


def get_emitter() -> ActionEmitter:
    global _emitter
    if _emitter is None:
        _emitter = ActionEmitter()
    return _emitter
