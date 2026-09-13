"""Plugin API routes — exposes plugin metadata and management via REST."""

from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, HTTPException

from contextsynapse.plugins.registry import get_plugin_registry

logger = logging.getLogger(__name__)


def create_plugin_router() -> APIRouter:
    """Create the plugin management router."""
    router = APIRouter(prefix="/plugins", tags=["plugins"])

    @router.get("/")
    async def list_plugins():
        """List all registered plugins and their status."""
        registry = get_plugin_registry()
        return registry.status()

    @router.get("/dashboard/cards")
    async def get_dashboard_cards():
        """Get all dashboard cards from all plugins (for frontend sidebar)."""
        registry = get_plugin_registry()
        return {"cards": [c.to_dict() for c in registry.all_dashboard_cards()]}

    @router.get("/schemas")
    async def get_all_plugin_schemas():
        """Get all schemas from all plugins."""
        registry = get_plugin_registry()
        return {"schemas": [s.to_dict() for s in registry.all_schemas()]}

    @router.get("/{plugin_name}")
    async def get_plugin_info(plugin_name: str):
        """Get detailed info about a specific plugin."""
        registry = get_plugin_registry()
        plugin = registry.get_plugin(plugin_name)
        if not plugin:
            raise HTTPException(status_code=404, detail=f"Plugin '{plugin_name}' not found")
        return plugin.info()

    @router.get("/{plugin_name}/sensors")
    async def get_plugin_sensors(plugin_name: str):
        """Get sensor status for a plugin."""
        registry = get_plugin_registry()
        plugin = registry.get_plugin(plugin_name)
        if not plugin:
            raise HTTPException(status_code=404, detail=f"Plugin '{plugin_name}' not found")
        sensors = []
        for sensor in plugin.sensors():
            health = await sensor.health_check()
            sensors.append({
                "name": sensor.name,
                "interval_seconds": sensor.interval_seconds,
                "enabled": sensor.enabled,
                "batch_size": sensor.batch_size,
                "decay_config": sensor.decay_config(),
                "health": health,
            })
        return {"plugin": plugin_name, "sensors": sensors}

    return router
