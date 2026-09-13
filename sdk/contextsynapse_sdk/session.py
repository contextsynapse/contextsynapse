"""AIContextDB Session operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, List, Optional

from .models import ContextExport, QualityReport

if TYPE_CHECKING:
    from .client import AIContextDB


class Session:
    """Represents an AIContextDB session with context operations."""

    def __init__(self, client: AIContextDB, session_id: str, name: str = "", **kwargs):
        self._client = client
        self.session_id = session_id
        self.name = name
        self.status = kwargs.get("status", "active")
        self.member_count = kwargs.get("member_count", 0)

    def export(self, format: str = "messages", max_tokens: Optional[int] = None) -> Any:
        """Export session context in LLM-ready format.

        Args:
            format: 'messages' (OpenAI/Anthropic), 'prompt' (single string), or 'markdown'
            max_tokens: Optional token budget

        Returns:
            Formatted context (list of messages, string, etc.)
        """
        params = {"format": format}
        if max_tokens:
            params["max_tokens"] = max_tokens
        data = self._client._get(f"/context/sessions/{self.session_id}/export", params=params)
        return data.get("preview", data)

    def contribute(
        self,
        content: str,
        content_type: str = "text",
        role: str = "background",
        label: Optional[str] = None,
    ) -> dict:
        """Contribute context to this session.

        Args:
            content: The context content
            content_type: 'text', 'json', 'code', etc.
            role: Context role (background, retrieved, instruction, etc.)
            label: Optional label
        """
        payload: Dict[str, Any] = {
            "content": content,
            "content_type": content_type,
            "role": role,
        }
        if label:
            payload["label"] = label
        return self._client._post(f"/context/sessions/{self.session_id}/context", payload)

    def upload(self, filepath: str) -> dict:
        """Upload a file into this session's context.

        Args:
            filepath: Path to the file to upload
        """
        import os
        filename = os.path.basename(filepath)
        with open(filepath, "rb") as f:
            return self._client._upload(
                f"/context/sessions/{self.session_id}/documents/upload",
                f,
                filename,
            )

    def get_members(self) -> List[dict]:
        """List agents with access to this session."""
        data = self._client._get(f"/context/sessions/{self.session_id}/members")
        return data.get("members", [])

    def get_quality(self) -> QualityReport:
        """Get context quality report."""
        data = self._client._get(f"/dashboard/sessions/{self.session_id}/quality")
        return QualityReport(
            session_id=self.session_id,
            overall_score=data.get("overall_score", 0),
            grade=data.get("grade", "?"),
            total_items=data.get("total_items", 0),
            stale_items=data.get("stale_items", 0),
            fresh_items=data.get("fresh_items", 0),
        )

    def get_analytics(self) -> dict:
        """Get session usage analytics."""
        return self._client._get(f"/dashboard/sessions/{self.session_id}/analytics")

    async def subscribe(self) -> AsyncIterator:
        """Subscribe to real-time session events via WebSocket.

        Usage:
            async for event in session.subscribe():
                print(f"{event.agent_id}: {event.event_type}")
        """
        from .ws import subscribe_session
        async for event in subscribe_session(self._client, self.session_id):
            yield event

    def __repr__(self) -> str:
        return f"Session(id={self.session_id!r}, name={self.name!r})"
