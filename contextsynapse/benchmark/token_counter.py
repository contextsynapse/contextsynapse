"""
Token-Counting LLM Wrapper
============================
Wraps LLMClient to intercept every call and record:
    - Input tokens (estimated from prompt length)
    - Output tokens (estimated from response length)
    - Cost (from model pricing)
    - Call count
    - Duration
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class LLMCallRecord:
    """A single LLM call record."""
    agent_id: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    duration_ms: int
    operation: str  # "extract", "analyze", "summarize"
    prompt_preview: str = ""  # first 100 chars


@dataclass
class AgentUsage:
    """Aggregate usage for one agent."""
    agent_id: str
    total_calls: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    total_duration_ms: int = 0
    calls: List[LLMCallRecord] = field(default_factory=list)


# Rough token estimation: ~4 chars per token for English text
def estimate_tokens(text: str) -> int:
    """Estimate token count from text length."""
    return max(1, len(text) // 4)


# Model costs per 1M tokens (same as gateway/cost_tracker.py)
MODEL_COSTS = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4.1": {"input": 2.00, "output": 8.00},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "claude-opus-4": {"input": 15.00, "output": 75.00},
    "claude-haiku-3-5": {"input": 0.80, "output": 4.00},
    "gpt-oss-120b": {"input": 0.00, "output": 0.00},
    "llama-3.3-70b": {"input": 0.59, "output": 0.79},
    "deepseek-r1": {"input": 0.55, "output": 2.19},
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost in USD."""
    costs = MODEL_COSTS.get(model)
    if not costs:
        for key in MODEL_COSTS:
            if key in model or model in key:
                costs = MODEL_COSTS[key]
                break
    if not costs:
        costs = {"input": 1.0, "output": 3.0}

    return round(
        (input_tokens / 1_000_000) * costs["input"]
        + (output_tokens / 1_000_000) * costs["output"],
        6,
    )


class TokenCountingClient:
    """Wraps LLMClient and counts every token."""

    def __init__(self, llm_client, agent_id: str = "benchmark", model_name: str = ""):
        self._client = llm_client
        self.agent_id = agent_id
        self.model_name = model_name or getattr(llm_client, "model", "unknown")
        self._usage: Dict[str, AgentUsage] = {}

    def generate(self, prompt: str, system: str = "", max_tokens: int = 4096,
                 operation: str = "", agent_id: str = "") -> str:
        """Call LLM and record token usage."""
        aid = agent_id or self.agent_id
        full_input = (system + "\n" + prompt) if system else prompt
        input_tokens = estimate_tokens(full_input)

        start = time.monotonic_ns()
        result = self._client.generate(prompt, system=system, max_tokens=max_tokens)
        elapsed_ms = (time.monotonic_ns() - start) // 1_000_000

        output_tokens = estimate_tokens(result)
        total = input_tokens + output_tokens
        cost = estimate_cost(self.model_name, input_tokens, output_tokens)

        record = LLMCallRecord(
            agent_id=aid,
            model=self.model_name,
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            total_tokens=total,
            cost_usd=cost,
            duration_ms=elapsed_ms,
            operation=operation,
            prompt_preview=prompt[:100],
        )

        if aid not in self._usage:
            self._usage[aid] = AgentUsage(agent_id=aid)
        usage = self._usage[aid]
        usage.total_calls += 1
        usage.total_prompt_tokens += input_tokens
        usage.total_completion_tokens += output_tokens
        usage.total_tokens += total
        usage.total_cost_usd += cost
        usage.total_duration_ms += elapsed_ms
        usage.calls.append(record)

        logger.debug(
            "LLM call: agent=%s op=%s tokens=%d cost=$%.4f",
            aid, operation, total, cost,
        )
        return result

    def generate_json(self, prompt: str, system: str = "", max_tokens: int = 4096,
                      operation: str = "", agent_id: str = "") -> Dict[str, Any]:
        """Call LLM for JSON and record token usage."""
        aid = agent_id or self.agent_id
        full_input = (system + "\n" + prompt) if system else prompt
        input_tokens = estimate_tokens(full_input)

        start = time.monotonic_ns()
        result = self._client.generate_json(prompt, system=system, max_tokens=max_tokens)
        elapsed_ms = (time.monotonic_ns() - start) // 1_000_000

        import json
        result_text = json.dumps(result)
        output_tokens = estimate_tokens(result_text)
        total = input_tokens + output_tokens
        cost = estimate_cost(self.model_name, input_tokens, output_tokens)

        record = LLMCallRecord(
            agent_id=aid,
            model=self.model_name,
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            total_tokens=total,
            cost_usd=cost,
            duration_ms=elapsed_ms,
            operation=operation,
            prompt_preview=prompt[:100],
        )

        if aid not in self._usage:
            self._usage[aid] = AgentUsage(agent_id=aid)
        usage = self._usage[aid]
        usage.total_calls += 1
        usage.total_prompt_tokens += input_tokens
        usage.total_completion_tokens += output_tokens
        usage.total_tokens += total
        usage.total_cost_usd += cost
        usage.total_duration_ms += elapsed_ms
        usage.calls.append(record)

        return result

    def get_usage(self, agent_id: str = "") -> AgentUsage:
        """Get usage for a specific agent."""
        aid = agent_id or self.agent_id
        return self._usage.get(aid, AgentUsage(agent_id=aid))

    def get_all_usage(self) -> Dict[str, AgentUsage]:
        """Get usage for all agents."""
        return dict(self._usage)

    def get_total(self) -> Dict[str, Any]:
        """Get aggregate totals across all agents."""
        total_calls = sum(u.total_calls for u in self._usage.values())
        total_prompt = sum(u.total_prompt_tokens for u in self._usage.values())
        total_completion = sum(u.total_completion_tokens for u in self._usage.values())
        total_cost = sum(u.total_cost_usd for u in self._usage.values())
        total_duration = sum(u.total_duration_ms for u in self._usage.values())
        return {
            "total_calls": total_calls,
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "total_tokens": total_prompt + total_completion,
            "total_cost_usd": round(total_cost, 6),
            "total_duration_ms": total_duration,
            "agents": len(self._usage),
        }

    def reset(self):
        """Reset all usage counters."""
        self._usage.clear()
