"""Base classes for vertical plugins built on AIContextDB.

A vertical plugin extends AIContextDB with domain-specific:
- Schemas (node/edge types for the domain)
- Sensors (data feeds that populate the graph on a schedule)
- API routes (FastAPI routers mounted on the core server)
- MCP tools (additional tools exposed via the MCP server)
- Dashboard cards (UI components injected into the frontend)

Example:
    from contextsynapse.plugins.base import VerticalPlugin, Sensor
    from fastapi import APIRouter

    class WeatherSensor(Sensor):
        name = "weather"
        interval_seconds = 600

        async def collect(self, db):
            data = await fetch_weather()
            return [{"id": f"weather_{city}", "type": "WeatherReading",
                     "properties": {"city": city, "temp": t}}
                    for city, t in data.items()]

    class WeatherVertical(VerticalPlugin):
        name = "weather"
        version = "0.1.0"
        description = "Weather data integration for AIContextDB"

        def sensors(self):
            return [WeatherSensor()]

        def api_routers(self):
            router = APIRouter(tags=["weather"])
            @router.get("/forecast/{city}")
            async def forecast(city: str):
                return {"city": city, "forecast": "sunny"}
            return [PluginRoute(router=router, prefix="/weather")]
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from fastapi import APIRouter

logger = logging.getLogger(__name__)


class PluginState(str, Enum):
    REGISTERED = "registered"
    STARTING = "starting"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class PluginSchema:
    """A graph schema definition provided by a plugin.

    Schemas define the node types, edge types, and property constraints
    for a domain. They drive ingestion, validation, and query optimization.
    """
    name: str
    version: str = "1.0"
    node_types: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    edge_types: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "node_types": self.node_types,
            "edge_types": self.edge_types,
            "description": self.description,
        }


@dataclass
class PluginRoute:
    """A FastAPI router provided by a plugin."""
    router: Any  # FastAPI APIRouter
    prefix: str = ""
    tags: List[str] = field(default_factory=list)
    dependencies: List[Any] = field(default_factory=list)


@dataclass
class PluginMCPTool:
    """An MCP tool definition provided by a plugin."""
    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: Any  # callable
    annotations: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DashboardCard:
    """A frontend dashboard card provided by a plugin.

    Cards are injected into the core dashboard UI. The component_path
    points to the JS component (served by the plugin or bundled).
    """
    id: str
    title: str
    component_path: str
    route: str
    icon: str = "box"
    category: str = "general"
    required_role: str = "viewer"
    order: int = 100
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "component_path": self.component_path,
            "route": self.route,
            "icon": self.icon,
            "category": self.category,
            "required_role": self.required_role,
            "order": self.order,
            "description": self.description,
        }


class Sensor(ABC):
    """A data source that feeds the graph on a schedule or trigger.

    Sensors collect data from external sources and return it as
    node/edge dicts for upsert into the graph. The core engine
    handles scheduling, retries, and graph writes.

    Subclasses must implement `name` and `collect()`.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique sensor name within the plugin."""

    @property
    def interval_seconds(self) -> int:
        """Collection interval. Default 5 minutes."""
        return 300

    @property
    def enabled(self) -> bool:
        """Whether this sensor is active. Override to add config-driven toggle."""
        return True

    @property
    def batch_size(self) -> int:
        """Max nodes to upsert per collection cycle."""
        return 1000

    @abstractmethod
    async def collect(self, db: Any) -> List[Dict[str, Any]]:
        """Collect data and return nodes/edges to upsert.

        Each dict should have:
            - id: str (deterministic for idempotent upsert)
            - type: str (node type matching a PluginSchema node_type)
            - properties: dict

        For edges, include source_id, target_id, edge_type.

        Args:
            db: AIContextDB instance for the target graph.

        Returns:
            List of node/edge dicts to upsert.
        """

    def decay_config(self) -> Optional[Dict[str, Any]]:
        """Return decay parameters for this sensor's data.

        Example: {"ttl_hours": 24, "decay_rate": 0.1, "min_confidence": 0.2}
        """
        return None

    async def health_check(self) -> Dict[str, Any]:
        """Check if the sensor's data source is reachable.

        Returns:
            {"healthy": bool, "message": str, "latency_ms": float}
        """
        return {"healthy": True, "message": "ok", "latency_ms": 0.0}


class VerticalPlugin(ABC):
    """Base class for vertical applications built on AIContextDB.

    A vertical plugin is a domain-specific extension that adds
    schemas, sensors, API routes, MCP tools, and dashboard cards
    to the core AIContextDB engine.

    Plugins are discovered via Python entry points:

        [project.entry-points."contextsynapse.plugins"]
        my_vertical = "my_package.plugin:MyVertical"

    The core server auto-discovers and loads all installed plugins
    at startup.
    """

    def __init__(self):
        self._state: PluginState = PluginState.REGISTERED
        self._db: Optional[Any] = None

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique plugin name (lowercase, no spaces). e.g. 'finance', 'legal'."""

    @property
    @abstractmethod
    def version(self) -> str:
        """Semantic version string. e.g. '0.1.0'."""

    @property
    def description(self) -> str:
        """Human-readable description."""
        return ""

    @property
    def author(self) -> str:
        """Plugin author."""
        return ""

    @property
    def requires_core_version(self) -> str:
        """Minimum AIContextDB version required. e.g. '>=1.0.0'."""
        return ">=0.0.0"

    @property
    def state(self) -> PluginState:
        return self._state

    # ── Schema ──

    def schemas(self) -> List[PluginSchema]:
        """Return graph schemas this vertical needs.

        Schemas are registered with the core schema registry on startup.
        They define node types, edge types, and property constraints
        for the domain.
        """
        return []

    # ── Data Feeds ──

    def sensors(self) -> List[Sensor]:
        """Return sensor instances this vertical provides.

        Sensors are scheduled by the core engine. Each sensor's
        collect() method is called on its interval_seconds schedule.
        """
        return []

    # ── API ──

    def api_routers(self) -> List[PluginRoute]:
        """Return FastAPI routers to mount on the core server.

        Routes are mounted under /v1/{plugin.name}/ by default.
        """
        return []

    # ── MCP ──

    def mcp_tools(self) -> List[PluginMCPTool]:
        """Return additional MCP tool definitions.

        Tools are registered with the MCP server and become available
        to Claude/Copilot agents.
        """
        return []

    # ── UI ──

    def dashboard_cards(self) -> List[DashboardCard]:
        """Return UI cards to inject into the dashboard.

        Cards appear in the sidebar and as pages/widgets in the
        core dashboard. The component_path points to a JS file
        served by the plugin.
        """
        return []

    # ── Lifecycle ──

    async def on_startup(self, db: Any) -> None:
        """Called when the core server starts.

        Use this to register schemas, seed initial data, validate
        configuration, or set up background tasks.

        Args:
            db: AIContextDB instance (or GraphRegistry for multi-graph).
        """
        self._db = db
        self._state = PluginState.RUNNING

    async def on_shutdown(self) -> None:
        """Called when the core server shuts down.

        Use this to clean up resources, flush buffers, or save state.
        """
        self._state = PluginState.STOPPED

    def on_graph_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Called when a graph event occurs (node added, edge removed, etc.).

        Override to react to graph changes relevant to your vertical.

        Args:
            event_type: e.g. 'node_added', 'edge_removed', 'graph_saved'
            payload: Event-specific data.
        """
        pass

    # ── Metadata ──

    def info(self) -> Dict[str, Any]:
        """Return plugin metadata as a dict."""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "state": self._state.value,
            "requires_core_version": self.requires_core_version,
            "schemas": [s.to_dict() for s in self.schemas()],
            "sensors": [{"name": s.name, "interval": s.interval_seconds,
                         "enabled": s.enabled} for s in self.sensors()],
            "routes": [{"prefix": r.prefix, "tags": r.tags}
                       for r in self.api_routers()],
            "mcp_tools": [{"name": t.name, "description": t.description}
                          for t in self.mcp_tools()],
            "dashboard_cards": [c.to_dict() for c in self.dashboard_cards()],
        }
