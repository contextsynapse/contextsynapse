"""
LangGraph / LangChain Chat Message History backed by AIContextDB.

Stores conversation messages as graph nodes (label: ChatMessage) with
FOLLOWED_BY edges forming a linked-list per session.

Usage:
    from contextsynapse.adapters.langgraph import AIContextDBChatMessageHistory

    history = AIContextDBChatMessageHistory(session_id="conv-123")
    history.add_user_message("Hello")
    history.add_ai_message("Hi there!")
    print(history.messages)
"""

import json
import logging
from datetime import datetime, timezone
from typing import List, Optional

from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    messages_from_dict,
    message_to_dict,
)

from .._base import AIContextDBConnection

logger = logging.getLogger(__name__)

MESSAGE_LABEL = "ChatMessage"
MESSAGE_EDGE = "FOLLOWED_BY"


class AIContextDBChatMessageHistory(BaseChatMessageHistory):
    """
    Chat message history stored as a linked list of graph nodes.

    Each message becomes a ChatMessage node with:
        - session_id, role, content, timestamp, sequence

    Edges (FOLLOWED_BY) link messages in order, making conversation
    flow visible in graph queries.
    """

    def __init__(
        self,
        session_id: str,
        namespace: str = "chat_history",
        connection: Optional[AIContextDBConnection] = None,
    ):
        self.session_id = session_id
        self.conn = connection or AIContextDBConnection(namespace=namespace)

    @property
    def messages(self) -> List[BaseMessage]:
        """Retrieve all messages for this session, ordered by sequence."""
        nodes = self.conn.get_nodes(
            label=MESSAGE_LABEL,
            where={"session_id": self.session_id},
        )
        # Sort by sequence number
        nodes.sort(key=lambda n: n.properties.get("sequence", 0))

        messages = []
        for node in nodes:
            msg_data = json.loads(node.properties.get("message_data", "{}"))
            if msg_data:
                try:
                    restored = messages_from_dict([msg_data])
                    messages.extend(restored)
                except Exception:
                    # Fallback: reconstruct from role/content
                    messages.append(self._reconstruct_message(node.properties))
            else:
                messages.append(self._reconstruct_message(node.properties))

        return messages

    def add_message(self, message: BaseMessage) -> None:
        """Add a message to the history."""
        # Get current sequence number
        existing = self.conn.get_nodes(
            label=MESSAGE_LABEL,
            where={"session_id": self.session_id},
        )
        sequence = len(existing)

        # Find the last message node for edge linking
        last_node_id = None
        if existing:
            existing.sort(key=lambda n: n.properties.get("sequence", 0))
            last_node_id = existing[-1].id

        # Serialize the full message
        msg_dict = message_to_dict(message)

        props = {
            "session_id": self.session_id,
            "role": message.type,  # "human", "ai", "system"
            "content": message.content[:500] if message.content else "",  # preview
            "message_data": json.dumps(msg_dict, default=str),
            "sequence": sequence,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        node = self.conn.add_node(MESSAGE_LABEL, props)

        # Link to previous message
        if last_node_id:
            self.conn.add_edge(
                source=last_node_id,
                target=node.id,
                label=MESSAGE_EDGE,
                properties={"session_id": self.session_id},
            )

    def clear(self) -> None:
        """Delete all messages for this session."""
        self.conn.query(
            f'DELETE NODES WHERE label = "{MESSAGE_LABEL}" '
            f'AND session_id = "{self.session_id}"'
        )

    @staticmethod
    def _reconstruct_message(props: dict) -> BaseMessage:
        """Reconstruct a message from node properties when deserialization fails."""
        role = props.get("role", "human")
        content = props.get("content", "")
        if role == "ai":
            return AIMessage(content=content)
        elif role == "system":
            return SystemMessage(content=content)
        return HumanMessage(content=content)
