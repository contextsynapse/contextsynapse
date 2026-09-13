"""
Benchmark Runner
==================
Orchestrates baseline vs shared context benchmark runs.

Usage:
    runner = BenchmarkRunner(model="gpt-4o-mini")
    results = runner.run()
    print(BenchmarkReporter.generate_text_report(results))
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .workloads import Workload, get_all_workloads
from .agents import BaselineAgents, SharedAgents
from .token_counter import TokenCountingClient
from .reporter import BenchmarkReporter

logger = logging.getLogger(__name__)


class BenchmarkRunner:
    """Orchestrates cost savings benchmark."""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        provider: str = "",
        workloads: Optional[List[str]] = None,
        verbose: bool = False,
    ):
        """
        Args:
            model: LLM model to use for benchmark.
            provider: LLM provider (auto-detected if empty).
            workloads: List of workload names to run (None = all).
            verbose: Print progress.
        """
        self.model = model
        self.provider = provider
        self.verbose = verbose
        self._workload_names = workloads

    def run(self) -> Dict[str, Any]:
        """Run the full benchmark and return results."""
        workloads = get_all_workloads()
        if self._workload_names:
            workloads = [w for w in workloads if w.name in self._workload_names]

        if not workloads:
            return {"error": "No workloads to run"}

        # Create LLM client
        llm = self._create_llm()

        workload_results = []
        for workload in workloads:
            if self.verbose:
                print(f"\n[Benchmark] Running workload: {workload.name} ({workload.difficulty})")

            result = self._run_workload(workload, llm)
            workload_results.append(result)

        # Compute aggregates
        aggregate = self._compute_aggregate(workload_results)
        one_liner = BenchmarkReporter.generate_one_liner({
            "aggregate": aggregate,
            "model": self.model,
            "workloads_count": len(workloads),
        })

        return {
            "model": self.model,
            "provider": self.provider,
            "workloads_count": len(workloads),
            "workload_results": workload_results,
            "aggregate": aggregate,
            "one_liner": one_liner,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    def _run_workload(self, workload: Workload, llm_base) -> Dict[str, Any]:
        """Run baseline + shared for a single workload."""

        # --- Baseline run ---
        if self.verbose:
            print(f"  [Baseline] 3 independent agents...")

        baseline_llm = TokenCountingClient(llm_base, agent_id="baseline", model_name=self.model)
        baseline_agents = BaselineAgents(baseline_llm)

        baseline_start = time.monotonic()
        baseline_output = baseline_agents.run_all(workload.text)
        baseline_wall = int((time.monotonic() - baseline_start) * 1000)

        baseline_totals = baseline_llm.get_total()

        # --- Shared context run ---
        if self.verbose:
            print(f"  [Shared] 3 agents with AIContextDB...")

        shared_llm = TokenCountingClient(llm_base, agent_id="shared", model_name=self.model)

        # Create a fresh graph connection for shared mode
        conn = self._create_connection(f"benchmark_{workload.name.lower().replace(' ', '_')}")
        shared_agents = SharedAgents(shared_llm, conn=conn)

        shared_start = time.monotonic()
        shared_output = shared_agents.run_all(workload.text)
        shared_wall = int((time.monotonic() - shared_start) * 1000)

        shared_totals = shared_llm.get_total()

        # --- Compute per-agent breakdown ---
        baseline_per_agent = {
            aid: {
                "calls": u.total_calls,
                "tokens": u.total_tokens,
                "cost": round(u.total_cost_usd, 6),
            }
            for aid, u in baseline_llm.get_all_usage().items()
        }
        shared_per_agent = {
            aid: {
                "calls": u.total_calls,
                "tokens": u.total_tokens,
                "cost": round(u.total_cost_usd, 6),
            }
            for aid, u in shared_llm.get_all_usage().items()
        }

        # --- Duplicate analysis ---
        baseline_entities = set()
        shared_entities = set()
        for aid, u in baseline_llm.get_all_usage().items():
            for call in u.calls:
                if call.operation in ("extract", "extract_duplicate"):
                    baseline_entities.add(f"{aid}:{call.operation}")
        extraction_calls_baseline = sum(
            1 for u in baseline_llm.get_all_usage().values()
            for c in u.calls if c.operation in ("extract", "extract_duplicate")
        )
        extraction_calls_shared = sum(
            1 for u in shared_llm.get_all_usage().values()
            for c in u.calls if c.operation == "extract"
        )

        # Graph stats
        graph_nodes = 0
        graph_edges = 0
        if conn:
            try:
                graph_nodes = len(conn.get_nodes())
                graph_edges = len(conn.get_edges())
            except Exception:
                pass

        result = {
            "workload_name": workload.name,
            "difficulty": workload.difficulty,
            "domain": workload.domain,
            "document_length": len(workload.text),
            "baseline": baseline_totals,
            "shared": shared_totals,
            "baseline_wall_ms": baseline_wall,
            "shared_wall_ms": shared_wall,
            "baseline_per_agent": baseline_per_agent,
            "shared_per_agent": shared_per_agent,
            "extraction_calls": {
                "baseline": extraction_calls_baseline,
                "shared": extraction_calls_shared,
                "duplicate_avoided": extraction_calls_baseline - extraction_calls_shared,
            },
            "graph_stats": {
                "nodes": graph_nodes,
                "edges": graph_edges,
            },
        }

        if self.verbose:
            b = baseline_totals
            s = shared_totals
            savings = ((b["total_tokens"] - s["total_tokens"]) / b["total_tokens"] * 100) if b["total_tokens"] else 0
            print(f"  Result: {b['total_tokens']:,} -> {s['total_tokens']:,} tokens ({savings:.0f}% savings)")
            print(f"          {b['total_calls']} -> {s['total_calls']} API calls")
            print(f"          ${b['total_cost_usd']:.4f} -> ${s['total_cost_usd']:.4f}")

        return result

    def _compute_aggregate(self, workload_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Compute aggregate statistics across all workloads."""
        b_tokens = sum(r["baseline"]["total_tokens"] for r in workload_results)
        s_tokens = sum(r["shared"]["total_tokens"] for r in workload_results)
        b_cost = sum(r["baseline"]["total_cost_usd"] for r in workload_results)
        s_cost = sum(r["shared"]["total_cost_usd"] for r in workload_results)
        b_calls = sum(r["baseline"]["total_calls"] for r in workload_results)
        s_calls = sum(r["shared"]["total_calls"] for r in workload_results)

        token_savings = ((b_tokens - s_tokens) / b_tokens * 100) if b_tokens else 0
        cost_savings = ((b_cost - s_cost) / b_cost * 100) if b_cost else 0
        call_savings = ((b_calls - s_calls) / b_calls * 100) if b_calls else 0

        return {
            "baseline_total_tokens": b_tokens,
            "shared_total_tokens": s_tokens,
            "token_savings_pct": round(token_savings, 1),
            "baseline_total_cost": round(b_cost, 6),
            "shared_total_cost": round(s_cost, 6),
            "cost_savings_pct": round(cost_savings, 1),
            "money_saved": round(b_cost - s_cost, 6),
            "baseline_total_calls": b_calls,
            "shared_total_calls": s_calls,
            "call_savings_pct": round(call_savings, 1),
            "total_duplicate_extractions_avoided": sum(
                r["extraction_calls"]["duplicate_avoided"] for r in workload_results
            ),
        }

    def _create_llm(self):
        """Create the base LLM client."""
        # Load .env if keys not in environment
        if not os.environ.get("OPENAI_API_KEY") and not os.environ.get("GROQ_API_KEY"):
            env_path = Path(__file__).resolve().parents[2] / ".env"
            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    key, _, value = line.partition("=")
                    key, value = key.strip(), value.strip()
                    if key and key not in os.environ:
                        os.environ[key] = value

        from contextsynapse.llm.client import get_llm_client
        provider = self.provider
        if not provider:
            # Auto-detect from model name
            if "gpt" in self.model or "o3" in self.model or "o4" in self.model:
                provider = "openai"
            elif "claude" in self.model:
                provider = "anthropic"
            elif "llama" in self.model or "deepseek" in self.model:
                provider = "groq"
            else:
                provider = None  # let get_llm_client auto-detect

        if provider:
            return get_llm_client(provider=provider, model=self.model)
        return get_llm_client(model=self.model)

    def _create_connection(self, namespace: str):
        """Create a fresh graph connection for shared mode."""
        try:
            from contextsynapse.adapters._base import AIContextDBConnection
            return AIContextDBConnection(namespace=namespace)
        except Exception as e:
            logger.warning("Could not create graph connection: %s", e)
            return None
