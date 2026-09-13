"""
LangGraph Checkpoint Saver backed by AIContextDB.

Stores workflow checkpoints as graph nodes (label: LangGraphCheckpoint)
with edges linking parent → child checkpoints. This means checkpoint
lineage is queryable with AIQL — a unique advantage over SQLite/Postgres savers.

Usage:
    from contextsynapse.adapters.langgraph import AIContextDBCheckpointSaver

    saver = AIContextDBCheckpointSaver(namespace="my_workflow")
    graph = StateGraph(...)
    compiled = graph.compile(checkpointer=saver)
"""

import json
import uuid
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Optional, Sequence, Tuple

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langchain_core.runnables import RunnableConfig

from .._base import AIContextDBConnection

logger = logging.getLogger(__name__)

# Node/edge labels used in the graph
CHECKPOINT_LABEL = "LangGraphCheckpoint"
CHECKPOINT_EDGE = "NEXT_CHECKPOINT"


class AIContextDBCheckpointSaver(BaseCheckpointSaver):
    """
    Persist LangGraph checkpoints as nodes in AIContextDB.

    Each checkpoint becomes a node with:
        - label: "LangGraphCheckpoint"
        - properties: thread_id, checkpoint_id, parent_id, state (JSON),
                      metadata (JSON), timestamp

    Parent → child relationships are stored as NEXT_CHECKPOINT edges,
    making workflow lineage visible in graph queries.
    """

    def __init__(
        self,
        namespace: str = "langgraph_checkpoints",
        connection: Optional[AIContextDBConnection] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.conn = connection or AIContextDBConnection(namespace=namespace)

    # ------------------------------------------------------------------
    # Required interface
    # ------------------------------------------------------------------

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: Optional[dict] = None,
    ) -> RunnableConfig:
        """Save a checkpoint to the graph."""
        thread_id = config["configurable"]["thread_id"]
        checkpoint_id = checkpoint["id"]
        parent_id = config["configurable"].get("checkpoint_id")

        # Serialize state and metadata to JSON strings
        node_props = {
            "thread_id": thread_id,
            "checkpoint_id": checkpoint_id,
            "parent_id": parent_id or "",
            "state": json.dumps(checkpoint, default=str),
            "metadata": json.dumps(metadata, default=str) if metadata else "{}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if new_versions:
            node_props["channel_versions"] = json.dumps(new_versions, default=str)

        node = self.conn.add_node(CHECKPOINT_LABEL, node_props)

        # Link to parent checkpoint if one exists
        if parent_id:
            parent_nodes = self.conn.get_nodes(
                label=CHECKPOINT_LABEL,
                where={"thread_id": thread_id, "checkpoint_id": parent_id},
            )
            if parent_nodes:
                self.conn.add_edge(
                    source=parent_nodes[0].id,
                    target=node.id,
                    label=CHECKPOINT_EDGE,
                    properties={"thread_id": thread_id},
                )

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_id": checkpoint_id,
            }
        }

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        """Retrieve the latest (or specific) checkpoint for a thread."""
        thread_id = config["configurable"]["thread_id"]
        checkpoint_id = config["configurable"].get("checkpoint_id")

        nodes = self.conn.get_nodes(label=CHECKPOINT_LABEL, where={"thread_id": thread_id})
        if not nodes:
            return None

        if checkpoint_id:
            # Find specific checkpoint
            matches = [n for n in nodes if n.properties.get("checkpoint_id") == checkpoint_id]
            if not matches:
                return None
            node = matches[0]
        else:
            # Find latest by timestamp
            nodes.sort(key=lambda n: n.properties.get("timestamp", ""), reverse=True)
            node = nodes[0]

        return self._node_to_tuple(node)

    def list(
        self,
        config: Optional[RunnableConfig] = None,
        *,
        filter: Optional[Dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        """List checkpoints for a thread, newest first."""
        if config is None:
            return

        thread_id = config["configurable"]["thread_id"]
        nodes = self.conn.get_nodes(label=CHECKPOINT_LABEL, where={"thread_id": thread_id})

        # Sort by timestamp descending
        nodes.sort(key=lambda n: n.properties.get("timestamp", ""), reverse=True)

        # Apply 'before' filter
        if before:
            before_id = before["configurable"].get("checkpoint_id")
            if before_id:
                before_ts = None
                for n in nodes:
                    if n.properties.get("checkpoint_id") == before_id:
                        before_ts = n.properties.get("timestamp")
                        break
                if before_ts:
                    nodes = [n for n in nodes if n.properties.get("timestamp", "") < before_ts]

        # Apply limit
        if limit:
            nodes = nodes[:limit]

        for node in nodes:
            tup = self._node_to_tuple(node)
            if tup:
                yield tup

    # ------------------------------------------------------------------
    # Async variants (delegate to sync for now)
    # ------------------------------------------------------------------

    async def aput(self, config, checkpoint, metadata, new_versions=None):
        return self.put(config, checkpoint, metadata, new_versions)

    async def aget_tuple(self, config):
        return self.get_tuple(config)

    async def alist(self, config=None, *, filter=None, before=None, limit=None):
        for item in self.list(config, filter=filter, before=before, limit=limit):
            yield item

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _node_to_tuple(self, node) -> Optional[CheckpointTuple]:
        """Convert a graph node back to a CheckpointTuple."""
        try:
            props = node.properties
            checkpoint = json.loads(props.get("state", "{}"))
            metadata = json.loads(props.get("metadata", "{}"))
            parent_id = props.get("parent_id", "")
            thread_id = props.get("thread_id", "")

            config = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_id": props.get("checkpoint_id", ""),
                }
            }
            parent_config = None
            if parent_id:
                parent_config = {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_id": parent_id,
                    }
                }

            return CheckpointTuple(
                config=config,
                checkpoint=checkpoint,
                metadata=metadata,
                parent_config=parent_config,
            )
        except Exception as exc:
            logger.warning(f"Failed to deserialize checkpoint node {node.id}: {exc}")
            return None
