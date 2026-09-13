"""
LLM Entity Extractor
=====================
Auto-extract entities and relationships from text using any LLM provider.
Creates graph nodes and edges automatically — the key differentiator
vs. manual graph building.

Usage:
    from contextsynapse.extraction.llm_entity_extractor import EntityExtractor

    extractor = EntityExtractor(db)
    result = extractor.extract("Alice is an engineer at Acme Corp who maintains the Auth Service.")
    # → creates Person(Alice), Company(Acme Corp), System(Auth Service)
    # → creates WORKS_AT(Alice→Acme), MAINTAINS(Alice→Auth Service)

    # Or standalone (without graph):
    from contextsynapse.extraction.llm_entity_extractor import extract_entities_from_text
    entities, relationships = extract_entities_from_text("Alice works at Acme.")
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """Extract all entities and relationships from the following text.

For each entity, identify:
- name: the entity's name or identifier
- type: one of Person, Organization, System, Technology, Concept, Location, Event, Document, Product
- properties: key attributes mentioned (role, status, description, etc.)

For each relationship, identify:
- source: the name of the source entity
- target: the name of the target entity
- type: relationship label in UPPER_SNAKE_CASE (e.g., WORKS_AT, MAINTAINS, USES, DEPENDS_ON, PART_OF, CREATED_BY)
- properties: any qualifiers (since, role, etc.)

Return JSON with this exact structure:
{
  "entities": [
    {"name": "...", "type": "Person", "properties": {"role": "..."}}
  ],
  "relationships": [
    {"source": "...", "target": "...", "type": "WORKS_AT", "properties": {}}
  ]
}

Text to extract from:
"""

SYSTEM_PROMPT = (
    "You are a knowledge graph entity extractor. "
    "Extract structured entities and relationships from text. "
    "Be thorough but precise — only extract what is explicitly stated or strongly implied. "
    "Always return valid JSON."
)


@dataclass
class ExtractionResult:
    """Result of entity extraction."""
    entities: List[Dict[str, Any]] = field(default_factory=list)
    relationships: List[Dict[str, Any]] = field(default_factory=list)
    node_ids: List[str] = field(default_factory=list)
    edge_ids: List[str] = field(default_factory=list)
    raw_response: Dict[str, Any] = field(default_factory=dict)


def extract_entities_from_text(
    text: str,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> Tuple[List[Dict], List[Dict]]:
    """Extract entities and relationships from text (standalone, no graph).

    Returns (entities, relationships) as lists of dicts.
    """
    from ..llm import get_llm_client

    llm = get_llm_client(provider=provider, model=model)
    result = llm.generate_json(
        prompt=EXTRACTION_PROMPT + text,
        system=SYSTEM_PROMPT,
    )
    entities = result.get("entities", [])
    relationships = result.get("relationships", [])
    return entities, relationships


class EntityExtractor:
    """Extract entities from text and create graph nodes/edges.

    Supports schema-driven extraction — pass a YAML schema path or
    ExtractionSchema to constrain the LLM to only extract types
    defined in the schema.
    """

    def __init__(
        self,
        db=None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        schema=None,
    ):
        """
        Args:
            db: AIContextDB instance to create nodes/edges in. If None, extraction only.
            provider: LLM provider ('openai', 'anthropic', 'groq', 'ollama').
            model: Model name override.
            schema: Path to YAML schema file, or ExtractionSchema instance.
                    If provided, extraction is constrained to schema-defined types.
        """
        self.db = db
        self._schema = None
        if schema is not None:
            from .schema_loader import load_schema, ExtractionSchema
            if isinstance(schema, (str, type(None))):
                self._schema = load_schema(schema) if schema else None
            elif isinstance(schema, ExtractionSchema):
                self._schema = schema
            else:
                self._schema = load_schema(str(schema))
        self.provider = provider
        self.model = model

    def extract(
        self,
        text: str,
        source: str = "llm_extraction",
        agent_id: Optional[str] = None,
        deduplicate: bool = True,
    ) -> ExtractionResult:
        """Extract entities from text and optionally create graph nodes/edges.

        Args:
            text: Text to extract entities from.
            source: Source label for provenance.
            agent_id: Agent performing the extraction.
            deduplicate: If True, merge with existing nodes by name+type match.

        Returns:
            ExtractionResult with created node/edge IDs.
        """
        # Use schema-driven prompt if schema is loaded
        if self._schema:
            from .schema_loader import schema_to_prompt
            from ..llm import get_llm_client

            llm = get_llm_client(provider=self.provider, model=self.model)
            prompt = schema_to_prompt(self._schema) + text
            raw = llm.generate_json(prompt=prompt, system=SYSTEM_PROMPT)
            entities = raw.get("entities", [])
            relationships = raw.get("relationships", [])
            facts = raw.get("facts", [])
        else:
            entities, relationships = extract_entities_from_text(
                text, provider=self.provider, model=self.model,
            )
            facts = []

        result = ExtractionResult(
            entities=entities,
            relationships=relationships,
            raw_response={"entities": entities, "relationships": relationships, "facts": facts},
        )

        # Create fact nodes if schema defines fact_types
        if facts and self.db:
            from ..core.graph_structures import GraphNode
            for f in facts:
                node_id = str(uuid.uuid4())
                props = {
                    "statement": f.get("statement", ""),
                    "confidence": float(f.get("confidence", 0.5)),
                    "source_text": f.get("source_text", ""),
                    "_source": source,
                    **f.get("properties", {}),
                }
                if agent_id:
                    props["_agent_id"] = agent_id
                fact_type = f.get("type", "Fact")
                node = GraphNode(id=node_id, label=fact_type, properties=props)
                self.db.add_node(node, write_through=True)
                result.node_ids.append(node_id)

        if not self.db:
            return result

        # Create graph nodes
        from ..core.graph_structures import GraphNode, GraphEdge

        name_to_id: Dict[str, str] = {}

        for entity in entities:
            name = entity.get("name", "")
            etype = entity.get("type", "Entity")
            props = entity.get("properties", {})

            if not name:
                continue

            # Dedup: check if entity already exists
            if deduplicate:
                existing = self._find_existing(name, etype)
                if existing:
                    name_to_id[name] = existing
                    continue

            node_id = str(uuid.uuid4())
            props["name"] = name
            props["_source"] = source
            if agent_id:
                props["_agent_id"] = agent_id

            node = GraphNode(id=node_id, label=etype, properties=props)
            self.db.add_node(node, write_through=True)
            name_to_id[name] = node_id
            result.node_ids.append(node_id)

        # Create graph edges
        for rel in relationships:
            src_name = rel.get("source", "")
            tgt_name = rel.get("target", "")
            rel_type = rel.get("type", "RELATED_TO")
            props = rel.get("properties", {})

            src_id = name_to_id.get(src_name)
            tgt_id = name_to_id.get(tgt_name)

            if not src_id or not tgt_id:
                continue

            edge_id = str(uuid.uuid4())
            props["_source"] = source
            if agent_id:
                props["_agent_id"] = agent_id

            edge = GraphEdge(
                id=edge_id, source=src_id, target=tgt_id,
                label=rel_type, properties=props,
            )
            self.db.add_edge(edge)
            result.edge_ids.append(edge_id)

        logger.info(
            "Extracted %d entities, %d relationships from %d chars",
            len(result.node_ids), len(result.edge_ids), len(text),
        )
        return result

    def extract_from_chunks(
        self,
        chunks: List[str],
        source: str = "llm_extraction",
        agent_id: Optional[str] = None,
    ) -> ExtractionResult:
        """Extract entities from multiple text chunks and merge results.

        Useful for document processing — extract per chunk, then deduplicate
        across chunks so the same entity mentioned on pages 1 and 5 becomes
        one node, not two.
        """
        combined = ExtractionResult()

        for chunk in chunks:
            if len(chunk.strip()) < 20:
                continue
            try:
                result = self.extract(
                    chunk, source=source, agent_id=agent_id, deduplicate=True,
                )
                combined.entities.extend(result.entities)
                combined.relationships.extend(result.relationships)
                combined.node_ids.extend(result.node_ids)
                combined.edge_ids.extend(result.edge_ids)
            except Exception as e:
                logger.warning("Extraction failed for chunk (%d chars): %s", len(chunk), e)

        return combined

    def _find_existing(self, name: str, entity_type: str) -> Optional[str]:
        """Find an existing node by name and type."""
        if not self.db:
            return None
        try:
            nodes = self.db.get_all_nodes()
            for n in nodes:
                props = n.properties if hasattr(n, "properties") else {}
                label = n.label if hasattr(n, "label") else ""
                n_name = props.get("name", "")
                if n_name.lower() == name.lower() and label == entity_type:
                    return n.id if hasattr(n, "id") else None
        except Exception:
            pass
        return None
