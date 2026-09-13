"""Operator registry -- resolve stage names to operator instances."""
from __future__ import annotations

from typing import List, Optional


def resolve_operators(execution_plan, compiled_schema=None) -> List:
    """Map stage names from an ExecutionPlan to operator instances."""
    from .operators.detect_signals import DetectSignalsOperator
    from .operators.extract_entities import ExtractEntitiesOperator
    from .operators.cluster_topics import ClusterTopicsOperator
    from .operators.link_cross_reference import LinkCrossReferenceOperator
    from .operators.validate_gate import ValidateGateOperator
    from .operators.deduplicate import DeduplicateOperator
    from .operators.synthesize_cu import SynthesizeCUOperator
    from .operators.embed import EmbedOperator
    from .operators.index_bm25 import IndexBM25Operator
    from .operators.parse_records import ParseRecordsOperator
    from .operators.resolve_entities import ResolveEntitiesOperator
    from .operators.build_edges import BuildEdgesOperator
    from .operators.store_documents import StoreDocumentsOperator
    from .operators.infer_domains import InferDomainsOperator
    from .operators.filter_content import FilterContentOperator
    from .operators.extract_preferences import ExtractPreferencesOperator
    from .operators.sdlc_scan import SDLCScanOperator

    _REGISTRY = {
        "detect_signals": lambda: DetectSignalsOperator(
            signal_types=execution_plan.signals or None,
            significance_threshold=execution_plan.significance_threshold,
        ),
        "extract_entities": lambda: ExtractEntitiesOperator(),
        "cluster_topics": lambda: ClusterTopicsOperator(
            method=execution_plan.topic_method,
        ),
        "link_cross_reference": lambda: LinkCrossReferenceOperator(),
        "validate_gate": lambda: ValidateGateOperator(),
        "deduplicate": lambda: DeduplicateOperator(),
        "synthesize_cu": lambda: SynthesizeCUOperator(
            max_cus=getattr(execution_plan, 'max_cus', 8),
        ),
        "embed": lambda: EmbedOperator(),
        "index_bm25": lambda: IndexBM25Operator(),
        "parse_records": lambda: ParseRecordsOperator(),
        "resolve_entities": lambda: ResolveEntitiesOperator(),
        "build_edges": lambda: BuildEdgesOperator(),
        "store_documents": lambda: StoreDocumentsOperator(),
        "infer_domains": lambda: InferDomainsOperator(),
        "filter_content": lambda: FilterContentOperator(),
        "extract_preferences": lambda: ExtractPreferencesOperator(),
        "sdlc_scan": lambda: SDLCScanOperator(config=getattr(execution_plan, 'pipeline_params', None) or {}),
    }

    operators = []
    for name in execution_plan.stages:
        factory = _REGISTRY.get(name)
        if factory:
            operators.append(factory())
    return operators
