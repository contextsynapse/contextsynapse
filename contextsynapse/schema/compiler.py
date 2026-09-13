"""SchemaCompiler — compiles an EnhancedSchema into stage-specific rule sets.

The compiler transforms a declarative schema definition into concrete plans
that each pipeline stage can consume directly:

- ExtractionPlan: what types/fields to extract
- ValidationRules: how to validate extracted data
- DedupPolicy references: how to deduplicate per type
- Edge rules and derivation rules

Usage:
    from contextsynapse.schema.compiler import SchemaCompiler

    compiled = SchemaCompiler.compile(enhanced_schema)
    plan = compiled.extraction_plan
    rules = compiled.validation_rules
    ingestion_schema = compiled.to_ingestion_schema()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .sdl import (
    DedupPolicy,
    DerivationRule,
    EdgeTypeDef,
    EnhancedSchema,
    ExtractionConfig,
    FieldDef,
    NodeTypeDef,
)


# ── ExtractionPlan ────────────────────────────────────────────────────

@dataclass
class ExtractionPlan:
    """What the extraction stage is allowed to produce."""

    allowed_types: List[str] = field(default_factory=list)
    required_fields: Dict[str, List[str]] = field(default_factory=dict)
    optional_fields: Dict[str, List[str]] = field(default_factory=dict)
    descriptions: Dict[str, str] = field(default_factory=dict)
    allowed_edges: List[str] = field(default_factory=list)
    edge_source_target: Dict[str, Tuple[str, str]] = field(default_factory=dict)


# ── ValidationRules ───────────────────────────────────────────────────

@dataclass
class ValidationRules:
    """Rules for validating extracted entities."""

    field_validators: Dict[str, Dict[str, FieldDef]] = field(default_factory=dict)
    min_confidence: Dict[str, float] = field(default_factory=dict)
    strict_mode: bool = False


# ── ExecutionPlan ─────────────────────────────────────────────────────

@dataclass
class ExecutionPlan:
    """Pipeline execution plan derived from schema extraction config."""
    parser: str = "text"
    parser_config: Dict[str, Any] = field(default_factory=dict)
    stages: List[str] = field(default_factory=list)
    signals: List[str] = field(default_factory=list)
    significance_threshold: float = 0.3
    topic_method: str = "llm_with_fallback"
    field_mapping: Dict[str, str] = field(default_factory=dict)
    edge_fields: Dict[str, str] = field(default_factory=dict)


# ── CompiledSchema ────────────────────────────────────────────────────

@dataclass
class CompiledSchema:
    """Fully compiled schema ready for pipeline consumption."""

    name: str
    version: str
    extraction_plan: ExtractionPlan
    validation_rules: ValidationRules
    dedup_strategies: Dict[str, DedupPolicy] = field(default_factory=dict)
    edge_rules: Dict[str, EdgeTypeDef] = field(default_factory=dict)
    derivation_rules: List[DerivationRule] = field(default_factory=list)
    execution_plan: Optional[ExecutionPlan] = None
    _enhanced: EnhancedSchema = field(default=None, repr=False)

    def to_ingestion_schema(self):
        """Convert to legacy IngestionSchema for backward compatibility."""
        from contextsynapse.ingestion.schema_extractor import IngestionSchema

        node_types: Dict[str, Dict[str, Any]] = {}
        for type_name in self.extraction_plan.allowed_types:
            node_types[type_name] = {
                "required": list(self.extraction_plan.required_fields.get(type_name, [])),
                "optional": list(self.extraction_plan.optional_fields.get(type_name, [])),
            }
            desc = self.extraction_plan.descriptions.get(type_name, "")
            if desc:
                node_types[type_name]["description"] = desc

        edge_types: Dict[str, Dict[str, Any]] = {}
        for edge_name, edge_def in self.edge_rules.items():
            edge_types[edge_name] = {
                "from": edge_def.source,
                "to": edge_def.target,
            }

        return IngestionSchema(
            name=self.name,
            node_types=node_types,
            edge_types=edge_types,
        )


# ── SchemaCompiler ────────────────────────────────────────────────────

class SchemaCompiler:
    """Compiles an EnhancedSchema into a CompiledSchema."""

    @staticmethod
    def compile(schema: EnhancedSchema) -> CompiledSchema:
        """Compile an EnhancedSchema into stage-specific rule sets."""
        extraction_plan = SchemaCompiler._build_extraction_plan(schema)
        validation_rules = SchemaCompiler._build_validation_rules(schema)
        dedup_strategies = SchemaCompiler._build_dedup_strategies(schema)
        edge_rules = dict(schema.edge_types)
        derivation_rules = list(schema.derivation_rules)

        # Build execution plan from extraction config
        exec_plan = None
        if schema.extraction:
            ext = schema.extraction
            stages = ext.stages
            if not stages:
                # Default stages per strategy
                if ext.strategy == "conversation":
                    stages = [
                        "detect_signals", "extract_entities",
                        "extract_preferences", "store_documents",
                        "resolve_entities", "build_edges",
                        "cluster_topics", "link_cross_reference",
                        "validate_gate", "deduplicate",
                        "synthesize_cu", "embed", "index_bm25",
                    ]
                else:
                    stages = [
                        "extract_entities", "store_documents",
                        "resolve_entities", "build_edges",
                        "cluster_topics", "link_cross_reference",
                        "validate_gate", "deduplicate",
                        "synthesize_cu", "embed", "index_bm25",
                    ]
            exec_plan = ExecutionPlan(
                parser=ext.parser,
                parser_config={
                    "chunk_method": ext.chunk_method,
                    "significance_threshold": ext.significance_threshold,
                },
                stages=stages,
                signals=ext.signals,
                significance_threshold=ext.significance_threshold,
                topic_method=ext.topic_method,
                field_mapping=ext.field_mapping,
                edge_fields=ext.edge_fields,
            )
        elif schema.node_types:
            # Schema has node_types but no extraction block — auto-generate default plan
            # This ensures ALL schemas benefit from the universal pipeline
            exec_plan = ExecutionPlan(
                parser="text",
                stages=[
                    "extract_entities", "store_documents",
                    "resolve_entities", "build_edges",
                    "cluster_topics", "link_cross_reference",
                    "validate_gate", "deduplicate",
                    "synthesize_cu", "embed", "index_bm25",
                ],
            )

        return CompiledSchema(
            name=schema.name,
            version=schema.version,
            extraction_plan=extraction_plan,
            validation_rules=validation_rules,
            dedup_strategies=dedup_strategies,
            edge_rules=edge_rules,
            derivation_rules=derivation_rules,
            execution_plan=exec_plan,
            _enhanced=schema,
        )

    @staticmethod
    def _build_extraction_plan(schema: EnhancedSchema) -> ExtractionPlan:
        """Build the extraction plan from node/edge type definitions."""
        allowed_types: List[str] = []
        required_fields: Dict[str, List[str]] = {}
        optional_fields: Dict[str, List[str]] = {}
        descriptions: Dict[str, str] = {}

        for type_name, node_def in schema.node_types.items():
            allowed_types.append(type_name)
            required_fields[type_name] = node_def.required_fields
            optional_fields[type_name] = [
                name for name, f in node_def.fields.items() if not f.required
            ]
            if node_def.description:
                descriptions[type_name] = node_def.description

        allowed_edges: List[str] = []
        edge_source_target: Dict[str, Tuple[str, str]] = {}
        for edge_name, edge_def in schema.edge_types.items():
            allowed_edges.append(edge_name)
            edge_source_target[edge_name] = (edge_def.source, edge_def.target)

        return ExtractionPlan(
            allowed_types=allowed_types,
            required_fields=required_fields,
            optional_fields=optional_fields,
            descriptions=descriptions,
            allowed_edges=allowed_edges,
            edge_source_target=edge_source_target,
        )

    @staticmethod
    def _build_validation_rules(schema: EnhancedSchema) -> ValidationRules:
        """Build validation rules from field definitions."""
        field_validators: Dict[str, Dict[str, FieldDef]] = {}
        min_confidence: Dict[str, float] = {}

        for type_name, node_def in schema.node_types.items():
            field_validators[type_name] = dict(node_def.fields)
            if node_def.min_confidence > 0.0:
                min_confidence[type_name] = node_def.min_confidence

        return ValidationRules(
            field_validators=field_validators,
            min_confidence=min_confidence,
            strict_mode=schema.strict_mode,
        )

    @staticmethod
    def _build_dedup_strategies(schema: EnhancedSchema) -> Dict[str, DedupPolicy]:
        """Extract dedup policies per node type."""
        return {
            type_name: node_def.dedup
            for type_name, node_def in schema.node_types.items()
        }
