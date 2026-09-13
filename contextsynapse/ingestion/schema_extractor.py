"""IngestionSchema model and loader for the extraction pipeline.

Provides a typed schema that governs what node/edge types the extraction
pipeline is allowed to produce, what fields are required vs optional, and
how to fall back to a permissive default when no schema is configured.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default node types — permissive schema covering common categories
# ---------------------------------------------------------------------------

DEFAULT_NODE_TYPES: Dict[str, Dict[str, Any]] = {
    "Knowledge": {
        "required": ["name"],
        "optional": ["description", "source", "confidence"],
    },
    "Fact": {
        "required": ["name"],
        "optional": ["description", "source", "confidence", "evidence"],
    },
    "Finding": {
        "required": ["name"],
        "optional": ["description", "source", "confidence", "methodology"],
    },
    "Insight": {
        "required": ["name"],
        "optional": ["description", "source", "confidence", "reasoning"],
    },
    "Decision": {
        "required": ["name"],
        "optional": ["description", "rationale", "alternatives", "decided_by"],
    },
    "Entity": {
        "required": ["name"],
        "optional": ["description", "entity_type", "aliases"],
    },
    "Person": {
        "required": ["name"],
        "optional": ["role", "organization", "email"],
    },
    "Organization": {
        "required": ["name"],
        "optional": ["description", "industry", "url"],
    },
    "Event": {
        "required": ["name"],
        "optional": ["date", "location", "description", "participants"],
    },
    "Document": {
        "required": ["name"],
        "optional": ["url", "author", "date", "content_type", "summary"],
    },
    "TextChunk": {
        "required": ["name"],
        "optional": ["text", "source", "chunk_index", "token_count"],
    },
    "Requirement": {
        "required": ["name"],
        "optional": ["description", "priority", "status", "assignee"],
    },
    "CodeFile": {
        "required": ["name"],
        "optional": ["path", "language", "description", "functions"],
    },
    "Topic": {
        "required": ["name"],
        "optional": ["description", "keywords", "relevance"],
    },
}

# ---------------------------------------------------------------------------
# Default edge types
# ---------------------------------------------------------------------------

DEFAULT_EDGE_TYPES: Dict[str, Dict[str, Any]] = {
    "RELATES_TO": {"description": "General relationship"},
    "MENTIONS": {"description": "Source mentions target"},
    "PART_OF": {"description": "Target contains source"},
    "DEPENDS_ON": {"description": "Source depends on target"},
    "DERIVED_FROM": {"description": "Source was derived from target"},
    "AUTHORED_BY": {"description": "Document/artifact authored by person"},
    "BELONGS_TO": {"description": "Entity belongs to organization/group"},
    "OCCURRED_AT": {"description": "Event occurred at location/time"},
    "SUPPORTS": {"description": "Source evidence supports target claim"},
    "CONTRADICTS": {"description": "Source contradicts target"},
}

# ---------------------------------------------------------------------------
# IngestionSchema dataclass
# ---------------------------------------------------------------------------


@dataclass
class IngestionSchema:
    """Schema governing what an extraction pipeline may produce."""

    name: str
    node_types: Dict[str, Dict[str, Any]]
    edge_types: Dict[str, Dict[str, Any]]
    content_type: Optional[str] = None
    field_hints: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    @classmethod
    def default(cls) -> IngestionSchema:
        """Return a permissive default schema with standard types."""
        return cls(
            name="default",
            node_types=dict(DEFAULT_NODE_TYPES),
            edge_types=dict(DEFAULT_EDGE_TYPES),
        )


# ---------------------------------------------------------------------------
# load_schema — resolve schema from graph metadata or fall back to default
# ---------------------------------------------------------------------------


def _parse_yaml_schema(yaml_str: str, name: str = "custom") -> Optional[IngestionSchema]:
    """Parse a YAML schema string into IngestionSchema."""
    try:
        import yaml
        data = yaml.safe_load(yaml_str)
        if not data or not isinstance(data, dict):
            return None
        node_types = {}
        for nt_name, nt_def in data.get("node_types", {}).items():
            if isinstance(nt_def, dict):
                node_types[nt_name] = {
                    "required": nt_def.get("required", nt_def.get("fields", ["name"])),
                    "optional": nt_def.get("optional", []),
                    "description": nt_def.get("description", ""),
                }
        edge_types = {}
        for et_name, et_def in data.get("edge_types", {}).items():
            if isinstance(et_def, dict):
                edge_types[et_name] = {
                    "from": et_def.get("source", et_def.get("from", "*")),
                    "to": et_def.get("target", et_def.get("to", "*")),
                }
        if node_types:
            hints = data.get("field_hints", {})
            if data.get("strategy_hints"):
                hints["_strategy_hints"] = data["strategy_hints"]
            return IngestionSchema(
                name=data.get("name", name),
                node_types=node_types,
                edge_types=edge_types,
                content_type=data.get("content_type"),
                field_hints=hints,
            )
    except Exception:
        pass
    return None


def load_schema(
    db,
    content_type: Optional[str] = None,
    context_id: Optional[str] = None,
) -> IngestionSchema:
    """Load the ingestion schema for *db*.

    Resolution order:
      1. Context's extraction_schema_yaml (from context manager config)
      2. _context_meta node's schema property (JSON or dict)
      3. Builtin schema matching context type (from schema registry)
      4. Permissive default schema
    """
    # 1. Try context manager's extraction schema (YAML)
    if context_id:
        try:
            from ..context.context_manager import ContextManager
            cm = ContextManager()
            ctx = cm.get_context(context_id)
            if ctx and ctx.config.get("extraction_schema_yaml"):
                schema = _parse_yaml_schema(ctx.config["extraction_schema_yaml"], name=f"context:{context_id}")
                if schema:
                    schema.content_type = content_type or ctx.context_type
                    logger.debug("Loaded schema from context manager: %s", context_id)
                    return schema
        except Exception:
            pass

    # 2. Try _context_meta node's schema property
    try:
        meta = db.get_node("_context_meta")
        if meta:
            # Check for YAML schema in config
            config = meta.properties.get("config", {})
            if isinstance(config, dict) and config.get("extraction_schema_yaml"):
                schema = _parse_yaml_schema(config["extraction_schema_yaml"])
                if schema:
                    schema.content_type = content_type
                    return schema

            # Check for JSON schema
            if meta.properties.get("schema"):
                raw = meta.properties["schema"]
                data = json.loads(raw) if isinstance(raw, str) else raw
                schema = IngestionSchema(
                    name=data.get("name", "custom"),
                    node_types=data.get("node_types", {}),
                    edge_types=data.get("edge_types", {}),
                    content_type=content_type or data.get("content_type"),
                    field_hints=data.get("field_hints", {}),
                )
                logger.debug("Loaded custom ingestion schema '%s'", schema.name)
                return schema
    except Exception as exc:
        logger.warning("Failed to load schema from _context_meta: %s", exc)

    # 3. Try builtin schema from registry matching content type
    if content_type:
        try:
            from ..extraction.schema_registry import get_schema_registry
            reg = get_schema_registry()
            yaml_str = reg.get_schema(content_type)
            if yaml_str:
                schema = _parse_yaml_schema(yaml_str, name=content_type)
                if schema:
                    schema.content_type = content_type
                    return schema
        except Exception:
            pass

    # 4. Fallback — permissive default
    schema = IngestionSchema.default()
    schema.content_type = content_type
    return schema


# ---------------------------------------------------------------------------
# A2: SchemaPatternGenerator — generate regex patterns from schema
# ---------------------------------------------------------------------------

# Field name → regex pattern mapping
FIELD_PATTERNS = {
    "email": r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
    "phone": r'\b\+?[\d\s\-\(\)]{7,15}\b',
    "url": r'https?://[^\s<>"{}|\\^`\[\]]+',
    "date": r'\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{1,2},?\s*\d{4}\b',
    "percentage": r'\b\d+(?:\.\d+)?%\b',
    "amount": r'\$[\d,]+(?:\.\d{2})?|\b\d+(?:,\d{3})+(?:\.\d{2})?\b',
}

NAMED_ENTITY_PATTERN = r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b'


@dataclass
class ExtractionPattern:
    """A compiled regex pattern targeting a specific node type and field."""
    pattern: re.Pattern
    target_type: str
    target_field: str
    description: str = ""


class SchemaPatternGenerator:
    """Generate regex extraction patterns from schema definition.

    Named entity patterns (capitalized multi-word) only apply to entity-like
    types: Person, Organization, Entity. Other types (Fact, Decision, Event)
    are only created when their distinguishing fields (statement, date) match.
    """

    # Types where a capitalized name alone is enough to create a candidate
    ENTITY_TYPES = {"Person", "Organization", "Entity"}

    def __init__(self, schema: IngestionSchema):
        self.schema = schema

    def generate(self) -> List[ExtractionPattern]:
        patterns = []
        structural = {"Document", "TextChunk", "ContextMeta"}
        for type_name, type_def in self.schema.node_types.items():
            if type_name in structural:
                continue
            all_fields = type_def.get("required", []) + type_def.get("optional", [])

            # Named entity pattern — only for entity-like types
            if "name" in all_fields and type_name in self.ENTITY_TYPES:
                patterns.append(ExtractionPattern(
                    pattern=re.compile(NAMED_ENTITY_PATTERN),
                    target_type=type_name,
                    target_field="name",
                    description=f"Named entity for {type_name}",
                ))

            # Field-specific patterns
            for field_name in all_fields:
                if field_name in FIELD_PATTERNS:
                    patterns.append(ExtractionPattern(
                        pattern=re.compile(FIELD_PATTERNS[field_name]),
                        target_type=type_name,
                        target_field=field_name,
                        description=f"{field_name} pattern for {type_name}",
                    ))

            # Statement/fact patterns — these CREATE the node (not just fill a field)
            if "statement" in all_fields:
                patterns.append(ExtractionPattern(
                    pattern=re.compile(r'[A-Z][^.!?]*(?:\d+%|(?:19|20)\d{2}|"[^"]+")[^.!?]*[.!?]'),
                    target_type=type_name,
                    target_field="_statement_creates_node",
                    description=f"Statement with data for {type_name}",
                ))

        return patterns


# ---------------------------------------------------------------------------
# B1: regex_extract — fast schema-driven regex extraction
# ---------------------------------------------------------------------------


# Words that indicate a name is a location, not a person
_LOCATION_HINTS = {
    "states", "kingdom", "republic", "union", "islands", "coast", "gulf",
    "sea", "ocean", "river", "mountain", "valley", "peninsula", "strait",
    "america", "africa", "europe", "asia", "arabia", "emirates",
}

# Words that indicate organization, not person
_ORG_HINTS = {
    "commission", "council", "committee", "agency", "department", "ministry",
    "corporation", "company", "group", "institute", "university", "bank",
    "pentagon", "congress", "parliament", "senate", "nato", "opec",
}


_CONCEPT_HINTS = frozenset({
    "insurance", "learning", "network", "model", "system", "algorithm",
    "framework", "architecture", "protocol", "pattern", "method", "analysis",
    "processing", "detection", "classification", "regression", "optimization",
    "validation", "testing", "deployment", "integration", "management",
    "security", "authentication", "authorization", "encryption",
    "database", "storage", "index", "cache", "queue", "stream",
    "variable", "function", "module", "package", "library", "dependencies",
    "life", "term", "whole", "universal", "premium", "coverage",
    "portable", "retail", "frozen", "updated", "install", "description",
})


def _guess_entity_type(name: str, schema_types: set) -> str:
    """Guess the best entity type for a regex-extracted name."""
    name_lower = name.lower()
    words = set(name_lower.split())

    if words & _LOCATION_HINTS:
        if "Location" in schema_types:
            return "Location"
        return "Organization"
    if words & _ORG_HINTS:
        return "Organization"
    if words & _CONCEPT_HINTS:
        if "Concept" in schema_types:
            return "Concept"
        if "Topic" in schema_types:
            return "Topic"
        return "Concept"
    # Default: Concept (safer than Person — regex can't distinguish real names from concepts)
    if "Concept" in schema_types:
        return "Concept"
    return "Entity"


def regex_extract(text: str, schema: IngestionSchema) -> List[Dict[str, Any]]:
    """Phase 1: Fast regex extraction driven by schema patterns.

    Returns list of {"label": str, "properties": dict}.
    Each name only creates ONE node (best-guess type), not duplicates across types.
    """
    gen = SchemaPatternGenerator(schema)
    patterns = gen.generate()
    schema_types = set(schema.node_types.keys())

    candidates: Dict[str, Dict[str, Any]] = {}  # "name_lower" -> node dict
    # Track names already seen (by lowercase) — only one type per name
    seen_names: Dict[str, str] = {}  # name_lower -> assigned type

    for ep in patterns:
        matches = ep.pattern.findall(text)
        for match in matches:
            if not match or len(match.strip()) < 2:
                continue
            match = match.strip()

            if ep.target_field == "name":
                name_lower = match.lower()
                # Only create ONE node per name — pick best type
                if name_lower in seen_names:
                    continue  # already assigned a type
                best_type = _guess_entity_type(match, schema_types)
                seen_names[name_lower] = best_type
                key = f"{best_type}:{name_lower}"
                if key not in candidates:
                    candidates[key] = {
                        "label": best_type,
                        "properties": {"name": match, "confidence": 0.7, "_extraction_method": "regex"},
                    }
            elif ep.target_field == "_statement_creates_node":
                # Statement pattern — creates a Fact/Decision node from the statement text
                name = match[:80].strip()
                key = f"{ep.target_type}:{name.lower()}"
                if key not in candidates:
                    candidates[key] = {
                        "label": ep.target_type,
                        "properties": {"name": name, "statement": match, "confidence": 0.7, "_extraction_method": "regex"},
                    }
            else:
                # Field pattern — attaches to existing candidate of same type
                for ckey, cnode in candidates.items():
                    if cnode["label"] == ep.target_type and ep.target_field not in cnode["properties"]:
                        cnode["properties"][ep.target_field] = match
                        break

    return list(candidates.values())


# ---------------------------------------------------------------------------
# B2: build_llm_prompt + llm_extract — schema-aware LLM extraction
# ---------------------------------------------------------------------------


def build_llm_prompt(text: str, schema: IngestionSchema) -> str:
    """Build a schema-constrained LLM extraction prompt."""
    structural = {"Document", "TextChunk", "ContextMeta"}
    lines = [
        "Extract entities and relationships from the following text.",
        "Return ONLY valid JSON with two arrays: 'entities' and 'relationships'.",
        "",
        "Node types to extract:"
    ]

    for type_name, type_def in schema.node_types.items():
        if type_name in structural:
            continue
        required = type_def.get("required", [])
        optional = type_def.get("optional", [])
        req_str = ", ".join(f"{f} (required)" for f in required)
        opt_str = ", ".join(optional)
        fields = req_str + (", " + opt_str if opt_str else "")
        desc = type_def.get("description", "")
        lines.append(f"- {type_name}: {fields}" + (f" — {desc}" if desc else ""))

    if schema.edge_types:
        lines.append("")
        lines.append("Relationships to extract:")
        for edge_name, edge_def in schema.edge_types.items():
            src = edge_def.get("from", "*")
            tgt = edge_def.get("to", "*")
            lines.append(f"- {edge_name}: {src} -> {tgt}")

    # Strategy hints (if provided)
    strategy_hints = schema.field_hints.get("_strategy_hints", "")
    if strategy_hints:
        lines.extend(["", "Content-specific instructions:", strategy_hints])

    lines.extend([
        "",
        "Rules:",
        "- Only extract types listed above.",
        "- If unsure about a field, omit it rather than guess.",
        "- Each entity needs at minimum its required fields.",
        "- Extract facts as separate items with 'statement' field.",
        "",
        "Text:",
        "---",
        text[:3000],
        "---",
        "",
        'Return JSON: {"entities": [{"type": "...", "name": "...", ...}], "relationships": [{"type": "...", "source": "...", "target": "..."}]}'
    ])

    return "\n".join(lines)


def llm_extract(text: str, schema: IngestionSchema, llm_fn=None):
    """Phase 2: LLM extraction constrained by schema.

    Args:
        text: Content to extract from.
        schema: IngestionSchema defining allowed types.
        llm_fn: Optional callable(prompt) -> str. If None, returns empty.

    Returns tuple: (nodes: List[dict], edges: List[dict])
        nodes: [{"label": str, "properties": dict}, ...]
        edges: [{"label": str, "source_name": str, "target_name": str, "properties": dict}, ...]
    """
    if not llm_fn:
        return [], []

    prompt = build_llm_prompt(text, schema)
    response = None
    try:
        response = llm_fn(prompt)
        # Strip markdown code fences if present
        if isinstance(response, str):
            response = response.strip()
            if response.startswith("```"):
                response = response.split("\n", 1)[-1]  # remove first line
                if response.endswith("```"):
                    response = response[:-3]
                response = response.strip()
        data = json.loads(response)
        entities = data.get("entities", [])
        relationships = data.get("relationships", [])
    except (json.JSONDecodeError, Exception) as e:
        logger.warning("LLM extraction JSON parse failed: %s (response: %.100s...)", e, str(response or "")[:100])
        return [], []

    nodes = []
    for ent in entities:
        ent_type = ent.pop("type", "Entity")
        name = ent.get("name", "")
        if not name:
            continue
        nodes.append({
            "label": ent_type,
            "properties": {**ent, "confidence": 0.85, "_extraction_method": "llm"},
        })

    edges = []
    for rel in relationships:
        rel_type = rel.get("type", "RELATED_TO")
        source = rel.get("source", "")
        target = rel.get("target", "")
        if source and target:
            edges.append({
                "label": rel_type,
                "source_name": source,
                "target_name": target,
                "properties": {"confidence": 0.8, "_extraction_method": "llm"},
            })

    return nodes, edges


# ---------------------------------------------------------------------------
# B3: merge_extractions — combine Phase 1 + Phase 2
# ---------------------------------------------------------------------------


def merge_extractions(
    phase1: List[Dict[str, Any]],
    phase2: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Merge regex + LLM extractions. LLM wins on type conflicts.

    Strategy:
    1. Start with LLM results (better types, cleaner names)
    2. Add regex-only entities that LLM missed (no name overlap)
    3. LLM type wins when regex and LLM extracted same entity with different types
    """
    # Start with LLM results (they have better types)
    merged: Dict[str, Dict[str, Any]] = {}
    llm_names = set()  # lowercase names from LLM

    for node in phase2:
        name = node['properties'].get('name', '').strip()
        if not name:
            continue
        key = f"{node['label']}:{name.lower()}"
        merged[key] = {"label": node["label"], "properties": dict(node["properties"])}
        llm_names.add(name.lower())

    # Add regex entities that LLM missed
    for node in phase1:
        name = node['properties'].get('name', '').strip()
        if not name:
            continue
        name_lower = name.lower()

        # Skip if LLM already has this name (any type) — LLM type is better
        if name_lower in llm_names:
            continue

        # Skip if LLM has a shorter version of this name
        # e.g., regex: "Defense Secretary Lloyd Austin", LLM: "Lloyd Austin"
        llm_match = False
        for ln in llm_names:
            if ln in name_lower or name_lower in ln:
                llm_match = True
                break
        if llm_match:
            continue

        key = f"{node['label']}:{name_lower}"
        if key not in merged:
            merged[key] = {"label": node["label"], "properties": dict(node["properties"])}

    # Merge properties: for matched entities, fill gaps from regex
    for node in phase1:
        name = node['properties'].get('name', '').strip().lower()
        for key, m in merged.items():
            m_name = m['properties'].get('name', '').lower()
            if name and (name in m_name or m_name in name):
                # Fill missing fields from regex
                for k, v in node['properties'].items():
                    if k.startswith('_') or k in ('name', 'confidence'):
                        continue
                    if v and not m['properties'].get(k):
                        m['properties'][k] = v
                m['properties']['_extraction_method'] = 'merged'
                break

    # Deduplicate: if same name appears with multiple types, keep best one
    by_name: Dict[str, List] = {}
    for node in merged.values():
        name = node['properties'].get('name', '').lower()
        by_name.setdefault(name, []).append(node)

    final = []
    for name, nodes in by_name.items():
        if len(nodes) == 1:
            final.append(nodes[0])
        else:
            # Prefer LLM (merged/llm) over regex, prefer specific type over "Entity"
            best = nodes[0]
            for n in nodes[1:]:
                method = n['properties'].get('_extraction_method', '')
                best_method = best['properties'].get('_extraction_method', '')
                # LLM/merged wins over regex
                if method in ('llm', 'merged') and best_method == 'regex':
                    best = n
                # Specific type wins over "Entity"
                elif n['label'] != 'Entity' and best['label'] == 'Entity':
                    best = n
            final.append(best)

    return final


# ---------------------------------------------------------------------------
# Edge inference — create edges from co-occurring entities in text
# ---------------------------------------------------------------------------

# Patterns that suggest relationships between entity types
RELATIONSHIP_HINTS = {
    ("Person", "Organization"): "WORKS_AT",
    ("Organization", "Organization"): "RELATED_TO",
    ("Person", "Event"): "PARTICIPATED_IN",
    ("Person", "Decision"): "DECIDED_BY",
}


def _find_edge_type(a_type, b_type, a_name, b_name, schema):
    """Find the best schema edge type for a pair of node types."""
    edge_label = None
    source_name, target_name = a_name, b_name

    for etype, edef in schema.edge_types.items():
        efrom = edef.get("from", edef.get("source", "*"))
        eto = edef.get("to", edef.get("target", "*"))
        if efrom == a_type and eto == b_type:
            edge_label = etype
            break
        if efrom == b_type and eto == a_type:
            edge_label = etype
            source_name, target_name = b_name, a_name
            break

    # Fallback to hints for different-type pairs
    if not edge_label and a_type != b_type:
        pair = (a_type, b_type)
        reverse_pair = (b_type, a_type)
        edge_label = RELATIONSHIP_HINTS.get(pair) or RELATIONSHIP_HINTS.get(reverse_pair)
        if RELATIONSHIP_HINTS.get(reverse_pair) and not RELATIONSHIP_HINTS.get(pair):
            source_name, target_name = b_name, a_name

    return edge_label, source_name, target_name


def infer_edges(
    nodes: List[Dict[str, Any]],
    text: str,
    schema: IngestionSchema,
) -> List[Dict[str, Any]]:
    """Infer edges between extracted nodes based on co-occurrence in text.

    Two passes:
    1. Paragraph-level: entities in the same paragraph get edges (confidence 0.7)
    2. Document-level: all entity pairs checked for schema-defined edges (confidence 0.5)
       This catches connections across paragraphs (critical for conversations).

    Returns list of {"label": str, "source_name": str, "target_name": str, "properties": dict}.
    """
    edges = []
    seen = set()

    def _add_edge(label, src_name, tgt_name, confidence, method):
        edge_key = f"{label}:{src_name.lower()}:{tgt_name.lower()}"
        if edge_key not in seen:
            seen.add(edge_key)
            edges.append({
                "label": label,
                "source_name": src_name,
                "target_name": tgt_name,
                "properties": {"confidence": confidence, "_extraction_method": method},
            })

    # Pass 1: Paragraph-level co-occurrence (high confidence)
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [text]

    for para in paragraphs:
        para_lower = para.lower()
        present = [n for n in nodes
                   if n["properties"].get("name", "") and
                   n["properties"]["name"].lower() in para_lower]

        for i, a in enumerate(present):
            for b in present[i + 1:]:
                a_name = a["properties"]["name"]
                b_name = b["properties"]["name"]
                if a_name.lower() == b_name.lower():
                    continue

                edge_label, src, tgt = _find_edge_type(
                    a["label"], b["label"], a_name, b_name, schema)
                if edge_label:
                    _add_edge(edge_label, src, tgt, 0.7, "co_occurrence_paragraph")

    # Pass 2: Adjacent-paragraph window — cross-type, proximity-based edges
    # Only allow "safe" edge types at document level. IMPLEMENTS, DECIDED, SOLVED_BY
    # require explicit LLM extraction — too strong for co-occurrence inference.
    _PROXIMITY_ONLY = {"USED_WITH", "RELATED_TO", "DISCUSSES", "INVOLVES",
                       "PART_OF", "ABOUT", "ADDRESSES", "RECOMMENDS"}
    if len(paragraphs) > 1:
        for pi in range(len(paragraphs)):
            window = " ".join(paragraphs[pi:pi + 2]).lower()
            present = [n for n in nodes
                       if n["properties"].get("name", "") and
                       n["properties"]["name"].lower() in window]

            for i, a in enumerate(present):
                for b in present[i + 1:]:
                    a_name = a["properties"]["name"]
                    b_name = b["properties"]["name"]
                    if a_name.lower() == b_name.lower() or a["label"] == b["label"]:
                        continue

                    edge_label, src, tgt = _find_edge_type(
                        a["label"], b["label"], a_name, b_name, schema)
                    if edge_label and edge_label in _PROXIMITY_ONLY:
                        _add_edge(edge_label, src, tgt, 0.5, "co_occurrence_adjacent")

    return edges
