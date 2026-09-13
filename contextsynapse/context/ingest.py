"""
Unified Ingestion
=================
Single ``ingest()`` entry-point that auto-classifies data and routes it to the
appropriate store (graph, vector, document, blob).
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from .agents import AgentRegistry, ProvenanceRecord
from ..security.data_security import PIIDetector

logger = logging.getLogger(__name__)

_pii_detector = PIIDetector()

# MIME types considered "binary / blob"
_BLOB_MIMES = {
    "image/", "audio/", "video/",
    "application/pdf", "application/zip",
    "application/octet-stream",
}


def _is_blob_mime(mime: str) -> bool:
    for prefix in _BLOB_MIMES:
        if mime.startswith(prefix):
            return True
    return False


def _looks_like_json(text: str) -> bool:
    stripped = text.strip()
    return (stripped.startswith("{") and stripped.endswith("}")) or \
           (stripped.startswith("[") and stripped.endswith("]"))


def _looks_like_xml(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith("<") and stripped.endswith(">")


class UnifiedIngestor:
    """
    Auto-classify and ingest data into the right backing store for a session.

    Delegates to:
      - Structured (dict/list) → graph nodes
      - Unstructured (text) → graph node + vector embedding (when available)
      - Semi-structured (JSON/XML/YAML string) → parse → structured path
      - Binary (bytes) → BlobStore

    All writes record provenance via the AgentRegistry.
    """

    def __init__(
        self,
        session_manager,
        agent_registry: AgentRegistry,
        blob_store=None,
        embedding_hooks=None,
    ):
        self._session_mgr = session_manager
        self._agent_registry = agent_registry
        self._blob_store = blob_store
        self._embedding_hooks = embedding_hooks  # Optional EmbeddingHooks instance

    def ingest(
        self,
        session_id: str,
        agent_id: str,
        data: Union[str, bytes, dict, list],
        data_type: str = "auto",
        metadata: Optional[Dict[str, Any]] = None,
        source: Optional[str] = None,
        mime_type: Optional[str] = None,
        filename: Optional[str] = None,
        tags: Optional[List[str]] = None,
        sensitivity: Optional[str] = None,
        auto_pii_scan: bool = True,
    ) -> Dict[str, Any]:
        """
        Ingest data into a context session.

        Args:
            tags: Classification tags applied to the ingested items.
            sensitivity: Override sensitivity level (public|internal|confidential|restricted).
                         If None, determined automatically (public unless PII detected).
            auto_pii_scan: When True (default), scan text content for PII and
                           auto-escalate sensitivity to ``confidential`` if found.

        Returns::

            {
                "node_ids": [...],
                "edge_ids": [...],
                "blob_id": str | None,
                "data_type": "structured" | "unstructured" | "semi_structured" | "binary",
                "tags": [...],
                "sensitivity": str,
                "pii_detected": bool,
                "pii_types": [...],
            }
        """
        # Set provenance context for all graph writes during ingestion
        from contextsynapse.core.write_context import set_write_context
        set_write_context(agent_id=agent_id, origin="ingest", verified=True)

        session = self._session_mgr.get_session(session_id)
        if session is None:
            raise ValueError(f"Session {session_id} not found")

        meta = metadata or {}
        applied_tags = list(tags or [])
        pii_detected = False
        pii_types: List[str] = []

        # --- PII auto-scan on text content ---
        if auto_pii_scan and isinstance(data, str):
            pii_result = _pii_detector.detect_pii(data)
            if pii_result:
                pii_detected = True
                pii_types = list(pii_result.keys())
                if "pii" not in applied_tags:
                    applied_tags.append("pii")
                for ptype in pii_types:
                    tag = f"pii:{ptype}"
                    if tag not in applied_tags:
                        applied_tags.append(tag)
        elif auto_pii_scan and isinstance(data, dict):
            for v in data.values():
                if isinstance(v, str):
                    pii_result = _pii_detector.detect_pii(v)
                    if pii_result:
                        pii_detected = True
                        pii_types = list(set(pii_types) | set(pii_result.keys()))
            if pii_detected:
                if "pii" not in applied_tags:
                    applied_tags.append("pii")

        # Determine sensitivity
        if sensitivity is not None:
            applied_sensitivity = sensitivity
        elif pii_detected:
            applied_sensitivity = "confidential"
        else:
            applied_sensitivity = "public"

        # Store tags + sensitivity in metadata so graph nodes carry them
        meta["_ctx_tags"] = applied_tags
        meta["_ctx_sensitivity"] = applied_sensitivity
        meta["_ctx_pii_detected"] = pii_detected

        result: Dict[str, Any] = {
            "node_ids": [],
            "edge_ids": [],
            "blob_id": None,
            "data_type": data_type,
            "tags": applied_tags,
            "sensitivity": applied_sensitivity,
            "pii_detected": pii_detected,
            "pii_types": pii_types,
        }

        # --- auto-classification ---
        if data_type == "auto":
            data_type = self._classify(data, mime_type)
            result["data_type"] = data_type

        # --- route to handler ---
        if data_type == "binary":
            result = {**self._ingest_binary(session, agent_id, data, meta, source, mime_type, filename, result), **{
                "tags": applied_tags, "sensitivity": applied_sensitivity,
                "pii_detected": pii_detected, "pii_types": pii_types,
            }}
        elif data_type == "structured":
            result = {**self._ingest_structured(session, agent_id, data, meta, source, result), **{
                "tags": applied_tags, "sensitivity": applied_sensitivity,
                "pii_detected": pii_detected, "pii_types": pii_types,
            }}
        elif data_type == "semi_structured":
            result = {**self._ingest_semi_structured(session, agent_id, data, meta, source, result), **{
                "tags": applied_tags, "sensitivity": applied_sensitivity,
                "pii_detected": pii_detected, "pii_types": pii_types,
            }}
        else:  # unstructured
            result = {**self._ingest_unstructured(session, agent_id, data, meta, source, result), **{
                "tags": applied_tags, "sensitivity": applied_sensitivity,
                "pii_detected": pii_detected, "pii_types": pii_types,
            }}

        # --- provenance ---
        for nid in result["node_ids"]:
            self._agent_registry.record_provenance(ProvenanceRecord(
                session_id=session_id,
                agent_id=agent_id,
                operation="ingest",
                target_type="node",
                target_id=nid,
                metadata={
                    "data_type": data_type, "source": source or "api",
                    "tags": applied_tags, "sensitivity": applied_sensitivity,
                    "pii_detected": pii_detected,
                },
            ))

        if result.get("blob_id"):
            self._agent_registry.record_provenance(ProvenanceRecord(
                session_id=session_id,
                agent_id=agent_id,
                operation="ingest",
                target_type="blob",
                target_id=result["blob_id"],
                metadata={"data_type": "binary", "source": source or "api"},
            ))

        # --- Auto-embed via hooks ---
        if self._embedding_hooks:
            for nid in result.get("node_ids", []):
                try:
                    self._embedding_hooks.on_ingest(
                        session_id=session_id,
                        node_id=nid,
                        data=data,
                        data_type=data_type,
                        mime_type=mime_type,
                        metadata={"agent_id": agent_id, "source": source or "ingest"},
                    )
                except Exception as e:
                    logger.warning(f"Embedding hook failed for node {nid}: {e}")

        # Touch session timestamp
        self._session_mgr.touch_session(session_id)

        return result

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def _classify(self, data: Any, mime_type: Optional[str] = None) -> str:
        if isinstance(data, bytes):
            return "binary"
        if isinstance(data, (dict, list)):
            return "structured"
        if isinstance(data, str):
            if mime_type and _is_blob_mime(mime_type):
                return "binary"
            if _looks_like_json(data) or _looks_like_xml(data):
                return "semi_structured"
            return "unstructured"
        return "unstructured"

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _ingest_structured(
        self, session, agent_id, data, meta, source, result
    ) -> Dict[str, Any]:
        """Dict/list → graph nodes."""
        graph = self._get_graph(session)
        if graph is None:
            return result

        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            node_id = item.pop("id", None) or str(uuid.uuid4())
            label = item.pop("type", None) or item.pop("label", None) or "Entity"
            props = {**item, **meta}
            props["_ctx_session_id"] = session.session_id
            props["_ctx_agent_id"] = agent_id
            props["_ctx_source"] = source or "ingest"

            try:
                from ..core.graph_structures import GraphNode
                node = GraphNode(id=node_id, label=label, properties=props)
                graph.add_node(node)
                result["node_ids"].append(node_id)
            except Exception as e:
                logger.warning(f"Failed to add node: {e}")
        return result

    def _ingest_unstructured(
        self, session, agent_id, data, meta, source, result
    ) -> Dict[str, Any]:
        """Plain text → graph node of type TextChunk."""
        graph = self._get_graph(session)
        if graph is None:
            return result

        text = str(data)
        node_id = str(uuid.uuid4())
        props = {
            "content": text,
            "char_count": len(text),
            "_ctx_session_id": session.session_id,
            "_ctx_agent_id": agent_id,
            "_ctx_source": source or "ingest",
            **meta,
        }
        try:
            from ..core.graph_structures import GraphNode
            node = GraphNode(id=node_id, label="TextChunk", properties=props)
            graph.add_node(node)
            result["node_ids"].append(node_id)
        except Exception as e:
            logger.warning(f"Failed to add text node: {e}")
        return result

    def _ingest_semi_structured(
        self, session, agent_id, data, meta, source, result
    ) -> Dict[str, Any]:
        """JSON/XML string → parse → structured path."""
        text = str(data).strip()
        try:
            parsed = json.loads(text)
            return self._ingest_structured(session, agent_id, parsed, meta, source, result)
        except (json.JSONDecodeError, TypeError):
            pass
        # Fallback: treat as unstructured
        return self._ingest_unstructured(session, agent_id, data, meta, source, result)

    def _ingest_binary(
        self, session, agent_id, data, meta, source, mime_type, filename, result
    ) -> Dict[str, Any]:
        """Binary data → blob store + reference node."""
        if self._blob_store is None:
            logger.warning("BlobStore not configured — storing reference node only")
            return self._ingest_unstructured(
                session, agent_id,
                f"[Binary data: {filename or 'unknown'}, {mime_type or 'unknown'}]",
                meta, source, result,
            )

        raw = data if isinstance(data, bytes) else base64.b64decode(data)
        blob_info = self._blob_store.store(
            session_id=session.session_id,
            data=raw,
            filename=filename or "untitled",
            mime_type=mime_type or "application/octet-stream",
            agent_id=agent_id,
        )
        result["blob_id"] = blob_info["blob_id"]

        # Create reference node in graph
        graph = self._get_graph(session)
        if graph:
            node_id = blob_info["blob_id"]
            props = {
                "filename": filename,
                "mime_type": mime_type,
                "size_bytes": blob_info.get("size_bytes", len(raw)),
                "content_hash": blob_info.get("content_hash"),
                "storage_path": blob_info.get("storage_path", ""),
                "_ctx_session_id": session.session_id,
                "_ctx_agent_id": agent_id,
                "_ctx_source": source or "ingest",
                **meta,
            }
            try:
                from ..core.graph_structures import GraphNode
                node = GraphNode(id=node_id, label="Blob", properties=props)
                graph.add_node(node)
                result["node_ids"].append(node_id)
            except Exception as e:
                logger.warning(f"Failed to add blob reference node: {e}")

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_graph(self, session):
        """Get the backing graph for a session."""
        if self._session_mgr._graph_registry is None:
            return None
        return self._session_mgr._graph_registry.get_graph(
            session.graph_namespace, load_if_missing=True
        )
