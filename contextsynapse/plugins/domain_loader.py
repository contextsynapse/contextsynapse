"""Domain Plugin Loader — discovers and loads domain plugins.

Searches for domain.yaml files in:
  1. plugins/ directory (built-in)
  2. verticals/*/config/ (vertical-specific)
  3. ~/.contextsynapse/plugins/ (user-installed)

Usage:
    loader = PluginLoader()
    finance = loader.get_domain("finance")
    life_sci = loader.get_domain("life_science")
    all_domains = loader.list_domains()
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from contextsynapse.plugins.domain_interface import DomainPlugin

logger = logging.getLogger(__name__)

# Project root — two levels up from this file (contextsynapse/plugins/ -> project root)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Search paths for domain.yaml files, in priority order
_SEARCH_DIRS = [
    _PROJECT_ROOT / "plugins",                       # top-level domain plugins
    _PROJECT_ROOT / "contextsynapse" / "plugins",    # built-in
    _PROJECT_ROOT / "verticals",                     # vertical-specific (*/config/)
    Path.home() / ".contextsynapse" / "plugins",     # user-installed
]


class PluginLoader:
    """Discovers, loads, and caches domain plugins.

    The loader scans known directories for ``domain.yaml`` files,
    parses them into ``DomainPlugin`` instances, and caches them
    by ``domain_id``.  Plugins can also be registered at runtime.

    The active domain is determined by the ``CONTEXTSYNAPSE_DOMAIN``
    environment variable, defaulting to ``"finance"``.
    """

    def __init__(self) -> None:
        self._domains: Dict[str, DomainPlugin] = {}
        self._scan_complete = False
        self._scan_and_load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_domain(self, domain_id: str) -> Optional[DomainPlugin]:
        """Get a loaded domain plugin by its ID.

        Args:
            domain_id: The domain identifier (e.g. "finance", "life_science").

        Returns:
            DomainPlugin instance or None if not found.
        """
        return self._domains.get(domain_id)

    def list_domains(self) -> List[Dict[str, Any]]:
        """List all loaded domain plugins with summary metadata.

        Returns:
            List of dicts with keys: domain_id, domain_name, version,
            entity_count, sensor_count.
        """
        return [
            {
                "domain_id": plugin.domain_id,
                "domain_name": plugin.domain_name,
                "version": plugin.version,
                "entity_count": len(plugin.entity_types),
                "sensor_count": len(plugin.sensors),
            }
            for plugin in self._domains.values()
        ]

    def get_active_domain(self) -> Optional[DomainPlugin]:
        """Get the currently active domain plugin.

        Determined by the ``CONTEXTSYNAPSE_DOMAIN`` environment variable.
        Falls back to ``"finance"`` if not set.  Returns None if the
        requested domain is not loaded.

        Returns:
            The active DomainPlugin, or None.
        """
        domain_id = os.environ.get("CONTEXTSYNAPSE_DOMAIN", "finance")
        plugin = self.get_domain(domain_id)
        if plugin is None:
            logger.warning(
                "Active domain '%s' not found among loaded domains: %s",
                domain_id,
                list(self._domains.keys()),
            )
        return plugin

    def register_domain(self, plugin: DomainPlugin) -> None:
        """Register a domain plugin at runtime.

        If a plugin with the same ``domain_id`` is already loaded it
        will be replaced (with a warning).

        Args:
            plugin: A fully configured DomainPlugin instance.

        Raises:
            ValueError: If the plugin has no domain_id.
        """
        if not plugin.domain_id:
            raise ValueError("Cannot register a domain plugin without a domain_id")

        if plugin.domain_id in self._domains:
            logger.warning(
                "Replacing already-loaded domain '%s' (v%s -> v%s)",
                plugin.domain_id,
                self._domains[plugin.domain_id].version,
                plugin.version,
            )

        self._domains[plugin.domain_id] = plugin
        logger.info(
            "Registered domain '%s' v%s (%d entity types, %d sensors)",
            plugin.domain_id,
            plugin.version,
            len(plugin.entity_types),
            len(plugin.sensors),
        )

    def load_yaml(self, yaml_path: str) -> DomainPlugin:
        """Explicitly load a domain YAML file and register it.

        Args:
            yaml_path: Path to a domain.yaml file.

        Returns:
            The loaded DomainPlugin instance.
        """
        plugin = DomainPlugin.from_yaml(yaml_path)
        self.register_domain(plugin)
        return plugin

    @property
    def domain_count(self) -> int:
        """Number of loaded domain plugins."""
        return len(self._domains)

    # ------------------------------------------------------------------
    # Internal — scanning and loading
    # ------------------------------------------------------------------

    def _scan_and_load(self) -> None:
        """Scan all search directories for domain.yaml files and load them."""
        if self._scan_complete:
            return

        yaml_files = self._discover_yaml_files()

        for yf in yaml_files:
            try:
                plugin = DomainPlugin.from_yaml(str(yf))
                if plugin.domain_id in self._domains:
                    existing = self._domains[plugin.domain_id]
                    logger.debug(
                        "Domain '%s' already loaded from earlier path (keeping first, "
                        "skipping %s)",
                        plugin.domain_id,
                        yf,
                    )
                    continue
                self._domains[plugin.domain_id] = plugin
            except Exception:
                logger.exception("Failed to load domain YAML: %s", yf)

        self._scan_complete = True

        if self._domains:
            logger.info(
                "Domain loader ready: %d domain(s) loaded — %s",
                len(self._domains),
                ", ".join(sorted(self._domains.keys())),
            )
        else:
            logger.info("Domain loader ready: no domain YAML files found")

    def _discover_yaml_files(self) -> List[Path]:
        """Find all domain.yaml files across search directories.

        Search order:
          1. contextsynapse/plugins/domain.yaml, contextsynapse/plugins/*/domain.yaml
          2. verticals/*/config/domain.yaml
          3. ~/.contextsynapse/plugins/domain.yaml, ~/.contextsynapse/plugins/*/domain.yaml

        Returns:
            Deduplicated list of Path objects.
        """
        found: List[Path] = []
        seen: set = set()

        for search_dir in _SEARCH_DIRS:
            if not search_dir.exists():
                continue

            # Direct domain.yaml in the directory
            direct = search_dir / "domain.yaml"
            if direct.exists() and direct.resolve() not in seen:
                found.append(direct)
                seen.add(direct.resolve())

            # Subdirectories: look for domain.yaml or config/domain.yaml
            try:
                for child in sorted(search_dir.iterdir()):
                    if not child.is_dir():
                        continue

                    candidates = [
                        child / "domain.yaml",
                        child / "config" / "domain.yaml",
                    ]
                    for candidate in candidates:
                        if candidate.exists() and candidate.resolve() not in seen:
                            found.append(candidate)
                            seen.add(candidate.resolve())
            except PermissionError:
                logger.debug("Permission denied scanning: %s", search_dir)

        logger.debug("Discovered %d domain YAML file(s): %s", len(found), found)
        return found

    def __repr__(self) -> str:
        domains = ", ".join(sorted(self._domains.keys())) or "none"
        return f"PluginLoader(domains=[{domains}])"
