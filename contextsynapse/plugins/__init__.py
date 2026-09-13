"""AIContextDB Plugin System — build vertical applications on the core graph engine."""

from contextsynapse.plugins.base import (
    VerticalPlugin,
    Sensor,
    DashboardCard,
    PluginRoute,
    PluginSchema,
    PluginMCPTool,
)
from contextsynapse.plugins.domain_interface import (
    DomainPlugin,
    EntityType,
    EdgeType,
    SectorDefinition,
    SensorDefinition,
    ExtractionPattern,
)
from contextsynapse.plugins.domain_loader import PluginLoader
from contextsynapse.plugins.loader import discover_plugins, load_plugin
from contextsynapse.plugins.registry import PluginRegistry, get_plugin_registry

__all__ = [
    # Vertical plugin system
    "VerticalPlugin",
    "Sensor",
    "DashboardCard",
    "PluginRoute",
    "PluginSchema",
    "PluginMCPTool",
    "discover_plugins",
    "load_plugin",
    "PluginRegistry",
    "get_plugin_registry",
    # Domain plugin system
    "DomainPlugin",
    "EntityType",
    "EdgeType",
    "SectorDefinition",
    "SensorDefinition",
    "ExtractionPattern",
    "PluginLoader",
]
