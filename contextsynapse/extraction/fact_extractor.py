"""
Fact Extractor
===============
Extracts verifiable facts from text using LLM — claims, metrics,
dates, decisions — and creates graph nodes linked to source chunks.

Facts are first-class citizens in the knowledge graph:
  - Each fact becomes a node (Claim, Metric, Date, Decision)
  - Linked to source Document/Chunk via EXTRACTED_FROM edge
  - Confidence scores for reliability filtering
  - Source text preserved for verification

Usage:
    from contextsynapse.extraction.fact_extractor import FactExtractor

    extractor = FactExtractor(db)
    facts = extractor.extract_facts("Revenue was $4.2M in Q1, up 15%.")
    # → Metric(name="Revenue", value="4.2M", unit="USD", period="Q1")
    # → Claim(statement="Revenue up 15%", confidence=0.9)
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

FACT_EXTRACTION_PROMPT = """Extract all verifiable facts from the following text.

For each fact, identify:
- type: one of Claim, Metric, Date, Decision, Requirement, Definition
- statement: the fact in one clear sentence
- confidence: how certain this fact is (0.0 to 1.0)
- source_text: the exact quote from the text that supports this fact
- properties: type-specific fields:
  - Metric: {name, value, unit, period, comparison}
  - Date: {event, date, description}
  - Decision: {title, rationale, decided_by, status}
  - Requirement: {title, priority, status}
  - Definition: {term, definition}
  - Claim: {subject, predicate}

Return JSON:
{
  "facts": [
    {
      "type": "Metric",
      "statement": "Q1 revenue was $4.2M",
      "confidence": 0.95,
      "source_text": "Revenue reached $4.2 million in the first quarter",
      "properties": {"name": "Revenue", "value": "4.2M", "unit": "USD", "period": "Q1"}
    }
  ]
}

Text to extract facts from:
"""

FACT_SYSTEM_PROMPT = (
    "You are a fact extraction engine. Extract precise, verifiable facts from text. "
    "Be thorough — capture metrics, dates, decisions, claims, requirements, and definitions. "
    "Assign confidence scores honestly. Always return valid JSON."
)


@dataclass
class Fact:
    """A single extracted fact."""
    fact_type: str
    statement: str
    confidence: float
    source_text: str
    properties: Dict[str, Any] = field(default_factory=dict)
    node_id: Optional[str] = None  # set after graph creation


@dataclass
class FactExtractionResult:
    """Result of fact extraction."""
    facts: List[Fact] = field(default_factory=list)
    node_ids: List[str] = field(default_factory=list)
    edge_ids: List[str] = field(default_factory=list)


class FactExtractor:
    """Extract verifiable facts from text and create graph nodes."""

    def __init__(
        self,
        db=None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.db = db
        self.provider = provider
        self.model = model

    def extract_facts(
        self,
        text: str,
        source_node_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        fact_types: Optional[List[str]] = None,
    ) -> FactExtractionResult:
        """Extract facts from text and optionally create graph nodes.

        Args:
            text: Text to extract facts from.
            source_node_id: Optional node ID to link facts via EXTRACTED_FROM.
            agent_id: Agent performing extraction.
            fact_types: Limit to these fact types (None = all).

        Returns:
            FactExtractionResult with facts and created node/edge IDs.
        """
        from ..llm import get_llm_client

        llm = get_llm_client(provider=self.provider, model=self.model)

        prompt = FACT_EXTRACTION_PROMPT + text
        if fact_types:
            prompt = prompt.replace(
                "one of Claim, Metric, Date, Decision, Requirement, Definition",
                f"one of {', '.join(fact_types)}",
            )

        raw = llm.generate_json(prompt=prompt, system=FACT_SYSTEM_PROMPT)
        raw_facts = raw.get("facts", [])

        result = FactExtractionResult()

        for rf in raw_facts:
            fact = Fact(
                fact_type=rf.get("type", "Claim"),
                statement=rf.get("statement", ""),
                confidence=float(rf.get("confidence", 0.5)),
                source_text=rf.get("source_text", ""),
                properties=rf.get("properties", {}),
            )

            # Filter by requested types
            if fact_types and fact.fact_type not in fact_types:
                continue

            result.facts.append(fact)

            # Create graph node if db is available
            if self.db:
                node_id = self._create_fact_node(fact, agent_id)
                fact.node_id = node_id
                result.node_ids.append(node_id)

                # Link to source node
                if source_node_id and node_id:
                    edge_id = self._create_source_edge(node_id, source_node_id, agent_id)
                    if edge_id:
                        result.edge_ids.append(edge_id)

        logger.info("Extracted %d facts from %d chars", len(result.facts), len(text))
        return result

    def extract_facts_from_chunks(
        self,
        chunks: List[Dict[str, str]],
        agent_id: Optional[str] = None,
    ) -> FactExtractionResult:
        """Extract facts from multiple chunks.

        Args:
            chunks: List of {"text": "...", "node_id": "..."} dicts.
            agent_id: Agent performing extraction.
        """
        combined = FactExtractionResult()

        for chunk in chunks:
            text = chunk.get("text", "")
            node_id = chunk.get("node_id")

            if len(text.strip()) < 30:
                continue

            try:
                result = self.extract_facts(
                    text, source_node_id=node_id, agent_id=agent_id,
                )
                combined.facts.extend(result.facts)
                combined.node_ids.extend(result.node_ids)
                combined.edge_ids.extend(result.edge_ids)
            except Exception as e:
                logger.warning("Fact extraction failed for chunk: %s", e)

        return combined

    def _create_fact_node(self, fact: Fact, agent_id: Optional[str] = None) -> str:
        """Create a graph node for a fact."""
        from ..core.graph_structures import GraphNode

        node_id = str(uuid.uuid4())
        props = {
            "statement": fact.statement,
            "confidence": fact.confidence,
            "source_text": fact.source_text,
            "_source": "fact_extraction",
            **fact.properties,
        }
        if agent_id:
            props["_agent_id"] = agent_id

        node = GraphNode(id=node_id, label=fact.fact_type, properties=props)
        self.db.add_node(node, write_through=True)
        return node_id

    def _create_source_edge(
        self, fact_node_id: str, source_node_id: str, agent_id: Optional[str] = None,
    ) -> Optional[str]:
        """Create EXTRACTED_FROM edge linking fact to source."""
        from ..core.graph_structures import GraphEdge

        edge_id = str(uuid.uuid4())
        props = {"_source": "fact_extraction"}
        if agent_id:
            props["_agent_id"] = agent_id

        edge = GraphEdge(
            id=edge_id, source=fact_node_id, target=source_node_id,
            label="EXTRACTED_FROM", properties=props,
        )
        try:
            self.db.add_edge(edge)
            return edge_id
        except Exception:
            return None
