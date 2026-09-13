"""
Example Vertical Plugin: Weather Data Integration
==================================================

This example shows how to build a vertical application on ContextCore.
It demonstrates all plugin extension points:
- Custom schemas (WeatherReading, WeatherAlert node types)
- Data sensors (fetching weather data on a schedule)
- API routes (custom REST endpoints)
- MCP tools (Claude/Copilot integration)
- Dashboard cards (UI components)

Installation:
    pip install -e examples/weather_vertical/

    # Or register in pyproject.toml:
    [project.entry-points."contextsynapse.plugins"]
    weather = "weather_vertical.plugin:WeatherVertical"

Usage:
    The plugin auto-registers when the ContextCore server starts.
    Weather data appears as nodes in your graph, queryable via AIQL:

        FIND NODES WHERE type = 'WeatherReading' AND city = 'NYC'
"""
from __future__ import annotations

import logging
import random
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Query

from contextsynapse.plugins.base import (
    DashboardCard,
    PluginMCPTool,
    PluginRoute,
    PluginSchema,
    Sensor,
    VerticalPlugin,
)

logger = logging.getLogger(__name__)


# ── Schemas ──────────────────────────────────────────────────────────────

WEATHER_SCHEMA = PluginSchema(
    name="weather",
    version="1.0",
    description="Weather data schema for meteorological readings and alerts",
    node_types={
        "WeatherReading": {
            "properties": {
                "city": {"type": "string", "required": True},
                "country": {"type": "string", "required": True},
                "temperature_c": {"type": "float", "required": True},
                "humidity_pct": {"type": "float"},
                "wind_speed_kmh": {"type": "float"},
                "conditions": {"type": "string"},  # "sunny", "cloudy", "rain", etc.
                "recorded_at": {"type": "datetime", "required": True},
            },
            "description": "A single weather observation for a city",
        },
        "WeatherAlert": {
            "properties": {
                "city": {"type": "string", "required": True},
                "severity": {"type": "string", "enum": ["low", "medium", "high", "extreme"]},
                "alert_type": {"type": "string"},  # "heat", "storm", "flood", etc.
                "message": {"type": "string"},
                "issued_at": {"type": "datetime"},
                "expires_at": {"type": "datetime"},
            },
            "description": "A weather alert or warning",
        },
        "City": {
            "properties": {
                "name": {"type": "string", "required": True},
                "country": {"type": "string", "required": True},
                "latitude": {"type": "float"},
                "longitude": {"type": "float"},
                "timezone": {"type": "string"},
            },
            "description": "A city being monitored for weather",
        },
    },
    edge_types={
        "OBSERVED_IN": {
            "source": "WeatherReading",
            "target": "City",
            "description": "Links a reading to the city it was observed in",
        },
        "ALERT_FOR": {
            "source": "WeatherAlert",
            "target": "City",
            "description": "Links an alert to the affected city",
        },
    },
)


# ── Sensors ──────────────────────────────────────────────────────────────

class WeatherSensor(Sensor):
    """Simulated weather sensor that generates readings for demo cities.

    In a real plugin, this would call a weather API like OpenWeatherMap.
    """

    name = "weather_readings"
    interval_seconds = 300  # every 5 minutes

    CITIES = [
        {"name": "New York", "country": "US", "lat": 40.71, "lon": -74.01},
        {"name": "London", "country": "GB", "lat": 51.51, "lon": -0.13},
        {"name": "Tokyo", "country": "JP", "lat": 35.68, "lon": 139.69},
        {"name": "Mumbai", "country": "IN", "lat": 19.08, "lon": 72.88},
        {"name": "Sydney", "country": "AU", "lat": -33.87, "lon": 151.21},
    ]

    async def collect(self, db: Any) -> List[Dict[str, Any]]:
        """Generate weather readings for demo cities."""
        now = datetime.now(timezone.utc).isoformat()
        results = []

        for city in self.CITIES:
            # Simulated data — replace with real API call
            temp = round(random.uniform(-5, 40), 1)
            humidity = round(random.uniform(20, 95), 1)
            wind = round(random.uniform(0, 80), 1)
            conditions = random.choice(["sunny", "cloudy", "rain", "snow", "fog", "storm"])

            # City node (idempotent — same ID means upsert)
            city_id = f"city_{city['name'].lower().replace(' ', '_')}"
            results.append({
                "id": city_id,
                "type": "City",
                "properties": {
                    "name": city["name"],
                    "country": city["country"],
                    "latitude": city["lat"],
                    "longitude": city["lon"],
                },
            })

            # Weather reading node
            reading_id = f"weather_{city_id}_{now[:13].replace(':', '')}"
            results.append({
                "id": reading_id,
                "type": "WeatherReading",
                "properties": {
                    "city": city["name"],
                    "country": city["country"],
                    "temperature_c": temp,
                    "humidity_pct": humidity,
                    "wind_speed_kmh": wind,
                    "conditions": conditions,
                    "recorded_at": now,
                },
            })

            # Edge: reading -> city
            results.append({
                "source_id": reading_id,
                "target_id": city_id,
                "edge_type": "OBSERVED_IN",
                "properties": {"recorded_at": now},
            })

            # Generate alert if extreme conditions
            if temp > 38 or wind > 60:
                alert_id = f"alert_{city_id}_{now[:13].replace(':', '')}"
                alert_type = "heat" if temp > 38 else "storm"
                results.append({
                    "id": alert_id,
                    "type": "WeatherAlert",
                    "properties": {
                        "city": city["name"],
                        "severity": "high" if temp > 42 or wind > 70 else "medium",
                        "alert_type": alert_type,
                        "message": f"{'Extreme heat' if alert_type == 'heat' else 'High wind'} warning for {city['name']}",
                        "issued_at": now,
                    },
                })
                results.append({
                    "source_id": alert_id,
                    "target_id": city_id,
                    "edge_type": "ALERT_FOR",
                    "properties": {},
                })

        logger.info("WeatherSensor collected %d items for %d cities", len(results), len(self.CITIES))
        return results

    def decay_config(self):
        return {"ttl_hours": 48, "decay_rate": 0.05, "min_confidence": 0.1}

    async def health_check(self):
        return {"healthy": True, "message": "Demo sensor (simulated data)", "latency_ms": 0.1}


# ── API Routes ───────────────────────────────────────────────────────────

def create_weather_router() -> APIRouter:
    """Create weather-specific API routes."""
    router = APIRouter()

    @router.get("/cities")
    async def list_cities():
        """List all monitored cities."""
        return {
            "cities": [
                {"name": c["name"], "country": c["country"]}
                for c in WeatherSensor.CITIES
            ]
        }

    @router.get("/current/{city}")
    async def current_weather(city: str):
        """Get the most recent weather reading for a city.

        In a real plugin, this would query the graph for the latest
        WeatherReading node linked to the city.
        """
        return {
            "city": city,
            "temperature_c": round(random.uniform(15, 35), 1),
            "humidity_pct": round(random.uniform(30, 80), 1),
            "conditions": random.choice(["sunny", "cloudy", "rain"]),
            "note": "Demo data — connect to a real weather API for production use",
        }

    @router.get("/alerts")
    async def active_alerts(severity: str = Query(None, enum=["low", "medium", "high", "extreme"])):
        """Get active weather alerts, optionally filtered by severity."""
        return {
            "alerts": [],
            "note": "Query the graph for WeatherAlert nodes: FIND NODES WHERE type = 'WeatherAlert'",
        }

    return router


# ── MCP Tools ────────────────────────────────────────────────────────────

def _weather_lookup_handler(city: str) -> dict:
    """MCP tool handler for weather lookup."""
    return {
        "city": city,
        "temperature_c": round(random.uniform(15, 35), 1),
        "conditions": random.choice(["sunny", "cloudy", "rain"]),
    }


WEATHER_MCP_TOOL = PluginMCPTool(
    name="weather_lookup",
    description="Get current weather for a city. Returns temperature and conditions.",
    input_schema={
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "City name"},
        },
        "required": ["city"],
    },
    handler=_weather_lookup_handler,
)


# ── The Plugin Class ─────────────────────────────────────────────────────

class WeatherVertical(VerticalPlugin):
    """Weather data vertical for ContextCore.

    Demonstrates the complete plugin API:
    - Schema registration (WeatherReading, WeatherAlert, City)
    - Data collection via sensors (simulated weather data)
    - Custom API routes (/weather/cities, /weather/current/{city})
    - MCP tool (weather_lookup)
    - Dashboard card (weather overview)
    """

    name = "weather"
    version = "0.1.0"
    description = "Weather data integration — monitor conditions across cities"
    author = "ContextCore Examples"

    def schemas(self) -> List[PluginSchema]:
        return [WEATHER_SCHEMA]

    def sensors(self) -> List[Sensor]:
        return [WeatherSensor()]

    def api_routers(self) -> List[PluginRoute]:
        return [
            PluginRoute(
                router=create_weather_router(),
                prefix="/weather",
                tags=["weather"],
            )
        ]

    def mcp_tools(self) -> List[PluginMCPTool]:
        return [WEATHER_MCP_TOOL]

    def dashboard_cards(self) -> List[DashboardCard]:
        return [
            DashboardCard(
                id="weather-overview",
                title="Weather Monitor",
                component_path="weather/WeatherOverview.js",
                route="/dashboard/weather",
                icon="cloud",
                category="monitoring",
                description="Live weather conditions across monitored cities",
                order=50,
            ),
        ]

    async def on_startup(self, db) -> None:
        """Register schemas and log startup."""
        await super().on_startup(db)
        logger.info("Weather vertical started — monitoring %d cities", len(WeatherSensor.CITIES))

    def on_graph_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        """React to graph events — e.g., trigger alerts on extreme readings."""
        if event_type == "node_added":
            node_type = payload.get("label") or payload.get("type")
            if node_type == "WeatherReading":
                temp = payload.get("properties", {}).get("temperature_c", 0)
                if temp > 40:
                    logger.warning("Extreme temperature detected: %.1f°C", temp)
