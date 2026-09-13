"""
LLM Client — Multi-provider wrapper for AIContextDB.
Supports OpenAI, Anthropic, and Ollama with automatic provider detection.
"""

from .client import LLMClient, get_llm_client

__all__ = ["LLMClient", "get_llm_client"]
