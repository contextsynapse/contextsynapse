# contextcore/intelligence/geo_reference.py
"""GeoNode reference hierarchy — lookup and traversal.

Pre-loaded geographic reference data for resolving place names to
hierarchical GeoEntry objects. Supports exact match, alias match,
and case-insensitive lookup.

Usage:
    from contextsynapse.intelligence.geo_reference import GeoLookup

    geo = GeoLookup()
    entry = geo.lookup("California")  # GeoEntry(name="California", level="state", ...)
    chain = geo.get_hierarchy("San Francisco")  # [SF, CA, US, North America]
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from .geo_data import GEO_DATA

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GeoEntry:
    """A single geographic reference entry."""
    name: str
    level: str        # continent | region | country | state | city | district
    iso_code: str
    parent: str       # parent entry name (empty for continents)


class GeoLookup:
    """Geographic reference hierarchy with lookup and traversal."""

    def __init__(self):
        self._by_name: Dict[str, GeoEntry] = {}
        self._by_alias: Dict[str, str] = {}  # alias_lower -> canonical name
        self._by_level: Dict[str, List[GeoEntry]] = {}
        self._load()

    def _load(self):
        """Load reference data into lookup structures."""
        for name, level, iso_code, parent, aliases in GEO_DATA:
            entry = GeoEntry(name=name, level=level, iso_code=iso_code, parent=parent)
            self._by_name[name.lower()] = entry

            # Index by level
            self._by_level.setdefault(level, []).append(entry)

            # Index aliases
            for alias in aliases:
                self._by_alias[alias.lower()] = name

            # Also index the name itself and iso_code as aliases
            self._by_alias[name.lower()] = name
            if iso_code:
                self._by_alias[iso_code.lower()] = name

        logger.info("GeoLookup loaded: %d entries, %d aliases",
                     len(self._by_name), len(self._by_alias))

    def lookup(self, name: str) -> Optional[GeoEntry]:
        """Look up a place name (case-insensitive, alias-aware).

        Returns GeoEntry or None if not found.
        """
        if not name:
            return None
        key = name.strip().lower()

        # Alias match first (aliases can point to more specific entries)
        canonical = self._by_alias.get(key)
        if canonical:
            return self._by_name.get(canonical.lower())

        # Direct name match fallback
        entry = self._by_name.get(key)
        if entry:
            return entry

        return None

    def get_hierarchy(self, name: str) -> List[GeoEntry]:
        """Get the full hierarchy from a place up to its continent.

        Returns list ordered most-specific-first:
        [city, state, country, region, continent]
        """
        entry = self.lookup(name)
        if not entry:
            return []

        chain = [entry]
        visited = {entry.name.lower()}
        current = entry

        while current.parent:
            parent = self.lookup(current.parent)
            if not parent or parent.name.lower() in visited:
                break
            chain.append(parent)
            visited.add(parent.name.lower())
            current = parent

        return chain

    def get_by_level(self, level: str) -> List[GeoEntry]:
        """Get all entries at a given level (continent, region, country, state, city)."""
        return list(self._by_level.get(level, []))
