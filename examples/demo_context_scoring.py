#!/usr/bin/env python3
"""
Context Quality Scoring & Monitoring Demo
===========================================
Shows how ContextSynapse scores, monitors, and filters context:

  1. Node Quality Scoring   -- every node scored 0-100 at ingest (<1ms)
  2. Freshness Detection    -- extract event dates, classify staleness
  3. Quality Filtering      -- low-quality nodes filtered before delivery
  4. Context Usage Tracking -- measure if delivered context was useful
  5. Promotion Scoring      -- high-value memories promoted to main graph

Not just storing context -- measuring whether it's any good.

    pip install -e "."
    python examples/demo_context_scoring.py
"""

from __future__ import annotations
import time
import uuid
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

console = Console()


def section(title):
    console.print()
    console.rule(f"[bold cyan]{title}")
    console.print()


def main():
    console.print(Panel.fit(
        "[bold white]Context Quality Scoring & Monitoring[/bold white]\n"
        "[dim]Not just storing context -- measuring whether it's any good[/dim]",
        border_style="cyan",
    ))

    t0 = time.time()

    # =====================================================================
    section("1. Node Quality Scoring (ingest gate)")
    # =====================================================================

    from contextsynapse.context.quality import score_node

    console.print("  [bold]Every node scored 0-100 at ingest. <1ms. No I/O.[/bold]")
    console.print("  [dim]Scores based on: connectivity, name quality, specificity,[/dim]")
    console.print("  [dim]source reliability, duplication, property completeness[/dim]\n")

    test_nodes = [
        {
            "label": "Fact",
            "properties": {
                "name": "Tesla Revenue",
                "statement": "Tesla Q3 2026 revenue reached $25.2B, up 8% YoY from $23.4B",
                "subject": "TSLA",
                "source_url": "https://ir.tesla.com/q3-2026",
                "created_by": "pipeline:market_feed",
                "confidence": 0.95,
            },
            "edge_count": 4,
        },
        {
            "label": "Person",
            "properties": {
                "name": "Benjamin Netanyahu",
                "confidence": 0.90,
                "source_url": "https://reuters.com/article/...",
                "created_by": "pipeline:news_ingestion",
                "mention_count": 12,
            },
            "edge_count": 7,
        },
        {
            "label": "Fact",
            "properties": {
                "name": "x",
                "statement": "stuff",
            },
            "edge_count": 0,
        },
        {
            "label": "Person",
            "properties": {
                "name": "Match Results",
            },
            "edge_count": 0,
            "_is_duplicate": False,
        },
        {
            "label": "Document",
            "properties": {
                "title": "Q3 Earnings Report",
                "source_url": "https://sec.gov/filings/tsla-10q",
                "content_type": "financial",
            },
            "edge_count": 3,
        },
        {
            "label": "Fact",
            "properties": {
                "name": "NVDA Revenue",
                "statement": "Revenue was good",
            },
            "edge_count": 1,
            "_is_duplicate": True,
        },
    ]

    table = Table(title="Node Quality Scores (0-100)", box=box.ROUNDED)
    table.add_column("Type", style="cyan", width=10)
    table.add_column("Name", width=22)
    table.add_column("Score", justify="right", width=6)
    table.add_column("Grade", justify="center", width=8)
    table.add_column("Why", style="dim", width=38)

    for node in test_nodes:
        score = score_node(node)
        props = node.get("properties", {})
        name = props.get("name", props.get("title", "?"))[:20]

        if score >= 80:
            grade = "[green]A[/green]"
        elif score >= 60:
            grade = "[yellow]B[/yellow]"
        elif score >= 40:
            grade = "[yellow]C[/yellow]"
        elif score >= 20:
            grade = "[red]D[/red]"
        else:
            grade = "[red]F[/red]"

        # Build why
        reasons = []
        if node.get("edge_count", 0) == 0:
            reasons.append("no connections")
        elif node.get("edge_count", 0) > 5:
            reasons.append("well-connected")
        if props.get("source_url"):
            reasons.append("sourced")
        if node.get("_is_duplicate"):
            reasons.append("DUPLICATE")
        if len(props.get("statement", props.get("name", ""))) < 10:
            reasons.append("too short")
        if props.get("confidence", 0) > 0.9:
            reasons.append("high confidence")
        why = ", ".join(reasons) if reasons else "baseline"

        table.add_row(node["label"], name, str(score), grade, why)

    console.print(table)

    # Benchmark
    t_score = time.time()
    for _ in range(10000):
        score_node(test_nodes[0])
    score_ms = (time.time() - t_score) * 1000
    console.print(f"\n  [dim]Throughput: 10K scores in {score_ms:.1f}ms ({10000/score_ms*1000:,.0f} scores/s)[/dim]")

    # =====================================================================
    section("2. Freshness Detection")
    # =====================================================================

    from contextsynapse.intelligence.freshness import detect_event_date, score_freshness

    console.print("  [bold]Extracts event dates from text. Detects stale content.[/bold]")
    console.print("  [dim]Prevents treating old news as current just because we ingested it today.[/dim]\n")

    texts = [
        "Tesla reported Q3 2026 results showing $25.2B in revenue",
        "The Federal Reserve announced rate cuts on September 10, 2026",
        "Apple launched the iPhone 15 in September 2023",
        "GDP grew 3.2% last quarter according to latest data",
        "Kubernetes 1.28 was released in August 2024",
        "Historical analysis: the 2008 financial crisis began in September",
    ]

    table = Table(title="Freshness Detection", box=box.ROUNDED)
    table.add_column("Text", width=50)
    table.add_column("Event Date", width=12)
    table.add_column("Freshness", justify="center", width=12)
    table.add_column("Age", justify="right", width=8)

    for text in texts:
        result = detect_event_date(text)
        event_date = result.event_date if isinstance(result, object) and hasattr(result, 'event_date') else (result if isinstance(result, str) else "")
        if event_date:
            freshness = score_freshness(event_date)
            level = freshness.freshness
            color = {"fresh": "green", "recent": "green", "aging": "yellow", "stale": "red", "historical": "red"}.get(level, "white")
            age = f"{freshness.staleness_days}d" if freshness.staleness_days > 0 else "today"
            table.add_row(text[:48] + "..", event_date[:10], f"[{color}]{level}[/{color}]", age)
        else:
            table.add_row(text[:48] + "..", "[dim]none[/dim]", "[dim]unknown[/dim]", "[dim]-[/dim]")

    console.print(table)

    # =====================================================================
    section("3. Quality Filtering")
    # =====================================================================

    from contextsynapse.context.quality import QualityFilter

    console.print("  [bold]Low-quality nodes filtered before delivery to agents.[/bold]\n")

    # Simulate a batch of nodes with varying quality
    all_nodes_data = test_nodes.copy()
    scores = [(score_node(n), n) for n in all_nodes_data]
    scores.sort(key=lambda x: -x[0])

    console.print(f"  Total nodes:    {len(all_nodes_data)}")
    high = sum(1 for s, _ in scores if s >= 60)
    low = sum(1 for s, _ in scores if s < 40)
    console.print(f"  High quality:   {high} (score >= 60)")
    console.print(f"  Low quality:    {low} (score < 40, filtered out)")
    console.print(f"  Delivered:      {len(all_nodes_data) - low} nodes to agent")

    # =====================================================================
    section("4. Context Usage Tracking")
    # =====================================================================

    from contextsynapse.context.quality_tracker import ContextQualityTracker

    console.print("  [bold]Measures if context delivered to agents was useful.[/bold]")
    console.print("  [dim]Tracks: what was delivered -> what agent did -> was it referenced?[/dim]\n")

    tracker = ContextQualityTracker(window_seconds=600)

    # Simulate: deliver context to agent
    tracker.record_delivery(
        "research-agent",
        node_ids=["n-tesla-rev", "n-tesla-fsd", "n-nvidia-gpu", "n-fed-rates", "n-stale-news"],
        token_count=2400,
        node_labels=["Fact", "Fact", "Fact", "Fact", "Fact"],
        total_graph_nodes=50,
    )

    # Simulate: agent actions that reference delivered content
    tracker.record_agent_action("research-agent", "search_nodes", {"query": "tesla revenue growth"})
    tracker.record_agent_action("research-agent", "rag_query", {"question": "What is Tesla FSD status?"})
    tracker.record_agent_action("research-agent", "add_knowledge", {"content": "Tesla revenue analysis complete", "node_type": "Finding"})
    # Agent never looked at fed-rates or stale-news = waste

    report = tracker.compute_quality("research-agent")

    table = Table(title="Context Usage Report (research-agent)", box=box.ROUNDED)
    table.add_column("Metric", style="cyan", width=25)
    table.add_column("Value", justify="right", width=10)
    table.add_column("Meaning", style="dim", width=35)

    table.add_row("Relevance Score", f"{report.relevance_score:.1%}",
                  "Agent actions referenced delivered content")
    table.add_row("Freshness Score", f"{report.freshness_score:.1%}",
                  "How fresh were delivered nodes")
    table.add_row("Coverage Score", f"{report.coverage_score:.1%}",
                  f"Delivered {5}/{50} of graph nodes")
    table.add_row("Waste Ratio", f"{report.waste_ratio:.1%}",
                  "Tokens delivered but never used")
    table.add_row("Deliveries", str(report.deliveries_count), "Context exports to this agent")
    table.add_row("Actions", str(report.actions_count), "Tool calls after delivery")
    table.add_row("Tokens Delivered", f"{report.total_tokens_delivered:,}", "Total context tokens sent")
    console.print(table)

    # =====================================================================
    section("5. Promotion Scoring")
    # =====================================================================

    console.print("  [bold]High-value agent memories promoted to main graph.[/bold]")
    console.print("  [dim]Scores based on: access count, confidence, freshness, edge count[/dim]\n")

    promotion_candidates = [
        {"name": "TSLA revenue $25.2B", "access_count": 12, "confidence": 0.95, "age_days": 2, "edge_count": 5},
        {"name": "Fed rate cut signal", "access_count": 8, "confidence": 0.85, "age_days": 5, "edge_count": 3},
        {"name": "Random observation", "access_count": 1, "confidence": 0.40, "age_days": 30, "edge_count": 0},
        {"name": "NVDA GPU shortage easing", "access_count": 6, "confidence": 0.90, "age_days": 7, "edge_count": 4},
    ]

    table = Table(title="Promotion Candidates", box=box.ROUNDED)
    table.add_column("Memory", width=25)
    table.add_column("Accesses", justify="right", width=9)
    table.add_column("Confidence", justify="right", width=11)
    table.add_column("Age", justify="right", width=6)
    table.add_column("Promote?", justify="center", width=10)

    for c in promotion_candidates:
        # Simple promotion logic: high access + high confidence + recent = promote
        should_promote = c["access_count"] >= 5 and c["confidence"] >= 0.7 and c["age_days"] <= 14
        icon = "[green]YES[/green]" if should_promote else "[dim]no[/dim]"
        table.add_row(
            c["name"][:23],
            str(c["access_count"]),
            f"{c['confidence']:.0%}",
            f"{c['age_days']}d",
            icon,
        )
    console.print(table)

    # =====================================================================
    section("Summary")
    # =====================================================================

    elapsed = time.time() - t0
    console.print(Panel.fit(
        f"[bold green]Context Scoring Demo Complete[/bold green]\n\n"
        f"  Node Scoring:     {len(test_nodes)} nodes scored 0-100 (<1ms each)\n"
        f"  Freshness:        {len(texts)} texts analyzed for event dates\n"
        f"  Quality Filter:   {low} low-quality nodes blocked\n"
        f"  Usage Tracking:   relevance={report.relevance_score:.0%}, waste={report.waste_ratio:.0%}\n"
        f"  Promotion:        {sum(1 for c in promotion_candidates if c['access_count']>=5 and c['confidence']>=0.7)} candidates promoted\n"
        f"  Throughput:       {10000/score_ms*1000:,.0f} scores/sec\n"
        f"  Time:             {elapsed:.1f}s\n\n"
        f"  [bold]Context without quality is noise.[/bold]\n"
        f"  ContextSynapse scores, filters, tracks, and promotes --\n"
        f"  ensuring agents get the right context, not just more context.",
        border_style="green",
    ))


if __name__ == "__main__":
    main()
