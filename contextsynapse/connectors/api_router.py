"""
Connector API Router
=====================
Endpoints for managing live connectors (Kafka).
Note: RSS, Webhook, Scraper have been replaced by the unified ingestion pipeline.
"""

from __future__ import annotations
import logging
from fastapi import APIRouter, Body, Depends, HTTPException, Query

logger = logging.getLogger(__name__)


def create_connector_router(graph_registry, context_manager, session_manager, user_auth):
    """Create connector management router."""

    router = APIRouter(prefix="/connectors", tags=["connectors"])

    # Lazy-init connector manager
    _manager = None

    def _get_manager():
        nonlocal _manager
        if _manager is None:
            from .base import ConnectorManager
            _manager = ConnectorManager(
                graph_registry=graph_registry,
                context_manager=context_manager,
                session_manager=session_manager,
            )
        return _manager

    @router.post("")
    async def create_connector(req: dict = Body(...), user=Depends(user_auth)):
        """Create a new live connector.

        Body: {
            type: "kafka",
            name: "Kafka Topic",
            target_context_id: "ctx_123",
            config: { brokers: "...", topic: "...", etc },
            pipeline: "builtin:knowledge-graph",
            llm_model: "groq:llama-3.3-70b",
            poll_interval_minutes: 5
        }
        """
        from .base import ConnectorConfig

        ctype = req.get("type", "")
        if ctype not in ("kafka",):
            raise HTTPException(400, f"Unknown connector type: {ctype}")

        config = ConnectorConfig(
            connector_type=ctype,
            name=req.get("name", f"{ctype} connector"),
            target_context_id=req.get("target_context_id", ""),
            pipeline=req.get("pipeline", "builtin:knowledge-graph"),
            llm_model=req.get("llm_model", ""),
            poll_interval_minutes=req.get("poll_interval_minutes", 5),
            config=req.get("config", {}),
        )

        if not config.target_context_id:
            raise HTTPException(400, "target_context_id is required")

        # Create the appropriate connector
        connector = _create_connector(ctype, config)
        cid = _get_manager().register(connector)

        return {"connector_id": cid, **config.to_dict()}

    @router.get("")
    async def list_connectors(user=Depends(user_auth)):
        """List all configured connectors."""
        configs = _get_manager().list_connectors()
        statuses = {s.connector_id: s.to_dict() for s in _get_manager().list_status()}
        return {
            "connectors": [
                {**c.to_dict(), "status": statuses.get(c.connector_id, {})}
                for c in configs
            ]
        }

    @router.delete("/{connector_id}")
    async def delete_connector(connector_id: str, user=Depends(user_auth)):
        ok = _get_manager().unregister(connector_id)
        if not ok:
            raise HTTPException(404, "Connector not found")
        return {"deleted": True}

    @router.post("/{connector_id}/poll")
    async def poll_connector(connector_id: str, user=Depends(user_auth)):
        """Manually trigger a poll for this connector."""
        docs = _get_manager().poll_one(connector_id)
        return {"connector_id": connector_id, "new_documents": len(docs)}

    @router.post("/poll-all")
    async def poll_all(user=Depends(user_auth)):
        """Poll all active connectors."""
        results = _get_manager().poll_all()
        return {"results": results, "total_new": sum(results.values())}


    @router.get("/{connector_id}/status")
    async def get_status(connector_id: str, user=Depends(user_auth)):
        connector = _get_manager().get(connector_id)
        if not connector:
            raise HTTPException(404, "Connector not found")
        return connector.status.to_dict()

    return router


def _create_connector(ctype: str, config):
    """Factory: create connector by type."""
    if ctype == "kafka":
        from .kafka_connector import KafkaConnector
        return KafkaConnector(config)
    else:
        raise ValueError(f"Unknown connector type: {ctype}")
