"""
Context State — self-aware knowledge base.

The Context node becomes a living intelligence layer that tracks:
- Purpose (auto-detected from data sources)
- Coverage map (per-topic: counts, analysis quality, status)
- Content fingerprint (sources, entities, freshness)
- Agent activity log (who did what, quality, summary)
- Gaps (topics with data but no/poor analysis)

Updated incrementally on node add, rebuilt lazily on orient().

Usage:
    state = ContextState(db, namespace)
    state.rebuild()  # full rebuild from graph
    state.on_node_added(node)  # incremental update
    text = state.to_text()  # formatted for agent prompt
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Max entries in agent activity ring buffer
_MAX_AGENT_LOG = 20


def rate_finding_quality(content: str) -> float:
    """Rate the quality of a Finding/Insight based on content heuristics.

    Returns 0.0-1.0. Higher = more specific, more useful.
    No LLM needed — pure regex/heuristic.
    """
    if not content:
        return 0.1

    score = 0.3  # baseline

    # Has specific numbers?
    if re.search(r'\d+', content):
        score += 0.2

    # Has named entities (capitalized multi-char words)?
    caps = re.findall(r'[A-Z][a-z]{2,}', content)
    if len(caps) >= 3:
        score += 0.15
    elif len(caps) >= 1:
        score += 0.05

    # Sufficient length?
    if len(content) > 300:
        score += 0.1
    elif len(content) > 150:
        score += 0.05

    # Has source citations?
    citation_patterns = ["according to", "reported", "stated", "as per", "data shows"]
    if any(p in content.lower() for p in citation_patterns):
        score += 0.1

    # Penalize generic/vague content
    generic = ["covers", "various topics", "broad spectrum", "wide array",
               "highlights", "brings to light", "key developments",
               "unable to find", "no specific", "not available"]
    penalty = sum(0.1 for p in generic if p in content.lower())
    score -= min(0.3, penalty)

    # Penalize raw tool output (auto-generated, not real analysis)
    tool_output = ["found", "node(s) via aiql", "result(s):", "finding by",
                   "search_nodes", "search —", "[finding]", "[fact]"]
    tool_penalty = sum(0.15 for p in tool_output if p in content.lower())
    score -= min(0.4, tool_penalty)

    return round(min(1.0, max(0.1, score)), 2)


class ContextState:
    """Living intelligence layer for a context graph."""

    def __init__(self, db, namespace: str = ""):
        self._db = db
        self._namespace = namespace or getattr(db, "name", "")
        self._last_rebuilt: float = 0
        self._rebuild_ttl: float = 60.0  # seconds

        # State
        self.purpose: str = ""
        self.sources: List[str] = []
        self.freshness: str = ""
        self.status: str = "fresh"  # fresh | ingested | partially_analyzed | well_analyzed

        self.coverage: Dict[str, Dict[str, Any]] = {}
        self.fingerprint: Dict[str, Any] = {}
        self.agent_log: List[Dict[str, Any]] = []
        self.gaps: List[str] = []

        # Content for enriched briefing
        self._top_documents: List[Dict[str, Any]] = []
        self._top_facts: List[Dict[str, Any]] = []
        self._top_findings: List[Dict[str, Any]] = []

    def is_stale(self) -> bool:
        return (time.time() - self._last_rebuilt) > self._rebuild_ttl

    # ── Full Rebuild ────────────────────────────────────────────

    def rebuild(self) -> None:
        """Full rebuild from graph scan. Called lazily on orient() if stale."""
        t0 = time.time()
        try:
            # Use adapter directly for Redis/LMDB/CSR compatibility
            adapter = getattr(self._db, 'csr_adapter', None) or self._db
            nodes = list(adapter.get_all_nodes()) if hasattr(adapter, 'get_all_nodes') else []
        except Exception:
            nodes = []

        if not nodes:
            self.status = "fresh"
            self._last_rebuilt = time.time()
            return

        # Count edges
        total_edges = 0
        try:
            adapter = getattr(self._db, 'csr_adapter', None) or self._db
            if hasattr(adapter, 'get_edge_count'):
                total_edges = adapter.get_edge_count()
        except Exception:
            pass

        # Classify nodes
        facts, entities, passages, documents, findings, insights = [], [], [], [], [], []
        node_types: Dict[str, int] = defaultdict(int)
        source_urls = set()
        entity_names = []
        latest_created = ""

        _SKIP = {"AgentThought", "AgentAction", "AgentPresence",
                 "ExperimentRun", "ExperimentScore", "PipelineRun",
                 "VectorIndex", "BM25Index", "Session", "ContextRef",
                 "Context", "Project", "KnowledgeBase", "CodeBase",
                 "SystemStore", "UserStore", "WebStore", "GeneratedStore",
                 "MemoryStore", "ArtifactStore", "ToolStore"}

        for n in nodes:
            label = getattr(n, "label", "")
            if label in _SKIP:
                continue
            props = getattr(n, "properties", {}) or {}
            node_types[label] += 1

            # Track freshness
            created = props.get("_created_at", "")
            if created and created > latest_created:
                latest_created = created

            # Classify
            if label == "Fact":
                facts.append(props)
            elif label in ("Entity", "Person", "Organization", "Location", "Event"):
                entities.append(props)
                name = props.get("name", "")
                mentions = int(props.get("mention_count", 1))
                if name and len(name) > 2:
                    entity_names.append((name, mentions))
            elif label == "Passage":
                passages.append(props)
            elif label in ("Document", "WebPage"):
                documents.append(props)
                url = props.get("source_url") or props.get("source") or props.get("url") or ""
                if url:
                    try:
                        domain = urlparse(url).netloc
                        if domain:
                            source_urls.add(domain)
                    except Exception:
                        pass
            elif label == "Finding":
                findings.append(props)
            elif label == "Insight":
                insights.append(props)

        # ── Store content for enriched briefing ──
        self._top_documents = sorted(
            documents,
            key=lambda d: d.get("_created_at", ""),
            reverse=True,
        )[:5]

        self._top_facts = sorted(
            facts,
            key=lambda f: (float(f.get("confidence", 0.5)), len(f.get("content", f.get("name", "")))),
            reverse=True,
        )[:5]

        self._top_findings = sorted(
            findings + insights,
            key=lambda f: f.get("_created_at", ""),
            reverse=True,
        )[:5]

        # ── Build fingerprint ──
        # Rank entities: high mentions first, then prefer shorter recognizable names
        seen_ent = set()
        ranked_entities = []
        for name, mentions in sorted(entity_names, key=lambda x: (-x[1], len(x[0]))):
            if name.lower() not in seen_ent and 4 <= len(name) <= 35:
                seen_ent.add(name.lower())
                ranked_entities.append(name)

        # Top document titles for "what's in here"
        doc_titles = [d.get("name", d.get("title", ""))[:80] for d in documents if d.get("name") or d.get("title")][:5]

        # Top fact summaries
        fact_summaries = [f.get("name", f.get("statement", f.get("content", "")))[:100]
                          for f in facts if f.get("name") or f.get("statement") or f.get("content")][:5]

        # Entity breakdown by type
        entity_type_counts = {}
        for n in nodes:
            label = getattr(n, "label", "")
            if label in ("Person", "Organization", "Location", "Event", "Entity"):
                entity_type_counts[label] = entity_type_counts.get(label, 0) + 1

        # Embedded count
        embedded_count = sum(1 for n in nodes
                            if (getattr(n, "properties", {}) or {}).get("_embedded"))

        self.fingerprint = {
            "total_nodes": len(nodes),
            "total_edges": total_edges,
            "node_types": dict(node_types),
            "top_entities": ranked_entities[:15],
            "source_domains": sorted(source_urls),
            "doc_titles": doc_titles,
            "fact_summaries": fact_summaries,
            "entity_type_counts": entity_type_counts,
            "document_count": len(documents),
            "passage_count": len(passages),
            "fact_count": len(facts),
            "entity_count": len(entities),
            "finding_count": len(findings),
            "insight_count": len(insights),
            "embedded_count": embedded_count,
        }

        # ── Freshness ──
        if latest_created:
            try:
                dt = datetime.fromisoformat(latest_created.replace("Z", "+00:00"))
                self.freshness = dt.strftime("%Y-%m-%d")
            except Exception:
                self.freshness = latest_created[:10]

        # ── Sources + auto-detect purpose ──
        self.sources = sorted(source_urls)
        doc_count = node_types.get("Document", 0) + node_types.get("WebPage", 0)

        # Detect content type from sources + node mix
        content_type = "Knowledge base"
        if any("news" in d or "times" in d or "guardian" in d or "bbc" in d for d in source_urls):
            content_type = "News analysis"
        elif node_types.get("CodeFile", 0) > 0 or node_types.get("Task", 0) > 5:
            content_type = "Software project"
        elif node_types.get("Passage", 0) > node_types.get("Fact", 0):
            content_type = "Document analysis"
        elif node_types.get("Fact", 0) > 100:
            content_type = "Research data"

        if source_urls:
            domains = ", ".join(sorted(source_urls))
            self.purpose = f"{content_type} — {domains} ({doc_count} documents)"
        else:
            self.purpose = f"{content_type} ({len(nodes)} nodes)"

        # ── Coverage map from topic_scan ──
        try:
            from ..search.rag import topic_scan
            ts = topic_scan(self._db, self._namespace, max_topics=15, samples_per_topic=0)
            topics = ts.get("topics", [])
        except Exception:
            topics = []

        # Noise words that shouldn't be topics
        _NOISE_TOPICS = {"search", "key", "also", "news", "latest", "times",
                         "india", "page", "city", "data", "update", "report",
                         "check", "live", "read", "more", "click", "view",
                         "its", "insight", "finding", "analysis", "result",
                         "information", "available", "content", "based"}

        self.coverage = {}
        for t in topics:
            topic_name = t["name"]
            topic_count = t["count"]
            if topic_name in _NOISE_TOPICS:
                continue

            # Count analysis for this topic
            analysis_count = 0
            best_quality = 0.0
            best_summary = ""
            for f in findings + insights:
                content = f.get("content", "") or f.get("name", "")
                if topic_name not in content.lower():
                    continue
                quality = rate_finding_quality(content)
                analysis_count += 1
                if quality > best_quality:
                    best_quality = quality
                    # Clean summary: skip agent reasoning, tool output, meta-text
                    summary = content
                    # Remove common prefixes
                    for prefix in ["I need to", "I will", "To ", "Based on", "The task",
                                   "My task", "First,", "According to my", "This information",
                                   "These findings", "I have", "Now,", "Since ", "This will",
                                   "This is", "The goal", "I am ", "We need"]:
                        if summary.startswith(prefix):
                            # Find first sentence that's actual content
                            sentences = summary.split(". ")
                            for s in sentences[1:]:
                                if len(s) > 30 and not s.startswith(("I ", "The task", "My ")):
                                    summary = s
                                    break
                    # Remove raw tool output markers
                    if "Found " in summary and "node(s)" in summary:
                        summary = ""
                    if "result(s):" in summary:
                        summary = ""
                    best_summary = summary[:100] if summary else ""

            # Determine status
            if analysis_count == 0:
                status = "untouched"
            elif best_quality >= 0.6:
                status = "well_analyzed"
            elif best_quality >= 0.3:
                status = "analyzed"
            else:
                status = "poorly_analyzed"

            self.coverage[topic_name] = {
                "fact_count": topic_count,
                "analysis_count": analysis_count,
                "best_quality": best_quality,
                "status": status,
                "summary": best_summary if best_quality >= 0.3 else None,
            }

        # ── Agent activity log ──
        self.agent_log = []
        for f in (findings + insights):
            agent = f.get("_agent_id", "")
            if not agent:
                continue
            content = f.get("content", "") or f.get("name", "")
            quality = rate_finding_quality(content)
            created = f.get("_created_at", "")

            self.agent_log.append({
                "agent": agent,
                "time": created[:16] if created else "unknown",
                "action": "wrote Finding" if f in findings else "wrote Insight",
                "result_quality": "good" if quality >= 0.6 else "poor" if quality < 0.3 else "moderate",
                "result_summary": content[:80],
                "quality_score": quality,
            })

        # Sort by time (newest first), cap at max
        self.agent_log.sort(key=lambda x: x.get("time", ""), reverse=True)
        self.agent_log = self.agent_log[:_MAX_AGENT_LOG]

        # ── Gaps ──
        self.gaps = []
        for topic_name, cov in self.coverage.items():
            if cov["status"] == "untouched" and cov["fact_count"] >= 10:
                self.gaps.append(f"{topic_name} ({cov['fact_count']} facts, no analysis)")
            elif cov["status"] == "poorly_analyzed":
                self.gaps.append(f"{topic_name} (poor analysis — needs specifics)")

        # ── Overall status ──
        if not findings and not insights:
            self.status = "ingested" if facts else "fresh"
        elif any(c["status"] == "well_analyzed" for c in self.coverage.values()):
            self.status = "partially_analyzed"
            if all(c["status"] in ("well_analyzed", "analyzed") for c in self.coverage.values()):
                self.status = "well_analyzed"
        else:
            self.status = "partially_analyzed"

        self._last_rebuilt = time.time()
        elapsed = (time.time() - t0) * 1000
        logger.debug("[CONTEXT_STATE] Rebuilt in %.0fms: %d topics, %d agents, %d gaps",
                     elapsed, len(self.coverage), len(self.agent_log), len(self.gaps))

        # Persist to graph as a ContextIntelligence node
        self.save_to_graph()

    # ── Persist to Graph Node ─────────────────────────────────

    def save_to_graph(self) -> None:
        """Save context state as a ContextIntelligence node in the graph.

        This makes the intelligence instantly readable by any agent — just read the node.
        """
        try:
            from .graph_structures import GraphNode
            state_data = {
                "name": f"Context Intelligence: {self._namespace}",
                "purpose": self.purpose,
                "status": self.status,
                "freshness": self.freshness,
                "sources": json.dumps(self.sources),
                "coverage": json.dumps(self.coverage),
                "fingerprint": json.dumps(self.fingerprint),
                "agent_log": json.dumps(self.agent_log[:10]),  # keep compact
                "gaps": json.dumps(self.gaps),
                "briefing": self.to_text(),  # pre-rendered text ready for agent prompt
                "_last_rebuilt": datetime.now(timezone.utc).isoformat(),
            }

            node_id = f"ctx_intelligence_{self._namespace}"
            node = GraphNode(id=node_id, label="ContextIntelligence", properties=state_data)

            # Use direct add (bypass graph_intelligence hooks to avoid recursion)
            adapter = getattr(self._db, 'csr_adapter', None)
            if adapter:
                adapter.add_node(node_id, "ContextIntelligence", state_data, None)

                # Link Context → ContextIntelligence (find the Context node)
                try:
                    from .graph_structures import GraphEdge
                    import uuid as _uuid
                    context_nodes = adapter.get_nodes_by_type("Context") if hasattr(adapter, 'get_nodes_by_type') else []
                    for ctx_nid in context_nodes:
                        # Check if edge already exists
                        existing = adapter.get_neighbors(ctx_nid, "HAS_INTELLIGENCE") if hasattr(adapter, 'get_neighbors') else []
                        if not any(nid == node_id for nid, _ in existing):
                            adapter.add_edge(GraphEdge(
                                id=str(_uuid.uuid4()),
                                source=ctx_nid,
                                target=node_id,
                                label="HAS_INTELLIGENCE",
                                properties={},
                            ))
                        break  # only first Context node
                except Exception:
                    pass

                logger.debug("[CONTEXT_STATE] Saved ContextIntelligence node: %s", node_id)
        except Exception as e:
            logger.debug("[CONTEXT_STATE] Failed to save to graph: %s", e)

    def load_from_graph(self) -> bool:
        """Load context state from ContextIntelligence node. Returns True if found."""
        try:
            node_id = f"ctx_intelligence_{self._namespace}"
            adapter = getattr(self._db, 'csr_adapter', None)
            node = adapter.get_node(node_id) if adapter else None
            if not node:
                return False

            props = node.properties if hasattr(node, "properties") else {}
            self.purpose = props.get("purpose", "")
            self.status = props.get("status", "fresh")
            self.freshness = props.get("freshness", "")
            self.sources = json.loads(props.get("sources", "[]"))
            self.coverage = json.loads(props.get("coverage", "{}"))
            self.fingerprint = json.loads(props.get("fingerprint", "{}"))
            self.agent_log = json.loads(props.get("agent_log", "[]"))
            self.gaps = json.loads(props.get("gaps", "[]"))
            self._last_rebuilt = time.time()
            logger.debug("[CONTEXT_STATE] Loaded from graph node: %s", node_id)
            return True
        except Exception as e:
            logger.debug("[CONTEXT_STATE] Failed to load from graph: %s", e)
            return False

    # ── Incremental Update ──────────────────────────────────────

    def on_node_added(self, node) -> None:
        """Lightweight incremental update when a node is added."""
        label = getattr(node, "label", "")
        props = getattr(node, "properties", {}) or {}

        if label in ("Finding", "Insight"):
            content = props.get("content", "") or props.get("name", "")
            agent = props.get("_agent_id", "")
            quality = rate_finding_quality(content)

            self.agent_log.insert(0, {
                "agent": agent or "unknown",
                "time": props.get("_created_at", "")[:16],
                "action": f"wrote {label}",
                "result_quality": "good" if quality >= 0.6 else "poor" if quality < 0.3 else "moderate",
                "result_summary": content[:80],
                "quality_score": quality,
            })
            self.agent_log = self.agent_log[:_MAX_AGENT_LOG]

            # Mark coverage as stale so next orient() triggers rebuild
            self._last_rebuilt = 0

    # ── Format for Agent Prompt ─────────────────────────────────

    def to_text(self) -> str:
        """Format context state for agent prompt injection.

        Fast path: reads pre-rendered briefing from ContextIntelligence node.
        Slow path: full rebuild if node doesn't exist.
        """
        if self.is_stale():
            # Try loading from graph node first (instant, <5ms)
            if self.load_from_graph():
                # Return pre-rendered briefing from node via adapter
                node_id = f"ctx_intelligence_{self._namespace}"
                try:
                    adapter = getattr(self._db, 'csr_adapter', None)
                    node = adapter.get_node(node_id) if adapter else None
                    if node:
                        props = getattr(node, "properties", {}) or {}
                        briefing = props.get("briefing", "")
                        if briefing and len(briefing) > 50:
                            return briefing
                except Exception:
                    pass
            # Full rebuild if no node or briefing is empty
            self.rebuild()

        lines = []

        fp = self.fingerprint

        # Header
        lines.append(f"CONTEXT: {self.purpose}")
        lines.append(f"Status: {self.status} | {fp.get('total_nodes', 0)} nodes, {fp.get('total_edges', 0)} edges | {self.freshness or 'unknown'} data")
        if self.sources:
            lines.append(f"Sources: {', '.join(self.sources)}")

        # Data summary — one-line counts
        counts = []
        if fp.get("document_count"): counts.append(f"{fp['document_count']} documents")
        if fp.get("passage_count"): counts.append(f"{fp['passage_count']} passages")
        if fp.get("entity_count"): counts.append(f"{fp['entity_count']} entities")
        if fp.get("fact_count"): counts.append(f"{fp['fact_count']} facts")
        if fp.get("finding_count"): counts.append(f"{fp['finding_count']} findings")
        if fp.get("insight_count"): counts.append(f"{fp['insight_count']} insights")
        if fp.get("embedded_count"): counts.append(f"{fp['embedded_count']} embedded")
        if counts:
            lines.append(f"Data: {', '.join(counts)}")

        # Entity type breakdown
        etc = fp.get("entity_type_counts", {})
        if etc:
            parts = [f"{count} {label}" for label, count in sorted(etc.items(), key=lambda x: -x[1])]
            lines.append(f"Entities: {', '.join(parts)}")

        lines.append("")

        # What's here — document/article titles
        doc_titles = fp.get("doc_titles") or []
        if not doc_titles and self._top_documents:
            doc_titles = [d.get("name", d.get("title", ""))[:80] for d in self._top_documents if d.get("name") or d.get("title")]
        if doc_titles:
            lines.append("WHAT'S HERE:")
            for title in doc_titles[:5]:
                if title:
                    lines.append(f'  "{title[:80]}{"..." if len(title) > 80 else ""}"')
            lines.append("")

        # Key facts — actual fact content
        fact_sums = fp.get("fact_summaries") or []
        if not fact_sums and self._top_facts:
            fact_sums = [f.get("name", f.get("content", "")) for f in self._top_facts]
        if fact_sums:
            lines.append("KEY FACTS:")
            for content in fact_sums[:5]:
                if content:
                    lines.append(f"  - {content[:100]}{'...' if len(content) > 100 else ''}")
            lines.append("")

        # Agent findings — what other agents wrote (150 chars)
        if self._top_findings:
            lines.append("AGENT FINDINGS:")
            for f in self._top_findings:
                agent = f.get("_agent_id", "unknown")
                content = f.get("content", f.get("name", ""))
                if content:
                    lines.append(f'  {agent}: "{content[:150]}{"..." if len(content) > 150 else ""}"')
            lines.append("")

        # Coverage
        if self.coverage:
            lines.append("COVERAGE:")
            for topic, cov in sorted(self.coverage.items(), key=lambda x: -x[1]["fact_count"]):
                status = cov["status"]
                count = cov["fact_count"]
                if status == "well_analyzed":
                    icon = "+"
                    detail = f"good analysis"
                    if cov.get("summary"):
                        detail += f": {cov['summary']}"
                elif status == "analyzed":
                    icon = "~"
                    detail = "moderate analysis"
                    if cov.get("summary"):
                        detail += f": {cov['summary']}"
                elif status == "poorly_analyzed":
                    icon = "!"
                    detail = "poor analysis — needs deeper research"
                else:
                    icon = "-"
                    detail = "untouched — data available"
                lines.append(f"  [{icon}] {topic} ({count} facts): {detail}")
            lines.append("")

        # Key entities
        top_ents = self.fingerprint.get("top_entities", [])
        if top_ents:
            lines.append(f"KEY ENTITIES: {', '.join(top_ents[:12])}")
            lines.append("")

        # Gaps
        if self.gaps:
            lines.append("GAPS:")
            for g in self.gaps[:5]:
                lines.append(f"  - {g}")
            lines.append("")

        return "\n".join(lines)


# ── Singleton cache per graph ───────────────────────────────────

_state_cache: Dict[str, ContextState] = {}


def get_context_state(db, namespace: str = "") -> ContextState:
    """Get or create a ContextState for a graph."""
    ns = namespace or getattr(db, "name", "")
    if ns not in _state_cache:
        _state_cache[ns] = ContextState(db, ns)
    return _state_cache[ns]
