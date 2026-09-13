"""StageOperator -- the universal operator interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from ..ingest_content import Chunk
    from ..stage_executor import GraphContext


class StageOperator(ABC):
    """Base class for all pipeline stage operators."""

    name: str = "base"

    @abstractmethod
    def process(self, chunks: List["Chunk"], graph_ctx: "GraphContext") -> List["Chunk"]:
        ...
