"""AIContextDB SDK response models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Agent:
    agent_id: str
    name: str
    role: str = "agent"
    platform: str = "app"
    status: str = "active"
    api_key: Optional[str] = None
    capabilities: List[str] = field(default_factory=list)


@dataclass
class SessionInfo:
    session_id: str
    name: str
    status: str = "active"
    member_count: int = 0
    created_at: Optional[str] = None
    graph_namespace: Optional[str] = None


@dataclass
class ContextExport:
    format: str
    preview: Any = None
    count: int = 0
    estimated_tokens: int = 0


@dataclass
class QualityReport:
    session_id: str
    overall_score: float = 0.0
    grade: str = "?"
    total_items: int = 0
    stale_items: int = 0
    fresh_items: int = 0


@dataclass
class Event:
    event_type: str
    agent_id: Optional[str] = None
    content_type: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


@dataclass
class Task:
    """A task in the project graph."""
    id: str
    title: str
    description: str = ""
    status: str = "open"
    priority: str = "medium"
    assigned_to: str = ""
    tags: str = ""
    depends_on: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Task":
        return cls(
            id=d.get("id", ""),
            title=d.get("title", ""),
            description=d.get("description", ""),
            status=d.get("status", "open"),
            priority=d.get("priority", "medium"),
            assigned_to=d.get("assigned_to", ""),
            tags=d.get("tags", ""),
        )
