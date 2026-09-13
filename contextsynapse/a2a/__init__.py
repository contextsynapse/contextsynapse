"""
A2A (Agent-to-Agent) Protocol Support
=======================================
Implements Google's A2A protocol for agent interoperability.

- models.py      — Core dataclasses (Task, Message, Part, Artifact)
- task_manager.py — Task state machine + persistence
- handlers.py    — Server-side request handlers
- client.py      — Outbound HTTP client for calling external A2A agents
- discovery.py   — Remote agent card registry
- streaming.py   — SSE transport for real-time task events
"""

from .models import (
    TaskState,
    TextPart,
    FilePart,
    DataPart,
    Message,
    Artifact,
    TaskStatus,
    Task,
)

__all__ = [
    "TaskState", "TextPart", "FilePart", "DataPart",
    "Message", "Artifact", "TaskStatus", "Task",
]
