"""
Pipeline Context
================
Shared state object passed between pipeline stages.

Supports step-wise execution: after each stage the context is persisted,
allowing the user to review, edit, and resume.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class ClassificationResult:
    """Result from the input classifier."""
    file_type: str = "unknown"          # excel, csv, pdf, docx, txt, html, json, graph_file
    structure_type: str = "unknown"     # graph_columns, flat_records, hybrid_sheets, table_heavy, prose, mixed
    confidence: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)
    # For tabular: detected column roles
    column_roles: Dict[str, str] = field(default_factory=dict)  # col_name -> role (source, target, relation, property)
    sheet_roles: Dict[str, str] = field(default_factory=dict)   # sheet_name -> role (nodes, edges, matrix, data)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> ClassificationResult:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class StageResult:
    """Result from executing a single stage."""
    stage_name: str
    status: str = "pending"  # pending, running, completed, failed, skipped
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    summary: Dict[str, Any] = field(default_factory=dict)  # stage-specific summary for UI
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> StageResult:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class PipelineContext:
    """
    Shared state passed between pipeline stages.

    Serializable to JSON for persistence between step-wise executions.
    After each stage completes, the context is saved so the user can
    review intermediate results, edit data, and resume.
    """
    # Identity
    run_id: str = ""
    pipeline_id: str = ""
    graph_namespace: str = ""
    context_id: Optional[str] = None

    # Execution state
    status: str = "pending"          # pending, paused, running, completed, failed, cancelled
    mode: str = "run_all"            # run_all, step_by_step
    stages: List[str] = field(default_factory=list)       # ordered stage names to execute
    current_stage_index: int = 0     # index into stages list

    # Input
    source_filename: Optional[str] = None
    source_bytes_path: Optional[str] = None  # temp file path for binary data
    source_text: Optional[str] = None        # for text/URL input
    source_url: Optional[str] = None
    source_hash: str = ""                    # SHA-256 content hash for dedup
    intent: str = "graph_rag"               # graph_rag, build_graph, search_only, analytics

    # Stage outputs (accumulated)
    extraction_result: Optional[Dict[str, Any]] = None  # serialized ExtractionResult
    classification: Optional[Dict[str, Any]] = None     # serialized ClassificationResult
    tables: List[Dict[str, Any]] = field(default_factory=list)
    chunks: List[Dict[str, Any]] = field(default_factory=list)
    facts: List[Dict[str, Any]] = field(default_factory=list)
    nodes: List[Dict[str, Any]] = field(default_factory=list)
    edges: List[Dict[str, Any]] = field(default_factory=list)
    embeddings_count: int = 0
    vectors_stored: int = 0
    bm25_indexed: int = 0

    # User overrides (applied before next stage)
    user_overrides: Dict[str, Any] = field(default_factory=dict)

    # Stage log
    stage_results: List[Dict[str, Any]] = field(default_factory=list)

    # Sub-step log (granular progress within stages, persisted for review)
    sub_steps: List[Dict[str, Any]] = field(default_factory=list)

    # Pipeline params
    pipeline_params: Dict[str, Any] = field(default_factory=dict)  # llm_model, embedding_model, schema, etc.

    # Per-request credentials (not persisted to disk)
    github_token: Optional[str] = None  # overrides GITHUB_TOKEN env var for this run

    # Timestamps
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if not self.run_id:
            self.run_id = secrets.token_hex(12)
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at

    # ------------------------------------------------------------------
    # Execution helpers
    # ------------------------------------------------------------------

    @property
    def current_stage(self) -> Optional[str]:
        """Name of the current stage to execute."""
        if 0 <= self.current_stage_index < len(self.stages):
            return self.stages[self.current_stage_index]
        return None

    @property
    def is_done(self) -> bool:
        return self.current_stage_index >= len(self.stages)

    @property
    def remaining_stages(self) -> List[str]:
        return self.stages[self.current_stage_index:]

    def advance(self):
        """Move to the next stage."""
        self.current_stage_index += 1
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def record_stage(self, stage_name: str, status: str, summary: Optional[Dict] = None, error: Optional[str] = None):
        """Record the result of a stage execution."""
        now = datetime.now(timezone.utc).isoformat()
        result = StageResult(
            stage_name=stage_name,
            status=status,
            started_at=now,
            completed_at=now if status in ("completed", "failed", "skipped") else None,
            summary=summary or {},
            error=error,
        )
        self.stage_results.append(result.to_dict())
        self.updated_at = now

    def record_sub_step(self, message: str, progress: float = None):
        """Record a granular sub-step within the current stage."""
        self.sub_steps.append({
            "stage": self.current_stage or "unknown",
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "progress": progress,
        })

    def apply_overrides(self, overrides: Dict[str, Any]):
        """Apply user edits to intermediate data."""
        if "nodes" in overrides:
            self.nodes = overrides["nodes"]
        if "edges" in overrides:
            self.edges = overrides["edges"]
        if "chunks" in overrides:
            self.chunks = overrides["chunks"]
        if "facts" in overrides:
            self.facts = overrides["facts"]
        if "tables" in overrides:
            self.tables = overrides["tables"]
        if "classification" in overrides:
            self.classification = overrides["classification"]
        if "stages" in overrides:
            self.stages = overrides["stages"]
        self.user_overrides = overrides
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def skip_stage(self):
        """Skip the current stage."""
        if self.current_stage:
            self.record_stage(self.current_stage, "skipped")
            self.advance()

    def insert_stage(self, stage_name: str, position: Optional[int] = None):
        """Insert a stage at a given position (default: after current)."""
        pos = position if position is not None else self.current_stage_index + 1
        self.stages.insert(pos, stage_name)
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def remove_stage(self, stage_name: str):
        """Remove a pending stage by name (cannot remove completed stages)."""
        if stage_name in self.stages[self.current_stage_index:]:
            idx = self.stages.index(stage_name, self.current_stage_index)
            self.stages.pop(idx)
            self.updated_at = datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> PipelineContext:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in known}
        ctx = cls(**filtered)
        return ctx

    @classmethod
    def from_json(cls, s: str) -> PipelineContext:
        return cls.from_dict(json.loads(s))
