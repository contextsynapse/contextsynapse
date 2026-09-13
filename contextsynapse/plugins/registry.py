"""Plugin registry — manages plugin lifecycle and provides access to loaded plugins."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from contextsynapse.plugins.base import (
    DashboardCard,
    PluginMCPTool,
    PluginRoute,
    PluginSchema,
    PluginState,
    Sensor,
    VerticalPlugin,
)
from contextsynapse.plugins.loader import discover_plugins, validate_plugin

logger = logging.getLogger(__name__)

_global_registry: Optional["PluginRegistry"] = None


class PluginRegistry:
    """Central registry for all loaded plugins.

    The registry manages plugin lifecycle (discovery → validation →
    startup → shutdown) and provides aggregated access to all
    schemas, sensors, routes, MCP tools, and dashboard cards
    across all plugins.
    """

    def __init__(self):
        self._plugins: Dict[str, VerticalPlugin] = {}
        self._sensor_tasks: Dict[str, asyncio.Task] = {}
        self._started = False

    @property
    def plugins(self) -> Dict[str, VerticalPlugin]:
        return dict(self._plugins)

    def register(self, plugin: VerticalPlugin) -> bool:
        """Register a plugin manually (without entry point discovery)."""
        issues = validate_plugin(plugin)
        if issues:
            logger.error(
                "Plugin '%s' validation failed: %s", plugin.name, "; ".join(issues)
            )
            return False

        if plugin.name in self._plugins:
            logger.warning("Plugin '%s' already registered, replacing", plugin.name)

        self._plugins[plugin.name] = plugin
        logger.info("Registered plugin '%s' v%s", plugin.name, plugin.version)
        return True

    def discover_and_register(self, exclude: Optional[List[str]] = None) -> int:
        """Discover plugins via entry points and register them.

        Returns:
            Number of plugins successfully registered.
        """
        plugins = discover_plugins(exclude=exclude)
        count = 0
        for plugin in plugins:
            if self.register(plugin):
                count += 1
        return count

    async def startup_all(self, db: Any) -> None:
        """Start all registered plugins.

        Args:
            db: AIContextDB instance or GraphRegistry.
        """
        for name, plugin in self._plugins.items():
            try:
                plugin._state = PluginState.STARTING
                t0 = time.monotonic()
                await plugin.on_startup(db)
                elapsed = (time.monotonic() - t0) * 1000
                plugin._state = PluginState.RUNNING
                logger.info("Started plugin '%s' in %.1fms", name, elapsed)
            except Exception:
                plugin._state = PluginState.ERROR
                logger.exception("Failed to start plugin '%s'", name)

        self._started = True
        self._start_sensors(db)

    async def shutdown_all(self) -> None:
        """Shut down all plugins and cancel sensor tasks."""
        # Cancel sensor tasks first
        for task_name, task in self._sensor_tasks.items():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        self._sensor_tasks.clear()

        for name, plugin in self._plugins.items():
            try:
                await plugin.on_shutdown()
                logger.info("Stopped plugin '%s'", name)
            except Exception:
                logger.exception("Error stopping plugin '%s'", name)

        self._started = False

    def _start_sensors(self, db: Any) -> None:
        """Start background sensor collection tasks."""
        for plugin in self._plugins.values():
            if plugin.state != PluginState.RUNNING:
                continue
            for sensor in plugin.sensors():
                if not sensor.enabled:
                    continue
                task_name = f"{plugin.name}.{sensor.name}"
                task = asyncio.create_task(
                    self._sensor_loop(plugin, sensor, db),
                    name=task_name,
                )
                self._sensor_tasks[task_name] = task
                logger.info(
                    "Started sensor '%s' (every %ds)",
                    task_name,
                    sensor.interval_seconds,
                )

    async def _sensor_loop(
        self, plugin: VerticalPlugin, sensor: Sensor, db: Any
    ) -> None:
        """Run a sensor's collect() on its interval.

        Uses the StorageRouter to route data to the right store:
        - Entities + edges -> Graph (thin nodes)
        - Passages, facts -> DuckDB (content)
        - Prices -> DuckDB (time-series, no graph nodes)
        """
        from contextsynapse.storage.router import get_storage_router

        router = get_storage_router(graph=db, namespace=plugin.name)

        while True:
            try:
                if not sensor.enabled:
                    await asyncio.sleep(sensor.interval_seconds)
                    continue

                t0 = time.monotonic()
                results = await sensor.collect(db)
                elapsed_collect = (time.monotonic() - t0) * 1000

                # Route all items through the storage router
                t1 = time.monotonic()
                items = results[:sensor.batch_size]
                counts = router.ingest(items, namespace=plugin.name)
                elapsed_store = (time.monotonic() - t1) * 1000

                logger.debug(
                    "Sensor '%s.%s' collected %d items in %.1fms, stored in %.1fms "
                    "(graph: %d nodes + %d edges, content: %d rows, prices: %d)",
                    plugin.name, sensor.name, len(items),
                    elapsed_collect, elapsed_store,
                    counts["graph_nodes"], counts["graph_edges"],
                    counts["content_rows"], counts["prices"],
                )

                # Sleep AFTER collection so first run is immediate
                await asyncio.sleep(sensor.interval_seconds)

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Sensor '%s.%s' collection failed", plugin.name, sensor.name
                )
                await asyncio.sleep(60)

    # ── Aggregated access ──

    def get_plugin(self, name: str) -> Optional[VerticalPlugin]:
        return self._plugins.get(name)

    def all_schemas(self) -> List[PluginSchema]:
        """All schemas from all plugins."""
        schemas = []
        for plugin in self._plugins.values():
            schemas.extend(plugin.schemas())
        return schemas

    def all_sensors(self) -> List[tuple]:
        """All sensors as (plugin_name, sensor) tuples."""
        result = []
        for plugin in self._plugins.values():
            for sensor in plugin.sensors():
                result.append((plugin.name, sensor))
        return result

    def all_api_routers(self) -> List[tuple]:
        """All API routers as (plugin_name, PluginRoute) tuples."""
        result = []
        for plugin in self._plugins.values():
            for route in plugin.api_routers():
                result.append((plugin.name, route))
        return result

    def all_mcp_tools(self) -> List[tuple]:
        """All MCP tools as (plugin_name, PluginMCPTool) tuples."""
        result = []
        for plugin in self._plugins.values():
            for tool in plugin.mcp_tools():
                result.append((plugin.name, tool))
        return result

    def all_dashboard_cards(self) -> List[DashboardCard]:
        """All dashboard cards from all plugins, sorted by order."""
        cards = []
        for plugin in self._plugins.values():
            cards.extend(plugin.dashboard_cards())
        return sorted(cards, key=lambda c: c.order)

    def status(self) -> Dict[str, Any]:
        """Return registry status."""
        return {
            "plugins_registered": len(self._plugins),
            "plugins_running": sum(
                1 for p in self._plugins.values() if p.state == PluginState.RUNNING
            ),
            "sensors_active": len(self._sensor_tasks),
            "plugins": {
                name: plugin.info() for name, plugin in self._plugins.items()
            },
        }

    def emit_graph_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Forward a graph event to all running plugins."""
        for plugin in self._plugins.values():
            if plugin.state == PluginState.RUNNING:
                try:
                    plugin.on_graph_event(event_type, payload)
                except Exception:
                    logger.exception(
                        "Plugin '%s' failed handling event '%s'",
                        plugin.name,
                        event_type,
                    )


_registry_lock = threading.Lock()


def get_plugin_registry() -> PluginRegistry:
    """Get or create the global plugin registry singleton."""
    global _global_registry
    if _global_registry is None:
        with _registry_lock:
            if _global_registry is None:
                _global_registry = PluginRegistry()
    return _global_registry
