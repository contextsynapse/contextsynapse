"""
AIContextDB LangChain Adapter
=========================
Provides a graph-backed retriever, message history, and tools
for LangChain chains and agents.

Requirements:
    pip install langchain-core
"""

from .retriever import AIContextDBRetriever
from .message_history import AIContextDBChatMessageHistory
from .tools import create_langchain_tools

__all__ = [
    "AIContextDBRetriever",
    "AIContextDBChatMessageHistory",
    "create_langchain_tools",
]
