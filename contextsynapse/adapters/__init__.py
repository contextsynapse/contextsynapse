"""
AIContextDB Framework Adapters
==============================
Adapters that let agent frameworks use AIContextDB as their shared
memory, knowledge, and state persistence layer.

Supported frameworks:
    pip install langgraph       # LangGraph (checkpoint + tools)
    pip install langchain-core  # LangChain (tools + retriever)
    pip install crewai          # CrewAI (BaseTool subclasses)
    pip install pyautogen       # AutoGen (function-calling tools)
    pip install pydantic-ai     # Pydantic AI (typed tools with deps)
    pip install llama-index     # LlamaIndex (FunctionTool + retriever)
    # Swarm/Agno use plain functions — no extra install needed

Each adapter is optional — only loads if the framework is installed.
All adapters share the same AIContextDBConnection base.
"""

from ._base import AIContextDBConnection

# ── LangGraph ──
try:
    from .langgraph import (
        AIContextDBCheckpointSaver,
        AIContextDBChatMessageHistory,
        create_graph_tools,
    )
except ImportError:
    AIContextDBCheckpointSaver = None
    AIContextDBChatMessageHistory = None
    create_graph_tools = None

# ── LangChain ──
try:
    from .langchain import AIContextDBRetriever, create_langchain_tools
except ImportError:
    AIContextDBRetriever = None
    create_langchain_tools = None

# ── CrewAI ──
try:
    from .crewai import create_crewai_tools, GraphQueryTool, GraphWriteTool
except ImportError:
    create_crewai_tools = None
    GraphQueryTool = None
    GraphWriteTool = None

# ── AutoGen ──
try:
    from .autogen import create_autogen_tools, dispatch_tool_call as dispatch_autogen_call
except ImportError:
    create_autogen_tools = None
    dispatch_autogen_call = None

# ── Pydantic AI ──
try:
    from .pydantic_ai import create_pydantic_ai_tools
except ImportError:
    create_pydantic_ai_tools = None

# ── LlamaIndex ──
try:
    from .llamaindex import create_llamaindex_tools, AIContextDBQueryTool
except ImportError:
    create_llamaindex_tools = None
    AIContextDBQueryTool = None

# ── Swarm / Agno (plain functions, always available) ──
from .swarm import create_swarm_tools

# ── OpenAI / Codex ──
try:
    from .openai import configure as configure_openai, create_openai_tools, dispatch_tool_call
except ImportError:
    configure_openai = None
    create_openai_tools = None
    dispatch_tool_call = None

__all__ = [
    "AIContextDBConnection",
    # LangGraph
    "AIContextDBCheckpointSaver", "AIContextDBChatMessageHistory", "create_graph_tools",
    # LangChain
    "AIContextDBRetriever", "create_langchain_tools",
    # CrewAI
    "create_crewai_tools", "GraphQueryTool", "GraphWriteTool",
    # AutoGen
    "create_autogen_tools", "dispatch_autogen_call",
    # Pydantic AI
    "create_pydantic_ai_tools",
    # LlamaIndex
    "create_llamaindex_tools", "AIContextDBQueryTool",
    # Swarm / Agno
    "create_swarm_tools",
    # OpenAI / Codex
    "configure_openai", "create_openai_tools", "dispatch_tool_call",
]
