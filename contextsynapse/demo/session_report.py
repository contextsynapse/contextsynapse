"""Session demo report — multi-agent progressive context accumulation."""
from __future__ import annotations

from typing import Any, Dict, List, TYPE_CHECKING

if TYPE_CHECKING:
    from .session_demo import SessionDemoResult

_W = 66


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
    lines.append(f"    {'overall':<12} {_pct(coverage.get('overall')):>4}")
    return lines


def _agent_block(name: str, result, label: str) -> List[str]:
    tool_names = [tc["tool"] for tc in result.tool_calls]
    reads = [t for t in tool_names if t in ("agent_brief", "search_sdlc", "coverage_score")]
    writes = [t for t in tool_names if t in ("add_sdlc_node", "add_sdlc_edge")]
    text = (result.response_text or "").strip()
    preview = text[:300] + ("…" if len(text) > 300 else "")

    lines = [
        f"  {label}",
        f"    Model       : {result.model}",
        f"    Tool calls  : {len(result.tool_calls)} total ({len(reads)} reads, {len(writes)} writes)",
    ]
    for tc in result.tool_calls:
        rp = str(tc.get("result", ""))[:50]
        lines.append(f"      -> {tc['tool']}()  {rp}")
    lines += ["", "    Response:"]
    for line in preview.split("\n")[:8]:
        lines.append(f"      {line}")
    lines.append("")
    return lines


def format_session_report(result: "SessionDemoResult") -> str:
    """Format the multi-agent session demo report.

    Args:
        result: SessionDemoResult from run_session_demo()

    Returns:
        Multi-line string ready for print().
    """
    sep = "═" * _W
    thin = "─" * _W
    lines: List[str] = []

    lines += [
        "",
        sep,
        "  AIContextDB — Multi-Agent Session Demo".center(_W),
        sep,
        "",
    ]

    # Phase 1
    lines += [
        thin,
        f"  PHASE 1 — Atomic Context",
        thin,
    ]
    lines += _coverage_block(result.before_coverage, "Initial coverage (project seeded)")
    lines += [""]

    # Phase 2
    lines += [
        thin,
        f"  PHASE 2 — Session Context",
        thin,
        f"    Session ID  : {result.session_id}",
        "",
    ]

    # Phase 3
    lines += [
        thin,
        f"  PHASE 3 — Agents Join",
        thin,
        "    architect-agent  → write access",
        "    dev-agent        → write access",
        "",
    ]

    # Phase 4
    lines += [
        thin,
        f"  PHASE 4 — Agents Execute",
        thin,
        "",
    ]

    lines += _agent_block("architect", result.architect_result, "ARCHITECT AGENT")
    lines += _coverage_block(result.mid_coverage, "Coverage after architect")
    lines += [""]

    lines += _agent_block("developer", result.dev_result, "DEVELOPER AGENT")
    lines += _coverage_block(result.after_coverage, "Coverage after developer")
    lines += [""]

    # Verdict
    delta = (result.after_coverage.get("overall", 0) or 0) - (result.before_coverage.get("overall", 0) or 0)
    lines += [
        sep,
        "  VERDICT".center(_W),
        sep,
        f"  Coverage  : {_pct(result.before_coverage.get('overall'))}  →  "
        f"{_pct(result.mid_coverage.get('overall'))}  →  "
        f"{_pct(result.after_coverage.get('overall'))}  (+{delta:.0%})",
        f"  Agents    : 2 (architect + developer)",
        f"  Total time: {result.elapsed_ms}ms",
        "",
        "  Progressive context accumulation:",
        "    1. Atomic SDLC graph seeded via ingestion pipeline",
        "    2. Session created — shared workspace for agents",
        "    3. Agents granted write access",
        "    4. Architect enriched design → Developer built on it",
        "    5. Each agent saw the previous agent's work",
        sep,
        "",
    ]

    return "\n".join(lines)
