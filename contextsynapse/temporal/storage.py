"""Temporal Storage — per-namespace content snapshots for every node/edge version."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TemporalStorage:
    """Stores full property snapshots for every version of every node and edge.

    One JSON file per namespace: {base_path}/temporal/{namespace}/versions.json
    In-memory dict is the source of truth; file is persisted after every write.
    """

    def __init__(self, namespace: str, base_path: str = "contextcore_data"):
        self.namespace = namespace
        self._path = Path(base_path) / "temporal" / namespace / "versions.json"
        # entity_id -> list of snapshot dicts, oldest first
        self._versions: Dict[str, List[dict]] = {}

    def create_version(
        self,
        entity_id: str,
        entity_type: str,
        properties: dict,
        operation: str,
        timestamp: datetime,
        author: str = "",
        reason: str = "",
    ) -> None:
        """Snapshot the current properties for entity_id."""
        if entity_id not in self._versions:
            self._versions[entity_id] = []
        record = {
            "entity_id": entity_id,
            "entity_type": entity_type,
            "version": properties.get("version", len(self._versions.get(entity_id, [])) + 1),
            "properties": dict(properties),
            "operation": operation,
            "timestamp": timestamp.isoformat(),
            "author": author,
            "reason": reason,
        }
        self._versions[entity_id].append(record)
        self._save()

    def get_version(self, entity_id: str, version: int) -> Optional[dict]:
        """Return the snapshot for a specific version number, or None."""
        for record in self._versions.get(entity_id, []):
            if record.get("version") == version:
                return record
        return None

    def get_history(self, entity_id: str) -> List[dict]:
        """Return all snapshots for entity_id, oldest first."""
        return list(self._versions.get(entity_id, []))

    def load_versions(self) -> None:
        """Load snapshots from disk (called at startup)."""
        if not self._path.exists():
            return
        try:
            with open(self._path) as f:
                self._versions = json.load(f)
        except Exception as e:
            logger.warning("[TEMPORAL] Failed to load versions for %s: %s", self.namespace, e)
            self._versions = {}

    def save_versions(self) -> None:
        """Persist snapshots to disk."""
        self._save()

    def _save(self) -> None:
        import tempfile
        import os
        import sys
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self._versions, f)
            try:
                os.replace(tmp_path, str(self._path))
            except OSError:
                # Windows: os.replace can fail with WinError 5 if the target is locked.
                # Fall back to delete-then-rename.
                if sys.platform == "win32":
                    try:
                        os.remove(str(self._path))
                    except FileNotFoundError:
                        pass
                    os.rename(tmp_path, str(self._path))
                else:
                    raise
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
