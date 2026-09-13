"""
Benchmark Reporter
====================
Generates comparison reports from benchmark results.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List


class BenchmarkReporter:
    """Generate reports from benchmark results."""

    @staticmethod
    def generate_text_report(results: Dict[str, Any]) -> str:
        """Generate a human-readable text report."""
        lines = []
        lines.append("=" * 70)
        lines.append("  AIContextDB Cost Savings Benchmark Results")
        lines.append("=" * 70)
        lines.append("")

        model = results.get("model", "unknown")
        lines.append(f"Model: {model}")
        lines.append(f"Workloads tested: {results.get('workloads_count', 0)}")
        lines.append("")

        # Per-workload results
        for wr in results.get("workload_results", []):
            lines.append(f"--- {wr['workload_name']} ({wr['difficulty']}) ---")
            b = wr["baseline"]
            s = wr["shared"]

            lines.append(f"  {'Metric':<30} {'Baseline':>12} {'Shared':>12} {'Savings':>12}")
            lines.append(f"  {'-'*30} {'-'*12} {'-'*12} {'-'*12}")

            # Tokens
            b_tokens = b["total_tokens"]
            s_tokens = s["total_tokens"]
            savings_pct = ((b_tokens - s_tokens) / b_tokens * 100) if b_tokens else 0
            lines.append(f"  {'Total tokens':<30} {b_tokens:>12,} {s_tokens:>12,} {savings_pct:>11.1f}%")

            # Input tokens
            b_in = b["total_prompt_tokens"]
            s_in = s["total_prompt_tokens"]
            in_pct = ((b_in - s_in) / b_in * 100) if b_in else 0
            lines.append(f"  {'Input tokens':<30} {b_in:>12,} {s_in:>12,} {in_pct:>11.1f}%")

            # Output tokens
            b_out = b["total_completion_tokens"]
            s_out = s["total_completion_tokens"]
            out_pct = ((b_out - s_out) / b_out * 100) if b_out else 0
            lines.append(f"  {'Output tokens':<30} {b_out:>12,} {s_out:>12,} {out_pct:>11.1f}%")

            # API calls
            b_calls = b["total_calls"]
            s_calls = s["total_calls"]
            call_pct = ((b_calls - s_calls) / b_calls * 100) if b_calls else 0
            lines.append(f"  {'LLM API calls':<30} {b_calls:>12} {s_calls:>12} {call_pct:>11.1f}%")

            # Cost
            b_cost = b["total_cost_usd"]
            s_cost = s["total_cost_usd"]
            cost_pct = ((b_cost - s_cost) / b_cost * 100) if b_cost else 0
            lines.append(f"  {'Estimated cost':<30} ${b_cost:>11.4f} ${s_cost:>11.4f} {cost_pct:>11.1f}%")

            # Duration
            b_dur = b["total_duration_ms"]
            s_dur = s["total_duration_ms"]
            dur_pct = ((b_dur - s_dur) / b_dur * 100) if b_dur else 0
            lines.append(f"  {'Duration (ms)':<30} {b_dur:>12,} {s_dur:>12,} {dur_pct:>11.1f}%")

            lines.append("")

        # Aggregates
        agg = results.get("aggregate", {})
        lines.append("=" * 70)
        lines.append("  AGGREGATE RESULTS")
        lines.append("=" * 70)
        lines.append("")
        lines.append(f"  Total token savings:  {agg.get('token_savings_pct', 0):.1f}%")
        lines.append(f"  Total cost savings:   {agg.get('cost_savings_pct', 0):.1f}%")
        lines.append(f"  Total API call reduction: {agg.get('call_savings_pct', 0):.1f}%")
        lines.append(f"  Baseline total cost:  ${agg.get('baseline_total_cost', 0):.4f}")
        lines.append(f"  Shared total cost:    ${agg.get('shared_total_cost', 0):.4f}")
        lines.append(f"  Money saved:          ${agg.get('money_saved', 0):.4f}")
        lines.append("")

        # One-liner
        one_liner = results.get("one_liner", "")
        if one_liner:
            lines.append(f"  >> {one_liner}")
            lines.append("")

        lines.append("=" * 70)
        return "\n".join(lines)

    @staticmethod
    def generate_json_report(results: Dict[str, Any]) -> str:
        """Generate machine-readable JSON report."""
        return json.dumps(results, indent=2, default=str)

    @staticmethod
    def generate_one_liner(results: Dict[str, Any]) -> str:
        """Generate the marketing one-liner."""
        agg = results.get("aggregate", {})
        token_pct = agg.get("token_savings_pct", 0)
        cost_pct = agg.get("cost_savings_pct", 0)
        money = agg.get("money_saved", 0)
        model = results.get("model", "")
        workloads = results.get("workloads_count", 0)

        return (
            f"AIContextDB reduced token usage by {token_pct:.0f}% and API costs "
            f"by ${money:.4f} per task across 3 agents on {workloads} workloads ({model})."
        )

    @staticmethod
    def generate_markdown(results: Dict[str, Any]) -> str:
        """Generate markdown for blog/docs."""
        agg = results.get("aggregate", {})
        lines = [
            "## Cost Savings Benchmark Results",
            "",
            f"**Model tested:** {results.get('model', 'N/A')}",
            f"**Workloads:** {results.get('workloads_count', 0)}",
            "",
            "### Aggregate Savings",
            "",
            "| Metric | Baseline | AIContextDB | Savings |",
            "|--------|----------|-------------|---------|",
            f"| Total tokens | {agg.get('baseline_total_tokens', 0):,} | {agg.get('shared_total_tokens', 0):,} | **{agg.get('token_savings_pct', 0):.0f}%** |",
            f"| API calls | {agg.get('baseline_total_calls', 0)} | {agg.get('shared_total_calls', 0)} | **{agg.get('call_savings_pct', 0):.0f}%** |",
            f"| Estimated cost | ${agg.get('baseline_total_cost', 0):.4f} | ${agg.get('shared_total_cost', 0):.4f} | **{agg.get('cost_savings_pct', 0):.0f}%** |",
            "",
            "### How It Works",
            "",
            "**Baseline (no sharing):** Each of the 3 agents independently extracts entities from the document, ",
            "duplicating LLM calls. The analyst and summarizer both re-extract what the extractor already found.",
            "",
            "**With AIContextDB:** Agent 1 extracts entities and stores them in the shared graph. ",
            "Agent 2 queries the graph (free, local) instead of calling the LLM. ",
            "Agent 3 gets scoped, token-budgeted context from ContextHub instead of re-reading the full document.",
            "",
            f"> {results.get('one_liner', '')}",
        ]
        return "\n".join(lines)
