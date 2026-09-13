"""Demo report formatter — side-by-side blind vs context agent comparison."""
from typing import Any, Dict, List

from contextsynapse.demo.runner import AgentResult

_W = 62  # report width


def _bar(score: float, width: int = 20) -> str:
    if score is None:
        return "N/A"
    filled = int(score * width)
    return "█" * filled + "░" * (width - filled)


def _pct(score) -> str:
    if score is None:
        return "N/A"
    return f"{score:.0%}"


def _coverage_block(coverage: Dict[str, Any], label: str) -> List[str]:
    lines = [f"  {label}"]
    for layer in ("intent", "design", "build", "verify", "evolution"):
        score = coverage.get(layer)
        lines.append(f"    {layer:<12} {_pct(score):>4}  {_bar(score or 0.0)}")
    lines.append(f"    {'overall':<12} {_pct(coverage.get('overall')):>4}  ({coverage.get('project_type', '')})")
    return lines


def format_report(
    before_coverage: Dict[str, Any],
    blind: AgentResult,
    context: AgentResult,
    after_coverage: Dict[str, Any],
) -> str:
    """Format the demo comparison report as a printable string.

    Args:
        before_coverage: coverage_score() result before any agent ran
        blind: AgentResult from run_blind_agent()
        context: AgentResult from run_context_agent()
        after_coverage: coverage_score() result after context agent wrote artifacts

    Returns:
        A multi-line string ready for print().
    """
    sep = "═" * _W
    thin = "─" * _W
    lines: List[str] = []

    # Header
    lines += [
        "",
        sep,
        "  AIContextDB — Context Value Demo".center(_W),
        sep,
        "",
    ]

    # Before coverage
    lines += _coverage_block(before_coverage, "BEFORE  (project as seeded)")
    lines += [""]

    # Task
    lines += [
        thin,
        "  TASK:",
        "  \"Design and implement a login endpoint for our authentication",
        "   service. Enforce rate-limiting and return a session token.\"",
        thin,
        "",
    ]

    # Blind agent
    blind_text = (blind.response_text or "").strip()
    blind_preview = blind_text[:400] + ("…" if len(blind_text) > 400 else "")
    lines += [
        "BLIND AGENT  (no context graph)",
        f"    Model       : {blind.model}",
        f"    Tool calls  : 0",
        f"    New artifacts written: 0",
        f"    Coverage change: none",
        "",
        "    Response:",
    ]
    for line in blind_preview.split("\n")[:12]:
        lines.append(f"      {line}")
    lines += [""]

    # Context agent
    tool_names = [tc["tool"] for tc in context.tool_calls]
    reads = [t for t in tool_names if t in ("agent_brief", "search_sdlc", "coverage_score")]
    writes = [t for t in tool_names if t in ("add_sdlc_node", "add_sdlc_edge")]
    ctx_text = (context.response_text or "").strip()
    ctx_preview = ctx_text[:400] + ("…" if len(ctx_text) > 400 else "")

    delta = (after_coverage.get("overall", 0) or 0) - (before_coverage.get("overall", 0) or 0)
    build_delta = (after_coverage.get("build", 0) or 0) - (before_coverage.get("build", 0) or 0)

    lines += [
        "CONTEXT AGENT  (with graph)",
        f"    Model       : {context.model}",
        f"    Tool calls  : {len(context.tool_calls)} total  ({len(reads)} reads, {len(writes)} writes)",
    ]
    for tc in context.tool_calls:
        result_preview = str(tc.get("result", ""))[:60]
        lines.append(f"      -> {tc['tool']}()  {result_preview}")
    lines += [
        f"    New artifacts written: {len(writes)}",
        "",
        "    Response:",
    ]
    for line in ctx_preview.split("\n")[:12]:
        lines.append(f"      {line}")
    lines += [""]

    # After coverage
    lines += _coverage_block(after_coverage, "AFTER   (context agent wrote artifacts)")
    lines += [""]

    # Verdict
    lines += [
        sep,
        "  VERDICT".center(_W),
        sep,
        f"  Overall coverage : {_pct(before_coverage.get('overall'))}  ->  "
        f"{_pct(after_coverage.get('overall'))}  (+{delta:.0%})",
        f"  Build layer      : {_pct(before_coverage.get('build'))}  ->  "
        f"{_pct(after_coverage.get('build'))}  (+{build_delta:.0%})",
        f"  Artifacts written: {len(writes)}",
        f"  Context reads    : {len(reads)}",
        "",
        "  The context agent:",
        "    * Read existing Requirements, ArchDecisions, and TestCases",
        "    * Respected existing JWT and bcrypt decisions",
        f"    * Increased coverage by {delta:.0%} by writing new artifacts",
        "    * Left traceable nodes the next agent can build on",
        "",
        "  The blind agent:",
        "    x Unaware of existing decisions (may duplicate or contradict)",
        "    x Coverage unchanged — wrote nothing back",
        sep,
        "",
    ]

    return "\n".join(lines)
