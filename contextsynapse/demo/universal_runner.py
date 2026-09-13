"""Universal agent runner — any LLM provider with tool-calling support.

Supports: Anthropic, OpenAI, Groq, DeepSeek, Together, Mistral, Cerebras,
Fireworks, Ollama, and any OpenAI-compatible API.

The runner uses a common tool dispatch layer. Tool definitions are converted
to each provider's format automatically.

Usage::

    result = run_agent(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        system="You are an architect.",
        task="Design the login endpoint.",
        tools=_ALL_TOOLS,
        dispatch=lambda name, input: _dispatch_tool(name, input, base_path),
    )
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_MAX_TURNS = 20


@dataclass
class AgentResult:
    """Result from running one agent pass."""
    response_text: str
    tool_calls: List[Dict[str, Any]]
    model: str
    provider: str


def _convert_tools_to_openai(tools: List[Dict]) -> List[Dict]:
    """Convert Anthropic-style tool defs to OpenAI function-calling format."""
    openai_tools = []
    for t in tools:
        openai_tools.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return openai_tools


def _run_anthropic(
    model: str,
    system: str,
    task: str,
    tools: List[Dict],
    dispatch: Callable,
) -> AgentResult:
    """Run agent via Anthropic API with tool-use loop."""
    import anthropic
    client = anthropic.Anthropic()
    messages: List[Dict] = [{"role": "user", "content": task}]
    tool_calls_log: List[Dict] = []

    for _ in range(_MAX_TURNS):
        response = client.messages.create(
            model=model, max_tokens=2048, system=system,
            tools=tools, messages=messages,
        )

        if response.stop_reason == "end_turn":
            text = next((b.text for b in response.content if hasattr(b, "text")), "")
            return AgentResult(response_text=text, tool_calls=tool_calls_log, model=model, provider="anthropic")

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                result_str = dispatch(block.name, block.input)
                tool_calls_log.append({"tool": block.name, "input": block.input, "result": result_str})
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result_str})
            messages.append({"role": "user", "content": tool_results})
            continue
        break

    return AgentResult(response_text="", tool_calls=tool_calls_log, model=model, provider="anthropic")


def _run_openai_compatible(
    provider: str,
    model: str,
    system: str,
    task: str,
    tools: List[Dict],
    dispatch: Callable,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> AgentResult:
    """Run agent via OpenAI-compatible API (OpenAI, Groq, DeepSeek, Together, etc.)."""
    import openai

    client_kwargs = {}
    if base_url:
        client_kwargs["base_url"] = base_url
    if api_key:
        client_kwargs["api_key"] = api_key

    client = openai.OpenAI(**client_kwargs)
    openai_tools = _convert_tools_to_openai(tools)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": task},
    ]
    tool_calls_log: List[Dict] = []

    for _ in range(_MAX_TURNS):
        response = client.chat.completions.create(
            model=model, max_tokens=2048,
            messages=messages, tools=openai_tools,
        )
        choice = response.choices[0]

        if choice.finish_reason == "stop":
            return AgentResult(
                response_text=choice.message.content or "",
                tool_calls=tool_calls_log, model=model, provider=provider,
            )

        if choice.finish_reason == "tool_calls":
            messages.append(choice.message)
            for tc in choice.message.tool_calls:
                args = json.loads(tc.function.arguments)
                result_str = dispatch(tc.function.name, args)
                tool_calls_log.append({"tool": tc.function.name, "input": args, "result": result_str})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })
            continue
        break

    return AgentResult(response_text="", tool_calls=tool_calls_log, model=model, provider=provider)


def _run_ollama(
    model: str,
    system: str,
    task: str,
    tools: List[Dict],
    dispatch: Callable,
) -> AgentResult:
    """Run agent via Ollama local API with tool-calling."""
    import requests

    ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    openai_tools = _convert_tools_to_openai(tools)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": task},
    ]
    tool_calls_log: List[Dict] = []

    for _ in range(_MAX_TURNS):
        resp = requests.post(
            f"{ollama_url}/api/chat",
            json={"model": model, "messages": messages, "tools": openai_tools, "stream": False},
            timeout=120,
        )
        data = resp.json()
        msg = data.get("message", {})

        tool_calls = msg.get("tool_calls", [])
        if not tool_calls:
            return AgentResult(
                response_text=msg.get("content", ""),
                tool_calls=tool_calls_log, model=model, provider="ollama",
            )

        messages.append(msg)
        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            args = fn.get("arguments", {})
            if isinstance(args, str):
                args = json.loads(args)
            result_str = dispatch(name, args)
            tool_calls_log.append({"tool": name, "input": args, "result": result_str})
            messages.append({"role": "tool", "content": result_str})
        continue

    return AgentResult(response_text="", tool_calls=tool_calls_log, model=model, provider="ollama")


# ── Provider registry ─────────────────────────────────────────────────────────

_OPENAI_COMPATIBLE_PROVIDERS = {
    "openai": {"key_env": "OPENAI_API_KEY"},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY"},
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "key_env": "DEEPSEEK_API_KEY"},
    "together": {"base_url": "https://api.together.xyz/v1", "key_env": "TOGETHER_API_KEY"},
    "mistral": {"base_url": "https://api.mistral.ai/v1", "key_env": "MISTRAL_API_KEY"},
    "cerebras": {"base_url": "https://api.cerebras.ai/v1", "key_env": "CEREBRAS_API_KEY"},
    "fireworks": {"base_url": "https://api.fireworks.ai/inference/v1", "key_env": "FIREWORKS_API_KEY"},
    "perplexity": {"base_url": "https://api.perplexity.ai", "key_env": "PERPLEXITY_API_KEY"},
}


def run_agent(
    provider: str,
    model: str,
    system: str,
    task: str,
    tools: List[Dict],
    dispatch: Callable[[str, Dict], str],
) -> AgentResult:
    """Run an agent with tool-calling on ANY supported LLM provider.

    Args:
        provider: LLM provider name (anthropic, openai, groq, ollama, etc.)
        model: Model ID for the provider
        system: System prompt
        task: User task/message
        tools: List of tool definitions (Anthropic format — auto-converted for others)
        dispatch: Function(tool_name, tool_input) -> result_string

    Returns:
        AgentResult with response, tool call log, model, and provider.
    """
    provider = provider.lower()

    if provider == "anthropic":
        return _run_anthropic(model, system, task, tools, dispatch)

    if provider == "ollama":
        return _run_ollama(model, system, task, tools, dispatch)

    if provider in _OPENAI_COMPATIBLE_PROVIDERS:
        cfg = _OPENAI_COMPATIBLE_PROVIDERS[provider]
        return _run_openai_compatible(
            provider=provider, model=model, system=system, task=task,
            tools=tools, dispatch=dispatch,
            base_url=cfg.get("base_url"),
            api_key=os.environ.get(cfg.get("key_env", "")),
        )

    raise ValueError(
        f"Unknown provider: {provider}. "
        f"Supported: anthropic, ollama, {', '.join(_OPENAI_COMPATIBLE_PROVIDERS.keys())}"
    )
