"""
A2A Protocol Data Models
=========================
Core dataclasses matching the Google A2A specification.

Spec: https://google.github.io/A2A/
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Union


# ── Task States ───────────────────────────────────────────────────────

class TaskState(str, Enum):
    """A2A task lifecycle states."""
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


# Valid transitions enforced by TaskManager
VALID_TRANSITIONS = {
    TaskState.SUBMITTED: {TaskState.WORKING, TaskState.CANCELED},
    TaskState.WORKING: {TaskState.COMPLETED, TaskState.FAILED, TaskState.INPUT_REQUIRED, TaskState.CANCELED},
    TaskState.INPUT_REQUIRED: {TaskState.WORKING, TaskState.CANCELED},
    # Terminal states — no outbound transitions
    TaskState.COMPLETED: set(),
    TaskState.FAILED: set(),
    TaskState.CANCELED: set(),
}


# ── Message Parts ─────────────────────────────────────────────────────

@dataclass
class TextPart:
    """Plain text content."""
    text: str
    type: str = "text"

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "text": self.text}


@dataclass
class FilePart:
    """File content — either inline bytes (base64) or a URI reference."""
    name: str
    mime_type: str
    uri: Optional[str] = None
    bytes_b64: Optional[str] = None
    type: str = "file"

    def to_dict(self) -> Dict[str, Any]:
        f = {"name": self.name, "mimeType": self.mime_type}
        if self.uri:
            f["uri"] = self.uri
        if self.bytes_b64:
            f["bytes"] = self.bytes_b64
        return {"type": self.type, "file": f}


@dataclass
class DataPart:
    """Structured JSON data."""
    data: Dict[str, Any] = field(default_factory=dict)
    type: str = "data"

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "data": self.data}


Part = Union[TextPart, FilePart, DataPart]


def part_from_dict(d: Dict[str, Any]) -> Part:
    """Deserialize a Part from a dict."""
    t = d.get("type", "text")
    if t == "text":
        return TextPart(text=d.get("text", ""))
    elif t == "file":
        f = d.get("file", {})
        return FilePart(
            name=f.get("name", ""),
            mime_type=f.get("mimeType", "application/octet-stream"),
            uri=f.get("uri"),
            bytes_b64=f.get("bytes"),
        )
    elif t == "data":
        return DataPart(data=d.get("data", {}))
    return TextPart(text=str(d))


# ── Message ───────────────────────────────────────────────────────────

@dataclass
class Message:
    """A2A message — a sequence of parts sent by a user or agent."""
    role: str  # "user" or "agent"
    parts: List[Part] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "parts": [p.to_dict() for p in self.parts],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Message:
        return cls(
            role=d.get("role", "user"),
            parts=[part_from_dict(p) for p in d.get("parts", [])],
            metadata=d.get("metadata", {}),
        )

    @classmethod
    def text(cls, content: str, role: str = "user") -> Message:
        """Convenience: create a simple text message."""
        return cls(role=role, parts=[TextPart(text=content)])

    def get_text(self) -> str:
        """Extract concatenated text from all TextParts."""
        return "\n".join(p.text for p in self.parts if isinstance(p, TextPart))


# ── Artifact ──────────────────────────────────────────────────────────

@dataclass
class Artifact:
    """A2A artifact — a named output produced by a task."""
    name: str
    parts: List[Part] = field(default_factory=list)
    description: str = ""
    index: int = 0
    append: bool = False
    last_chunk: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "name": self.name,
            "parts": [p.to_dict() for p in self.parts],
            "index": self.index,
        }
        if self.description:
            d["description"] = self.description
        if self.append:
            d["append"] = True
        if not self.last_chunk:
            d["lastChunk"] = False
        if self.metadata:
            d["metadata"] = self.metadata
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Artifact:
        return cls(
            name=d.get("name", ""),
            parts=[part_from_dict(p) for p in d.get("parts", [])],
            description=d.get("description", ""),
            index=d.get("index", 0),
            append=d.get("append", False),
            last_chunk=d.get("lastChunk", True),
            metadata=d.get("metadata", {}),
        )


# ── Task Status ───────────────────────────────────────────────────────

@dataclass
class TaskStatus:
    """Current status of an A2A task."""
    state: TaskState
    message: Optional[Message] = None
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "state": self.state.value,
            "timestamp": self.timestamp,
        }
        if self.message:
            d["message"] = self.message.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> TaskStatus:
        msg = Message.from_dict(d["message"]) if d.get("message") else None
        return cls(
            state=TaskState(d.get("state", "submitted")),
            message=msg,
            timestamp=d.get("timestamp", ""),
        )


# ── Task ──────────────────────────────────────────────────────────────

@dataclass
class Task:
    """A2A task — the top-level unit of agent-to-agent work."""
    id: str = ""
    session_id: str = ""
    status: TaskStatus = field(default_factory=lambda: TaskStatus(state=TaskState.SUBMITTED))
    artifacts: List[Artifact] = field(default_factory=list)
    history: List[Message] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.id:
            self.id = secrets.token_hex(12)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "sessionId": self.session_id,
            "status": self.status.to_dict(),
            "artifacts": [a.to_dict() for a in self.artifacts],
            "history": [m.to_dict() for m in self.history],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Task:
        return cls(
            id=d.get("id", ""),
            session_id=d.get("sessionId", ""),
            status=TaskStatus.from_dict(d.get("status", {})),
            artifacts=[Artifact.from_dict(a) for a in d.get("artifacts", [])],
            history=[Message.from_dict(m) for m in d.get("history", [])],
            metadata=d.get("metadata", {}),
        )
