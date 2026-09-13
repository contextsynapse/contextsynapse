"""IngestContent — the universal interface between connectors and the stage pipeline."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List

@dataclass
class IngestContent:
    """Standardized content object produced by any connector."""
    content_type: str               # "text" | "conversation" | "structured"
    text: str = ""
    title: str = ""
    sections: List[Dict[str, Any]] = field(default_factory=list)
    messages: List[Dict[str, Any]] = field(default_factory=list)
    records: List[Dict[str, Any]] = field(default_factory=list)
    record_type: str = ""
    source: str = ""
    source_url: str = ""
    author: str = ""
    created_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class Chunk:
    """A unit of content to be processed by stage operators."""
    content: str
    index: int
    chunk_type: str = "paragraph"
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def token_estimate(self) -> int:
        return max(1, len(self.content) // 4)
