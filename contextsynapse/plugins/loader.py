"""Plugin discovery and loading via Python entry points.

Plugins register themselves in pyproject.toml:

    [project.entry-points."contextsynapse.plugins"]
    finance = "qgraph_finance.plugin:FinanceVertical"

The loader discovers all installed plugins and instantiates them.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional, Type

from contextsynapse.plugins.base import PluginState, VerticalPlugin

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "contextsynapse.plugins"


def discover_plugins(
    exclude: Optional[List[str]] = None,
) -> List[VerticalPlugin]:
    """Discover and instantiate all installed plugins.

    Uses Python entry points (PEP 621) to find plugins registered
    under the 'contextsynapse.plugins' group.

    Args:
        exclude: List of plugin names to skip.

    Returns:
        List of instantiated plugin objects.
    """
    exclude = set(exclude or [])
    plugins: List[VerticalPlugin] = []

    try:
        from importlib.metadata import entry_points
    except ImportError:
        from importlib_metadata import entry_points  # Python 3.9 fallback

    try:
        eps = entry_points(group=ENTRY_POINT_GROUP)
    except TypeError:
        # Python 3.9: entry_points() returns a dict
        all_eps = entry_points()
        eps = all_eps.get(ENTRY_POINT_GROUP, [])

    for ep in eps:
        if ep.name in exclude:
            logger.info("Skipping excluded plugin: %s", ep.name)
            continue

        try:
            t0 = time.monotonic()
            cls = ep.load()
            if not (isinstance(cls, type) and issubclass(cls, VerticalPlugin)):
                logger.warning(
                    "Entry point '%s' does not point to a VerticalPlugin subclass, skipping",
                    ep.name,
                )
                continue

            instance = cls()
            elapsed = (time.monotonic() - t0) * 1000
            logger.info(
                "Discovered plugin '%s' v%s (loaded in %.1fms)",
                instance.name,
                instance.version,
                elapsed,
            )
            plugins.append(instance)

        except Exception:
            logger.exception("Failed to load plugin '%s'", ep.name)

    return plugins


def load_plugin(
    module_path: str, class_name: str
) -> Optional[VerticalPlugin]:
    """Load a single plugin by module path and class name.

    For explicit loading without entry points (e.g. dev/testing).

    Args:
        module_path: e.g. "qgraph_finance.plugin"
        class_name: e.g. "FinanceVertical"

    Returns:
        Instantiated plugin or None on failure.
    """
    try:
        import importlib

        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)

        if not (isinstance(cls, type) and issubclass(cls, VerticalPlugin)):
            logger.error("%s.%s is not a VerticalPlugin subclass", module_path, class_name)
            return None

        instance = cls()
        logger.info("Loaded plugin '%s' v%s", instance.name, instance.version)
        return instance

    except Exception:
        logger.exception("Failed to load plugin %s.%s", module_path, class_name)
        return None


def validate_plugin(plugin: VerticalPlugin) -> List[str]:
    """Validate a plugin's configuration. Returns list of issues (empty = valid)."""
    issues: List[str] = []

    if not plugin.name or not plugin.name.strip():
        issues.append("Plugin name is empty")
    elif not plugin.name.replace("_", "").replace("-", "").isalnum():
        issues.append(f"Plugin name '{plugin.name}' contains invalid characters")
    if not plugin.version:
        issues.append("Plugin version is empty")

    seen_sensor_names = set()
    for sensor in plugin.sensors():
        if sensor.name in seen_sensor_names:
            issues.append(f"Duplicate sensor name: '{sensor.name}'")
        seen_sensor_names.add(sensor.name)
        if sensor.interval_seconds < 10:
            issues.append(f"Sensor '{sensor.name}' interval too short ({sensor.interval_seconds}s, min 10s)")

    seen_schema_names = set()
    for schema in plugin.schemas():
        if schema.name in seen_schema_names:
            issues.append(f"Duplicate schema name: '{schema.name}'")
        seen_schema_names.add(schema.name)

    for card in plugin.dashboard_cards():
        if not card.id:
            issues.append("Dashboard card has empty id")
        if not card.route:
            issues.append(f"Dashboard card '{card.id}' has empty route")

    return issues
