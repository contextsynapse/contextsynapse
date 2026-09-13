# tests/unit/test_geo_reference.py
"""Tests for GeoNode reference hierarchy."""
import pytest


class TestGeoLookup:
    """GeoLookup resolves place names to hierarchical GeoEntries."""

    def test_lookup_country_exact(self):
        from contextcore.intelligence.geo_reference import GeoLookup
        geo = GeoLookup()
        entry = geo.lookup("United States")
        assert entry is not None
        assert entry.name == "United States"
        assert entry.level == "country"
        assert entry.iso_code == "US"

    def test_lookup_country_case_insensitive(self):
        from contextcore.intelligence.geo_reference import GeoLookup
        geo = GeoLookup()
        entry = geo.lookup("united states")
        assert entry is not None
        assert entry.name == "United States"

    def test_lookup_city(self):
        from contextcore.intelligence.geo_reference import GeoLookup
        geo = GeoLookup()
        entry = geo.lookup("New York")
        assert entry is not None
        assert entry.level == "city"

    def test_lookup_unknown_returns_none(self):
        from contextcore.intelligence.geo_reference import GeoLookup
        geo = GeoLookup()
        assert geo.lookup("Xyzzyville") is None

    def test_hierarchy_city_to_continent(self):
        from contextcore.intelligence.geo_reference import GeoLookup
        geo = GeoLookup()
        chain = geo.get_hierarchy("San Francisco")
        levels = [e.level for e in chain]
        assert "city" in levels
        assert "state" in levels
        assert "country" in levels
        assert "continent" in levels
        # Order: most specific first
        assert levels.index("city") < levels.index("continent")

    def test_hierarchy_country(self):
        from contextcore.intelligence.geo_reference import GeoLookup
        geo = GeoLookup()
        chain = geo.get_hierarchy("India")
        assert len(chain) >= 2  # country + continent at minimum
        assert chain[0].name == "India"
        assert chain[-1].level == "continent"

    def test_get_by_level(self):
        from contextcore.intelligence.geo_reference import GeoLookup
        geo = GeoLookup()
        continents = geo.get_by_level("continent")
        assert len(continents) == 7
        names = [c.name for c in continents]
        assert "Asia" in names
        assert "Europe" in names

    def test_lookup_alias(self):
        from contextcore.intelligence.geo_reference import GeoLookup
        geo = GeoLookup()
        # Common aliases should resolve
        entry = geo.lookup("USA")
        assert entry is not None
        assert entry.name == "United States"

    def test_lookup_region(self):
        from contextcore.intelligence.geo_reference import GeoLookup
        geo = GeoLookup()
        entry = geo.lookup("European Union")
        assert entry is not None
        assert entry.level == "region"
