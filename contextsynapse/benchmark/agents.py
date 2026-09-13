"""
Benchmark Agents
=================
Simulated agent behavior for the benchmark.
Each agent does a specific job (extract, analyze, summarize).

Two modes:
    - Baseline: Each agent works independently (no shared context)
    - Shared: Agents share a graph via AIContextDB
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

EXTRACT_SYSTEM = """You are an entity extraction agent. Extract all entities,
relationships, and key facts from the given document.

Return JSON with this structure:
{
    "entities": [
        {"name": "...", "type": "...", "description": "..."}
    ],
    "relationships": [
        {"source": "...", "target": "...", "type": "...", "description": "..."}
    ],
    "facts": [
        {"statement": "...", "confidence": 0.9}
    ]
}"""

ANALYZE_SYSTEM = """You are an analysis agent. Given a set of entities and
relationships, identify:
1. Key decisions and their rationale
2. Risks and mitigation strategies
3. Dependencies between components
4. Priority recommendations

Return JSON:
{
    "decisions": [{"title": "...", "rationale": "...", "impact": "high/medium/low"}],
    "risks": [{"description": "...", "severity": "high/medium/low", "mitigation": "..."}],
    "dependencies": [{"from": "...", "to": "...", "type": "..."}],
    "recommendations": ["..."]
}"""

SUMMARIZE_SYSTEM = """You are a summarization agent. Given context about a
document's entities, decisions, and risks, produce a concise executive summary.

Return JSON:
{
    "summary": "2-3 paragraph executive summary",
    "key_points": ["point 1", "point 2", ...],
    "action_items": ["item 1", "item 2", ...]
}"""


class BaselineAgents:
    """Agents that work independently — NO shared context.

    Each agent receives the raw document and does all work from scratch.
    This is the "expensive" baseline that we measure against.
    """

    def __init__(self, llm_client):
        """
        Args:
            llm_client: TokenCountingClient instance
        """
        self.llm = llm_client

    def run_extractor(self, document: str) -> Dict[str, Any]:
        """Agent 1: Extract entities from raw document."""
        result = self.llm.generate_json(
            prompt=f"Extract all entities, relationships, and facts from this document:\n\n{document}",
            system=EXTRACT_SYSTEM,
            operation="extract",
            agent_id="baseline_extractor",
        )
        return result

    def run_analyst(self, document: str) -> Dict[str, Any]:
        """Agent 2: Analyze the document — independently re-reads everything."""
        # In baseline mode, the analyst ALSO calls the LLM to extract entities
        # because it has no access to what the extractor already found.
        entities = self.llm.generate_json(
            prompt=f"Extract all entities and relationships from this document:\n\n{document}",
            system=EXTRACT_SYSTEM,
            operation="extract_duplicate",
            agent_id="baseline_analyst",
        )

        # Then analyze
        result = self.llm.generate_json(
            prompt=f"Analyze these entities and relationships:\n\n{json.dumps(entities, indent=2)}",
            system=ANALYZE_SYSTEM,
            operation="analyze",
            agent_id="baseline_analyst",
        )
        return result

    def run_summarizer(self, document: str) -> Dict[str, Any]:
        """Agent 3: Summarize — independently re-reads everything."""
        # In baseline mode, the summarizer ALSO re-extracts and re-analyzes
        entities = self.llm.generate_json(
            prompt=f"Extract key entities and facts from this document:\n\n{document}",
            system=EXTRACT_SYSTEM,
            operation="extract_duplicate",
            agent_id="baseline_summarizer",
        )

        result = self.llm.generate_json(
            prompt=(
                f"Summarize this document based on these entities:\n\n"
                f"Entities: {json.dumps(entities, indent=2)}\n\n"
                f"Original document:\n{document[:2000]}"  # truncated
            ),
            system=SUMMARIZE_SYSTEM,
            operation="summarize",
            agent_id="baseline_summarizer",
        )
        return result

    def run_all(self, document: str) -> Dict[str, Any]:
        """Run all 3 agents on a document (baseline mode)."""
        extraction = self.run_extractor(document)
        analysis = self.run_analyst(document)
        summary = self.run_summarizer(document)
        return {
            "extraction": extraction,
            "analysis": analysis,
            "summary": summary,
        }


class SharedAgents:
    """Agents that share a graph via AIContextDB.

    Agent 1 extracts and stores in graph.
    Agents 2 and 3 query the graph instead of re-extracting.
    This is the "efficient" mode.
    """

    def __init__(self, llm_client, conn=None):
        """
        Args:
            llm_client: TokenCountingClient instance
            conn: AIContextDBConnection for shared graph
        """
        self.llm = llm_client
        self.conn = conn

    def run_extractor(self, document: str) -> Dict[str, Any]:
        """Agent 1: Extract entities and store in shared graph."""
        result = self.llm.generate_json(
            prompt=f"Extract all entities, relationships, and facts from this document:\n\n{document}",
            system=EXTRACT_SYSTEM,
            operation="extract",
            agent_id="shared_extractor",
        )

        # Store in graph (free — no LLM cost)
        if self.conn:
            self._store_in_graph(result)

        return result

    def run_analyst(self, document: str) -> Dict[str, Any]:
        """Agent 2: Query graph for entities, then analyze.

        KEY DIFFERENCE: No LLM call for extraction — queries the graph instead.
        """
        # Query graph for entities (FREE — local graph query, no LLM)
        entities = self._query_graph_entities()

        if not entities:
            # Fallback: if graph is empty, do minimal extraction
            entities = {"entities": [], "relationships": [], "facts": []}

        # Analyze (still needs LLM for reasoning)
        result = self.llm.generate_json(
            prompt=f"Analyze these entities and relationships:\n\n{json.dumps(entities, indent=2)}",
            system=ANALYZE_SYSTEM,
            operation="analyze",
            agent_id="shared_analyst",
        )

        # Store analysis results in graph too
        if self.conn:
            self._store_analysis(result)

        return result

    def run_summarizer(self, document: str) -> Dict[str, Any]:
        """Agent 3: Get scoped context from graph, then summarize.

        KEY DIFFERENCE: Gets pre-built context from ContextHub instead of
        re-reading the entire document + re-extracting entities.
        """
        # Build scoped context from graph (FREE — local, no LLM)
        context = self._build_context()

        # Summarize using scoped context (smaller input = fewer tokens)
        result = self.llm.generate_json(
            prompt=f"Summarize based on this context:\n\n{context}",
            system=SUMMARIZE_SYSTEM,
            operation="summarize",
            agent_id="shared_summarizer",
        )
        return result

    def run_all(self, document: str) -> Dict[str, Any]:
        """Run all 3 agents on a document (shared mode)."""
        extraction = self.run_extractor(document)
        analysis = self.run_analyst(document)
        summary = self.run_summarizer(document)
        return {
            "extraction": extraction,
            "analysis": analysis,
            "summary": summary,
        }

    def _store_in_graph(self, extraction: Dict[str, Any]):
        """Store extracted entities in the shared graph."""
        try:
            for entity in extraction.get("entities", []):
                self.conn.add_node(
                    label=entity.get("type", "Entity"),
                    properties={
                        "name": entity.get("name", ""),
                        "description": entity.get("description", ""),
                        "_source": "benchmark_extractor",
                    },
                )
            for rel in extraction.get("relationships", []):
                # Find source and target nodes
                nodes = self.conn.get_nodes()
                source_id = target_id = None
                for n in nodes:
                    props = n.properties or {}
                    if props.get("name") == rel.get("source"):
                        source_id = n.id
                    if props.get("name") == rel.get("target"):
                        target_id = n.id
                if source_id and target_id:
                    self.conn.add_edge(
                        source=source_id,
                        target=target_id,
                        label=rel.get("type", "RELATED_TO"),
                        properties={"description": rel.get("description", "")},
                    )
            for fact in extraction.get("facts", []):
                self.conn.add_node(
                    label="Fact",
                    properties={
                        "statement": fact.get("statement", ""),
                        "confidence": fact.get("confidence", 0.5),
                    },
                )
        except Exception as e:
            logger.debug("Failed to store in graph: %s", e)

    def _query_graph_entities(self) -> Dict[str, Any]:
        """Query the shared graph for entities (no LLM cost)."""
        entities = []
        relationships = []
        facts = []
        try:
            for node in self.conn.get_nodes():
                props = node.properties or {}
                if node.label == "Fact":
                    facts.append({
                        "statement": props.get("statement", ""),
                        "confidence": props.get("confidence", 0.5),
                    })
                else:
                    entities.append({
                        "name": props.get("name", ""),
                        "type": node.label,
                        "description": props.get("description", ""),
                    })

            for edge in self.conn.get_edges():
                relationships.append({
                    "source": edge.source,
                    "target": edge.target,
                    "type": edge.label,
                })
        except Exception as e:
            logger.debug("Failed to query graph: %s", e)

        return {"entities": entities, "relationships": relationships, "facts": facts}

    def _store_analysis(self, analysis: Dict[str, Any]):
        """Store analysis results in graph."""
        try:
            for decision in analysis.get("decisions", []):
                self.conn.add_node(
                    label="Decision",
                    properties={
                        "title": decision.get("title", ""),
                        "rationale": decision.get("rationale", ""),
                        "impact": decision.get("impact", "medium"),
                    },
                )
            for risk in analysis.get("risks", []):
                self.conn.add_node(
                    label="Risk",
                    properties={
                        "description": risk.get("description", ""),
                        "severity": risk.get("severity", "medium"),
                        "mitigation": risk.get("mitigation", ""),
                    },
                )
        except Exception as e:
            logger.debug("Failed to store analysis: %s", e)

    def _build_context(self) -> str:
        """Build scoped context from graph (no LLM cost)."""
        try:
            hub = self.conn.build_context(
                system_prompt="You are analyzing a document.",
                max_tokens=4000,
            )
            return hub.to_prompt(budget=4000)
        except Exception:
            # Fallback: manually build context from graph
            lines = []
            for node in self.conn.get_nodes():
                props = node.properties or {}
                name = props.get("name") or props.get("title") or props.get("statement", "")
                if name:
                    lines.append(f"[{node.label}] {name}: {props.get('description', props.get('rationale', ''))}")
            return "\n".join(lines[:50])  # Cap at 50 items
