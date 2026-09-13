"""Enhanced Schema Definition Language (SDL) for AIContextDB."""

from .sdl import (
    DedupPolicy,
    DerivationRule,
    EdgeTypeDef,
    EnhancedSchema,
    ExtractionConfig,
    FieldDef,
    NodeTypeDef,
)
from .compiler import (
    CompiledSchema,
    ExecutionPlan,
    ExtractionPlan,
    SchemaCompiler,
    ValidationRules,
)
from .dedup import SchemaDedupStrategy
from .derivation import DerivationEngine
from .validation_gate import ValidationGate, ValidationVerdict
from .composer import SchemaComposer

__all__ = [
    "CompiledSchema",
    "DedupPolicy",
    "DerivationRule",
    "EdgeTypeDef",
    "EnhancedSchema",
    "ExtractionConfig",
    "ExecutionPlan",
    "ExtractionPlan",
    "FieldDef",
    "NodeTypeDef",
    "SchemaCompiler",
    "SchemaComposer",
    "DerivationEngine",
    "SchemaDedupStrategy",
    "ValidationGate",
    "ValidationRules",
    "ValidationVerdict",
]
