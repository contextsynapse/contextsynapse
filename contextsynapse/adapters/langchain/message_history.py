"""
LangChain Chat Message History backed by AIContextDB.

Reuses the same implementation as the LangGraph adapter since both
frameworks share langchain_core.chat_history.BaseChatMessageHistory.

Usage:
    from contextsynapse.adapters.langchain import AIContextDBChatMessageHistory

    history = AIContextDBChatMessageHistory(session_id="conv-123")
    history.add_user_message("Hello")
"""

# Re-export from the langgraph adapter — same base class
from ..langgraph.message_history import AIContextDBChatMessageHistory

__all__ = ["AIContextDBChatMessageHistory"]
