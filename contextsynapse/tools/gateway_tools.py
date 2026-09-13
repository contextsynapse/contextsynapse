"""
Gateway Tools — Agent reporting and cost observability.

These tools let agents:
    1. Report external tool usage (so boundary has full visibility)
    2. Report token consumption (for cost tracking)
    3. Query their own usage/cost
    4. View boundary policies

5 tools: report_external, report_tokens, my_usage, boundary_costs, get_policy
"""

from __future__ import annotations

import json
import logging

from .registry import ToolContext, ToolParam, tool

logger = logging.getLogger(__name__)


@tool(
    "report_external", "gateway",
    "Report an external tool call you made outside of AIContextDB. "
    "This gives the boundary visibility into ALL your activity — "
    "web searches, file reads, API calls, etc. Call this after using any external tool.",
    params=[
        ToolParam("tool_name", "string", "Name of the external tool (e.g., 'web_search', 'file_read', 'curl')"),
        ToolParam("input_summary", "string", "What you sent to the tool (brief)", required=False, default=""),
        ToolParam("output_summary", "string", "What the tool returned (brief)", required=False, default=""),
        ToolParam("duration_ms", "integer", "How long the call took in milliseconds", required=False, default=0),
    ],
)
def _report_external(ctx: ToolContext, tool_name: str, input_summary: str = "",
                     output_summary: str = "", duration_ms: int = 0) -> str:
    try:
        from contextsynapse.gateway import get_gateway
        return get_gateway().observe_external(
            ctx=ctx,
            tool_name=tool_name,
            input_data=input_summary,
            output_data=output_summary,
            duration_ms=duration_ms,
        )
    except Exception as e:
        return f"Error reporting external tool: {e}"


@tool(
    "report_tokens", "gateway",
    "Report token usage from an LLM call you made. "
    "This helps track costs across the boundary. "
    "Call this after making LLM API calls.",
    params=[
        ToolParam("model", "string", "Model name (e.g., 'gpt-4o', 'claude-sonnet-4-5')"),
        ToolParam("input_tokens", "integer", "Number of input tokens"),
        ToolParam("output_tokens", "integer", "Number of output tokens"),
        ToolParam("operation", "string", "What the call was for", required=False, default=""),
    ],
)
def _report_tokens(ctx: ToolContext, model: str, input_tokens: int = 0,
                   output_tokens: int = 0, operation: str = "") -> str:
    try:
        from contextsynapse.gateway import get_gateway
        cost = get_gateway().record_tokens(
            ctx=ctx,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model,
            operation=operation,
        )
        total = input_tokens + output_tokens
        return f"Recorded {total:,} tokens ({model}), estimated cost: ${cost:.4f}"
    except Exception as e:
        return f"Error reporting tokens: {e}"


@tool(
    "my_usage", "gateway",
    "See your tool usage and cost report for this boundary. "
    "Shows which tools you've called, how many tokens you've used, "
    "and estimated costs.",
)
def _my_usage(ctx: ToolContext) -> str:
    try:
        from contextsynapse.gateway import get_gateway
        namespace = ctx.conn.namespace if ctx.conn else ""
        report = get_gateway().get_agent_report(namespace, ctx.agent_id or "unknown")

        lines = [f"Usage Report for {ctx.agent_name} ({report['period']})"]
        s = report["summary"]
        lines.append(f"  Tool calls: {s['total_tool_calls']} ({s['total_external_calls']} external)")
        lines.append(f"  Tokens: {s['total_input_tokens']:,} in / {s['total_output_tokens']:,} out")
        lines.append(f"  Estimated cost: ${s['total_cost_usd']:.4f}")

        if report["top_tools"]:
            lines.append("\nTop tools:")
            for t in report["top_tools"][:5]:
                fail = f" ({t['failures']} failures)" if t["failures"] else ""
                lines.append(f"  {t['tool']}: {t['calls']} calls, avg {t['avg_ms']}ms{fail}")

        if report["by_model"]:
            lines.append("\nBy model:")
            for m in report["by_model"]:
                lines.append(f"  {m['model']}: {m['input_tokens']:,}+{m['output_tokens']:,} tokens, ${m['cost_usd']:.4f}")

        return "\n".join(lines)
    except Exception as e:
        return f"Error getting usage: {e}"


@tool(
    "boundary_costs", "gateway",
    "Get the cost report for this entire boundary — "
    "all agents, total spend, and savings recommendations.",
)
def _boundary_costs(ctx: ToolContext) -> str:
    try:
        from contextsynapse.gateway import get_gateway
        namespace = ctx.conn.namespace if ctx.conn else ""
        report = get_gateway().get_boundary_report(namespace)

        t = report["totals"]
        lines = [
            f"Boundary Cost Report: {namespace} ({report['period']})",
            f"  Total cost: ${t['cost_usd']:.4f}",
            f"  Total tokens: {t['tokens']:,}",
            f"  Total tool calls: {t['tool_calls']}",
            f"  Active agents: {t['agents']}",
        ]

        if report["agents"]:
            lines.append("\nPer-agent breakdown:")
            for a in report["agents"]:
                lines.append(
                    f"  {a['agent_id']}: ${a['cost_usd']:.4f} "
                    f"({a['tool_calls']} calls, {a['external_calls']} external, "
                    f"{a['tokens']:,} tokens)"
                )

        if report["external_tools"]:
            lines.append("\nExternal tool usage:")
            for e in report["external_tools"][:5]:
                lines.append(f"  {e['tool']}: {e['calls']} calls by {e['agent_id']}")

        if report["savings_tips"]:
            lines.append("\nSavings tips:")
            for tip in report["savings_tips"]:
                lines.append(f"  - {tip}")

        return "\n".join(lines)
    except Exception as e:
        return f"Error getting boundary costs: {e}"


@tool(
    "get_tool_policy", "gateway",
    "View the tool access policy for this boundary — "
    "what tools are allowed/denied, rate limits, etc.",
)
def _get_tool_policy(ctx: ToolContext) -> str:
    try:
        from contextsynapse.gateway import get_gateway
        namespace = ctx.conn.namespace if ctx.conn else ""
        policy = get_gateway().get_policy(namespace)

        lines = [f"Tool Policy for: {namespace}"]
        lines.append(f"  Default: {'Allow' if policy['default_allow'] else 'Deny'}")
        lines.append(f"  External tools: {'Allowed' if policy['allow_external'] else 'Blocked'}")
        lines.append(f"  Rate limit: {policy['max_tool_calls_per_minute']}/min")
        if policy["max_cost_per_session"] > 0:
            lines.append(f"  Cost limit: ${policy['max_cost_per_session']:.2f}/session")

        if policy["allow_tools"]:
            lines.append(f"\n  Allowed tools: {', '.join(policy['allow_tools'])}")
        if policy["deny_tools"]:
            lines.append(f"  Denied tools: {', '.join(policy['deny_tools'])}")
        if policy["allow_categories"]:
            lines.append(f"  Allowed categories: {', '.join(policy['allow_categories'])}")
        if policy["deny_categories"]:
            lines.append(f"  Denied categories: {', '.join(policy['deny_categories'])}")

        return "\n".join(lines)
    except Exception as e:
        return f"Error getting policy: {e}"
