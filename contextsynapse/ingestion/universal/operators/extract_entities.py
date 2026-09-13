"""ExtractEntitiesOperator -- extract named entities from chunks."""
from __future__ import annotations

import logging
import re
from typing import List, TYPE_CHECKING

from .base import StageOperator

if TYPE_CHECKING:
    from ..ingest_content import Chunk
    from ..stage_executor import GraphContext

logger = logging.getLogger(__name__)

# Regex patterns for entity extraction fallback
_ENTITY_PATTERNS = {
    "Technology": re.compile(
        r"\b((?:[A-Z][a-zA-Z]*(?:\.js|\.py|\.io|DB|SQL|API|SDK|MCP|LLM|AI|ML|CSS|HTML))"
        r"|(?:React|Python|FastAPI|Redis|Docker|Kubernetes|PostgreSQL|MongoDB"
        r"|TypeScript|JavaScript|GraphQL|Rust|Go|Node|Angular|Vue|Svelte"
        r"|TensorFlow|PyTorch|Kafka|RabbitMQ|Elasticsearch|Nginx|Qdrant"
        r"|NumPy|Pandas|Scikit|OpenCV|Spark|Hadoop|Terraform|Jenkins))\b"
    ),
    "Organization": re.compile(
        r"\b(Google|Microsoft|Apple|Amazon|Meta|OpenAI|Anthropic|Netflix"
        r"|Tesla|SpaceX|GitHub|AWS|Azure|IBM|Oracle|Salesforce|ISRO|NASA"
        r"|Facebook|Twitter|LinkedIn|Uber|Airbnb|Stripe|Shopify)\b"
    ),
}
# NOTE: Person regex removed — too many false positives (matches any 2 capitalized words
# like "Term Life Insurance", "Install Dependencies", "Variable Life Insurance").
# Person entities require LLM extraction to distinguish real names from concepts.

# Words/phrases that should never be extracted as entities
_FALSE_POSITIVES = frozenset({
    "The", "This", "That", "These", "Those", "What", "When", "Where",
    "Which", "How", "Why", "Who", "Here", "There", "Also", "However",
    "But", "And", "Then", "Next", "First", "Last", "Each", "Every",
    "Some", "Many", "Most", "Much", "Very", "Just", "Only", "Still",
    "Already", "Never", "Always", "Often", "Sometimes", "Perhaps",
    "Maybe", "Sure", "Yes", "Thanks", "Hello", "Dear", "Please",
    "New", "Old", "Good", "Bad", "Big", "Small", "High", "Low",
    "True", "False", "None", "Null", "Start", "End", "Stop",
})


def _regex_extract_entities(text: str) -> List[dict]:
    """Extract entities using regex patterns. Returns list of {label, name}."""
    results = []
    seen: set = set()

    for label, pattern in _ENTITY_PATTERNS.items():
        for m in pattern.finditer(text):
            name = m.group(1).strip()
            key = name.lower()
            if key in seen:
                continue
            # Filter false positives
            first_word = name.split()[0]
            if first_word in _FALSE_POSITIVES:
                continue
            if len(name) < 2:
                continue
            seen.add(key)
            results.append({"label": label, "name": name})

    return results


class ExtractEntitiesOperator(StageOperator):
    """Extract named entities from chunks and create entity nodes + MENTIONS edges."""

    name = "extract_entities"

    def __init__(self, use_llm: bool = True):
        self.use_llm = use_llm

    def process(self, chunks: List["Chunk"], graph_ctx: "GraphContext") -> List["Chunk"]:
        # Build edge_type_map: (source_label, target_label) -> edge_name
        edge_type_map: dict = {}
        schema = getattr(graph_ctx, "compiled_schema", None)
        if schema is not None:
            raw_edge_types = getattr(schema, "edge_types", None) or {}
            for edge_name, defn in raw_edge_types.items():
                if isinstance(defn, dict):
                    src = defn.get("source", "")
                    tgt = defn.get("target", "")
                else:
                    src = getattr(defn, "source", "")
                    tgt = getattr(defn, "target", "")
                if src and tgt:
                    edge_type_map[(src, tgt)] = edge_name

        # Collect significant chunks for batched LLM extraction
        significant_chunks = [
            c for c in chunks if c.metadata.get("significance", 0.5) >= 0.3
        ]

        # Batch LLM extraction: one call for all chunks instead of per-chunk
        chunk_entities = self._extract_batch(significant_chunks, graph_ctx)

        for idx, chunk in enumerate(significant_chunks):
            entities = chunk_entities.get(idx, [])
            if not entities:
                continue

            graph_ctx.log(f"Chunk {chunk.index}: {len(entities)} entities [{', '.join(e['name'] for e in entities[:5])}]")

            entity_node_ids: List[str] = []
            entity_labels: List[str] = []
            turn_node_id = chunk.metadata.get("turn_node_id", "")

            for ent in entities:
                label = ent["label"]
                name = ent["name"]

                existing_id = graph_ctx.find_entity(label, name)
                if existing_id:
                    node_id = existing_id
                else:
                    node_id = graph_ctx.add_node(label, {
                        "name": name,
                        "entity_type": label.lower(),
                    })

                entity_node_ids.append(node_id)
                entity_labels.append(label)

                if turn_node_id:
                    graph_ctx.add_edge(turn_node_id, node_id, "MENTIONS")

            # Create entity-to-entity edges based on schema edge_types
            edge_count = 0
            if edge_type_map and len(entity_node_ids) >= 2:
                for i in range(len(entity_node_ids)):
                    for j in range(i + 1, len(entity_node_ids)):
                        label_a, label_b = entity_labels[i], entity_labels[j]
                        id_a, id_b = entity_node_ids[i], entity_node_ids[j]
                        edge_name = edge_type_map.get((label_a, label_b))
                        if edge_name:
                            graph_ctx.add_edge(id_a, id_b, edge_name)
                            edge_count += 1
                        else:
                            edge_name = edge_type_map.get((label_b, label_a))
                            if edge_name:
                                graph_ctx.add_edge(id_b, id_a, edge_name)
                                edge_count += 1
            if edge_count > 0:
                graph_ctx.log(f"Chunk {chunk.index}: {edge_count} entity-to-entity edges from schema")

            chunk.metadata["entity_node_ids"] = entity_node_ids

        return chunks

    def _extract_batch(self, chunks: List["Chunk"], graph_ctx: "GraphContext") -> dict:
        """Extract entities from all chunks — one batched LLM call, regex fallback.

        Returns: {chunk_index: [{"label": ..., "name": ...}, ...]}
        """
        if not chunks:
            return {}

        # Try batched LLM extraction first
        if self.use_llm:
            try:
                result = self._llm_extract_batch(chunks, graph_ctx)
                if result:
                    return result
            except Exception as e:
                logger.debug("LLM batch extraction failed (%s), falling back to regex", e)

        # Fallback: regex per chunk
        return {i: _regex_extract_entities(c.content) for i, c in enumerate(chunks)}

    def _llm_extract_batch(self, chunks: List["Chunk"], graph_ctx: "GraphContext") -> dict:
        """One LLM call for all chunks — returns {chunk_index: entities}."""
        import json as _json
        from ...llm.client import get_llm_client

        llm = get_llm_client()
        if not llm:
            raise RuntimeError("No LLM client available")

        # Build a single prompt with all chunks (only user turns, truncated)
        numbered_texts = []
        for i, chunk in enumerate(chunks):
            text = chunk.content[:500]
            role = chunk.metadata.get("role", "")
            # Include both user and assistant turns but truncate assistant
            if role == "assistant":
                text = text[:200]
            numbered_texts.append(f"[{i}] {text}")

        # Cap total prompt size (~4000 chars max)
        combined = "\n".join(numbered_texts)
        if len(combined) > 4000:
            combined = combined[:4000]

        prompt = (
            "Extract all named entities from the numbered text segments below.\n"
            "Return a JSON object where keys are segment numbers and values are arrays of entities.\n"
            "Each entity has 'label' (Person/Technology/Organization/Concept/Location/Event) and 'name'.\n"
            "Only extract real, specific entities — not generic words or common phrases.\n\n"
            f"{combined}\n\n"
            'Example: {"0": [{"label": "Person", "name": "Alice"}, {"label": "Technology", "name": "Redis"}], "1": []}\n'
            "JSON:"
        )

        try:
            result = llm.generate_json(prompt, system="You are a precise named entity extractor.")
        except Exception:
            raw = llm.generate(prompt, system="You are a precise named entity extractor. Respond with JSON only.")
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            result = _json.loads(raw)

        if not isinstance(result, dict):
            return {}

        # Parse and validate
        valid_labels = {"Person", "Technology", "Organization", "Concept", "Location", "Event"}
        batch_result = {}
        global_seen: set = set()

        for key, entities in result.items():
            try:
                idx = int(key)
            except (ValueError, TypeError):
                continue
            if not isinstance(entities, list):
                continue

            extracted = []
            for ent in entities:
                if not isinstance(ent, dict):
                    continue
                label = ent.get("label", "Concept")
                name = ent.get("name", "").strip()
                if not name or len(name) < 2:
                    continue
                if label not in valid_labels:
                    label = "Concept"
                if name.split()[0] in _FALSE_POSITIVES:
                    continue
                extracted.append({"label": label, "name": name})

            batch_result[idx] = extracted

        return batch_result
